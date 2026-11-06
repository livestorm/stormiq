"""The v1 measures registry (`record/measures.py`).

One test per measure covers its value, every gate and every state, on
synthetic sessions: a public webinar and an internal meeting where everyone
is a team member. The grouped measures (concentration, room map, channels,
segments, lead time, replay) also cover their rows and row gates. Also covers the registry's contract (the version pin, the
glossary anchors in docs/product.md §13, `validate_measures`) and the rules
every measure shares (sources, exclusions, the internal rule, no person in
the output). Pure functions: no database, Redis or network. Every name and
id below is made up.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import synthetic_session as synthetic
from livestorm_app.livestorm.payloads import parse_person
from livestorm_app.record import measures
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.measures import (
    APPROXIMATION,
    AUDIENCE_ONLY,
    INSUFFICIENT,
    JSON_KEYS,
    METRICS_VERSION,
    OK,
    PARTIAL,
    REGISTRY,
    REGISTRY_PIN,
    SOURCES,
    STATES,
    UNAVAILABLE,
    UNITS,
    UNRESOLVED,
    MeasureDef,
    compute_measures,
    registry_pin,
    validate_measures,
)
from livestorm_app.record.roster import PAGINATED, RosterRows, event_fields, resolve_field_definitions

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCT_DOC = REPO_ROOT / "docs" / "product.md"

T0 = int(datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).timestamp())
LENGTH_S = 3600

VITALS = (
    "m.roster_coverage",
    "m.audience_registrants",
    "m.team_count",
    "m.audience_attendees",
    "m.show_up_rate",
    "m.time_in_room",
    "m.wrote_rate",
    "m.questions_qa",
    "m.time_to_answer",
    "m.author_resolution",
    "m.livestorm_counts",
)
GROUPED = (
    "m.chat_concentration",
    "m.room_map",
    "m.channel_quality",
    "m.segment_quality",
    "m.lead_time_show_up",
    "m.replay_caught_up",
)
AUDIENCE_MEASURES = tuple(measure_id for measure_id, definition in REGISTRY.items() if definition.excludes)

# Every registry pin ever published, by METRICS_VERSION. Never edit an entry:
# a changed measure version gets a new METRICS_VERSION and a new line here.
PUBLISHED_PINS = {
    1: "3120ffec1fc37731bd6bfec4e7c863b8f98bfa2507e5617e04f4e0e75c617e07",
    2: "0b51257a2ab5dd195a1840683fe81f5185cc8130b75104834617845b359100ea",
    3: "864d68d8871758f3a65cce51e25a172b4026a366c9eda7998a272486567e8432",
}


# ── Synthetic sessions ──────────────────────────────────────────────────────


def person(person_id, *, role="participant", attended=True, guest=False, duration=None, messages=0, **detail):
    """A people record. `duration` is the attendance in seconds (default:
    1,800 for an attendee). Extra keyword arguments go in registrant_detail."""
    registrant = {
        "attended": attended,
        "is_guest_speaker": guest,
        "attendance_duration": (1800 if attended else 0) if duration is None else duration,
        "has_viewed_replay": False,
        "created_at": T0 - 86400,
    }
    registrant.update(detail)
    return {
        "id": person_id,
        "type": "people",
        "attributes": {
            "role": role,
            "first_name": "Pat",
            "last_name": person_id,
            "email": f"{person_id}@example.test",
            "messages_count": messages,
            "questions_count": 0,
            "votes_count": 0,
            "up_votes_count": 0,
            "registrant_detail": registrant,
        },
    }


def webinar(audience=12, absent=3):
    """A public webinar: a host, a co-host and a guest speaker, then
    `audience` audience members who attended (`a-01`…) and `absent` who
    didn't (`n-01`…)."""
    people = [
        person("host", role="team_member"),
        person("cohost", role="team_member"),
        person("guest", guest=True),
    ]
    people += [person(f"a-{index:02d}", duration=600 * index) for index in range(1, audience + 1)]
    people += [person(f"n-{index:02d}", attended=False) for index in range(1, absent + 1)]
    return people


def on_stage_only(absent=2):
    """A public session whose attendees are all on stage: a host and two
    guest speakers (guest speakers don't make it internal), and `absent`
    audience members who didn't come."""
    people = [person("host", role="team_member"), person("guest", guest=True), person("guest-2", guest=True)]
    return people + [person(f"n-{index:02d}", attended=False) for index in range(1, absent + 1)]


def all_hands(people_count=12, absent=1):
    """An internal meeting: everyone is a team member."""
    return [
        person(f"t-{index:02d}", role="team_member", attended=index <= people_count - absent, duration=60 * index)
        for index in range(1, people_count + 1)
    ]


def session_payload(people, **attributes):
    attended = sum(1 for p in people if p["attributes"]["registrant_detail"].get("attended"))
    base = {
        "started_at": T0,
        "ended_at": T0 + LENGTH_S,
        "duration": LENGTH_S,
        "registrants_count": len(people),
        "attendees_count": attended,
    }
    base.update(attributes)
    return {"data": {"id": "s1", "type": "sessions", "attributes": base}, "included": people}


def message(index, author, *, flag=None, linked=True, at=60):
    record = {
        "id": f"m-{index:03d}",
        "type": "chat_messages",
        "attributes": {
            "text_content": "hello",
            "created_at": T0 + at,
            "author_id": f"u-{author}",
            "from_team_member": flag == "team",
            "from_guest_speaker": flag == "guest",
        },
    }
    if linked and author is not None:
        record["relationships"] = {"people": {"data": {"type": "people", "id": author}}}
    return record


def chat(*authors, linked=True, flags=None):
    flags = flags or {}
    return {"data": [message(index, author, flag=flags.get(author), linked=linked) for index, author in enumerate(authors, 1)]}


def question(index, asker, *, asked=100, answered=None, response=None, responder="host", **attributes):
    record = {
        "id": f"q-{index:03d}",
        "type": "questions",
        "attributes": {
            "question": "why?",
            "created_at": T0 + asked if isinstance(asked, int) else asked,
            "responded_at": (T0 + answered if isinstance(answered, int) else answered),
            "response": response,
            "question_author_id": asker,
            "response_author_id": responder if response else None,
        },
    }
    record["attributes"].update(attributes)
    return record


def questions(*items):
    return {"data": list(items)}


def row(people, chat_payload=None, questions_payload=None, *, event_payload=None, **attributes):
    return {
        "session_id": "s1",
        "session_payload": session_payload(people, **attributes),
        "event_payload": event_payload,
        "chat_payload": chat() if chat_payload is None else chat_payload,
        "questions_payload": questions() if questions_payload is None else questions_payload,
    }


def complete(people):
    """The stored roster of the same people, complete."""
    return RosterRows(people=[parse_person(p) for p in people], source=PAGINATED, complete=True, record_count=len(people))


def compute(cached, roster_rows=None):
    """Every measure in its JSON form, checked."""
    found = {measure_id: value.to_json() for measure_id, value in compute_measures(load_record_inputs(cached, roster_rows=roster_rows)).items()}
    validate_measures(found)
    return found


def one(measure_id, people, *args, roster_rows=None, **kwargs):
    return compute(row(people, *args, **kwargs), roster_rows=roster_rows)[measure_id]


# ── The registry ────────────────────────────────────────────────────────────


def test_the_registry_holds_the_vitals_then_the_grouped_measures_in_order():
    assert tuple(REGISTRY) == VITALS + GROUPED
    for measure_id, definition in REGISTRY.items():
        assert definition.id == measure_id and definition.version >= 1
        assert definition.unit in UNITS
        assert definition.sources and set(definition.sources) <= set(SOURCES)
        assert definition.excludes in ("", AUDIENCE_ONLY)
        assert all(gate.below in STATES for gate in definition.gates)
    with pytest.raises(TypeError):
        REGISTRY["m.new"] = REGISTRY["m.team_count"]  # read-only


def _edited(measure_id, **changes):
    edited = dict(REGISTRY)
    edited[measure_id] = MeasureDef(**{**vars(REGISTRY[measure_id]), **changes})
    return edited


def test_metrics_version_pins_registry():
    """Changing a measure's version, unit, sources, exclusions or gates, or
    adding or removing a measure, changes the pin: bump the measure's version
    if its rule changed, bump METRICS_VERSION, set REGISTRY_PIN to
    `registry_pin()` and add the pair to PUBLISHED_PINS. A published pin
    never changes."""
    assert registry_pin() == REGISTRY_PIN, (
        "the registry changed (a version, unit, source, exclusion or gate): bump the measure's version, "
        "bump METRICS_VERSION and pin the new set"
    )
    assert PUBLISHED_PINS[METRICS_VERSION] == REGISTRY_PIN, "a published pin changed, or METRICS_VERSION wasn't bumped"
    assert METRICS_VERSION == max(PUBLISHED_PINS)
    assert len(set(PUBLISHED_PINS.values())) == len(PUBLISHED_PINS)
    assert registry_pin(_edited("m.show_up_rate", version=2)) != REGISTRY_PIN
    assert registry_pin({k: v for k, v in REGISTRY.items() if k != "m.team_count"}) != REGISTRY_PIN


@pytest.mark.parametrize(
    "measure_id, changes",
    [
        ("m.wrote_rate", {"gates": (measures.Gate("den", measures.WROTE_RATE_MIN_ATTENDEES + 10),)}),
        ("m.time_to_answer", {"gates": (measures.Gate("den", measures.TIME_TO_ANSWER_MIN_ANSWERED, below=PARTIAL),)}),
        ("m.time_in_room", {"gates": (measures.Gate("attendees", 1),)}),
        ("m.show_up_rate", {"sources": ("roster",)}),
        ("m.audience_attendees", {"excludes": ""}),
        ("m.time_to_answer", {"unit": "count"}),
    ],
    ids=["gate minimum", "gate below", "gate removed", "sources", "excludes", "unit"],
)
def test_editing_a_declared_rule_without_a_new_version_changes_the_pin(measure_id, changes):
    """Stored numbers are keyed by METRICS_VERSION, so a gate, source,
    exclusion or unit can't change under the same pin."""
    assert registry_pin(_edited(measure_id, **changes)) != REGISTRY_PIN
    reordered = dict(reversed(list(REGISTRY.items())))
    assert registry_pin(reordered) == REGISTRY_PIN, "the pin doesn't depend on order"


