"""The Follow-up's Session analysis: its brief (`record/session_brief.py`)
and its runner (`api_logic.run_overall_analysis`: English from the brief,
French a translation of it), on the synthetic webinar."""

from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import pytest
import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.ai import session_analysis
from livestorm_app.record.session_brief import chat_lines, clock, number_lines, question_lines


def _run(row=None, *, stored="current", answer="## What happened\nIt went well.", language="English", bundle=None):
    """Runs the Session analysis on `row` with the model faked. Returns
    `(result, seen)`: `seen` holds the brief, the language and the saves."""
    row = dict(row or synthetic.cached_row())
    if bundle is not None:
        row["analysis_bundle"] = bundle
    labels = synthetic.chat_labels(row)
    understanding = synthetic.understanding(row) if stored == "current" else stored
    seen = {"saved": []}

    def write(brief, *, output_language, api_key, ctx):
        seen.update(brief=brief, language=output_language, job=ctx.job_kind)
        return answer

    def translate(**kwargs):
        seen.update(translated=kwargs["source_markdown"])
        return "## Ce qui s'est passé\nTrès bien."

    patches = [
        mock.patch.object(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row)),
        mock.patch.object(api_logic, "fetch_chat_labels", lambda session_id, organization_id: labels),
        mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "fetch_question_replies", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "merge_cached_bundle_entry", lambda *args, **kwargs: seen["saved"].append(args)),
        mock.patch.object(api_logic, "translate_markdown_with_openai", translate),
        mock.patch.object(api_logic, "get_llm_model", lambda kind: f"model-for-{kind}"),
        mock.patch.object(session_analysis, "write_analysis", write),
        mock.patch("livestorm_app.db.fetch_understanding", lambda session_id, organization_id: understanding),
    ]
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        result = api_logic.run_overall_analysis("llm-key", row["session_id"], language)
    return result, seen


# ── The brief ──


def test_the_brief_holds_what_stormiq_measured_and_found():
    _, seen = _run()
    text = seen["brief"].text
    for section in ("## Numbers", "## The chat", "## Chapters", "## Audience questions", "## Transcript"):
        assert section in text, section
    assert "- Attended live (audience): 5, 71% of those registered" in text
    assert "- Median time in the room: about 75% of the session" in text, "an approximation says so"
    assert "- 5:06 Pricing for larger teams (content)" in text
    assert '(chat, not marked, but the talk seems to answer it) "What about pricing?"; answered in the talk at 5:18' in text
    assert '27:30 (Q&A, still open) "Is there an API?"' in text
    assert "0:21 S0: welcome everyone thanks for joining us today" in text
    assert "times: minutes:seconds from the start of the session" in text


def test_the_brief_is_the_same_bytes_every_time_and_names_nobody():
    _, first = _run()
    _, second = _run()
    assert first["brief"].sha256 == second["brief"].sha256
    text = first["brief"].text
    row = synthetic.cached_row()
    for person in synthetic.people():
        attributes = person.get("attributes", person)
        for value in (attributes.get("first_name"), attributes.get("last_name"), attributes.get("email"), person.get("id")):
            if value:
                assert str(value) not in text, f"{value!r} reached the brief"
    for name in row["transcript_speaker_names"].values():
        assert name not in text
    assert "P-" not in text and "@" not in text


def test_only_numbers_the_report_would_show_are_in_the_brief():
    measures = {
        "m.audience_attendees": {"state": "ok", "value": 40},
        "m.show_up_rate": {"state": "insufficient", "value": None},
        "m.wrote_rate": {"state": "unresolved", "value": 0.3},
        "m.time_to_answer": {"state": "ok", "value": 95},
        "m.speaker_talk_time": {"state": "ok", "rows": [{"key": "0", "share": 0.9}, {"key": "1", "share": 0.004}, {"key": "unknown", "share": 0.1}]},
    }
    assert number_lines(measures) == [
        "- Attended live (audience): 40",
        "- Median time to answer a Q&A question: 1:35",
        "- Talk time: S0 90%",
    ]
    assert number_lines({}) == []


