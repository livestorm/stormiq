"""The Transcript tab's room view (`record/transcript_view.py`) and its route,
`GET /api/sessions/{id}/record/transcript`, on the synthetic webinar."""

from __future__ import annotations

import dataclasses
import json
from unittest import mock

import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.record.alignment import ASSUMED, MISALIGNED
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.transcript_view import (
    ATTENDEE,
    TEAM,
    short_name,
    speaker_name,
    transcript_view,
    unclear_spans,
)


def _no_stored_roster():
    return (
        mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "fetch_session_roster", mock.Mock(side_effect=AssertionError("no stored roster"))),
    )


def _get(row, path=None):
    status, roster = _no_stored_roster()
    with status, roster, synthetic.serving(row) as client:
        return client.get(path or f"/api/sessions/{row['session_id']}/record/transcript")


# ── Pure helpers ──


def test_short_names_keep_the_first_word_and_the_last_initial():
    assert short_name("Priya Sharma") == "Priya S."
    assert short_name("  Ana  María  de la Cruz ") == "Ana C."
    assert short_name("Cher") == "Cher"
    assert short_name("") is None and short_name(None) is None and short_name(42) is None


def test_a_speaker_is_named_as_saved_or_by_order():
    saved = {"0": "Nora Vale", "1": "  "}
    assert speaker_name("0", saved, 1) == "Nora Vale"
    assert speaker_name("1", saved, 2) == "Speaker 2"
    assert speaker_name(None, saved, 3) == "Speaker 3"


def test_unclear_words_are_found_in_order_and_never_guessed():
    text = "Paste the metadata URL, then the metadata again"
    words = [("paste", 0.9), ("metadata", 0.4), ("URL", 0.95), ("metadata", 0.5), ("absent", 0.1), ("again", 0.2)]
    spans = unclear_spans(text, words)
    assert [text[a:b] for a, b in spans] == ["metadata", "metadata", "again"]
    assert unclear_spans("hello", [("hello", float("nan"))]) == []


# ── The view ──


def test_speech_is_on_the_session_clock_when_the_alignment_allows_seconds():
    row = synthetic.cached_row()
    inputs = load_record_inputs(row)
    view = transcript_view(inputs, row["transcript_speaker_names"])
    assert view["clock"]["interleave"] is True
    offset = view["clock"]["offsetS"]
    assert offset == round(inputs.alignment.offset_s, 2)
    utterances = inputs.transcript.utterances
    assert [line["id"] for line in view["speech"]] == list(utterances.sort_values(["r_start", "order"])["uid"])
    for line in view["speech"]:
        assert abs(line["t"] - (line["r"] + offset)) < 0.011
    names = row["transcript_speaker_names"]
    assert [s["name"] for s in view["speakers"]][:2] == [names["0"], names["1"]]
    assert any(line["unclear"] for line in view["speech"])


def test_speech_stays_off_the_clock_unless_the_alignment_allows_seconds():
    row = synthetic.cached_row()
    inputs = load_record_inputs(row)
    for status in (ASSUMED, MISALIGNED):
        view = transcript_view(dataclasses.replace(inputs, alignment=dataclasses.replace(inputs.alignment, status=status)))
        assert view["clock"] == {"interleave": False, "offsetS": None}
        assert view["speech"] and all(line["t"] is None for line in view["speech"])
        assert view["room"] and all(line["t"] is not None for line in view["room"]), "the audience keeps Livestorm's clock"


def test_the_room_has_every_message_question_and_answer_by_the_records_ids():
    row = synthetic.cached_row()
    inputs = load_record_inputs(row)
    view = transcript_view(inputs)
    events = inputs.events
    assert sorted(line["id"] for line in view["room"]) == sorted(events["eid"])
    ts = [line["t"] for line in view["room"]]
    assert ts == sorted(ts)
    questions = [line for line in view["room"] if line["kind"] == "question"]
    answered = set(events.loc[events["kind"] == "answer", "reply_to"])
    assert questions and all(line["answered"] == (line["id"] in answered) for line in questions)
    assert all('"open"' not in json.dumps(line) for line in questions), "never says a question is open"


def test_authors_are_short_names_from_the_roster_and_nothing_else():
    row = synthetic.cached_row()
    inputs = load_record_inputs(row)
    view = transcript_view(inputs)
    authors = {line["author"] for line in view["room"]}
    assert authors - {TEAM, ATTENDEE}, "a public session names its writers"
    assert all(author in (TEAM, ATTENDEE) or author.endswith(".") or " " not in author for author in authors)
    text = json.dumps(view)
    assert "@" not in text, "no email address"
    assert '"P-' not in text, "no person ref"
    people_ids = {str(person.get("id")) for person in synthetic.person_rows()} - {"None"}
    assert not any(f'"{pid}"' in text for pid in people_ids), "no people id"


def test_an_internal_session_names_nobody():
    row = synthetic.cached_row()
    inputs = load_record_inputs(row)
    meta = dataclasses.replace(inputs.roster.meta, internal=True)
    internal = dataclasses.replace(inputs, roster=dataclasses.replace(inputs.roster, meta=meta))
    view = transcript_view(internal)
    assert all(line["author"] == (TEAM if line["team"] else ATTENDEE) for line in view["room"])


def test_a_session_without_a_transcript_has_only_the_room():
    row = {**synthetic.cached_row(), "transcript_payload": None}
    view = transcript_view(load_record_inputs(row))
    assert view["speech"] == [] and view["speakers"] == []
    assert view["clock"] == {"interleave": False, "offsetS": None}
    assert view["room"]


# ── The route ──


def test_the_route_serves_the_view_with_the_callers_scope():
    row = synthetic.cached_row()
    response = _get(row)
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["sessionId", "updatedAt", "version", "clock", "speakers", "speech", "room"]
    assert body["sessionId"] == row["session_id"]
    assert body["clock"]["interleave"] is True
    missing = _get(row, "/api/sessions/no-such-session/record/transcript")
    assert missing.status_code == 404 and missing.json()["detail"]["resource"] == "Session transcript"


def test_the_route_needs_a_caller():
    row = synthetic.cached_row()
    status, roster = _no_stored_roster()
    with status, roster, synthetic.serving(row) as client:
        client.cookies.clear()
        assert client.get(f"/api/sessions/{row['session_id']}/record/transcript").status_code == 401


def test_a_bug_in_the_view_returns_500_without_its_detail(monkeypatch):
    from livestorm_app.record import transcript_view as module

    def broken(inputs, speaker_names=None):
        raise RuntimeError("synthetic failure with internal detail")

    monkeypatch.setattr(module, "transcript_view", broken)
    response = _get(synthetic.cached_row())
    assert response.status_code == 500
    assert "internal detail" not in response.text


def test_the_frontend_fixture_is_the_route_response():
    """`frontend/tests/fixtures/transcript-v1.json` is what the route sends
    for the synthetic webinar. After a deliberate change, regenerate it:
    `.venv/bin/python tests/synthetic_session.py`."""
    committed = synthetic.TRANSCRIPT_FIXTURE.read_text(encoding="utf-8")
    assert synthetic.dump(_get(synthetic.cached_row()).json()) == committed