def github_anchor(heading):
    """The anchor GitHub gives a Markdown heading (before numbering repeats)."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def glossary_sections():
    """{anchor: section text} for every heading in product.md §13. Anchors
    are numbered over the whole file, as GitHub numbers repeats."""
    seen = Counter()
    sections = {}
    in_glossary = False
    current = None
    fenced = False
    for line in PRODUCT_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        heading = None if fenced else re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if heading is None:
            if current is not None:
                sections[current].append(line)
            continue
        slug = github_anchor(heading.group(2))
        anchor = slug if seen[slug] == 0 else f"{slug}-{seen[slug]}"
        seen[slug] += 1
        if len(heading.group(1)) == 2:
            in_glossary = heading.group(2).startswith("13.")
        current = anchor if in_glossary else None
        if current is not None:
            sections[current] = []
    return {anchor: "\n".join(lines) for anchor, lines in sections.items()}


def test_every_measure_has_a_glossary_anchor():
    """Each measure's `glossary` is a heading in product.md §13 whose section
    names the measure, and §13 names no measure the registry doesn't have."""
    sections = glossary_sections()
    assert "measures-v1-report" in sections
    for measure_id, definition in REGISTRY.items():
        assert definition.glossary in sections, f"{measure_id}: no #{definition.glossary} in product.md §13"
        assert f"`{measure_id}`" in sections[definition.glossary], f"{measure_id}: its glossary section doesn't name it"
    named = set(re.findall(r"`(m\.[a-z_]+)`", "\n".join(sections.values())))
    assert named <= set(REGISTRY), named - set(REGISTRY)


# ── The contract every measure follows ──────────────────────────────────────


@pytest.mark.parametrize(
    "cached",
    [
        row(webinar()),
        row(all_hands()),
        synthetic.cached_row(),
        {"session_id": "s1"},
        row([], chat(), questions()),
    ],
    ids=["webinar", "all-hands", "synthetic", "empty row", "no people"],
)
def test_every_measure_is_valid_strict_json(cached):
    found = compute(cached)
    assert list(found) == list(REGISTRY)
    for measure_id, item in found.items():
        assert tuple(item) == JSON_KEYS
        assert item["id"] == measure_id and item["state"] in STATES
        if item["state"] in (UNAVAILABLE, UNRESOLVED, INSUFFICIENT):
            assert item["value"] is None
    json.dumps(found, allow_nan=False)


def test_a_session_without_its_payloads_is_unavailable():
    found = compute({"session_id": "s1"})
    for item in found.values():
        assert item["state"] == UNAVAILABLE and item["value"] is None and item["num"] is None
    assert found["m.wrote_rate"]["coverage"] == {"missing": ["roster", "chat", "questions"]}


@pytest.mark.parametrize(
    "payload, unavailable",
    [
        (
            "chat_payload",
            {"m.wrote_rate", "m.author_resolution", "m.chat_concentration", "m.room_map", "m.channel_quality", "m.segment_quality"},
        ),
        (
            "questions_payload",
            {
                "m.wrote_rate", "m.author_resolution", "m.questions_qa", "m.time_to_answer", "m.room_map",
                "m.channel_quality", "m.segment_quality",
            },
        ),
    ],
)
def test_a_source_that_was_not_fetched_makes_its_measures_unavailable(payload, unavailable):
    cached = {**row(webinar()), payload: None}
    found = compute(cached)
    assert {measure_id for measure_id, item in found.items() if item["state"] == UNAVAILABLE} == unavailable


def test_a_stored_roster_without_a_session_payload():
    """The stored people are there, the session's own attributes aren't:
    the roster measures compute, those that need the session don't."""
    people = webinar()
    found = compute({"session_id": "s1"}, roster_rows=complete(people))
    unavailable = {measure_id for measure_id, item in found.items() if item["state"] == UNAVAILABLE}
    assert unavailable == {
        "m.show_up_rate", "m.livestorm_counts", "m.wrote_rate", "m.questions_qa", "m.time_to_answer", "m.author_resolution",
        "m.chat_concentration", "m.room_map", "m.channel_quality", "m.segment_quality", "m.lead_time_show_up",
    }
    assert found["m.lead_time_show_up"]["coverage"] == {"missing": ["session"]}, "no start, no lead time"
    assert found["m.show_up_rate"]["coverage"] == {"missing": ["session"]}
    assert found["m.livestorm_counts"]["coverage"] == {"missing": ["session"]}
    assert found["m.show_up_rate"]["excluded"] is None, "an unavailable measure leaves nobody out"
    assert (found["m.roster_coverage"]["value"], found["m.roster_coverage"]["state"]) == (1.0, OK)
    assert found["m.audience_registrants"]["value"] == 15 and found["m.audience_attendees"]["value"] == 12
    assert found["m.time_in_room"]["state"] == INSUFFICIENT, "no session, no length"


def test_an_empty_source_is_not_a_missing_one():
    found = compute(row(webinar(), chat(), questions()))
    assert all(item["state"] != UNAVAILABLE for item in found.values())


def test_audience_measures_say_whom_they_left_out():
    public = compute(row(webinar()))
    internal = compute(row(all_hands()))
    for measure_id in REGISTRY:
        if measure_id in AUDIENCE_MEASURES:
            assert list(public[measure_id]["excluded"]) == ["team", "guestSpeakers", "total", "internal"]
            assert public[measure_id]["excluded"]["internal"] is False
            assert internal[measure_id]["excluded"] == {"team": 0, "guestSpeakers": 0, "total": 0, "internal": True}
        else:
            assert public[measure_id]["excluded"] is None and internal[measure_id]["excluded"] is None
    assert set(AUDIENCE_MEASURES) == {
        "m.audience_registrants", "m.audience_attendees", "m.show_up_rate", "m.time_in_room", "m.wrote_rate", *GROUPED,
    }


def test_an_internal_session_is_never_0_of_0():
    """Everyone is a team member: the audience measures count everyone, as
    the classic app does on an internal session."""
    found = compute(row(all_hands(12, absent=1), chat("t-01", "t-02", flags={"t-01": "team", "t-02": "team"})))
    assert found["m.audience_registrants"]["value"] == 12
    assert found["m.audience_attendees"]["value"] == 11
    assert (found["m.show_up_rate"]["num"], found["m.show_up_rate"]["den"]) == (11, 12)
    assert (found["m.wrote_rate"]["num"], found["m.wrote_rate"]["den"]) == (2, 11)
    assert found["m.time_in_room"]["den"] == 11
    vitals = [measure_id for measure_id in AUDIENCE_MEASURES if measure_id in VITALS]
    assert all(found[measure_id]["value"] not in (None, 0) for measure_id in vitals)
    assert found["m.team_count"]["value"] == 12, "roles are never relabelled"


def test_measures_carry_no_person_or_message():
    text = json.dumps(compute(synthetic.cached_row()))
    assert [value for value in synthetic.PRIVATE_VALUES if value in text] == []
    assert "@" not in text


def test_computing_does_not_change_the_row():
    cached = synthetic.cached_row()
    before = copy.deepcopy(cached)
    compute(cached)
    assert cached == before


# ── validate_measures ───────────────────────────────────────────────────────


def _valid():
    return compute(row(webinar()))


def _broken(change):
    found = _valid()
    change(found)
    return found


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.pop("m.show_up_rate"),
        lambda m: m.update({"m.unknown": dict(m["m.team_count"], id="m.unknown")}),
        lambda m: m["m.show_up_rate"].update(version=2),
        lambda m: m["m.show_up_rate"].update(id="m.team_count"),
        lambda m: m["m.show_up_rate"].update(state="great"),
        lambda m: m["m.show_up_rate"].update(state=INSUFFICIENT),
        lambda m: m["m.show_up_rate"].update(value=math.nan),
        lambda m: m["m.show_up_rate"].update(value=-0.5),
        lambda m: m["m.show_up_rate"].update(value=True),
        lambda m: m["m.show_up_rate"].update(num=-1),
        lambda m: m["m.show_up_rate"].update(den=2.5),
        lambda m: m["m.show_up_rate"].update(num=True),
        lambda m: m["m.show_up_rate"].update(coverage=None),
        lambda m: m["m.show_up_rate"].update(excluded=[]),
        lambda m: m["m.show_up_rate"].update(livestorm=[]),
        lambda m: m["m.show_up_rate"].update(state=UNRESOLVED, value=None),
        lambda m: m["m.show_up_rate"].update(state=UNRESOLVED, value=None, num=None),  # den too is hidden
        lambda m: m["m.show_up_rate"].update(state=UNRESOLVED, value=None, den=None),  # num too is hidden
        lambda m: m["m.show_up_rate"].update(rows={}),
        lambda m: m["m.show_up_rate"].pop("rows"),
        lambda m: m["m.team_count"].update(rows=[["team", 2]]),
        lambda m: m["m.team_count"]["rows"][0].pop("key"),
        lambda m: m["m.team_count"]["rows"][1].update(key="team"),
        lambda m: m["m.show_up_rate"]["coverage"].update(when=datetime.now(timezone.utc)),
        lambda m: m["m.show_up_rate"]["livestorm"].update(value=math.inf),
    ],
)
def test_validate_measures_refuses_a_broken_set(change):
    with pytest.raises(ValueError):
        validate_measures(_broken(change))


def test_validate_measures_refuses_what_is_not_a_set():
    with pytest.raises(ValueError):
        validate_measures([])


def test_validate_measures_accepts_hidden_values_and_counts_without_den():
    validate_measures(compute(row(webinar(audience=2), chat("a-01"))))


# ── m.roster_coverage ───────────────────────────────────────────────────────


def test_roster_coverage_of_the_include_list_is_partial_without_a_count():
    item = one("m.roster_coverage", webinar())
    assert (item["value"], item["num"], item["den"], item["state"]) == (None, 18, None, PARTIAL)
    assert item["coverage"] == {"roster": "partial", "people": 18, "recordCount": None, "source": "session_include", "syncedAt": None}


def test_roster_coverage_of_a_complete_stored_roster_is_ok():
    people = webinar()
    item = one("m.roster_coverage", people, roster_rows=complete(people))
    assert (item["value"], item["num"], item["den"], item["state"]) == (1.0, 18, 18, OK)
    assert item["coverage"]["source"] == PAGINATED


def test_roster_coverage_of_a_short_stored_roster_is_partial():
    people = webinar()
    rows = RosterRows(people=[parse_person(p) for p in people], source=PAGINATED, complete=True, record_count=24)
    item = one("m.roster_coverage", people, roster_rows=rows)
    assert (item["value"], item["num"], item["den"], item["state"]) == (0.75, 18, 24, PARTIAL)


