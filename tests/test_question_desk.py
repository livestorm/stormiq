"""The question desk (`record/question_desk.py`) and its routes,
`GET`/`PUT /api/sessions/{id}/record/questions`,
`PUT /api/sessions/{id}/record/questions/located` and
`GET /api/sessions/{id}/record/questions.csv`, on the synthetic webinar."""

from __future__ import annotations

import copy
import csv
import dataclasses
import io
from contextlib import ExitStack
from unittest import mock

import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.record.evidence import CHAT
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.question_desk import (
    ALOUD,
    CONFIRMED,
    COVERED,
    CSV_COLUMNS,
    HERE,
    OPEN,
    REJECTED,
    WRITTEN,
    find_question,
    open_questions_csv,
    question_desk,
)
from livestorm_app.record.transcript_view import ATTENDEE, TEAM
from livestorm_app.record.understanding import NONE, READY, STALE, located_answers

_stored_labels = synthetic.chat_labels  # the synthetic chat, labelled (`synthetic.CHAT_LABELS`)


def _inputs(row=None, *, labelled=True):
    row = row or synthetic.cached_row()
    return load_record_inputs(row, stored_labels=_stored_labels(row) if labelled else None)


def _internal(inputs):
    meta = dataclasses.replace(inputs.roster.meta, internal=True)
    return dataclasses.replace(inputs, roster=dataclasses.replace(inputs.roster, meta=meta))


def _by_text(desk):
    return {q["text"]: q for q in desk["questions"]}


# ── The desk ──


def test_every_audience_question_has_its_livestorm_status():
    desk = question_desk(_inputs(), {})
    rows = _by_text(desk)
    assert rows["Will the slides be shared?"]["status"] == WRITTEN
    assert rows["Will the slides be shared?"]["answer"] == "Yes, see the chat."
    assert rows["How does pricing work for larger teams with several hundred seats?"]["status"] == ALOUD
    assert rows["Can we export to CSV?"]["status"] == WRITTEN
    assert rows["Is there an API?"]["status"] == OPEN and rows["Is there an API?"]["answer"] is None
    assert all(q["source"] == "qa" for q in desk["questions"] if q["id"].startswith("Q"))


def test_chat_questions_come_from_fresh_labels_and_logistics_are_counted_apart():
    desk = question_desk(_inputs(), {})
    chat = [q for q in desk["questions"] if q["source"] == "chat"]
    assert [q["text"] for q in chat] == ["What about pricing?"], "content questions only; no team or guest message"
    assert chat[0]["status"] == OPEN and chat[0]["id"].startswith("C")
    summary = desk["summary"]
    assert summary["logistics"] == 1 and summary["chatQuestions"] == "ready"
    assert summary["total"] == 5
    assert (summary[WRITTEN], summary[ALOUD], summary[HERE], summary[OPEN]) == (2, 1, 0, 2)


def test_without_labels_the_desk_has_the_qa_questions_only():
    desk = question_desk(_inputs(labelled=False), {})
    assert {q["source"] for q in desk["questions"]} == {"qa"}
    assert desk["summary"]["chatQuestions"] == "none" and desk["summary"]["logistics"] == 0


def test_labels_for_another_chat_are_stale_and_read_nowhere():
    row = synthetic.cached_row()
    stored = _stored_labels(row)
    changed = copy.deepcopy(row)
    changed["chat_payload"]["data"][0]["attributes"]["text_content"] = "Edited"
    desk = question_desk(load_record_inputs(changed, stored_labels=stored), {})
    assert desk["summary"]["chatQuestions"] == "stale"
    assert {q["source"] for q in desk["questions"]} == {"qa"}


