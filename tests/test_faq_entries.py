"""The editable FAQ (`record/faq_entries.py`, `api_logic.get_session_faq`,
`save_session_faq_entries`, `get_session_faq_export`) and its routes,
`GET`/`PUT /api/sessions/{id}/record/faq` and `GET …/record/faq.{md,csv,pdf}`,
on the synthetic webinar."""

from __future__ import annotations

import copy
import csv
import io
from unittest import mock

import pytest
import synthetic_session as synthetic

from livestorm_app import api_logic, db
from livestorm_app.record import faq_entries as faq
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.question_desk import desk_keys, question_desk
from livestorm_app.record.redaction import public_redactor, saved_speaker_names

API_Q = "Is there an API?"


def _row(*, api_question=API_Q, api_answer=None):
    row = synthetic.cached_row()
    questions = copy.deepcopy(row["questions_payload"])
    for item in questions["data"]:
        if item["id"] == "qst-004":
            item["attributes"]["question"] = api_question
            if api_answer:
                item["attributes"]["response"] = api_answer
    row["questions_payload"] = questions
    return row


def _draft(row=None, saved=None, located=None, *, labelled=True):
    row = row or _row()
    inputs = load_record_inputs(row, stored_labels=synthetic.chat_labels(row) if labelled else None)
    desk = question_desk(inputs, saved or {}, located or {}, "ready" if located else "none")
    redactor = public_redactor(inputs.roster, saved_speaker_names(row))
    questions = [{**q, "answered_as": q["status"]} for q in desk["questions"]]
    return faq.draft_entries(questions, desk_keys(inputs), redactor.redact)


def _by_question(entries):
    return {entry["question"]: entry for entry in entries}


# ── The draft, from the desk ──


def test_one_entry_per_desk_question_in_the_desks_order_with_its_source():
    entries = _draft()
    assert [e["question"] for e in entries] == [
        "Will the slides be shared?",
        "What about pricing?",
        "How does pricing work for larger teams with several hundred seats?",
        "Can we export to CSV?",
        API_Q,
    ], "in the desk's (time) order"
    rows = _by_question(entries)
    assert rows["Will the slides be shared?"]["source"] == faq.WRITTEN
    assert rows["Will the slides be shared?"]["answer"] == "Yes, see the chat."
    aloud = rows["How does pricing work for larger teams with several hundred seats?"]
    assert aloud["source"] == faq.ALOUD and aloud["answer"] == "Answered live: see the replay at 5:00." and aloud["t"] == 300
    assert rows[API_Q]["source"] == faq.NONE and rows[API_Q]["answer"] == "" and rows[API_Q]["t"] is None
    assert all(e["origin"] == faq.DESK and len(e["deskIds"]) == 1 for e in entries)
    assert rows[API_Q]["deskIds"] == ["qa:qst-004"], "the desk's stable key, not the evidence id"
    assert len({e["id"] for e in entries}) == len(entries)


def test_logistics_and_team_questions_are_left_out():
    questions = [e["question"] for e in _draft()]
    assert "Is there a replay?" not in questions, "a logistics question"
    assert "Happy to cover pricing later" not in questions, "a guest speaker's: the team's"
    assert [q for q in questions if "pricing" in q.lower()] == [
        "What about pricing?",
        "How does pricing work for larger teams with several hundred seats?",
    ]
    assert len(_draft(labelled=False)) == 4, "without labels the desk has the Q&A questions only"


def test_the_hosts_reply_then_the_talk_fill_the_answer():
    saved = {"qa:qst-004": {"answered": True, "reply": "Yes, a REST API.", "located": None}}
    located = {
        "Q002": {"t": 330.5, "quote": "Pricing works per team, from five seats."},
        "Q004": {"t": 1700, "quote": "We have an API too."},
    }
    rows = _by_question(_draft(saved=saved, located=located))
    assert rows[API_Q]["source"] == faq.HOST and rows[API_Q]["answer"] == "Yes, a REST API." and rows[API_Q]["t"] is None
    talk = rows["How does pricing work for larger teams with several hundred seats?"]
    assert talk["source"] == faq.TALK and talk["answer"] == "Pricing works per team, from five seats." and talk["t"] == 330.5


