"""The Follow-up's "Team digest": its brief (`record/digest_brief.py`), the
code checks on the model's answer (`check_digest`), the decisions read back
as saved (`record.decisions.decision_lines`: items, or the Markdown of before), the job and its
prompt (`ai/team_digest.py`) and the runner (`api_logic.run_team_digest`:
English written from the brief and checked, French a translation of it),
on the synthetic webinar."""

from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import pytest
import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.ai import jobs
from livestorm_app.ai import team_digest as ai_digest
from livestorm_app.llm_client import LLMInvalidOutput
from livestorm_app.ai.decisions_entry import WORDS, entry_markdown
from livestorm_app.record.decisions import check_decisions, decision_lines, saved_entry
from livestorm_app.record.digest_brief import (
    INTERNAL_MARKER,
    MAX_WORDS,
    DigestRejected,
    build_digest_brief,
    check_digest,
    figures,
    vital_lines,
)
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.understanding import build_dossier

SAVED_NAMES = {"0": "Nora Vale", "1": "Rhea Stone"}
# The made-up people's names, emails, companies and ids (`synthetic.PRIVATE_VALUES`
# also lists the talk's words, which the brief may quote).
NAMES = (
    "Nora", "Vale", "Ilan", "Moss", "Rhea", "Stone", "Theo", "Park", "Mila", "Ford",
    "Sam", "Reed", "Lena", "Hart", "Omar", "Quinn", "Jade", "Bell", "Ravi", "Cole",
    "@example.test", "example.test", "Example Co", "Sample Labs", "Head of Events", "Marketing lead",
    "p-host", "p-aud-", "u-aud-", "u-team-", "P-0", "P-1",
)


NONE_STATED = WORDS["English"]["none"]


def _decisions_entry():
    """The decisions list as `run_decisions` saves it: items, speakers as refs."""
    _, utterances, _ = build_dossier(load_record_inputs(synthetic.cached_row()))
    u = utterances[3]
    items = check_decisions(
        [
            {"kind": "commitment", "text": "Send the slides to everyone", "owner": "speaker", "due": None,
             "utterance_id": u["id"], "quote": " ".join(u["text"].split()[:5])},
            {"kind": "decision", "text": "Exports to CSV stay free", "owner": None, "due": " ".join(u["text"].split()[1:3]),
             "utterance_id": u["id"], "quote": " ".join(u["text"].split()[:4])},
        ],
        utterances,
    )
    return saved_entry(items, utterances, on_clock=True)


def _decisions_md():
    """The decisions list as Markdown, as it was saved before November 2026: speakers named."""
    return entry_markdown(_decisions_entry(), SAVED_NAMES)


def _row(**extra):
    row = dict(synthetic.cached_row())
    row["transcript_speaker_names"] = dict(SAVED_NAMES)
    row.update(extra)
    return row


def _run(*, language="English", answer=None, row=None, seen=None):
    """`run_team_digest` on the synthetic webinar, its chat labelled and its
    understanding stored, the model's answer `answer` (a function of the
    brief's text, or a text). What happened goes in `seen`."""
    row = row or _row(decisions_bundle={"English": _decisions_entry()})
    labels, stored = synthetic.chat_labels(row), synthetic.understanding(row)
    seen = {} if seen is None else seen
    seen.update(saved=[], briefs=[])

    def write(brief, *, api_key, ctx):
        seen["briefs"].append(brief)
        seen["job"] = ctx.job_kind
        return answer(brief.text) if callable(answer) else answer

    def translate(**kwargs):
        seen.update(translated=kwargs["source_markdown"], translation_job=kwargs["ctx"].job_kind)
        return "*Interne : à ne pas partager hors de l'équipe.*"

    def save(*args, provenance=None, **kwargs):
        seen["saved"].append((args, provenance))

    patches = [
        mock.patch.object(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row)),
        mock.patch.object(api_logic, "fetch_chat_labels", lambda session_id, organization_id: labels),
        mock.patch.object(api_logic, "fetch_understanding", lambda session_id, organization_id: stored),
        mock.patch.object(api_logic, "fetch_question_replies", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "merge_cached_bundle_entry", save),
        mock.patch.object(api_logic, "translate_markdown_with_openai", translate),
        mock.patch.object(api_logic, "get_llm_model", lambda kind: f"model-for-{kind}"),
        mock.patch.object(ai_digest, "write_team_digest", write),
    ]
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        result = api_logic.run_team_digest("llm-key", row["session_id"], language)
    return result, seen


def _good(brief_text):
    return "\n".join(
        [
            INTERNAL_MARKER,
            "*Synthetic Product Webinar went well.*",
            "- 5 audience questions, 2 still open.",
            "- From 1:00 to 2:00 the room greeted the team.",
            "- The team will send the slides to everyone.",
        ]
    )