def test_the_hosts_tick_and_reply_answer_an_open_question_only():
    inputs = _inputs()
    rows = _by_text(question_desk(inputs, {}))
    open_qa = find_question(inputs, rows["Is there an API?"]["id"])
    written = find_question(inputs, rows["Can we export to CSV?"]["id"])
    saved = {
        f"qa:{open_qa['livestormId']}": {"answered": True, "reply": "Yes: docs.example.test/api"},
        f"qa:{written['livestormId']}": {"answered": True, "reply": "ignored"},
    }
    marked = _by_text(question_desk(inputs, saved))
    assert marked["Is there an API?"]["status"] == HERE
    assert marked["Is there an API?"]["reply"] == "Yes: docs.example.test/api"
    assert marked["Can we export to CSV?"]["status"] == WRITTEN, "Livestorm's own answer wins"
    unmarked = {f"qa:{open_qa['livestormId']}": {"answered": False, "reply": "drafting"}}
    assert _by_text(question_desk(inputs, unmarked))["Is there an API?"]["status"] == OPEN


def test_find_question_names_a_desk_row_with_its_livestorm_id():
    inputs = _inputs()
    rows = _by_text(question_desk(inputs, {}))
    api = rows["Is there an API?"]
    assert find_question(inputs, api["id"]) == {"id": api["id"], "source": "qa", "livestormId": "qst-004", "status": OPEN, "located": None}
    assert find_question(inputs, rows["What about pricing?"]["id"])["source"] == "chat"
    events = inputs.events
    team_chat = events[(events["kind"] == CHAT) & events["is_team"]]["eid"].iloc[0]
    assert find_question(inputs, str(team_chat)) is None, "a team message is not a desk row"
    assert find_question(inputs, "Q999") is None


def test_authors_are_short_names_on_every_session():
    inputs = _inputs()
    authors = {q["author"] for q in question_desk(inputs, {})["questions"]}
    assert all(author.endswith(".") or author in (TEAM, ATTENDEE) for author in authors)
    assert any(author not in (TEAM, ATTENDEE) for author in authors)
    internal = {q["author"] for q in question_desk(_internal(inputs), {})["questions"]}
    assert authors <= internal, "an internal session names its people too"


# ── Answers located in the talk ──


def _located(row=None):
    row = row or synthetic.cached_row()
    inputs = _inputs(row)
    return inputs, located_answers(synthetic.understanding(row), inputs)


def test_a_located_answer_makes_an_open_question_covered_and_shows_where_an_aloud_one_was():
    inputs, (located, state) = _located()
    assert state == READY
    desk = question_desk(inputs, {}, located, state)
    rows = _by_text(desk)
    pricing = rows["What about pricing?"]
    assert (pricing["status"], pricing["located"]) == (COVERED, {"t": 318.9, "quote": "Pricing works per team and scales with seats."})
    aloud = rows["How does pricing work for larger teams with several hundred seats?"]
    assert aloud["status"] == ALOUD and aloud["located"]["t"] == 318.9, "where in the talk"
    assert rows["Is there an API?"]["status"] == OPEN and rows["Is there an API?"]["located"] is None
    assert (desk["summary"]["covered"], desk["summary"]["open"], desk["summary"]["located"]) == (1, 1, READY)
    assert question_desk(inputs, {})["summary"]["located"] == NONE


def test_the_hosts_decision_on_a_located_answer():
    inputs, (located, state) = _located()
    pricing = find_question(inputs, _by_text(question_desk(inputs, {}, located, state))["What about pricing?"]["id"], {}, located)
    aloud = find_question(inputs, "Q002", {}, located)
    key, aloud_key = f"chat:{pricing['livestormId']}", f"qa:{aloud['livestormId']}"

    def rows(saved):
        return _by_text(question_desk(inputs, saved, located, state))

    confirmed = rows({key: {"answered": True, "reply": None, "located": CONFIRMED}})["What about pricing?"]
    assert confirmed["status"] == HERE and confirmed["located"]["t"] == 318.9, "answered here, the passage kept"
    rejected = rows({key: {"answered": False, "reply": None, "located": REJECTED}, aloud_key: {"answered": False, "located": REJECTED}})
    assert (rejected["What about pricing?"]["status"], rejected["What about pricing?"]["located"]) == (OPEN, None)
    assert rejected["How does pricing work for larger teams with several hundred seats?"]["located"] is None
    reopened = rows({key: {"answered": False, "reply": None, "located": CONFIRMED}})["What about pricing?"]
    assert (reopened["status"], reopened["located"]) == (OPEN, None), "marked open again: never suggested again"