# ── m.audience_registrants and m.team_count ─────────────────────────────────


def test_audience_registrants_count_the_audience_and_leave_out_the_team():
    item = one("m.audience_registrants", webinar(audience=12, absent=3))
    assert (item["value"], item["num"], item["den"], item["state"]) == (15, None, None, PARTIAL)
    assert item["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}


def test_audience_registrants_of_a_complete_roster_are_ok():
    people = webinar()
    assert one("m.audience_registrants", people, roster_rows=complete(people))["state"] == OK


def test_a_participant_raised_to_team_by_their_own_chat_is_left_out():
    item = one("m.audience_registrants", webinar(audience=12, absent=3), chat("a-01", flags={"a-01": "team"}))
    assert item["value"] == 14 and item["excluded"]["team"] == 3


def test_audience_registrants_of_an_internal_session_are_everyone():
    item = one("m.audience_registrants", all_hands(12))
    assert item["value"] == 12 and item["excluded"]["internal"] is True


def test_team_count_counts_the_team_and_guest_speakers():
    people = webinar()
    people[2]["attributes"]["registrant_detail"]["attended"] = False  # the guest speaker didn't come
    item = one("m.team_count", people)
    assert (item["value"], item["state"]) == (3, PARTIAL)
    assert item["rows"] == [
        {"key": "team", "people": 2, "attendees": 2},
        {"key": "guest_speaker", "people": 1, "attendees": 0},
    ]
    assert one("m.team_count", people, roster_rows=complete(people))["state"] == OK


def test_team_count_of_an_internal_session_keeps_livestorms_roles():
    item = one("m.team_count", all_hands(12, absent=2))
    assert item["value"] == 12 and item["rows"][0] == {"key": "team", "people": 12, "attendees": 10}


# ── m.audience_attendees ────────────────────────────────────────────────────


def test_audience_attendees_count_the_audience_who_came():
    people = webinar(audience=12, absent=3)
    item = one("m.audience_attendees", people)
    assert (item["value"], item["state"]) == (12, PARTIAL)
    assert item["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}
    assert item["coverage"]["attendanceUnknown"] == 0
    assert one("m.audience_attendees", people, roster_rows=complete(people))["state"] == OK


def test_unknown_attendance_is_partial_and_counted():
    """On a complete roster, one audience member's attendance is unknown:
    they are not an attendee, and every measure over attendance says so."""
    people = webinar(audience=12, absent=3)
    del people[3]["attributes"]["registrant_detail"]["attended"]
    found = compute(row(people, chat("a-02")), roster_rows=complete(people))
    item = found["m.audience_attendees"]
    assert (item["value"], item["state"], item["coverage"]["attendanceUnknown"]) == (11, PARTIAL, 1)
    show_up = found["m.show_up_rate"]
    assert (show_up["num"], show_up["den"], show_up["state"], show_up["coverage"]["attendanceUnknown"]) == (11, 15, PARTIAL, 1)
    wrote = found["m.wrote_rate"]
    assert (wrote["num"], wrote["den"], wrote["state"], wrote["coverage"]["unresolvedItems"]) == (1, 11, PARTIAL, 0)
    assert found["m.roster_coverage"]["state"] == OK, "only the attendance is unknown"


def test_audience_measures_leave_out_the_team_over_registrants_or_attendees():
    """The co-host and the guest speaker registered but didn't come: the
    measures over everyone registered leave them out, the measures over
    attendees don't have them to leave out."""
    people = webinar(audience=12, absent=3)
    for record in people[1:3]:
        record["attributes"]["registrant_detail"]["attended"] = False
    found = compute(row(people, chat("a-01")), roster_rows=complete(people))
    for measure_id in ("m.audience_registrants", "m.show_up_rate"):
        assert found[measure_id]["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}, measure_id
    for measure_id in ("m.audience_attendees", "m.time_in_room", "m.wrote_rate"):
        assert found[measure_id]["excluded"] == {"team": 1, "guestSpeakers": 0, "total": 1, "internal": False}, measure_id


# ── m.show_up_rate ──────────────────────────────────────────────────────────


def test_show_up_rate_is_audience_attendees_over_audience_registrants():
    item = one("m.show_up_rate", webinar(audience=12, absent=3))
    assert (item["value"], item["num"], item["den"], item["state"]) == (0.8, 12, 15, PARTIAL)
    assert item["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}
    # Livestorm's own figure counts everyone it counts: 15 attendees of 18.
    assert item["livestorm"] == {"attendeesCount": 15, "registrantsCount": 18, "value": 0.8333}


def test_show_up_rate_of_a_complete_roster_is_ok():
    people = webinar()
    assert one("m.show_up_rate", people, roster_rows=complete(people))["state"] == OK


def test_show_up_rate_without_an_audience_is_insufficient():
    item = one("m.show_up_rate", on_stage_only(absent=0))
    assert (item["value"], item["num"], item["den"], item["state"]) == (None, 0, 0, INSUFFICIENT)
    assert item["excluded"] == {"team": 1, "guestSpeakers": 2, "total": 3, "internal": False}


def test_show_up_rate_without_livestorms_counters():
    item = one("m.show_up_rate", webinar(), registrants_count=None, attendees_count=None)
    assert item["livestorm"] == {"attendeesCount": None, "registrantsCount": None, "value": None}
    assert item["value"] == 0.8


def test_livestorms_figure_over_no_registrant_is_empty_not_zero():
    item = one("m.show_up_rate", webinar(), registrants_count=0, attendees_count=0)
    assert item["livestorm"] == {"attendeesCount": 0, "registrantsCount": 0, "value": None}


def test_show_up_rate_of_an_internal_session_counts_everyone():
    item = one("m.show_up_rate", all_hands(12, absent=3))
    assert (item["value"], item["num"], item["den"]) == (0.75, 9, 12)
    assert item["excluded"]["internal"] is True


# ── m.time_in_room ──────────────────────────────────────────────────────────


def test_time_in_the_room_is_the_median_share_of_the_session():
    # Audience attendances 600, 1,200 … 3,000 s of a 3,600 s session.
    item = one("m.time_in_room", webinar(audience=5))
    assert (item["value"], item["num"], item["den"], item["state"]) == (0.5, None, 5, APPROXIMATION)
    assert item["coverage"]["sessionLengthS"] == LENGTH_S
    assert item["coverage"]["medianAttendanceS"] == 1800, "the same median in seconds: the page computes nothing"
    assert item["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}


def test_time_in_the_room_is_an_approximation_even_on_a_complete_roster():
    people = webinar(audience=5)
    assert one("m.time_in_room", people, roster_rows=complete(people))["state"] == APPROXIMATION


def test_time_in_the_room_caps_each_share_at_the_whole_session():
    people = webinar(audience=3)
    for record in people[3:6]:
        record["attributes"]["registrant_detail"]["attendance_duration"] = 2 * LENGTH_S
    item = one("m.time_in_room", people)
    assert (item["value"], item["coverage"]["medianAttendanceS"]) == (1.0, LENGTH_S)


def test_time_in_the_room_counts_a_negative_attendance_as_zero():
    people = webinar(audience=1)
    people[3]["attributes"]["registrant_detail"]["attendance_duration"] = -120
    item = one("m.time_in_room", people)
    assert (item["value"], item["den"], item["coverage"]["medianAttendanceS"]) == (0.0, 1, 0.0)


def test_time_in_the_room_leaves_out_unknown_durations_and_counts_them():
    people = webinar(audience=4)
    del people[3]["attributes"]["registrant_detail"]["attendance_duration"]
    item = one("m.time_in_room", people)
    assert (item["den"], item["coverage"]["durationUnknown"]) == (3, 1)
    assert item["value"] == round(1800 / LENGTH_S, 4)
    assert item["state"] == APPROXIMATION, "approximation comes first; coverage says what is missing"


def test_time_in_the_room_without_attendees_is_insufficient():
    item = one("m.time_in_room", on_stage_only(absent=2))
    assert (item["value"], item["den"], item["state"]) == (None, 0, INSUFFICIENT)
    assert item["coverage"]["medianAttendanceS"] is None


def test_time_in_the_room_without_a_session_length_is_insufficient():
    item = one("m.time_in_room", webinar(audience=4), ended_at=None, duration=None)
    assert (item["value"], item["state"], item["coverage"]["sessionLengthS"]) == (None, INSUFFICIENT, None)
    assert item["coverage"]["medianAttendanceS"] is None


# ── m.wrote_rate ────────────────────────────────────────────────────────────


def test_wrote_rate_counts_attendees_who_wrote_in_chat_or_qa():
    people = webinar(audience=12, absent=3)
    found = one(
        "m.wrote_rate",
        people,
        chat("a-01", "a-01", "a-02", "host", "n-01", flags={"host": "team"}),  # n-01 didn't attend
        questions(question(1, "a-03"), question(2, "a-02")),
        roster_rows=complete(people),
    )
    assert (found["value"], found["num"], found["den"], found["state"]) == (0.25, 3, 12, OK)
    assert found["coverage"]["chatAuthors"] == "resolved" and found["coverage"]["unresolvedItems"] == 0
    assert found["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}


def test_wrote_rate_is_partial_on_the_include_roster():
    assert one("m.wrote_rate", webinar(), chat("a-01"))["state"] == PARTIAL


@pytest.mark.parametrize(
    "audience, expected",
    [(9, (None, 1, 9, INSUFFICIENT)), (10, (0.1, 1, 10, PARTIAL))],
    ids=["9 attendees", "10 attendees"],
)
def test_wrote_rate_needs_ten_attendees_and_shows_the_counts_below(audience, expected):
    found = one("m.wrote_rate", webinar(audience=audience), chat("a-01"))
    assert (found["value"], found["num"], found["den"], found["state"]) == expected


def test_wrote_rate_of_a_chat_cached_before_people_links_is_unresolved():
    """Nobody is guessed: without the chat's authors, the numerator is unknown."""
    people = webinar()
    found = one("m.wrote_rate", people, chat("a-01", "a-02", linked=False), roster_rows=complete(people))
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, None, None, UNRESOLVED)
    assert found["coverage"]["chatAuthors"] == "unresolved" and found["coverage"]["unresolvedItems"] == 2


def test_wrote_rate_with_no_asker_resolved_is_unresolved():
    """An empty chat and questions whose askers aren't in the roster: who
    wrote is unknown, never "0% wrote"."""
    people = webinar(audience=12)
    asked = questions(*(question(index, f"stranger-{index}") for index in range(1, 6)))
    found = one("m.wrote_rate", people, chat(), asked, roster_rows=complete(people))
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, None, None, UNRESOLVED)
    assert found["coverage"]["chatAuthors"] == "empty" and found["coverage"]["questionAuthors"] == "unresolved"
    assert found["coverage"]["unresolvedItems"] == 5


def test_wrote_rate_with_some_askers_resolved_is_partial():
    people = webinar(audience=12)
    asked = questions(question(1, "stranger-1"), question(2, "a-03"))
    found = one("m.wrote_rate", people, chat(), asked, roster_rows=complete(people))
    assert (found["value"], found["num"], found["den"], found["state"]) == (round(1 / 12, 4), 1, 12, PARTIAL)


def test_unresolved_wins_over_insufficient():
    found = one("m.wrote_rate", webinar(audience=4), chat("a-01", linked=False))
    assert (found["num"], found["den"], found["state"]) == (None, None, UNRESOLVED)


def test_wrote_rate_with_some_authors_unresolved_is_partial():
    people = webinar()
    found = one("m.wrote_rate", people, chat("a-01", "a-02", "outsider"), roster_rows=complete(people))
    assert (found["value"], found["num"], found["state"]) == (round(2 / 12, 4), 2, PARTIAL)
    assert found["coverage"]["chatAuthors"] == "partial" and found["coverage"]["unresolvedItems"] == 1


def test_wrote_rate_of_an_internal_session_counts_everyone():
    found = one("m.wrote_rate", all_hands(12, absent=1), chat("t-01", "t-02", "t-03", flags={"t-01": "team"}))
    assert (found["num"], found["den"]) == (3, 11)
    assert found["excluded"]["internal"] is True


# ── m.questions_qa ──────────────────────────────────────────────────────────


def test_questions_count_every_question_and_what_answered_it():
    found = one(
        "m.questions_qa",
        webinar(),
        questions_payload=questions(
            question(1, "a-01", answered=200, response="Yes."),
            question(2, "a-02", response_orally=True),
            question(3, "a-03", answered=300),  # marked answered, no text
            question(4, "host"),  # a team member's question counts too
            question(5, "a-04"),
        ),
    )
    assert (found["value"], found["num"], found["den"], found["state"]) == (0.6, 3, 5, OK)
    assert found["rows"] == [
        {"key": "written", "questions": 1},
        {"key": "oral", "questions": 1},
        {"key": "marked", "questions": 1},
        {"key": "open", "questions": 2},
    ]
    assert found["coverage"] == {"questions": 5} and found["excluded"] is None


@pytest.mark.parametrize(
    "attributes, oral",
    [
        ({"response_orally": True}, True),
        ({"response_orally": "true"}, True),
        ({"responded_orally": True}, True),
        ({"response_orally": False, "responded_orally": True}, False),
        ({"response_orally": None, "responded_orally": True}, True),
        ({}, False),
    ],
)
def test_the_oral_flag_follows_the_classic_spelling_rule(attributes, oral):
    found = one("m.questions_qa", webinar(), questions_payload=questions(question(1, "a-01", **attributes)))
    assert found["num"] == (1 if oral else 0)


def test_no_question_is_insufficient_not_zero():
    found = one("m.questions_qa", webinar())
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, 0, 0, INSUFFICIENT)


# ── m.time_to_answer ────────────────────────────────────────────────────────


def test_time_to_answer_is_the_median_delay_of_answered_questions():
    found = one(
        "m.time_to_answer",
        webinar(),
        questions_payload=questions(
            question(1, "a-01", asked=100, answered=130, response="A"),
            question(2, "a-02", asked=100, answered=190, response="B"),
            question(3, "a-03", asked="2026-10-01T09:05:00Z", answered="2026-10-01T09:07:00Z", response="C"),
            question(4, "a-04", asked=100, response_orally=True),  # answered, no time
            question(5, "a-05", asked=500, answered=400, response="D"),  # answered before it was asked: left out
            question(6, "a-06"),
        ),
    )
    assert (found["value"], found["num"], found["den"], found["state"]) == (90, None, 3, OK)
    assert found["coverage"] == {"answered": 5, "timed": 3}


def test_an_answer_in_the_same_second_counts_as_zero_delay():
    found = one(
        "m.time_to_answer",
        webinar(),
        questions_payload=questions(
            question(1, "a-01", asked=100, answered=100, response="A"),
            question(2, "a-02", asked=100, answered=130, response="B"),
            question(3, "a-03", asked=100, answered=190, response="C"),
        ),
    )
    assert (found["value"], found["den"], found["state"]) == (30, 3, OK)


def test_time_to_answer_needs_three_timed_answers():
    found = one(
        "m.time_to_answer",
        webinar(),
        questions_payload=questions(
            question(1, "a-01", asked=100, answered=130, response="A"),
            question(2, "a-02", asked=100, answered=190, response="B"),
        ),
    )
    assert (found["value"], found["den"], found["state"]) == (None, 2, INSUFFICIENT)


# ── m.author_resolution ─────────────────────────────────────────────────────


def test_author_resolution_counts_distinct_authors_matched_to_people():
    people = webinar()
    found = one(
        "m.author_resolution",
        people,
        chat("a-01", "a-01", "a-02", "outsider"),
        questions(question(1, "a-01", answered=200, response="Yes.")),
        roster_rows=complete(people),
    )
    # a-01 (chat and question), a-02, host (answer) resolve; outsider doesn't.
    assert (found["value"], found["num"], found["den"], found["state"]) == (0.75, 3, 4, PARTIAL)
    assert found["rows"][0] == {
        "key": "chat", "items": 4, "withoutAuthor": 0, "resolvedItems": 3, "authors": 3, "resolvedAuthors": 2,
        "resolvedShare": 0.6667, "state": "partial",
    }
    assert [r["key"] for r in found["rows"]] == ["chat", "question", "answer"]


def test_author_resolution_when_everyone_resolves():
    people = webinar()
    found = one("m.author_resolution", people, chat("a-01", "host"), roster_rows=complete(people))
    assert (found["value"], found["num"], found["den"], found["state"]) == (1.0, 2, 2, OK)
    assert one("m.author_resolution", people, chat("a-01", "host"))["state"] == PARTIAL, "the include roster is partial"


def test_author_resolution_of_a_chat_cached_before_people_links_is_unresolved():
    people = webinar()
    found = one(
        "m.author_resolution", people, chat("a-01", "a-02", linked=False), questions(question(1, "a-01")),
        roster_rows=complete(people),
    )
    assert (found["value"], found["num"], found["state"]) == (None, None, UNRESOLVED), "the question alone would read 100%"
    assert found["den"] is None, "the denominator would count only the linked authors"
    assert found["rows"][0]["withoutAuthor"] == 2 and found["rows"][0]["state"] == "unresolved"
    assert [r["state"] for r in found["rows"]] == ["unresolved", "resolved", "empty"], "each source keeps its own state"


def test_author_resolution_without_any_author_is_insufficient():
    found = one("m.author_resolution", webinar())
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, 0, 0, INSUFFICIENT)
    assert [r["state"] for r in found["rows"]] == ["empty", "empty", "empty"]


# ── m.livestorm_counts ──────────────────────────────────────────────────────


def test_livestorm_counts_are_provenance_only():
    people = webinar(audience=2, absent=1)
    people[3]["attributes"]["messages_count"] = 4
    people[4]["attributes"]["messages_count"] = 1
    found = one("m.livestorm_counts", people)
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, None, None, PARTIAL)
    assert found["rows"] == [
        {"key": "registrants", "value": 6, "source": "session"},
        {"key": "attendees", "value": 5, "source": "session"},
        {"key": "messages", "value": 5, "source": "people"},
        {"key": "questions", "value": 0, "source": "people"},
        {"key": "votes", "value": 0, "source": "people"},
        {"key": "upVotes", "value": 0, "source": "people"},
    ]
    assert one("m.livestorm_counts", people, roster_rows=complete(people))["state"] == OK


def test_livestorm_counts_without_counters_or_people():
    found = one("m.livestorm_counts", [], registrants_count=None, attendees_count=None)
    assert [r["value"] for r in found["rows"]] == [None] * 6


# ── The grouped audience measures: helpers ──────────────────────────────────


def crowd(prefix, count, *, attended=None, duration=1800, **detail):
    """`count` audience members `{prefix}-01`…; the first `attended` of
    them came (default: all). Extra keyword arguments go in
    registrant_detail."""
    came = count if attended is None else attended
    return [
        person(f"{prefix}-{index:02d}", attended=index <= came, duration=duration if index <= came else 0, **detail)
        for index in range(1, count + 1)
    ]


def hosts():
    return [person("host", role="team_member"), person("cohost", role="team_member"), person("guest", guest=True)]


def authors(**counts):
    """Chat authors repeated by count: `authors(a=3, b=1)` is a, a, a, b."""
    return [author.replace("_", "-") for author, count in counts.items() for _ in range(count)]


def hidden(rows, *names):
    return all(row[name] is None for row in rows for name in names)


# ── m.chat_concentration ────────────────────────────────────────────────────


def test_chat_concentration_is_the_fewest_audience_authors_who_wrote_half():
    people = webinar(audience=12)
    # 20 audience messages: 8 + 6 + 4 + 2. The host's 5 are team messages.
    writers = authors(a_01=8, a_02=6, a_03=4, a_04=2) + ["host"] * 5
    found = one("m.chat_concentration", people, chat(*writers, flags={"host": "team"}), roster_rows=complete(people))
    assert (found["value"], found["num"], found["den"], found["state"]) == (2, None, 20, OK)
    assert found["rows"] == [
        {"key": "top", "authors": 2, "messages": 14, "share": 0.7},
        {"key": "rest", "authors": 2, "messages": 6, "share": 0.3},
    ]
    assert found["coverage"]["authors"] == 4 and found["coverage"]["chatAuthors"] == "resolved"
    assert found["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}


def test_one_author_who_wrote_exactly_half_is_enough():
    people = webinar(audience=12)
    found = one("m.chat_concentration", people, chat(*authors(a_01=10, a_02=5, a_03=5)), roster_rows=complete(people))
    assert (found["value"], found["den"], found["rows"][0]["share"]) == (1, 20, 0.5)


def test_chat_concentration_needs_twenty_audience_messages_and_hides_k_below():
    people = webinar(audience=12)
    found = one("m.chat_concentration", people, chat(*authors(a_01=10, a_02=9)), roster_rows=complete(people))
    assert (found["value"], found["den"], found["state"]) == (None, 19, INSUFFICIENT)
    assert [r["key"] for r in found["rows"]] == ["top", "rest"]
    assert hidden(found["rows"], "authors", "messages", "share"), "the rows would give k away"


def test_chat_concentration_without_audience_messages_is_insufficient():
    found = one("m.chat_concentration", webinar(), chat("host", "cohost", flags={"host": "team", "cohost": "team"}))
    assert (found["value"], found["den"], found["state"], found["coverage"]["authors"]) == (None, 0, INSUFFICIENT, 0)


def test_chat_concentration_is_partial_on_the_include_roster():
    found = one("m.chat_concentration", webinar(), chat(*authors(a_01=12, a_02=8)))
    assert (found["value"], found["state"]) == (1, PARTIAL)


def test_chat_concentration_of_a_chat_cached_before_people_links_is_unresolved():
    people = webinar()
    found = one("m.chat_concentration", people, chat(*authors(a_01=12, a_02=8), linked=False), roster_rows=complete(people))
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, None, None, UNRESOLVED)
    assert hidden(found["rows"], "authors", "messages", "share")
    assert found["coverage"]["chatAuthors"] == "unresolved" and found["coverage"]["unresolvedItems"] == 20


def test_chat_concentration_counts_only_resolved_messages_and_says_so():
    """Never guessed: the outsider's 15 messages could be one person or
    many, so k is over the resolved ones, and partial."""
    people = webinar()
    found = one(
        "m.chat_concentration", people, chat(*authors(a_01=12, a_02=8, outsider=15)), roster_rows=complete(people)
    )
    assert (found["value"], found["den"], found["state"]) == (1, 20, PARTIAL)
    assert found["coverage"]["unresolvedItems"] == 15 and found["coverage"]["chatAuthors"] == "partial"


def test_chat_concentration_of_an_internal_session_counts_everyone():
    writers = authors(t_01=9, t_02=7, t_03=4)
    found = one("m.chat_concentration", all_hands(12), chat(*writers, flags={a: "team" for a in writers}))
    assert (found["value"], found["den"]) == (2, 20)
    assert found["excluded"] == {"team": 0, "guestSpeakers": 0, "total": 0, "internal": True}


# ── m.room_map ──────────────────────────────────────────────────────────────


def _cells(found):
    return {r["key"]: (r["people"], r["share"]) for r in found["rows"]}


def test_the_room_map_places_audience_attendees_by_stayed_and_wrote():
    # Attendances 600 … 7,200 s of a 3,600 s session: a-05 to a-12 stayed
    # (75% or more), a-01 to a-04 left. a-01 and a-06 chat, a-07 asks.
    people = webinar(audience=12)
    found = one(
        "m.room_map", people, chat("a-01", "a-06", "host", flags={"host": "team"}), questions(question(1, "a-07")),
        roster_rows=complete(people),
    )
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, None, 12, APPROXIMATION)
    assert _cells(found) == {
        "stayed_wrote": (2, 0.1667),
        "stayed_quiet": (6, 0.5),
        "left_wrote": (1, 0.0833),
        "left_quiet": (3, 0.25),
    }
    assert [(r["stayed"], r["wrote"]) for r in found["rows"]] == [(True, True), (True, False), (False, True), (False, False)]
    assert found["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}


def test_the_room_map_needs_ten_attendees_and_shows_the_counts_below():
    found = one("m.room_map", webinar(audience=9), chat("a-09"))
    assert (found["den"], found["state"]) == (9, INSUFFICIENT)
    assert _cells(found)["stayed_wrote"] == (1, None) and hidden(found["rows"], "share")


def test_the_room_map_leaves_out_unknown_durations_and_counts_them():
    people = webinar(audience=11)
    del people[3]["attributes"]["registrant_detail"]["attendance_duration"]
    found = one("m.room_map", people)
    assert (found["den"], found["coverage"]["durationUnknown"], found["state"]) == (10, 1, APPROXIMATION)


def test_the_room_map_without_a_session_length_places_nobody():
    """Without a length nobody can be placed, so the attendee gate (den 0)
    is the one that fails; the length gate is only a backstop."""
    found = one("m.room_map", webinar(audience=12), ended_at=None, duration=None)
    assert (found["den"], found["state"], found["coverage"]["sessionLengthS"]) == (0, INSUFFICIENT, None)
    assert [r["people"] for r in found["rows"]] == [0, 0, 0, 0]


def test_the_room_map_of_a_chat_cached_before_people_links_is_unresolved():
    people = webinar(audience=12)
    found = one("m.room_map", people, chat("a-01", "a-02", linked=False), roster_rows=complete(people))
    assert (found["den"], found["state"]) == (None, UNRESOLVED)
    assert hidden(found["rows"], "people", "share"), "who wrote is unknown, so every cell is"


def test_the_room_map_of_an_internal_session_counts_everyone():
    found = one("m.room_map", all_hands(12, absent=1), chat("t-01", "t-11", flags={"t-01": "team", "t-11": "team"}))
    # Attendances of 60 … 660 s: nobody stayed 75% of the hour.
    assert found["den"] == 11 and found["excluded"]["internal"] is True
    assert _cells(found) == {"stayed_wrote": (0, 0.0), "stayed_quiet": (0, 0.0), "left_wrote": (2, 0.1818), "left_quiet": (9, 0.8182)}


# ── m.channel_quality ───────────────────────────────────────────────────────


def channel_crowd(direct=6):
    """61 audience members by default: 25 from a newsletter UTM (20 came),
    22 from a partner's site (11 came), 5 by registration type and 3 from an
    ads UTM (both too small: Other), and `direct` with no source."""
    return (
        hosts()
        + crowd("nl", 25, attended=20, utm_source="Newsletter", utm_medium="email")
        + crowd("pa", 22, attended=11, referrer="https://www.partner.example/post?id=synthetic")
        + crowd("rt", 5, registration_type="detailed")
        + crowd("ad", 3, utm_source="Ads")
        + crowd("dr", direct, attended=2)
    )


def test_channel_quality_rows_per_channel_with_small_ones_in_other():
    people = channel_crowd()
    # pa-22 didn't come but wrote: wrote counts attendees only.
    found = one("m.channel_quality", people, chat("nl-01", "nl-02", "pa-01", "pa-22"), roster_rows=complete(people))
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, None, None, OK)
    assert [(r["key"], r["kind"]) for r in found["rows"]] == [
        ("newsletter", "channel"), ("partner.example", "channel"), ("(other)", "other"), ("direct_or_unknown", "none"),
    ]
    newsletter = found["rows"][0]
    assert newsletter == {
        "key": "newsletter", "kind": "channel", "registrants": 25, "share": round(25 / 61, 4), "attendees": 20,
        "showUpRate": 0.8, "wrote": 2, "wroteRate": 0.1, "medianTimeShare": 0.5, "medianAttendanceS": 1800.0, "state": OK,
    }
    # 11 of 22 came: the median is over the attendees (900 s with the no-shows' 0 s).
    partner = found["rows"][1]
    assert partner == {
        "key": "partner.example", "kind": "channel", "registrants": 22, "share": round(22 / 61, 4), "attendees": 11,
        "showUpRate": 0.5, "wrote": 1, "wroteRate": round(1 / 11, 4), "medianTimeShare": 0.5, "medianAttendanceS": 1800.0,
        "state": OK,
    }
    other = found["rows"][2]
    assert (other["channels"], other["registrants"], other["state"]) == (2, 8, INSUFFICIENT)
    assert other["showUpRate"] is None and other["share"] == round(8 / 61, 4), "a small row hides its rates, not its size"
    assert found["rows"][3] == {
        "key": "direct_or_unknown", "kind": "none", "registrants": 6, "share": round(6 / 61, 4), "attendees": 2,
        "showUpRate": None, "wrote": 0, "wroteRate": None, "medianTimeShare": None, "medianAttendanceS": None,
        "state": INSUFFICIENT,
    }, "under 20, the direct row hides its rates too"
    assert found["coverage"]["withSource"] == 55 and found["coverage"]["sourcedShare"] == round(55 / 61, 4)
    assert found["coverage"]["channels"] == 2 and found["coverage"]["channelRule"] == 1
    assert found["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}


def test_channel_rows_of_twenty_show_their_rates():
    """At exactly 20 registrants a channel is named, and the Other and
    direct rows show their rates."""
    people = (
        hosts()
        + crowd("nl", 25, attended=20, utm_source="newsletter")
        + crowd("pa", 20, attended=15, referrer="https://partner.example/")
        + crowd("ad", 10, attended=5, utm_source="ads")
        + crowd("rt", 10, attended=5, registration_type="detailed")
        + crowd("dr", 20, attended=10)
    )
    found = one("m.channel_quality", people, roster_rows=complete(people))
    assert found["state"] == OK
    summary = [(r["key"], r["kind"], r["registrants"], r["showUpRate"], r["state"]) for r in found["rows"]]
    assert summary == [
        ("newsletter", "channel", 25, 0.8, OK),
        ("partner.example", "channel", 20, 0.75, OK),
        ("(other)", "other", 20, 0.5, OK),
        ("direct_or_unknown", "none", 20, 0.5, OK),
    ]
    assert all(r["medianAttendanceS"] == 1800.0 for r in found["rows"])


def test_channel_quality_is_partial_on_the_include_roster():
    assert one("m.channel_quality", channel_crowd())["state"] == PARTIAL


def test_channel_quality_is_partial_when_an_author_or_an_attendance_is_unknown():
    people = channel_crowd()
    assert one("m.channel_quality", people, roster_rows=complete(people))["state"] == OK
    found = one("m.channel_quality", people, chat("nl-01", "outsider"), roster_rows=complete(people))
    assert (found["state"], found["coverage"]["unresolvedItems"]) == (PARTIAL, 1)
    assert (found["rows"][0]["wrote"], found["rows"][0]["wroteRate"]) == (1, 0.05), "some resolved: who wrote is shown"
    people[5]["attributes"]["registrant_detail"]["attended"] = None  # nl-03
    found = one("m.channel_quality", people, roster_rows=complete(people))
    assert (found["state"], found["coverage"]["attendanceUnknown"]) == (PARTIAL, 1)


def test_channel_quality_needs_half_the_registrants_with_a_source():
    people = channel_crowd(direct=60)
    found = one("m.channel_quality", people, roster_rows=complete(people))
    assert (found["state"], found["coverage"]["sourcedShare"]) == (INSUFFICIENT, round(55 / 115, 4))
    assert hidden(found["rows"], "share", "showUpRate", "wroteRate", "medianTimeShare", "medianAttendanceS")
    assert {r["state"] for r in found["rows"]} == {INSUFFICIENT}
    assert found["rows"][0]["registrants"] == 25 and found["rows"][0]["attendees"] == 20, "the counts are still shown"


def test_channel_quality_needs_two_channels_of_twenty():
    people = hosts() + crowd("nl", 25, utm_source="newsletter") + crowd("ad", 19, utm_source="ads")
    found = one("m.channel_quality", people, roster_rows=complete(people))
    assert (found["state"], found["coverage"]["channels"]) == (INSUFFICIENT, 1)
    assert [r["key"] for r in found["rows"]] == ["newsletter", "(other)"]


def test_channel_quality_without_any_source_is_insufficient_not_direct_100():
    """An internal meeting has no UTM, referrer or registration type. One
    direct row of everyone would be "direct 100%" in counts, so there is
    no row; the coverage says why."""
    found = one("m.channel_quality", all_hands(30, absent=2))
    assert found["state"] == INSUFFICIENT and found["excluded"]["internal"] is True
    assert (found["coverage"]["registrants"], found["coverage"]["withSource"], found["coverage"]["sourcedShare"]) == (30, 0, 0.0)
    assert found["rows"] == []


def test_a_channel_named_like_a_reserved_row_folds_into_other():
    people = hosts() + crowd("nl", 25, utm_source="newsletter") + crowd("x", 25, utm_source="direct_or_unknown") + crowd("dr", 3)
    found = one("m.channel_quality", people, roster_rows=complete(people))
    assert [r["key"] for r in found["rows"]] == ["newsletter", "(other)", "direct_or_unknown"]
    assert found["rows"][1]["registrants"] == 25 and found["state"] == INSUFFICIENT


def without_who_wrote(rows):
    return [{name: value for name, value in r.items() if name not in ("wrote", "wroteRate")} for r in rows]


@pytest.mark.parametrize(
    "unknown, coverage",
    [
        ({"chat_payload": chat("nl-01", "pa-01", linked=False)}, ("chatAuthors", 2)),
        ({"questions_payload": questions(question(1, "stranger-1"), question(2, "stranger-2"))}, ("questionAuthors", 2)),
    ],
    ids=["chat cached before people links", "no asker resolved"],
)
def test_channel_quality_with_unresolved_authors_hides_only_who_wrote(unknown, coverage):
    """Where people registered from, who came and how long they stayed
    don't depend on authors: only `wrote` and `wroteRate` are hidden, the
    measure is partial, not unresolved, and its coverage says why."""
    people = channel_crowd()
    known = one("m.channel_quality", people, chat("nl-01", "pa-01"), roster_rows=complete(people))
    found = one(
        "m.channel_quality", people, unknown.get("chat_payload"), unknown.get("questions_payload"), roster_rows=complete(people)
    )
    assert (known["state"], found["state"]) == (OK, PARTIAL)
    source, items = coverage
    assert (found["coverage"][source], found["coverage"]["unresolvedItems"]) == ("unresolved", items)
    assert hidden(found["rows"], "wrote", "wroteRate") and known["rows"][0]["wrote"] == 1
    assert without_who_wrote(found["rows"]) == without_who_wrote(known["rows"])
    newsletter = found["rows"][0]
    assert (newsletter["share"], newsletter["showUpRate"], newsletter["medianTimeShare"], newsletter["medianAttendanceS"]) == (
        round(25 / 61, 4), 0.8, 0.5, 1800.0,
    )


def test_channel_quality_with_unresolved_authors_still_needs_its_gates():
    """Unresolved authors don't lift a gate: below it, every value is
    hidden; with no source at all there is still no row."""
    people = channel_crowd(direct=60)
    found = one("m.channel_quality", people, chat("nl-01", linked=False), roster_rows=complete(people))
    assert found["state"] == INSUFFICIENT
    assert hidden(found["rows"], "share", "showUpRate", "wrote", "wroteRate", "medianTimeShare", "medianAttendanceS")
    assert found["rows"][0]["registrants"] == 25
    no_source = hosts() + crowd("dr", 30)
    found = one("m.channel_quality", no_source, chat("dr-01", linked=False), roster_rows=complete(no_source))
    assert (found["state"], found["rows"], found["coverage"]["withSource"]) == (INSUFFICIENT, [], 0)


def test_channel_quality_without_any_source_on_a_public_webinar_has_no_rows():
    """Not only an internal meeting: an audience with no UTM, referrer or
    registration type never gets a one-row "direct_or_unknown: N of N"."""
    people = hosts() + crowd("dr", 40, attended=30)
    found = one("m.channel_quality", people, chat("dr-01"), roster_rows=complete(people))
    assert found["excluded"]["internal"] is False
    assert (found["state"], found["rows"]) == (INSUFFICIENT, [])
    assert (found["coverage"]["registrants"], found["coverage"]["withSource"]) == (40, 0)


def test_only_measures_whose_value_needs_authors_go_unresolved():
    """With a chat cached before people links, the measures whose value is
    who wrote are unresolved; channel and segment quality are partial."""
    people = channel_crowd() + answered("seg", 12, team_size="1-10")
    found = compute(row(people, chat("nl-01", linked=False), event_payload=event()))
    unresolved = {measure_id for measure_id, item in found.items() if item["state"] == UNRESOLVED}
    assert unresolved == {"m.wrote_rate", "m.author_resolution", "m.chat_concentration", "m.room_map"}
    assert (found["m.channel_quality"]["state"], found["m.segment_quality"]["state"]) == (PARTIAL, INSUFFICIENT)


def test_channel_quality_leaves_out_the_team():
    people = channel_crowd()
    for record in people[:2]:
        record["attributes"]["registrant_detail"]["utm_source"] = "newsletter"
    found = one("m.channel_quality", people, roster_rows=complete(people))
    assert found["rows"][0]["registrants"] == 25


def test_channel_quality_of_an_internal_session_counts_everyone():
    people = [person(f"t-{i:02d}", role="team_member", utm_source="slack") for i in range(20)]
    people += [person(f"u-{i:02d}", role="team_member", attended=i < 10, utm_source="mail") for i in range(20)]
    found = one("m.channel_quality", people)
    assert found["excluded"]["internal"] is True
    assert [(r["key"], r["registrants"], r["showUpRate"]) for r in found["rows"]] == [("mail", 20, 0.5), ("slack", 20, 1.0)]


# ── m.segment_quality ───────────────────────────────────────────────────────


EVENT_FIELDS = [
    {"id": "email", "type": "text", "order": 0},
    {"id": "team_size", "type": "unique_select", "order": 1},
    {"id": "topics", "type": "multiple_select", "order": 2},
    {"id": "comment", "type": "text", "order": 3},
]


def event(fields=EVENT_FIELDS):
    return {"data": {"id": "e1", "type": "events", "attributes": {"fields": fields}}}


def answered(prefix, count, *, team_size=None, topics=None, **detail):
    fields = []
    if team_size is not None:
        fields.append({"id": "team_size", "type": "unique_select", "value": team_size})
    if topics is not None:
        fields.append({"id": "topics", "type": "multiple_select", "value": topics})
    fields.append({"id": "comment", "type": "text", "value": "free text"})
    return crowd(prefix, count, fields=fields, **detail)


def segment_crowd():
    """27 audience members: team size 1-10 (12, 9 came), 11-50 (11) or
    51-200 (4: Other); topics ai and video (15), ai alone (10) or sales (2: Other)."""
    return (
        hosts()
        + answered("s", 12, attended=9, team_size="1-10", topics=["ai", "video"])
        + answered("m", 3, team_size="11-50", topics=["ai", "video"])
        + answered("l", 8, team_size="11-50", topics=["ai"])
        + answered("x", 2, team_size="51-200", topics=["ai"])
        + answered("y", 2, team_size="51-200", topics=["sales"])
    )


def _segments(found):
    return {r["key"]: (r["registrants"], r["share"], r["showUpRate"], r["state"]) for r in found["rows"]}


def test_segment_quality_rows_per_select_value():
    people = segment_crowd()
    found = one("m.segment_quality", people, event_payload=event(), roster_rows=None)
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, None, None, PARTIAL)
    assert _segments(found) == {
        "team_size:1-10": (12, round(12 / 27, 4), 0.75, OK),
        "team_size:11-50": (11, round(11 / 27, 4), 1.0, OK),
        "team_size:(other)": (4, round(4 / 27, 4), None, INSUFFICIENT),
        "topics:ai": (25, round(25 / 27, 4), 0.88, OK),
        "topics:video": (15, round(15 / 27, 4), 0.8, OK),
        "topics:(other)": (2, round(2 / 27, 4), None, INSUFFICIENT),
    }
    first = found["rows"][0]
    assert {key: first[key] for key in ("kind", "field", "value", "label")} == {
        "kind": "value", "field": "team_size", "value": "1-10", "label": "1-10",
    }
    assert found["coverage"]["fields"] == [
        {"field": "team_size", "name": "Team size", "type": "unique_select", "answered": 27, "segments": 2},
        {"field": "topics", "name": "Topics", "type": "multiple_select", "answered": 27, "segments": 2},
    ]
    assert (found["coverage"]["selectFields"], found["coverage"]["fieldsSource"]) == (2, "event_fields")


