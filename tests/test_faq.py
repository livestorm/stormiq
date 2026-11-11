"""The Follow-up's FAQ: its brief (`record/faq_brief.py`, from the question
desk), its runner (`api_logic.run_faq`: English from the brief, French a
translation of it) and its routes, on the synthetic webinar."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest import mock

import pytest
import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.ai import faq
from livestorm_app.record.faq_brief import build_faq_brief, question_block


def _run(row=None, *, stored="current", labelled=True, answer="## Questions and answers\n### Q?\nA.", language="English", bundle=None):
    """Runs the FAQ on `row` with the model faked. Returns `(result, seen)`:
    `seen` holds the brief, the language and the saves."""
    row = dict(row or synthetic.cached_row())
    if bundle is not None:
        row["faq_bundle"] = bundle
    labels = synthetic.chat_labels(row) if labelled else None
    understanding = synthetic.understanding(row) if stored == "current" else stored
    seen = {"saved": []}

    def write(brief, *, output_language, api_key, ctx):
        seen.update(brief=brief, language=output_language, job=ctx.job_kind)
        return answer

    def translate(**kwargs):
        seen.update(translated=kwargs["source_markdown"], translation_job=kwargs["ctx"].job_kind)
        return "## Questions et réponses\n### Q ?\nR."

    patches = [
        mock.patch.object(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row)),
        mock.patch.object(api_logic, "fetch_chat_labels", lambda session_id, organization_id: labels),
        mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "fetch_question_replies", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "merge_cached_bundle_entry", lambda *args, **kwargs: seen["saved"].append(args)),
        mock.patch.object(api_logic, "translate_markdown_with_openai", translate),
        mock.patch.object(api_logic, "get_llm_model", lambda kind: f"model-for-{kind}"),
        mock.patch.object(faq, "write_faq", write),
        mock.patch("livestorm_app.db.fetch_understanding", lambda session_id, organization_id: understanding),
    ]
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        result = api_logic.run_faq("llm-key", row["session_id"], language)
    return result, seen


# ── The brief ──


def test_the_brief_holds_each_question_with_its_answers_and_no_author():
    _, seen = _run()
    text = seen["brief"].text
    assert text.startswith("# FAQ brief") and "## Questions" in text
    assert "### q1" in text
    assert 'answered in the talk at 5:18: "' in text, "a covered question carries the talk's passage"
    assert "status: not answered yet" in text
    assert "written answer: \"" in text
    authors = {q["author"] for q in json.loads(synthetic.QUESTIONS_FIXTURE.read_text(encoding="utf-8"))["questions"]}
    assert authors and not [author for author in authors if author in text], "who asked never reaches the model"
    assert "author" not in text and "Q001" not in text, "never who asked, never the desk's own ids"


def test_a_question_block_says_what_it_has():
    block = question_block(3, {"source": "qa", "t": 75, "answered_as": "aloud", "text": "  Is   there an API? ", "author": "Sam R."})
    assert block.splitlines() == [
        "### q3",
        "asked: 1:15, in the Q&A",
        "status: answered aloud during the session",
        'question: "Is there an API?"',
        "the words of the spoken answer weren't found: point to the replay",
    ]
    assert "Sam" not in block
    replied = question_block(1, {"source": "chat", "t": 10, "answered_as": "here", "text": "Price?", "reply": "From 99 a month."})
    assert 'host\'s answer: "From 99 a month."' in replied and "in the chat" in replied


def test_the_same_desk_gives_the_same_bytes_and_no_desk_no_brief():
    rows = [{"source": "qa", "t": 1, "answered_as": "open", "text": "Why?"}]
    assert build_faq_brief(title="T", internal=False, questions=rows) == build_faq_brief(title="T", internal=False, questions=rows)
    with pytest.raises(ValueError):
        build_faq_brief(title="T", internal=False, questions=[])


# ── The runner ──


def test_the_english_faq_is_written_from_the_desk_and_saved():
    result, seen = _run()
    assert seen["language"] == "English" and seen["job"] == "faq"
    assert result["markdown"].startswith("## Questions and answers")
    assert seen["saved"] == [(synthetic.SESSION_ID, "faq_bundle", "English", "## Questions and answers\n### Q?\nA.")]


def test_the_french_faq_is_a_translation_of_the_english_one():
    result, seen = _run(language="French", bundle={"English": "## Questions and answers\n### Q?\nA."})
    assert "brief" not in seen and seen["translation_job"] == "translation"
    assert result["markdown"].startswith("## Questions et réponses")
    with pytest.raises(RuntimeError, match="English FAQ first"):
        _run(language="French", bundle={})


def test_the_session_must_be_analysed_as_it_is_now():
    with pytest.raises(api_logic.SessionNotAnalysedError, match="chat's questions"):
        _run(labelled=False)
    with pytest.raises(api_logic.SessionNotAnalysedError, match="answers it finds in the talk"):
        _run(stored=None)
    stale = {**synthetic.understanding(synthetic.cached_row()), "dossier_hash": "0" * 64}
    with pytest.raises(api_logic.SessionNotAnalysedError, match="changed since it was analysed"):
        _run(stored=stale)


def test_without_a_transcript_the_desk_is_enough():
    _, seen = _run({**synthetic.cached_row(), "transcript_payload": None}, stored=None)
    assert "### q1" in seen["brief"].text and "answered in the talk" not in seen["brief"].text


def test_an_empty_answer_saves_nothing():
    with pytest.raises(RuntimeError, match="empty FAQ"):
        _run(answer="   ")


def test_the_prompt_asks_for_the_two_sections_in_the_language():
    text = faq.load_instructions("French")
    assert "Write in French" in text and "{output_language}" not in text
    assert "## Questions and answers" in text and "## Still to answer" in text
    assert "Never write a person's name" in text


# ── The routes ──


def test_a_faq_job_is_started_once_per_language():
    calls = []
    with (
        mock.patch.object(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: {"faq_bundle": None}),
        mock.patch.object(api_logic, "enqueue_unique_job_sync", lambda *args: calls.append(args) or {"jobId": args[1], "jobStatus": "queued"}),
    ):
        api_logic.start_faq("s1", "English", organization_id="org-a")
        with pytest.raises(RuntimeError, match="English version first"):
            api_logic.start_faq("s1", "French", organization_id="org-a")
    assert calls[0][:2] == ("run_faq_job", "faq:s1:English")