def test_located_answers_are_used_only_while_read_from_the_session_as_it_is():
    row = synthetic.cached_row()
    stored = synthetic.understanding(row)
    inputs = _inputs(row)
    assert located_answers(None, inputs) == ({}, NONE)
    assert located_answers({**stored, "dossier_hash": "0" * 64}, inputs) == ({}, STALE)
    assert located_answers({**stored, "version": stored["version"] + 1}, inputs) == ({}, STALE)
    unlabelled = _inputs(row, labelled=False)
    assert located_answers(stored, unlabelled) == ({}, STALE), "the chat's questions changed: its ids may have"
    without_transcript = _inputs({**row, "transcript_payload": None})
    assert located_answers(stored, without_transcript) == ({}, NONE)


def test_a_located_answer_never_changes_the_dossier_or_the_csv():
    inputs, (located, state) = _located()
    assert "What about pricing?" in open_questions_csv(inputs, {}), "not confirmed: still open"
    pricing = find_question(inputs, "C0010", {}, located)
    confirmed = {f"chat:{pricing['livestormId']}": {"answered": True, "reply": None, "located": CONFIRMED}}
    assert "What about pricing?" not in open_questions_csv(inputs, confirmed)
    from livestorm_app.record.question_desk import audience_questions

    assert {q["answered_as"] for q in audience_questions(inputs)} <= {"written", "aloud", "open"}


# ── The CSV ──


def _csv_rows(text):
    return list(csv.reader(io.StringIO(text)))


def test_the_csv_lists_open_questions_with_full_names_and_no_email():
    text = open_questions_csv(_inputs(), {})
    rows = _csv_rows(text)
    assert tuple(rows[0]) == CSV_COLUMNS
    questions = {row[0]: row for row in rows[1:]}
    assert set(questions) == {"Is there an API?", "What about pricing?"}, "open questions only"
    assert questions["Is there an API?"][3] == "Q&A" and questions["What about pricing?"][3] == "Chat"
    assert all(row[1] and " " in row[1] for row in rows[1:]), "a full name, not a short one"
    assert "@" not in text, "never an email"


def test_the_csv_names_askers_on_an_internal_session_too():
    public = _csv_rows(open_questions_csv(_inputs(), {}))
    internal = _csv_rows(open_questions_csv(_internal(_inputs()), {}))
    assert all(row[1] for row in internal[1:]), "every asker named"
    # Everyone is audience on an internal session, so the guest's chat question joins.
    assert [row for row in internal if row[0] != "Happy to cover pricing later"] == public


def test_a_question_answered_in_stormiq_leaves_the_csv():
    inputs = _inputs()
    api = find_question(inputs, _by_text(question_desk(inputs, {}))["Is there an API?"]["id"])
    text = open_questions_csv(inputs, {f"qa:{api['livestormId']}": {"answered": True, "reply": None}})
    assert "Is there an API?" not in text


def test_csv_cells_that_look_like_formulas_are_neutralised():
    row = synthetic.cached_row()
    row["questions_payload"]["data"][3]["attributes"]["question"] = '=HYPERLINK("http://x")'
    rows = _csv_rows(open_questions_csv(_inputs(row), {}))
    assert [r[0] for r in rows[1:] if "HYPERLINK" in r[0]] == ["'=HYPERLINK(\"http://x\")"]


# ── The routes ──