def test_multi_select_shares_can_add_up_to_more_than_one():
    found = one("m.segment_quality", segment_crowd(), event_payload=event())
    topics = [r["share"] for r in found["rows"] if r["field"] == "topics"]
    assert sum(topics) > 1


def test_segment_labels_come_from_the_options_when_known():
    people = segment_crowd()
    attributes = [
        {"slug": "team_size", "name": "Team size", "type": "unique_select",
         "options": [{"value": "11-50", "label": "11 to 50", "position": 0}, {"value": "1-10", "label": "1 to 10", "position": 1}]},
    ]
    fields, source = resolve_field_definitions(event_fields(event()), attributes)
    rows = RosterRows(
        people=[parse_person(p) for p in people], complete=True, record_count=len(people), fields=fields, fields_source=source,
    )
    found = one("m.segment_quality", people, roster_rows=rows)
    assert found["state"] == OK
    team_size = [(r["value"], r["label"]) for r in found["rows"] if r["field"] == "team_size"]
    assert team_size == [("11-50", "11 to 50"), ("1-10", "1 to 10"), (None, None)], "in the options' order, Other last"


def test_a_field_needs_two_values_of_ten():
    people = hosts() + answered("s", 12, team_size="1-10") + answered("m", 9, team_size="11-50")
    found = one("m.segment_quality", people, event_payload=event())
    assert found["state"] == INSUFFICIENT
    assert found["coverage"]["fields"][0]["segments"] == 1
    assert _segments(found)["team_size:1-10"] == (12, None, None, INSUFFICIENT), "the measure's value is hidden"