def test_questions_and_answers_are_redacted():
    row = _row(
        api_question="Theo Park here (theo.park@example.test): is there an API? See https://example.test/x",
        api_answer="Yes Theo, mail mila.ford@example.test or call +33 6 12 34 56 78.",
    )
    entry = _by_question(_draft(row))
    text = next(q for q in entry if q.startswith("[name]"))
    assert text == "[name] here ([email]): is there an API? See [link]"
    answer = entry[text]["answer"]
    assert answer == "Yes [name], mail [email] or call [phone]."
    for value in ("Theo", "Park", "example.test", "+33"):
        assert value not in text + answer


# ── Validation ──


def _entry(**fields):
    return {"id": "m-1", "question": "Q?", "answer": "", "source": "none", "origin": "manual", "deskIds": [], **fields}


def test_entries_are_cleaned_and_their_source_follows_the_answer():
    cleaned = faq.validate_entries([
        _entry(question="  What?\x00 ", answer="Typed.\r\nTwo lines", source="none"),
        _entry(id="m-2", answer="   ", source="written", t=12),
        _entry(id="m-3", answer="From the talk", source="talk", t=61.0),
        _entry(id="m-4", answer="Mine", source="host", t=61),
    ])
    assert cleaned[0]["question"] == "What?" and cleaned[0]["answer"] == "Typed.\nTwo lines" and cleaned[0]["source"] == faq.MANUAL
    assert cleaned[1]["source"] == faq.NONE and cleaned[1]["answer"] == "" and cleaned[1]["t"] is None
    assert cleaned[2]["t"] == 61 and cleaned[3]["t"] is None, "a time only for the talk or a live answer"


@pytest.mark.parametrize(
    "entries,message",
    [
        ([_entry()] * 2, "repeated id"),
        ([_entry(id=f"m-{i}") for i in range(faq.MAX_ENTRIES + 1)], "at most 200"),
        ([_entry(question="x" * 501)], "longer than 500"),
        ([_entry(answer="x" * 4001)], "longer than 4000"),
        ([_entry(source="ai")], "unknown source"),
        ([_entry(origin="model")], "unknown origin"),
        ([_entry(question="   ")], "needs a question"),
        ([_entry(id="bad id!")], "malformed"),
        ([_entry(deskIds=["qa:x y"])], "malformed desk id"),
        ([_entry(answer={"html": "<b>"})], "must be text"),
        ("not a list", "list of entries"),
    ],
)
def test_bad_entries_are_refused(entries, message):
    with pytest.raises(faq.FaqInvalid, match=message):
        faq.validate_entries(entries)


# ── In step with the desk ──


def test_a_question_answered_on_the_desk_after_the_save_is_offered():
    before = _draft()
    entries = faq.validate_entries(before)
    stamp = faq.desk_stamp(before, entries)
    assert faq.desk_updates(entries, stamp, before) == []
    after = _draft(saved={"qa:qst-004": {"answered": True, "reply": "Yes, a REST API."}})
    updates = faq.desk_updates(entries, stamp, after)
    assert [(u["question"], u["answer"], u["source"], u["kind"]) for u in updates] == [
        (API_Q, "Yes, a REST API.", faq.HOST, faq.UPDATE_ANSWERED)
    ]
    # Saved again with the update left out: the entry is still open, so it is offered again.
    stamp = faq.desk_stamp(after, entries, stamp)
    assert [u["question"] for u in faq.desk_updates(entries, stamp, after)] == [API_Q]


def test_a_question_already_answered_in_the_faq_or_edited_by_the_user_is_never_offered():
    before = _draft()
    entries = faq.validate_entries(before)
    for entry in entries:
        if entry["question"] == API_Q:
            entry["answer"] = "The user's own answer."
    stamp = faq.desk_stamp(before, entries)
    after = _draft(saved={"qa:qst-004": {"answered": True, "reply": "Yes, a REST API."}})
    assert faq.desk_updates(entries, stamp, after) == [], "a user-edited answer is never overwritten"