def _client(row, saved, stored_understanding=None):
    """The real app on `row`, with its labels stored, the saved replies in
    memory and `stored_understanding` (default none); returns `(stack, client)`."""

    def fetch(session_id, organization_id):
        return dict(saved) if session_id == row["session_id"] and organization_id == row["organization_id"] else {}

    def save(session_id, *, organization_id, source, livestorm_id, evidence_id, answered, reply, updated_by):
        if session_id != row["session_id"] or organization_id != row["organization_id"]:
            return False
        saved[f"{source}:{livestorm_id}"] = {"answered": answered, "reply": reply, "evidence_id": evidence_id, "by": updated_by}
        return True

    def decide(session_id, *, organization_id, source, livestorm_id, evidence_id, decision, updated_by):
        if session_id != row["session_id"] or organization_id != row["organization_id"]:
            return False
        before = saved.get(f"{source}:{livestorm_id}", {"answered": False, "reply": None})
        saved[f"{source}:{livestorm_id}"] = {
            **before,
            "answered": before["answered"] or decision == CONFIRMED,
            "located": decision,
            "evidence_id": evidence_id,
            "by": updated_by,
        }
        return True

    def understanding(session_id, organization_id):
        ok = session_id == row["session_id"] and organization_id == row["organization_id"]
        return stored_understanding if ok else None

    labels = _stored_labels(row)
    stack = ExitStack()
    for patch in (
        mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "fetch_session_roster", mock.Mock(side_effect=AssertionError("no stored roster"))),
        mock.patch.object(api_logic, "fetch_chat_labels", lambda session_id, organization_id: labels),
        mock.patch.object(api_logic, "fetch_question_replies", fetch),
        mock.patch.object(api_logic, "save_question_reply", save),
        mock.patch.object(api_logic, "save_located_decision", decide),
        mock.patch.object(api_logic, "fetch_understanding", understanding),
    ):
        stack.enter_context(patch)
    client = stack.enter_context(synthetic.serving(row))
    return stack, client


def test_the_desk_route_serves_the_desk():
    row = synthetic.cached_row()
    stack, client = _client(row, {})
    with stack:
        response = client.get(f"/api/sessions/{row['session_id']}/record/questions")
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["sessionId", "updatedAt", "version", "summary", "questions"]
    assert body["summary"]["total"] == 5


def test_marking_a_question_saves_it_by_livestorm_id_and_returns_the_desk():
    row, saved = synthetic.cached_row(), {}
    stack, client = _client(row, saved)
    with stack:
        path = f"/api/sessions/{row['session_id']}/record/questions"
        api = _by_text(client.get(path).json())["Is there an API?"]
        response = client.put(path, json={"questionId": api["id"], "answered": True, "reply": "  See the docs.  "})
    assert response.status_code == 200, response.text
    assert saved == {"qa:qst-004": {"answered": True, "reply": "See the docs.", "evidence_id": api["id"], "by": "synthetic-viewer"}}
    assert _by_text(response.json())["Is there an API?"]["status"] == HERE


def test_marking_an_unknown_or_malformed_question_is_refused():
    row, saved = synthetic.cached_row(), {}
    stack, client = _client(row, saved)
    with stack:
        path = f"/api/sessions/{row['session_id']}/record/questions"
        unknown = client.put(path, json={"questionId": "Q999", "answered": True})
        malformed = client.put(path, json={"questionId": "P-0001", "answered": True})
        too_long = client.put(path, json={"questionId": "Q001", "answered": True, "reply": "x" * 4001})
    assert unknown.status_code == 404
    assert malformed.status_code == 422 and too_long.status_code == 422
    assert saved == {}