def test_a_field_below_the_gate_hides_its_rates_when_another_field_passes():
    people = (
        hosts()
        + answered("s", 12, team_size="1-10", topics=["ai"])
        + answered("m", 10, team_size="1-10", topics=["video"])
        + answered("x", 5, team_size="11-50", topics=["ai"])
        + answered("y", 5, team_size="51-200", topics=["video"])
    )
    found = one("m.segment_quality", people, event_payload=event())
    assert found["state"] == PARTIAL
    segments = _segments(found)
    assert segments["team_size:1-10"] == (22, round(22 / 32, 4), None, INSUFFICIENT), "one value is not a comparison"
    assert segments["team_size:(other)"] == (10, round(10 / 32, 4), None, INSUFFICIENT), "its Other reaches 10, but the field fails"
    assert segments["topics:ai"] == (17, round(17 / 32, 4), 1.0, OK)


def test_the_other_row_of_a_shown_field_shows_its_rates_from_ten():
    people = (
        hosts()
        + answered("s", 12, team_size="1-10")
        + answered("m", 11, team_size="11-50")
        + answered("l", 6, attended=3, team_size="51-200")
        + answered("x", 4, attended=2, team_size="201+")
    )
    found = one("m.segment_quality", people, event_payload=event(), roster_rows=None)
    other = [r for r in found["rows"] if r["key"] == "team_size:(other)"]
    assert [(r["values"], r["registrants"], r["showUpRate"], r["medianAttendanceS"], r["state"]) for r in other] == [
        (2, 10, 0.5, 1800.0, OK)
    ]