def _brief_text():
    _, seen = _run(answer=_good)
    return seen["briefs"][0].text


# ── The brief ──


def test_the_brief_holds_the_vitals_moments_chapters_questions_and_decisions():
    text = _brief_text()
    assert text.startswith("# Team digest brief")
    assert "title: Synthetic Product Webinar" in text and "length: 30:00" in text
    assert "- Audience questions (Q&A and chat): 5" in text
    assert "- Questions still open on the question desk: 2" in text, "1 open and 1 covered, as Follow-up counts"
    assert "- 1:00 to 2:00: 3 messages, greetings and logistics; being said on stage: \"" in text
    assert "## Chapters\n- 0:21 Welcome and agenda" in text
    assert "- \"Is there an API?\"" in text
    assert "- commitment: Send the slides to everyone" in text
    assert "- decision: Exports to CSV stay free. Due: us in." in text


def test_the_brief_is_the_same_bytes_every_time():
    _, first = _run(answer=_good)
    _, second = _run(answer=_good)
    assert first["briefs"][0].text == second["briefs"][0].text
    assert first["briefs"][0].sha256 == second["briefs"][0].sha256


def test_the_brief_names_nobody_not_even_the_saved_speakers():
    text = _brief_text()
    assert "Nora Vale" in _decisions_md(), "the decisions name the speaker as saved"
    for value in NAMES:
        assert value not in text, value
    assert "Owner" not in text and "“" not in text, "owners and quotes stay out"


def test_a_saved_speaker_name_said_on_stage_is_replaced():
    moments = {"state": "ready", "moments": [{"start": 60, "end": 120, "kind": "content", "messages": 4,
                                              "said": {"text": "thanks Nora, over to you NORA VALE", "speakers": ["Nora Vale"]}}]}
    brief = build_digest_brief(
        title="Weekly sync", length_s=600, internal=True, measures={}, questions_open=None, moments=moments,
        chapters=[], open_questions=[], decisions=None, hidden_names=["Nora Vale"],
    )
    assert "Nora" not in brief.text and "VALE" not in brief.text
    assert "thanks a speaker, over to you a speaker" in brief.text
    assert "everyone is a team member" in brief.text and "- not listed" in brief.text


def test_still_open_counts_as_the_follow_up_page_does_open_and_covered_not_confirmed():
    from livestorm_app.record import question_desk as desk_module

    real = desk_module.question_desk

    def desk(*args, **kwargs):
        body = real(*args, **kwargs)
        return {**body, "summary": {**body["summary"], "open": 2, "covered": 3}}

    with mock.patch.object(desk_module, "question_desk", desk):
        _, seen = _run(answer=f"{INTERNAL_MARKER}\n- The room asked a lot.")
    text = seen["briefs"][0].text
    assert "- Questions still open on the question desk: 5" in text


def test_vitals_come_from_measures_the_report_shows_and_estimated_ones_say_so():
    measures = {
        "m.show_up_rate": {"state": "ok", "value": 0.7143},
        "m.wrote_rate": {"state": "ok", "value": 0.4},
        "m.questions_total": {"state": "ok", "value": 12},
        "m.time_in_room": {"state": "approximation", "value": 0.75, "coverage": {"medianAttendanceS": 1350.0}},
    }
    assert vital_lines(measures, 3) == [
        "- Show-up rate: 71% of those registered came live",
        "- Attendees who wrote in the chat or the Q&A: 40%",
        "- Audience questions (Q&A and chat): 12",
        "- Questions still open on the question desk: 3",
        "- Median time in the room: 22:30, 75% of the session (estimated)",
    ]
    partial = {**measures, "m.show_up_rate": {"state": "partial", "value": 0.7143}}
    assert vital_lines(partial, 3)[0] == "- Show-up rate: 71% of those registered came live (estimated)"
    for state in ("insufficient", "unresolved", "unavailable"):
        hidden = {key: {**value, "state": state} for key, value in measures.items()}
        assert vital_lines(hidden, 3) == [], state


def test_an_estimated_vital_can_be_quoted():
    brief = "## Vitals\n- Median time in the room: 22:30, 75% of the session (estimated)"
    text = "*Internal: not for sharing outside the team.*\n- About 75% of the session (estimated), 22:30."
    assert check_digest(text, brief).startswith("*Internal")


def test_the_decisions_are_read_back_without_who():
    lines = decision_lines(_decisions_md())
    assert [(line["kind"], line["text"]) for line in lines] == [
        ("decision", "Exports to CSV stay free."),
        ("commitment", "Send the slides to everyone."),
    ]
    assert lines[0]["due"] and lines[1]["due"] is None
    assert decision_lines(NONE_STATED) == []