def test_the_csv_route_is_a_private_download():
    row = synthetic.cached_row()
    stack, client = _client(row, {})
    with stack:
        response = client.get(f"/api/sessions/{row['session_id']}/record/questions.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "private, no-store"
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.startswith("﻿" + ",".join(CSV_COLUMNS))


def test_the_frontend_fixture_is_the_route_response():
    """`frontend/tests/fixtures/questions-v1.json` is what the desk route
    sends for the synthetic webinar, its chat labelled. After a deliberate
    change, regenerate it: `.venv/bin/python tests/synthetic_session.py`."""
    committed = synthetic.QUESTIONS_FIXTURE.read_text(encoding="utf-8")
    assert synthetic.dump(synthetic.question_desk_response()) == committed


def test_another_session_looks_missing_and_a_failure_hides_its_detail(monkeypatch):
    row, saved = synthetic.cached_row(), {}
    stack, client = _client(row, saved)
    with stack:
        missing = client.get("/api/sessions/someone-elses/record/questions")
        put_missing = client.put("/api/sessions/someone-elses/record/questions", json={"questionId": "Q001", "answered": True})
        from livestorm_app.record import question_desk as module

        monkeypatch.setattr(module, "question_desk", mock.Mock(side_effect=RuntimeError("secret internal detail")))
        broken = client.get(f"/api/sessions/{row['session_id']}/record/questions")
    assert missing.status_code == 404 and put_missing.status_code == 404
    assert broken.status_code == 500 and "secret internal detail" not in broken.text
    assert saved == {}


def test_confirming_a_located_answer_marks_it_answered_and_not_this_sets_it_aside():
    row, saved = synthetic.cached_row(), {}
    stack, client = _client(row, saved, synthetic.understanding(row))
    with stack:
        path = f"/api/sessions/{row['session_id']}/record/questions/located"
        desk = client.get(f"/api/sessions/{row['session_id']}/record/questions").json()
        assert desk["summary"]["located"] == READY
        pricing = _by_text(desk)["What about pricing?"]
        confirmed = client.put(path, json={"questionId": pricing["id"], "decision": "confirm"})
        aloud = client.put(path, json={"questionId": "Q002", "decision": "reject"})
    assert confirmed.status_code == 200 and aloud.status_code == 200, (confirmed.text, aloud.text)
    assert _by_text(confirmed.json())["What about pricing?"]["status"] == HERE
    after = _by_text(aloud.json())["How does pricing work for larger teams with several hundred seats?"]
    assert after["status"] == ALOUD and after["located"] is None
    assert {key: (value["answered"], value["located"]) for key, value in saved.items()} == {
        f"chat:{_livestorm_id(row, pricing['id'])}": (True, CONFIRMED),
        "qa:qst-002": (False, REJECTED),
    }


def _livestorm_id(row, question_id):
    return find_question(_inputs(row), question_id)["livestormId"]


def test_a_decision_needs_a_located_answer_to_decide_on():
    row, saved = synthetic.cached_row(), {}
    stack, client = _client(row, saved, synthetic.understanding(row))
    with stack:
        path = f"/api/sessions/{row['session_id']}/record/questions/located"
        nothing = client.put(path, json={"questionId": "Q004", "decision": "confirm"})
        aloud = client.put(path, json={"questionId": "Q002", "decision": "confirm"})
        unknown = client.put(path, json={"questionId": "Q999", "decision": "reject"})
        malformed = client.put(path, json={"questionId": "Q004", "decision": "maybe"})
        elsewhere = client.put("/api/sessions/someone-elses/record/questions/located", json={"questionId": "C0010", "decision": "confirm"})
    assert (nothing.status_code, aloud.status_code) == (409, 409), "nothing located; already answered aloud"
    assert (unknown.status_code, malformed.status_code, elsewhere.status_code) == (404, 422, 404)
    assert saved == {}


def test_a_stale_understanding_suggests_nothing():
    row, saved = synthetic.cached_row(), {}
    stale = {**synthetic.understanding(row), "dossier_hash": "0" * 64}
    stack, client = _client(row, saved, stale)
    with stack:
        desk = client.get(f"/api/sessions/{row['session_id']}/record/questions").json()
        refused = client.put(f"/api/sessions/{row['session_id']}/record/questions/located", json={"questionId": "C0010", "decision": "confirm"})
    assert desk["summary"]["located"] == STALE and desk["summary"]["covered"] == 0
    assert all(q["located"] is None for q in desk["questions"])
    assert refused.status_code == 409 and saved == {}