def test_a_new_desk_question_is_offered_and_a_removed_one_only_once_answered():
    before = _draft(labelled=False)
    entries = [e for e in faq.validate_entries(before) if e["question"] != API_Q]
    stamp = faq.desk_stamp(before, entries)
    after = _draft()  # the chat is labelled now: its content question joins the desk
    updates = faq.desk_updates(entries, stamp, after)
    assert [(u["question"], u["kind"]) for u in updates] == [("What about pricing?", faq.UPDATE_NEW)]
    answered = _draft(saved={"qa:qst-004": {"answered": True, "reply": "Yes."}})
    kinds = {u["question"]: u["kind"] for u in faq.desk_updates(entries, stamp, answered)}
    assert kinds[API_Q] == faq.UPDATE_ANSWERED, "the removed question, answered since, is offered again"


# ── Exports ──


def test_markdown_lists_each_question_and_its_answer():
    entries = [
        {"question": "Q1?", "answer": "A1.", "source": "written", "t": None},
        {"question": "Q2?", "answer": "Said on stage.", "source": "talk", "t": 3725},
        {"question": "Q3?", "answer": "", "source": "none", "t": None},
    ]
    assert faq.faq_markdown(entries, "Launch") == (
        "# FAQ: Launch\n\n## Q1?\n\nA1.\n\n## Q2?\n\nSaid on stage.\n(1:02:05 in the replay)\n\n## Q3?\n\nNot answered yet.\n"
    )


def test_the_csv_has_its_columns_in_order_and_neutralises_formulas():
    entries = [
        {"question": "=HYPERLINK(\"x\")", "answer": "+1 more", "source": "manual", "t": None},
        {"question": "Q2?", "answer": "Live.", "source": "aloud", "t": 75},
        {"question": "Q3?", "answer": "", "source": "none", "t": 75},
    ]
    rows = list(csv.reader(io.StringIO(faq.faq_csv(entries))))
    assert rows[0] == ["Question", "Answer", "Source", "Time in the replay"]
    assert rows[1] == ["'=HYPERLINK(\"x\")", "'+1 more", "Added by you", ""]
    assert rows[2] == ["Q2?", "Live.", "Answered live", "1:15"]
    assert rows[3] == ["Q3?", "", "Not answered yet", ""]


def test_replay_times():
    assert [faq.replay_time(t) for t in (None, -5, 0, 59.9, 61, 3600, "x")] == ["", "", "0:00", "0:59", "1:01", "1:00:00", ""]
    assert faq.aloud_line(None) == "Answered live: see the replay."


# ── Saved, read back, scoped (Postgres) ──

ORG = synthetic.ORGANIZATION_ID
SID = synthetic.SESSION_ID


def _seed(row=None):
    row = row or _row()
    db.upsert_cached_session(
        "key-a", SID, organization_id=ORG,
        **{field: row[field] for field in ("session_payload", "event_payload", "chat_payload", "questions_payload",
                                           "transcript_speaker_names")},
        faq_bundle={"English": "## An AI FAQ\n\nWritten before."},
    )
    db.upsert_cached_session("key-b", "sess-other", organization_id="org-other", session_payload={"name": "B"})


def test_the_draft_is_read_until_the_first_save_then_the_saved_entries(test_database):
    _seed()
    first = api_logic.get_session_faq(SID, organization_id=ORG)
    assert first["saved"] is False and first["deskUpdates"] == [] and first["updatedAt"] is None
    assert [e["question"] for e in first["entries"]][-1] == API_Q
    assert first["earlier"] == {"markdown": "## An AI FAQ\n\nWritten before."}
    assert db.fetch_session_faq(SID, ORG) is None, "nothing is stored before the first save"

    entries = [{**e, "answer": "Edited."} if e["question"] == "Will the slides be shared?" else e for e in first["entries"]]
    entries.append({"id": "m-1", "question": "Added by hand?", "answer": "Yes.", "source": "none", "origin": "manual", "deskIds": []})
    saved = api_logic.save_session_faq_entries(SID, entries, organization_id=ORG, updated_by="user-1")
    assert saved["saved"] is True and saved["updatedAt"]
    assert saved["entries"][0]["answer"] == "Edited." and saved["entries"][-1]["source"] == faq.MANUAL
    stored = db.fetch_session_faq(SID, ORG)
    assert stored["updated_by"] == "user-1" and stored["desk_stamp"]["qa:qst-004"] == faq.STAMP_OPEN

    # The host answers on the desk afterwards: the FAQ offers it, without touching the entries.
    db.save_question_reply(SID, organization_id=ORG, source="qa", livestorm_id="qst-004", evidence_id="Q004",
                           answered=True, reply="Yes, a REST API.", updated_by="user-1")
    after = api_logic.get_session_faq(SID, organization_id=ORG)
    assert [(u["question"], u["answer"], u["kind"]) for u in after["deskUpdates"]] == [(API_Q, "Yes, a REST API.", "answered")]
    assert after["entries"] == saved["entries"]


