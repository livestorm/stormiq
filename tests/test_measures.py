"""The v1 measures registry (`record/measures.py`).

One test per measure covers its value, every gate and every state, on
synthetic sessions: a public webinar and an internal meeting where everyone
is a team member. Also covers the registry's contract (the version pin, the
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
from datetime import datetime, timezone
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
from livestorm_app.record.roster import PAGINATED, RosterRows

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
AUDIENCE_MEASURES = tuple(measure_id for measure_id, definition in REGISTRY.items() if definition.excludes)

# Every registry pin ever published, by METRICS_VERSION. Never edit an entry:
# a changed measure version gets a new METRICS_VERSION and a new line here.
PUBLISHED_PINS = {
    1: "3120ffec1fc37731bd6bfec4e7c863b8f98bfa2507e5617e04f4e0e75c617e07",
}


# ── Synthetic sessions ──────────────────────────────────────────────────────


def person(person_id, *, role="participant", attended=True, guest=False, duration=None, messages=0, **detail):
    """A people record. `duration` is the attendance in seconds (default:
    1,800 for an attendee). Extra keyword arguments go in registrant_detail."""
    registrant = {
        "attended": attended,
        "is_guest_speaker": guest,
        "attendance_duration": (1800 if attended else 0) if duration is None else duration,
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


def row(people, chat_payload=None, questions_payload=None, **attributes):
    return {
        "session_id": "s1",
        "session_payload": session_payload(people, **attributes),
        "event_payload": None,
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


def test_the_registry_holds_the_vitals_in_order():
    assert tuple(REGISTRY) == VITALS
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
        ("chat_payload", {"m.wrote_rate", "m.author_resolution"}),
        ("questions_payload", {"m.wrote_rate", "m.author_resolution", "m.questions_qa", "m.time_to_answer"}),
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
    }
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
        "m.audience_registrants", "m.audience_attendees", "m.show_up_rate", "m.time_in_room", "m.wrote_rate",
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
    assert all(found[measure_id]["value"] not in (None, 0) for measure_id in AUDIENCE_MEASURES)
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
        lambda m: m["m.show_up_rate"].update(rows={}),
        lambda m: m["m.show_up_rate"].pop("rows"),
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


def test_author_resolution_without_any_author_is_insufficient():
    found = one("m.author_resolution", webinar())
    assert (found["value"], found["num"], found["den"], found["state"]) == (None, 0, 0, INSUFFICIENT)


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