def test_a_value_named_like_the_other_row_folds_into_it():
    people = (
        hosts()
        + answered("s", 12, team_size="1-10")
        + answered("m", 11, team_size="11-50")
        + answered("o", 10, team_size="(other)")
        + answered("x", 3, team_size="51-200")
    )
    found = one("m.segment_quality", people, event_payload=event())
    keys = [r["key"] for r in found["rows"]]
    assert keys == ["team_size:1-10", "team_size:11-50", "team_size:(other)"]
    assert (found["rows"][2]["values"], found["rows"][2]["registrants"]) == (2, 13)


def test_segment_quality_leaves_out_the_team():
    people = segment_crowd()
    for record in people[:3]:  # the host, the co-host and the guest speaker answer too
        record["attributes"]["registrant_detail"]["fields"] = [
            {"id": "team_size", "type": "unique_select", "value": "1-10"},
            {"id": "topics", "type": "multiple_select", "value": ["ai"]},
        ]
    found = one("m.segment_quality", people, event_payload=event())
    assert _segments(found) == _segments(one("m.segment_quality", segment_crowd(), event_payload=event()))
    assert [item["answered"] for item in found["coverage"]["fields"]] == [27, 27]
    assert found["excluded"]["total"] == 3


def test_segment_quality_is_partial_when_an_author_or_an_attendance_is_unknown():
    people = segment_crowd()
    fields, source = resolve_field_definitions(event_fields(event()))

    def rows():
        return RosterRows(
            people=[parse_person(p) for p in people], complete=True, record_count=len(people), fields=fields, fields_source=source,
        )

    assert one("m.segment_quality", people, roster_rows=rows())["state"] == OK
    found = one("m.segment_quality", people, chat("s-01", "outsider"), roster_rows=rows())
    assert (found["state"], found["coverage"]["unresolvedItems"]) == (PARTIAL, 1)
    people[3]["attributes"]["registrant_detail"]["attended"] = None  # s-01
    found = one("m.segment_quality", people, roster_rows=rows())
    assert (found["state"], found["coverage"]["attendanceUnknown"]) == (PARTIAL, 1)