def test_another_orgs_session_looks_missing_and_a_blank_scope_reads_nothing(test_database):
    _seed()
    api_logic.save_session_faq_entries(SID, [{"question": "Q?", "id": "m-1"}], organization_id=ORG, updated_by=None)
    with pytest.raises(api_logic.SessionNotFoundError):
        api_logic.get_session_faq(SID, organization_id="org-other")
    with pytest.raises(api_logic.SessionNotFoundError):
        api_logic.save_session_faq_entries(SID, [{"question": "Q?", "id": "m-1"}], organization_id="org-other", updated_by=None)
    assert db.save_session_faq(SID, organization_id="org-other", entries=[], desk_stamp={}, updated_by=None) is False
    assert db.save_session_faq(SID, organization_id="", entries=[], desk_stamp={}, updated_by=None) is False
    assert db.fetch_session_faq(SID, "org-other") is None and db.fetch_session_faq(SID, "") is None
    assert [e["question"] for e in db.fetch_session_faq(SID, ORG)["entries"]] == ["Q?"]


def test_the_exports(test_database):
    _seed()
    pdf = api_logic.get_session_faq_export(SID, "pdf", organization_id=ORG)
    assert pdf["content"].startswith(b"%PDF") and pdf["media_type"] == "application/pdf"
    md = api_logic.get_session_faq_export(SID, "md", organization_id=ORG)["content"].decode()
    assert md.startswith("# FAQ") and f"## {API_Q}" in md
    text = api_logic.get_session_faq_export(SID, "csv", organization_id=ORG)["content"].decode("utf-8")
    assert text.startswith("﻿Question,Answer,Source,Time in the replay")
    with pytest.raises(api_logic.SessionNotFoundError):
        api_logic.get_session_faq_export(SID, "pdf", organization_id="org-other")


# ── The routes ──


def test_the_routes_answer_400_for_bad_entries_and_500_without_detail():
    from fastapi.testclient import TestClient

    import app as app_module
    from livestorm_app.auth import Caller, get_caller

    app_module.app.dependency_overrides[get_caller] = lambda: Caller(kind="user", org_id=ORG, connection={"organization_id": ORG, "user_id": "u"})
    try:
        client = TestClient(app_module.app)
        with mock.patch.object(api_logic, "_faq_state", side_effect=AssertionError("never read")):
            bad = client.put(f"/api/sessions/{SID}/record/faq", json={"entries": [{"id": "a", "question": "Q", "source": "ai"}]})
        assert bad.status_code == 422
        with mock.patch.object(api_logic, "_faq_state", side_effect=AssertionError("never read")):
            repeated = client.put(f"/api/sessions/{SID}/record/faq", json={"entries": [{"id": "a", "question": "Q"}] * 2})
        assert repeated.status_code == 400 and "repeated id" in repeated.json()["detail"]["message"]
        with mock.patch.object(api_logic, "_question_desk_inputs", side_effect=RuntimeError("secret detail")):
            failed = client.get(f"/api/sessions/{SID}/record/faq")
        assert failed.status_code == 500 and "secret" not in failed.text
        assert failed.json()["detail"] == {"resource": "FAQ", "message": "The FAQ could not be read."}
    finally:
        app_module.app.dependency_overrides.pop(get_caller, None)
