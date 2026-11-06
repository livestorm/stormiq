"""The v1 record route, `GET /api/sessions/{id}/record`, and what it leaves alone.

Covers `api_logic.get_session_record` and `record/assemble.py` on the
synthetic webinar of `synthetic_session.py`: the response's shape and
values, the measures it serves, what it never carries, the internal flag,
errors, the committed frontend fixture, and the classic workspace staying
exactly as it was. Each measure's own rules are in `test_measures.py`.
No database, Redis or network: the scoped reads are patched at `api_logic`.
"""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
import synthetic_session as synthetic
from livestorm_app import api_logic, auth, oauth_client
from livestorm_app.record import assemble, measures
from synthetic_session import DURATION_S, LATE_START_S, ORGANIZATION_ID, SESSION_ID, T0

PATH = f"/api/sessions/{SESSION_ID}/record"
CONNECTIONS = {
    "conn-owner": {"connection_id": "conn-owner", "organization_id": ORGANIZATION_ID, "email": "owner@example.test",
                   "user_id": "user-owner", "access_token": "token-owner", "token_type": "Bearer", "profile": {}},
    "conn-other": {"connection_id": "conn-other", "organization_id": "other-org", "email": "other@example.test",
                   "user_id": "user-other", "access_token": "token-other", "token_type": "Bearer", "profile": {}},
}


@pytest.fixture
def rows(monkeypatch):
    """The synthetic row, served through both scoped readers of `api_logic`.
    Readers return the row's own objects, as the database driver's dicts
    would be shared, so any mutation by the record shows."""
    stored = {SESSION_ID: synthetic.cached_row()}

    def visible(session_id, organization_id):
        row = stored.get(session_id)
        return row if row is not None and organization_id == row["organization_id"] else None

    def read_fields(session_id, fields, *, organization_id):
        row = visible(session_id, organization_id)
        return {field: row.get(field) for field in fields} if row is not None else None

    def read_row(api_key, session_id, *, organization_id):
        return visible(session_id, organization_id)

    monkeypatch.setattr(api_logic, "fetch_session_fields", read_fields)
    monkeypatch.setattr(api_logic, "fetch_cached_session", read_row)
    monkeypatch.setattr(api_logic, "_record_cache", api_logic.OrderedDict())
    monkeypatch.setattr(api_logic, "_insights_cache", api_logic.OrderedDict())
    monkeypatch.setattr(auth, "refresh_connection_if_needed", lambda cid: CONNECTIONS.get(cid))
    monkeypatch.setattr(auth, "is_admin_email", lambda email: False)
    return stored


@pytest.fixture
def client(rows):
    return TestClient(app_module.app)


def get(client, path=PATH, cookie="conn-owner"):
    client.cookies.clear()
    if cookie:
        client.cookies.set(oauth_client.LIVESTORM_OAUTH_COOKIE, cookie)
    return client.get(path)


def record_for(row):
    return synthetic.record_response(row)


def fixture():
    return json.loads(synthetic.RECORD_FIXTURE.read_text(encoding="utf-8"))


# ── The response ────────────────────────────────────────────────────────────


def test_the_owner_gets_the_clock_the_roster_header_and_the_pulse(client):
    response = get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["sessionId", "updatedAt", "meta", "record"]
    assert body["sessionId"] == SESSION_ID and body["updatedAt"] == "2026-10-02T08:30:00Z"
    assert body["meta"] == {
        "clock": {
            "startedAtUnix": T0,
            "endS": DURATION_S,
            "endSource": "ended_at",
            "lateStartS": LATE_START_S,
            "timezone": "Europe/Paris",
            "alignment": None,
        }
    }
    record = body["record"]
    assert list(record) == ["version", "roster", "pulse", "metricsVersion", "measuresSource", "measures"]
    assert record["version"] == assemble.RECORD_VERSION == 3
    assert record["roster"] == {
        "source": "session_include",
        "complete": None,
        "coverage": "partial",
        "peopleCount": 10,
        "recordCount": None,
        "syncedAt": None,
        "internal": False,
    }