def test_segment_quality_without_a_select_field_is_insufficient():
    found = one("m.segment_quality", webinar(), event_payload=event([{"id": "comment", "type": "text"}]))
    assert (found["state"], found["rows"], found["coverage"]["selectFields"]) == (INSUFFICIENT, [], 0)
    assert one("m.segment_quality", webinar())["coverage"]["fieldsSource"] == "none", "no event fields cached"


def test_segment_quality_of_a_chat_cached_before_people_links_hides_only_who_wrote():
    """As channel quality: partial, not unresolved, with only `wrote` and
    `wroteRate` hidden."""
    people = segment_crowd()
    fields, source = resolve_field_definitions(event_fields(event()))
    stored = RosterRows(
        people=[parse_person(p) for p in people], complete=True, record_count=len(people), fields=fields, fields_source=source,
    )
    known = one("m.segment_quality", people, chat("s-01", "l-01"), roster_rows=stored)
    found = one("m.segment_quality", people, chat("s-01", "l-01", linked=False), roster_rows=stored)
    assert (known["state"], found["state"]) == (OK, PARTIAL)
    assert (found["coverage"]["chatAuthors"], found["coverage"]["unresolvedItems"]) == ("unresolved", 2)
    assert hidden(found["rows"], "wrote", "wroteRate") and known["rows"][0]["wrote"] == 1
    assert without_who_wrote(found["rows"]) == without_who_wrote(known["rows"])
    assert _segments(found)["team_size:1-10"] == (12, round(12 / 27, 4), 0.75, OK)


def test_segment_quality_of_an_internal_session_counts_everyone():
    people = [person(f"t-{i:02d}", role="team_member", fields=[{"id": "team_size", "type": "unique_select", "value": size}])
              for i, size in enumerate(["1-10"] * 10 + ["11-50"] * 10)]
    found = one("m.segment_quality", people, event_payload=event())
    assert found["excluded"]["internal"] is True
    assert _segments(found) == {"team_size:1-10": (10, 0.5, 1.0, OK), "team_size:11-50": (10, 0.5, 1.0, OK)}


# ── m.lead_time_show_up ─────────────────────────────────────────────────────


DAY = 86400


def lead_crowd(after=10):
    """Audience members by lead time: same day 40 (30 came), 1-3 days 30
    (15 came), 4-7 days 10, 8-14 days 5, 15+ days 25 (20 came), and
    `after` who registered after the start (all came)."""
    hours = 3600
    return (
        hosts()
        + crowd("sd", 40, attended=30, created_at=T0 - 2 * hours)
        + crowd("d1", 30, attended=15, created_at=T0 - 2 * DAY)
        + crowd("d4", 10, created_at=T0 - 5 * DAY)
        + crowd("d8", 5, created_at=T0 - 10 * DAY)
        + crowd("d15", 25, attended=20, created_at=T0 - 20 * DAY)
        + crowd("af", after, created_at=T0 + 300)
    )



