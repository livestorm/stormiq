"""The Report's "Who spoke up" (`record/voices.py`) and its route,
`GET /api/sessions/{id}/record/voices`, on the synthetic webinar."""

from __future__ import annotations

import dataclasses
import json
from unittest import mock

import pandas as pd
import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.voices import chat_voices


def _get(row, path=None):
    status = mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {})
    roster = mock.patch.object(api_logic, "fetch_session_roster", mock.Mock(side_effect=AssertionError("no stored roster")))
    with status, roster, synthetic.with_understanding(row), synthetic.serving(row) as client:
        return client.get(path or f"/api/sessions/{row['session_id']}/record/voices")


def _audience(inputs):
    events = inputs.events
    return events[events["kind"].isin(["chat", "question"]) & ~events["is_team"]]


def test_the_audience_who_wrote_most_active_first_with_their_counts():
    inputs = load_record_inputs(synthetic.cached_row())
    voices = chat_voices(inputs)
    audience = _audience(inputs)
    assert voices["total"]["messages"] == int((audience["kind"] == "chat").sum())
    assert voices["total"]["questions"] == int((audience["kind"] == "question").sum())
    people = voices["people"]
    assert people and len(people) <= 8
    written = [p["messages"] + p["questions"] for p in people]
    assert written == sorted(written, reverse=True)
    assert voices["others"]["people"] == voices["total"]["people"] - len(people)
    assert sum(written) + voices["others"]["messages"] + voices["others"]["questions"] == (
        voices["total"]["messages"] + voices["total"]["questions"]
    )
    assert all(0 < p["share"] <= 1 for p in people)


def test_names_are_short_and_nothing_else_identifies_anyone():
    inputs = load_record_inputs(synthetic.cached_row())
    voices = chat_voices(inputs)
    named = [p["name"] for p in voices["people"] if p["name"]]
    assert named, "a public session names its writers"
    assert all(name.endswith(".") or " " not in name for name in named)
    text = json.dumps(voices)
    assert "@" not in text, "no email address"
    assert '"P-' not in text, "no person ref"
    people_ids = {str(person.get("id")) for person in synthetic.person_rows()} - {"None"}
    assert not any(f'"{pid}"' in text for pid in people_ids), "no people id"


def test_each_person_shows_one_of_their_own_messages():
    inputs = load_record_inputs(synthetic.cached_row())
    events = inputs.events.set_index("eid")
    for person in chat_voices(inputs)["people"]:
        sample = person["sample"]
        assert sample and sample["id"] in events.index
        assert str(events.loc[sample["id"], "text"]).strip() == sample["text"]


def test_the_team_is_left_out_except_on_an_internal_session():
    inputs = load_record_inputs(synthetic.cached_row())
    public = chat_voices(inputs)
    meta = dataclasses.replace(inputs.roster.meta, internal=True)
    internal = chat_voices(dataclasses.replace(inputs, roster=dataclasses.replace(inputs.roster, meta=meta)))
    events = inputs.events
    team = events[events["kind"].isin(["chat", "question"]) & events["is_team"]]
    team = team[team["author_ref"].notna() | team["author_id"].notna()]
    assert not team.empty
    assert internal["internal"] is True
    assert internal["total"]["messages"] + internal["total"]["questions"] == (
        public["total"]["messages"] + public["total"]["questions"] + len(team)
    )


def test_unresolved_authors_are_told_apart_by_livestorm_id_and_never_named():
    inputs = load_record_inputs(synthetic.cached_row())
    events = inputs.events.copy()
    events["author_ref"] = None
    voices = chat_voices(dataclasses.replace(inputs, events=events))
    assert voices["people"] and all(p["name"] is None for p in voices["people"])


def test_an_empty_room_lists_nobody():
    inputs = load_record_inputs(synthetic.cached_row())
    voices = chat_voices(dataclasses.replace(inputs, events=inputs.events.iloc[0:0]))
    assert voices["people"] == [] and voices["total"] == {"people": 0, "messages": 0, "questions": 0}
    assert chat_voices(dataclasses.replace(inputs, events=pd.DataFrame()))["people"] == []


def test_the_route_serves_the_list_with_the_callers_scope():
    row = synthetic.cached_row()
    response = _get(row)
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["sessionId", "updatedAt", "version", "internal", "total", "people", "others"]
    missing = _get(row, "/api/sessions/no-such-session/record/voices")
    assert missing.status_code == 404 and missing.json()["detail"]["resource"] == "Who spoke up"


def test_a_bug_in_the_list_returns_500_without_its_detail(monkeypatch):
    from livestorm_app.record import voices as module

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic failure with internal detail")

    monkeypatch.setattr(module, "chat_voices", broken)
    response = _get(synthetic.cached_row())
    assert response.status_code == 500
    assert "internal detail" not in response.text


def test_the_frontend_fixture_is_the_route_response():
    """`frontend/tests/fixtures/voices-v1.json` is what the route sends for
    the synthetic webinar (regenerate: `.venv/bin/python tests/synthetic_session.py`)."""
    assert synthetic.dump(synthetic.voices_response()) == synthetic.VOICES_FIXTURE.read_text(encoding="utf-8")