def test_the_pulse_counts_every_event_once_on_the_session_clock(client):
    pulse = get(client).json()["record"]["pulse"]
    lanes = ["audienceChat", "teamChat", "questions", "answers"]
    assert pulse["lanes"] == lanes
    assert [row["minute"] for row in pulse["minutes"]] == list(range(DURATION_S // 60))
    assert all(list(row) == ["minute"] + lanes for row in pulse["minutes"])
    totals = {lane: sum(row[lane] for row in pulse["minutes"]) for lane in lanes}
    edges = pulse["edges"]
    for edge in ("before", "after"):
        for lane in lanes:
            totals[lane] += edges[edge]["counts"][lane]
    # 13 audience and 4 team messages (one from the guest speaker), 4 questions, 2 written answers.
    assert totals == {"audienceChat": 13, "teamChat": 4, "questions": 4, "answers": 2}
    # The waiting room and the message after the end stay on the edges.
    assert edges["before"] == {"counts": {"audienceChat": 3, "teamChat": 1, "questions": 1, "answers": 0}, "firstT": -300}
    assert edges["after"] == {"counts": {"audienceChat": 1, "teamChat": 0, "questions": 0, "answers": 0}, "lastT": DURATION_S + 40}
    assert pulse["minutes"][-1]["audienceChat"] == 1, "a message exactly at the end is in the last minute"
    assert pulse["minutes"][4] == {"minute": 4, "audienceChat": 0, "teamChat": 1, "questions": 0, "answers": 0}, "guest speaker chat is team chat"
    assert pulse["unplaced"] == 0


def test_the_record_serves_every_measure_live(client):
    record = get(client).json()["record"]
    assert record["metricsVersion"] == measures.METRICS_VERSION == 3
    assert record["measuresSource"] == "live"
    assert list(record["measures"]) == list(measures.REGISTRY)
    measures.validate_measures(record["measures"])
    show_up = record["measures"]["m.show_up_rate"]
    assert show_up == {
        "id": "m.show_up_rate",
        "version": 1,
        "value": 0.7143,
        "num": 5,
        "den": 7,
        "state": "partial",  # the include list's coverage is partial
        "coverage": {"roster": "partial", "people": 10, "recordCount": None, "attendanceUnknown": 0},
        "excluded": {"team": 2, "guestSpeakers": 1, "total": 3, "internal": False},
        "livestorm": {"attendeesCount": 8, "registrantsCount": 10, "value": 0.8},
        "rows": None,
    }


def test_a_session_without_started_at_has_no_clock_and_places_nothing(rows):
    row = rows[SESSION_ID]
    attributes = row["session_payload"]["data"]["attributes"]
    del attributes["started_at"]
    del attributes["ended_at"]
    record = record_for(row)
    assert record["meta"]["clock"] == {
        "startedAtUnix": None,  # never estimated_started_at
        "endS": DURATION_S,
        "endSource": "duration",
        "lateStartS": None,
        "timezone": "Europe/Paris",
        "alignment": None,
    }
    pulse = record["record"]["pulse"]
    assert pulse["minutes"] == [] and pulse["unplaced"] == 17 + 4 + 2
    assert all(count == 0 for edge in pulse["edges"].values() for count in edge["counts"].values())


def test_an_empty_row_still_answers(rows):
    row = {**rows[SESSION_ID], "session_payload": None, "event_payload": None, "chat_payload": None, "questions_payload": None}
    record = record_for(row)
    assert record["meta"]["clock"]["endSource"] == "none"
    assert record["record"]["roster"]["peopleCount"] == 0 and record["record"]["roster"]["internal"] is False
    assert record["record"]["pulse"]["minutes"] == [] and record["record"]["pulse"]["unplaced"] == 0
    assert {item["state"] for item in record["record"]["measures"].values()} == {"unavailable"}
    assert all(item["value"] is None for item in record["record"]["measures"].values())


def test_the_response_is_strict_json():
    json.dumps(record_for(synthetic.cached_row()), allow_nan=False)


# ── What it never carries ───────────────────────────────────────────────────


def test_the_record_carries_no_person_message_or_join_link(client, rows):
    source = json.dumps(rows[SESSION_ID], default=str)
    missing = [value for value in synthetic.PRIVATE_VALUES if value not in source]
    assert missing == [], "the scan would check nothing for these values"
    text = get(client).text
    assert [value for value in synthetic.PRIVATE_VALUES if value in text] == []
    assert "@" not in text


def test_the_record_does_not_change_the_cached_row(rows):
    before = copy.deepcopy(rows[SESSION_ID])
    record_for(rows[SESSION_ID])
    assert rows[SESSION_ID] == before


# ── Internal sessions ───────────────────────────────────────────────────────


def _all_hands(row):
    for person in row["session_payload"]["included"]:
        person["attributes"]["role"] = "team_member"
        person["attributes"]["registrant_detail"]["is_guest_speaker"] = False
    for message in row["chat_payload"]["data"]:
        message["attributes"]["from_team_member"] = True
    return row


def _team_guest_speakers(row):
    """Team members who are also guest speakers: the classic rule doesn't count them as team."""
    for person in row["session_payload"]["included"]:
        person["attributes"]["role"] = "team_member"
        person["attributes"]["registrant_detail"]["is_guest_speaker"] = True
    return row


def _no_people(row):
    row["session_payload"]["included"] = []
    return row


@pytest.mark.parametrize(
    "variant, internal",
    [(lambda row: row, False), (_all_hands, True), (_team_guest_speakers, False), (_no_people, False)],
    ids=["public", "all-hands", "team guest speakers", "no people"],
)
def test_the_internal_flag_matches_the_classic_workspace(variant, internal):
    """Same rule, same people here. The roster counts each person id once and
    skips people without an id, while classic counts every listed item, so
    a list with repeats or id-less people can differ (docs/api.md)."""
    row = variant(synthetic.cached_row())
    classic = api_logic._serialize_cached_session(SESSION_ID, copy.deepcopy(row))
    record = record_for(row)
    assert classic["meta"]["internalSession"] is internal
    assert record["record"]["roster"]["internal"] is internal


def test_an_all_hands_keeps_its_lanes():
    """Nobody is relabelled on an internal session: its chat is team chat,
    and the roster header says the session is internal."""
    record = record_for(_all_hands(synthetic.cached_row()))["record"]
    pulse = record["pulse"]
    assert record["roster"]["internal"] is True
    assert sum(row["audienceChat"] for row in pulse["minutes"]) == 0
    assert sum(row["teamChat"] for row in pulse["minutes"]) == 12


def test_an_all_hands_counts_everyone_in_its_audience_measures():
    """Audience-only measures take everyone as the audience on an internal
    session, and say so: never 0 of 0."""
    found = record_for(_all_hands(synthetic.cached_row()))["record"]["measures"]
    assert found["m.audience_registrants"]["value"] == 10
    assert (found["m.show_up_rate"]["num"], found["m.show_up_rate"]["den"]) == (8, 10)
    audience_only = [measure_id for measure_id, definition in measures.REGISTRY.items() if definition.excludes]
    assert len(audience_only) == 11
    for measure_id in audience_only:
        assert found[measure_id]["excluded"] == {"team": 0, "guestSpeakers": 0, "total": 0, "internal": True}, measure_id


def test_an_all_hands_without_sources_or_select_fields_says_so():
    """An internal meeting's people have no UTM, referrer, registration type
    or select answer: the channel and segment measures are insufficient with
    no rows, never "direct 100%", and their coverage says why."""
    row = _all_hands(synthetic.cached_row())
    for person in row["session_payload"]["included"]:
        detail = person["attributes"]["registrant_detail"]
        detail.update(utm_source=None, utm_medium=None, utm_campaign=None, referrer=None, registration_type=None)
        detail["fields"] = [field for field in detail["fields"] if field["type"] == "text"]
    found = record_for(row)["record"]["measures"]
    channels = found["m.channel_quality"]
    assert (channels["state"], channels["rows"]) == ("insufficient", [])
    assert (channels["coverage"]["registrants"], channels["coverage"]["withSource"]) == (10, 0)
    segments = found["m.segment_quality"]
    assert (segments["state"], segments["rows"], segments["coverage"]["fields"][0]["answered"]) == ("insufficient", [], 0)


# ── Access and errors ───────────────────────────────────────────────────────


def test_other_orgs_and_anonymous_callers_get_nothing(client):
    other = get(client, cookie="conn-other")
    assert other.status_code == 404
    assert other.json()["detail"]["resource"] == "Session record"
    assert get(client, cookie=None).status_code == 401
    assert get(client, path="/api/sessions/no-such-session/record").status_code == 404


def test_a_bug_in_the_record_returns_500_and_leaves_classic_working(client, monkeypatch):
    def broken(inputs):
        raise RuntimeError("synthetic failure with internal detail")

    monkeypatch.setattr(assemble, "assemble_record", broken)
    response = get(client)
    assert response.status_code == 500
    assert response.json() == {"detail": {"resource": "Session record", "message": "The session record could not be built."}}
    assert "internal detail" not in response.text
    assert get(client, path=f"/api/sessions/{SESSION_ID}").status_code == 200


def test_an_invalid_set_of_measures_is_never_served_or_cached(client, monkeypatch):
    """`measures_for_read` checks the set before it leaves: a measure whose
    version doesn't match the registry fails the read, and nothing is cached."""
    computed = assemble.compute_measures

    def stale(inputs):
        found = computed(inputs)
        show_up = found["m.show_up_rate"]
        found["m.show_up_rate"] = dataclasses.replace(show_up, version=show_up.version + 1)
        return found

    monkeypatch.setattr(assemble, "compute_measures", stale)
    with pytest.raises(ValueError, match="m.show_up_rate"):
        api_logic.get_session_record(SESSION_ID, organization_id=ORGANIZATION_ID)
    assert len(api_logic._record_cache) == 0
    response = get(client)
    assert response.status_code == 500 and "m.show_up_rate" not in response.text
    assert len(api_logic._record_cache) == 0


# ── The frontend fixture ────────────────────────────────────────────────────


def test_the_frontend_fixture_is_the_route_response(client):
    """`frontend/tests/fixtures/record-v1.json` is what the route sends for
    the synthetic webinar. If this fails after a deliberate change to the
    record, regenerate it: `.venv/bin/python tests/synthetic_session.py`.
    It compares the file's text, so key order and int or float count too."""
    committed = synthetic.RECORD_FIXTURE.read_text(encoding="utf-8")
    assert synthetic.dump(record_for(synthetic.cached_row())) == committed
    assert synthetic.dump(get(client).json()) == committed


def test_the_frontend_fixture_has_the_current_record_version():
    assert fixture()["record"]["version"] == assemble.RECORD_VERSION


def test_the_crowd_fixture_is_the_route_response_and_carries_no_person():
    """`frontend/tests/fixtures/record-v1-crowd.json` is what the route
    sends for the larger synthetic webinar. Regenerate it with the other
    fixture: `.venv/bin/python tests/synthetic_session.py`."""
    row = synthetic.crowd_row()
    committed = synthetic.RECORD_CROWD_FIXTURE.read_text(encoding="utf-8")
    assert synthetic.dump(record_for(row)) == committed
    private = synthetic.PRIVATE_VALUES + synthetic.CROWD_PRIVATE_VALUES
    source = json.dumps(row, default=str)
    assert [value for value in synthetic.CROWD_PRIVATE_VALUES if value not in source] == []
    assert [value for value in private if value in committed] == [] and "@" not in committed


def test_the_crowd_fixture_shows_a_value_in_every_grouped_measure():
    """So the frontend has a shown row of each kind to build against."""
    found = json.loads(synthetic.RECORD_CROWD_FIXTURE.read_text(encoding="utf-8"))["record"]["measures"]
    assert found["m.chat_concentration"]["value"] == 3
    assert all(r["share"] is not None for r in found["m.room_map"]["rows"])
    channel_kinds = {r["kind"] for r in found["m.channel_quality"]["rows"] if r["state"] == "ok" and r["showUpRate"] is not None}
    assert channel_kinds == {"channel", "other", "none"}
    segments = found["m.segment_quality"]["rows"]
    assert {r["state"] for r in segments} == {"ok", "insufficient"}
    lead = {r["key"]: r for r in found["m.lead_time_show_up"]["rows"]}
    assert lead["4-7..8-14"]["buckets"] == ["4-7", "8-14"] and lead["4-7..8-14"]["showUpRate"] is not None
    assert lead["after_start"]["state"] == "insufficient"
    assert found["m.replay_caught_up"]["value"] is not None


# ── Classic stays exactly as it was ─────────────────────────────────────────


def classic_snapshot():
    snapshot = json.loads(synthetic.CLASSIC_SNAPSHOT.read_text(encoding="utf-8"))
    return synthetic.row_from_json(snapshot["row"]), snapshot["workspace"]


def test_the_classic_workspace_is_unchanged():
    """The classic workspace of a frozen synthetic row, against the snapshot
    taken before the record route existed. It compares JSON text, so key
    order and int or float count too; only floats are rounded to 6 places,
    so the last bits of a platform's maths don't. Regenerate the snapshot
    only for an intended classic change: `.venv/bin/python tests/synthetic_session.py --classic`."""
    row, workspace = classic_snapshot()
    assert synthetic.dump(synthetic.classic_workspace(row)) == synthetic.dump(workspace)


def test_the_classic_route_is_unchanged_after_a_record_read(client, rows):
    row, workspace = classic_snapshot()
    rows[SESSION_ID] = row
    assert get(client).status_code == 200
    response = get(client, path=f"/api/sessions/{SESSION_ID}")
    assert response.status_code == 200
    assert synthetic.dump(synthetic.rounded(response.json())) == synthetic.dump(workspace)