def test_lead_time_show_up_merges_small_buckets_with_a_neighbour():
    people = lead_crowd()
    found = one("m.lead_time_show_up", people, roster_rows=complete(people))
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, None, 120, OK)
    summary = [(r["key"], r["buckets"], r["registrants"], r["attendees"], r["showUpRate"], r["state"]) for r in found["rows"]]
    assert summary == [
        ("same_day", ["same_day"], 40, 30, 0.75, OK),
        ("1-3", ["1-3"], 30, 15, 0.5, OK),
        # 8-14 (5) joins 4-7 (10), its smaller neighbour; then 4-7..8-14 (15) joins 15+ (25).
        ("4-7..15+", ["4-7", "8-14", "15+"], 40, 35, 0.875, OK),
        # Registering after the start is never merged: those people registered to join.
        ("after_start", ["after_start"], 10, 10, None, INSUFFICIENT),
    ]
    assert found["rows"][0]["share"] == round(40 / 120, 4)
    assert found["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}


def test_a_lead_time_bucket_of_twenty_keeps_its_row_and_its_rate():
    people = (
        hosts()
        + crowd("sd", 20, attended=10, created_at=T0 - 2 * 3600)
        + crowd("d1", 80, attended=40, created_at=T0 - 2 * DAY)
    )
    found = one("m.lead_time_show_up", people, roster_rows=complete(people))
    assert (found["den"], found["state"]) == (100, OK)
    assert [(r["key"], r["registrants"], r["showUpRate"], r["state"]) for r in found["rows"]] == [
        ("same_day", 20, 0.5, OK),
        ("1-3", 80, 0.5, OK),
    ]


def test_lead_time_is_partial_on_the_include_roster_or_with_an_unknown_attendance():
    assert one("m.lead_time_show_up", lead_crowd())["state"] == PARTIAL
    people = lead_crowd()
    assert one("m.lead_time_show_up", people, roster_rows=complete(people))["state"] == OK
    people[3]["attributes"]["registrant_detail"]["attended"] = None  # sd-01
    found = one("m.lead_time_show_up", people, roster_rows=complete(people))
    assert (found["state"], found["coverage"]["attendanceUnknown"]) == (PARTIAL, 1)


def test_lead_time_show_up_needs_a_hundred_registrants():
    people = lead_crowd(after=0)[:-11]  # 99 audience members: 14 of the 25 at 15+ days
    found = one("m.lead_time_show_up", people)
    assert (found["den"], found["state"]) == (99, INSUFFICIENT)
    assert hidden(found["rows"], "share", "showUpRate") and {r["state"] for r in found["rows"]} == {INSUFFICIENT}
    assert found["rows"][0]["registrants"] == 40, "the counts are still shown"


def test_lead_time_without_a_start_is_insufficient_and_says_why():
    found = one("m.lead_time_show_up", lead_crowd(), started_at=None)
    assert (found["den"], found["state"], found["rows"]) == (0, INSUFFICIENT, [])
    assert found["coverage"]["leadUnknown"] == 120


def test_lead_time_is_partial_when_a_registration_time_is_missing():
    people = lead_crowd()
    del people[3]["attributes"]["registrant_detail"]["created_at"]
    found = one("m.lead_time_show_up", people, roster_rows=complete(people))
    assert (found["den"], found["state"], found["coverage"]["leadUnknown"]) == (119, PARTIAL, 1)


@pytest.mark.parametrize(
    "sizes, expected",
    [
        ({"a": 25, "b": 30}, [["a"], ["b"]]),
        ({"a": 20, "b": 25}, [["a"], ["b"]]),  # 20 is enough
        ({"a": 5, "b": 30, "c": 25}, [["a", "b"], ["c"]]),
        ({"a": 30, "b": 5, "c": 5}, [["a", "b", "c"]]),  # b joins c, its smaller neighbour; still under 20, they join a
        ({"a": 25, "b": 5, "c": 25}, [["a", "b"], ["c"]]),  # neighbours tie: the earlier one
        # Smallest tie: the earlier one first. a joins b, then c joins d; had c
        # gone first, it would have joined b (neighbours tie), then a: abc, d.
        ({"a": 5, "b": 30, "c": 5, "d": 30}, [["a", "b"], ["c", "d"]]),
        ({"a": 3, "b": 4}, [["a", "b"]]),
        ({"a": 3}, [["a"]]),
    ],
)
def test_merging_small_buckets(sizes, expected):
    groups = measures._merge_small([[key] for key in sizes], sizes, 20)
    assert groups == expected
    assert sorted(key for group in groups for key in group) == sorted(sizes)


def test_lead_time_of_an_internal_session_counts_everyone():
    people = [person(f"t-{i:03d}", role="team_member", attended=i % 2 == 0, created_at=T0 - 2 * 3600) for i in range(100)]
    found = one("m.lead_time_show_up", people)
    assert found["excluded"]["internal"] is True
    assert [(r["key"], r["registrants"], r["showUpRate"]) for r in found["rows"]] == [("same_day", 100, 0.5)]


# ── m.replay_caught_up ──────────────────────────────────────────────────────


def replay_webinar(absent=11):
    """12 audience attendees and `absent` no-shows (`n-01`…): n-01 watched
    the replay, a-01 watched it again, and so did the co-host (team), who
    didn't come."""
    people = webinar(audience=12, absent=absent)
    people[15]["attributes"]["registrant_detail"]["has_viewed_replay"] = True  # n-01
    people[3]["attributes"]["registrant_detail"]["has_viewed_replay"] = True  # a-01 watched it again
    people[1]["attributes"]["registrant_detail"].update(attended=False, has_viewed_replay=True)  # the co-host: team
    return people


def test_replay_caught_up_counts_audience_no_shows_who_watched():
    found = one("m.replay_caught_up", replay_webinar())
    assert (found["value"], found["num"], found["den"], found["state"]) == (round(1 / 11, 4), 1, 11, PARTIAL)
    assert found["rows"] == [
        {"key": "no_shows", "people": 11, "viewedReplay": 1, "share": round(1 / 11, 4)},
        {"key": "attendees", "people": 12, "viewedReplay": 1, "share": round(1 / 12, 4)},
    ]
    assert found["excluded"] == {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False}
    assert (found["coverage"]["settled"], found["coverage"]["syncedAt"]) == (False, None)


def _synced(people, at):
    return RosterRows(people=[parse_person(p) for p in people], complete=True, record_count=len(people), synced_at=at)


def test_replay_views_settle_seven_days_after_the_end():
    people = replay_webinar()
    end = datetime.fromtimestamp(T0 + LENGTH_S, tz=timezone.utc)
    settled = one("m.replay_caught_up", people, roster_rows=_synced(people, end + timedelta(days=7)))
    assert (settled["state"], settled["coverage"]["settled"]) == (OK, True)
    assert settled["coverage"]["syncedAt"] == (end + timedelta(days=7)).isoformat()
    early = one("m.replay_caught_up", people, roster_rows=_synced(people, end + timedelta(days=6, hours=23)))
    assert (early["state"], early["coverage"]["settled"]) == (PARTIAL, False)
    no_end = one("m.replay_caught_up", people, roster_rows=_synced(people, end + timedelta(days=30)), ended_at=None, duration=None)
    assert no_end["coverage"]["settled"] is False


def test_an_unknown_replay_flag_is_left_out_and_counted():
    people = replay_webinar()
    people[16]["attributes"]["registrant_detail"]["has_viewed_replay"] = None  # n-02
    found = one("m.replay_caught_up", people)
    assert (found["num"], found["den"], found["coverage"]["replayUnknown"]) == (1, 10, 1)


def _settled(people, **header):
    end = datetime.fromtimestamp(T0 + LENGTH_S, tz=timezone.utc)
    rows = _synced(people, end + timedelta(days=7))
    return RosterRows(**{**vars(rows), **header})


def test_replay_caught_up_once_settled_is_partial_for_each_unknown():
    """On a settled, complete roster, each unknown makes it partial on its own."""
    people = replay_webinar()
    assert one("m.replay_caught_up", people, roster_rows=_settled(people))["state"] == OK

    no_show = replay_webinar()
    no_show[17]["attributes"]["registrant_detail"]["attended"] = None  # n-03: came or not, unknown
    found = one("m.replay_caught_up", no_show, roster_rows=_settled(no_show))
    assert (found["num"], found["den"], found["coverage"]["attendanceUnknown"], found["state"]) == (1, 10, 1, PARTIAL)

    attendee = replay_webinar()
    attendee[4]["attributes"]["registrant_detail"]["has_viewed_replay"] = None  # a-02
    found = one("m.replay_caught_up", attendee, roster_rows=_settled(attendee))
    assert found["rows"][1] == {"key": "attendees", "people": 11, "viewedReplay": 1, "share": round(1 / 11, 4)}
    assert (found["coverage"]["replayUnknown"], found["state"]) == (1, PARTIAL)

    short = one("m.replay_caught_up", people, roster_rows=_settled(people, complete=False))
    assert (short["coverage"]["settled"], short["state"]) == (True, PARTIAL)


def test_replay_caught_up_without_no_shows_is_insufficient():
    found = one("m.replay_caught_up", webinar(absent=0))
    assert (found["value"], found["den"], found["state"]) == (None, 0, INSUFFICIENT)
    assert hidden(found["rows"], "share") and found["rows"][1]["people"] == 12


@pytest.mark.parametrize(
    "absent, expected",
    [(9, (None, 1, 9, INSUFFICIENT)), (10, (0.1, 1, 10, OK))],
    ids=["9 no-shows", "10 no-shows"],
)
def test_replay_caught_up_needs_ten_no_shows_and_shows_the_counts_below(absent, expected):
    """One or two no-shows would read as 0% or 100%: below 10, the counts
    only."""
    people = replay_webinar(absent=absent)
    found = one("m.replay_caught_up", people, roster_rows=_settled(people))
    assert (found["value"], found["num"], found["den"], found["state"]) == expected
    assert found["rows"][0]["people"] == absent and found["rows"][0]["viewedReplay"] == 1
    assert (found["rows"][0]["share"] is None) == (absent < 10)
    assert (found["rows"][1]["share"] is None) == (absent < 10), "the attendees' share hides with the value"


def test_replay_caught_up_of_an_internal_session_counts_everyone():
    people = all_hands(12, absent=2)
    people[11]["attributes"]["registrant_detail"]["has_viewed_replay"] = True
    found = one("m.replay_caught_up", people)
    assert (found["num"], found["den"], found["excluded"]["internal"]) == (1, 2, True)


# ── Rows ────────────────────────────────────────────────────────────────────


def test_grouped_rows_have_unique_keys_and_carry_no_person():
    people = channel_crowd() + answered("seg", 12, team_size="1-10")
    found = compute(row(people, chat("nl-01", "seg-01"), event_payload=event()))
    for measure_id in GROUPED:
        keys = [r["key"] for r in found[measure_id]["rows"]]
        assert len(keys) == len(set(keys)), measure_id
    text = json.dumps({measure_id: found[measure_id] for measure_id in GROUPED})
    assert "Pat" not in text and "@" not in text and "synthetic" not in text and "free text" not in text
    assert "nl-01" not in text, "no Livestorm person id"


# ── The synthetic webinar ───────────────────────────────────────────────────


def test_the_synthetic_webinar():
    """The values the frontend fixture shows (record-v1.json)."""
    found = compute(synthetic.cached_row())
    summary = {measure_id: (item["value"], item["num"], item["den"], item["state"]) for measure_id, item in found.items()}
    assert summary == {
        "m.roster_coverage": (None, 10, None, PARTIAL),
        "m.audience_registrants": (7, None, None, PARTIAL),
        "m.team_count": (3, None, None, PARTIAL),
        "m.audience_attendees": (5, None, None, PARTIAL),
        "m.show_up_rate": (0.7143, 5, 7, PARTIAL),
        "m.time_in_room": (0.75, None, 5, APPROXIMATION),
        "m.wrote_rate": (None, 5, 5, INSUFFICIENT),
        "m.questions_qa": (0.75, 3, 4, OK),
        "m.time_to_answer": (None, None, 2, INSUFFICIENT),
        "m.author_resolution": (1.0, 8, 8, PARTIAL),
        "m.livestorm_counts": (None, None, None, PARTIAL),
        "m.chat_concentration": (None, None, 13, INSUFFICIENT),
        "m.room_map": (None, None, 5, INSUFFICIENT),
        "m.channel_quality": (None, None, None, INSUFFICIENT),
        "m.segment_quality": (None, None, None, INSUFFICIENT),
        "m.lead_time_show_up": (None, None, 7, INSUFFICIENT),
        "m.replay_caught_up": (None, 1, 2, INSUFFICIENT),
    }


def test_the_synthetic_webinar_with_its_chat_cached_before_people_links():
    cached = {**synthetic.cached_row(), "chat_payload": synthetic.chat_payload(people_links=False)}
    found = compute(cached)
    assert found["m.wrote_rate"]["state"] == UNRESOLVED and found["m.wrote_rate"]["num"] is None
    author_resolution = found["m.author_resolution"]
    assert (author_resolution["value"], author_resolution["num"], author_resolution["state"]) == (None, None, UNRESOLVED)
    assert all(item["num"] is None and item["den"] is None for item in found.values() if item["state"] == UNRESOLVED)


def test_measures_module_is_pure():
    """No database, network or web framework in the measures."""
    source = Path(measures.__file__).read_text(encoding="utf-8")
    for name in ("livestorm_app.db", "requests", "fastapi", "psycopg", "redis"):
        assert f"import {name}" not in source and f"from {name}" not in source