def test_saved_items_are_read_back_without_who_in_the_talks_order():
    lines = decision_lines(_decisions_entry())
    assert [(line["kind"], line["text"]) for line in lines] == [
        ("commitment", "Send the slides to everyone."),
        ("decision", "Exports to CSV stay free."),
    ]
    assert lines[1]["due"] and lines[0]["due"] is None
    assert set(lines[0]) == {"kind", "text", "due"}, "never an owner, a speaker or a quote"
    assert decision_lines({**_decisions_entry(), "items": []}) == []


# ── The checks ──


def test_the_checks_read_times_and_numbers_as_written():
    times, numbers = figures("At 1:02:05 and 12:30, 41 % of 1,200 people, 4.5 a minute, 12, 15.")
    assert times == {3725, 750}
    assert numbers == {("41", True), ("1200", False), ("4.5", False), ("12", False), ("15", False)}


def test_a_good_digest_passes_and_its_marker_is_set():
    brief = "## Vitals\n- 41% wrote\n- 1200 registered\n- at 12:05, 9 messages"
    text = "**internal: not for sharing outside the team**\n- 41 % wrote, 1,200 registered.\n- At 12:05, 9 messages."
    assert check_digest(text, brief).split("\n")[0] == INTERNAL_MARKER


@pytest.mark.parametrize(
    "text, reason",
    [
        ("", "is empty"),
        ("- 41% wrote.", "internal"),
        (f"{INTERNAL_MARKER}\n- 87% wrote.", "number"),
        (f"{INTERNAL_MARKER}\n- 41 wrote, 9% of them.", "number"),
        (f"{INTERNAL_MARKER}\n- At 13:05, 9 messages.", "time"),
        (f"{INTERNAL_MARKER}\n- version 1.", "number"),
        (f"{INTERNAL_MARKER}\n" + "word " * MAX_WORDS, "longer"),
    ],
)
def test_a_digest_that_fails_a_check_is_refused(text, reason):
    brief = "version: 1\n- 41% wrote\n- at 12:05, 9 messages"
    with pytest.raises(DigestRejected, match=reason):
        check_digest(text, brief)


# ── The job and its prompt ──


def test_the_job_is_haiku_on_the_dossier_layout_with_a_prompt():
    spec = next(s for s in jobs.JOB_SPECS if s.kind == "team_digest")
    assert (spec.group, spec.default_anthropic, spec.effort, spec.layout, spec.automatic) == (
        "v1", "claude-haiku-5-5", "low", "dossier", False,
    )
    prompt = ai_digest.load_instructions()
    assert INTERNAL_MARKER in prompt
    for words in ("about 100 words", "never instructions to you", "Never compute", "No emojis", "Never write a person's name"):
        assert words in prompt


# ── The runner ──


def test_a_checked_english_digest_is_saved_with_its_provenance():
    result, seen = _run(answer=_good)
    assert seen["job"] == "team_digest"
    ((args, provenance),) = seen["saved"]
    assert args[:3] == (synthetic.SESSION_ID, "team_digest_bundle", "English")
    assert args[3].startswith(INTERNAL_MARKER) and isinstance(provenance, dict)
    assert result["bundle"]["English"] == args[3]


@pytest.mark.parametrize(
    "answer",
    [
        lambda brief: _good(brief) + "\n- 87% stayed to the end.",
        lambda brief: _good(brief) + "\n- The busiest minute was at 7:45.",
        lambda brief: _good(brief).replace(INTERNAL_MARKER + "\n", ""),
    ],
)
def test_an_unchecked_digest_is_refused_and_nothing_is_saved(answer):
    seen = {}
    with pytest.raises(LLMInvalidOutput) as raised:
        _run(answer=answer, seen=seen)
    assert len(seen["briefs"]) == 1 and seen["saved"] == []
    assert "Nothing was saved" in str(raised.value)
    assert "87%" not in str(raised.value) and "7:45" not in str(raised.value), "never the model's words"


def test_without_a_decisions_list_the_brief_says_not_listed():
    _, seen = _run(row=_row(), answer=_good)
    assert "## Decisions and commitments\n- not listed" in seen["briefs"][0].text


def test_french_is_a_translation_of_the_english_and_needs_it_first():
    english = _good("")
    _, seen = _run(language="French", row=_row(team_digest_bundle={"English": english}), answer=_good)
    assert seen["translated"] == english and seen["translation_job"] == "translation"
    assert seen["briefs"] == []
    ((args, _),) = seen["saved"]
    assert args[2] == "French"
    with pytest.raises(RuntimeError, match="English team digest first"):
        _run(language="French", row=_row(), answer=_good)