def test_the_chat_lines_name_its_peaks_and_what_it_was_about():
    record = {
        "pulse": {"minutes": [{"minute": 0, "audienceChat": 1}, {"minute": 4, "audienceChat": 6}, {"minute": 9, "audienceChat": 0}]},
        "measures": {"m.chat_themes": {"state": "insufficient", "rows": [{"key": "tech", "messages": 2}, {"key": "content", "messages": 0}]}},
    }
    assert chat_lines(record) == [
        "- Busiest minutes of the audience's chat: 4:00 (6 messages), 0:00 (1 message)",
        "- What the audience's chat was about (messages): sound, video or connection issues 2",
    ]
    assert chat_lines({}) == []


def test_question_lines_carry_no_author_and_say_where_the_talk_answered():
    lines = question_lines([
        {"t": -30, "source": "qa", "text": "Slides?", "answered_as": "written", "author": "Priya S.", "located": None},
        {"t": 200, "source": "chat", "text": "API?", "answered_as": "covered", "located": {"t": 319, "quote": "yes, there is one"}},
    ])
    assert lines == [
        '- before (Q&A, answered in writing in Livestorm) "Slides?"',
        '- 3:20 (chat, not marked, but the talk seems to answer it) "API?"; answered in the talk at 5:19: "yes, there is one"',
    ]
    assert "Priya" not in "".join(lines)
    assert (clock(3725), clock(None), clock(-1)) == ("1:02:05", "-", "before")


def test_a_flagged_figure_is_never_stated_as_a_fact():
    row = synthetic.cached_row()
    stored = synthetic.understanding(row)
    figure = {"utterance": "U0029", "span": "scales with seats", "flags": ["two_values", "acronym"]}
    _, seen = _run(row, stored={**stored, "result": {**stored["result"], "figures": [figure]}})
    line = next(line for line in seen["brief"].text.splitlines() if "scales with seats\"" in line and line.startswith("- "))
    assert line.startswith("- 5:18 ") and "two values: do not state it as a fact" in line and "acronym is unclear" in line


# ── The runner ──


def test_the_english_analysis_is_written_from_the_brief_and_saved():
    result, seen = _run()
    assert seen["language"] == "English" and seen["job"] == "overall_analysis"
    assert result["markdown"] == "## What happened\nIt went well."
    assert seen["saved"] == [(synthetic.SESSION_ID, "analysis_bundle", "English", "## What happened\nIt went well.")]


def test_the_french_analysis_is_a_translation_of_the_english_one():
    result, seen = _run(language="French", bundle={"English": "## What happened\nIt went well."})
    assert "brief" not in seen, "no new reading for a translation"
    assert seen["translated"] == "## What happened\nIt went well."
    assert result["markdown"].startswith("## Ce qui s'est passé")
    with pytest.raises(RuntimeError, match="English analysis first"):
        _run(language="French", bundle={})


def test_the_session_must_be_analysed_as_it_is_now():
    with pytest.raises(api_logic.SessionNotAnalysedError, match='Run "Analyse session" on the Report first'):
        _run(stored=None)
    stale = {**synthetic.understanding(synthetic.cached_row()), "dossier_hash": "0" * 64}
    with pytest.raises(api_logic.SessionNotAnalysedError, match="changed since it was analysed"):
        _run(stored=stale)


def test_no_transcript_and_an_empty_answer_save_nothing():
    with pytest.raises(RuntimeError, match="needs the transcript"):
        _run({**synthetic.cached_row(), "transcript_payload": None}, stored=None)
    with pytest.raises(RuntimeError, match="empty session analysis"):
        _run(answer="   ")


def test_the_prompt_asks_for_four_sections_in_the_language():
    text = session_analysis.load_instructions("French")
    assert "Write in French" in text and "{output_language}" not in text
    for heading in ("## What happened", "## What landed with the audience", "## Questions", "## What to do next"):
        assert heading in text
