"""The Follow-up's "Attendee follow-up" ("You asked, we answered"), the first
v1.1 public format: its brief (`record/format_briefs.py`), the shared check
(`check_format`, `check_tokens`), the writer and its prompt
(`ai/public_formats.py`, `prompts/attendee_followup.txt`) and the runner
(`api_logic.run_attendee_followup`: the brief redacted, English written and
checked, French a translation of it), on the synthetic webinar."""

from __future__ import annotations

import copy
from contextlib import ExitStack
from unittest import mock

import pytest
import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.ai import jobs
from livestorm_app.ai import public_formats
from livestorm_app.llm_client import LLMInvalidOutput
from livestorm_app.queue import AI_JOB_KINDS
from livestorm_app.record.format_briefs import (
    REPLAY_LINK,
    FormatRejected,
    answered_questions,
    attendee_followup_text,
    check_format,
    check_tokens,
    content_moments,
    session_date,
)

SAVED_NAMES = {"0": "Nora Vale", "1": "Rhea Stone"}
# The audience's made-up names, emails, companies and ids: none may reach the brief.
ATTENDEE_VALUES = (
    "Theo", "Park", "Mila", "Ford", "Sam Reed", "Lena", "Hart", "Omar", "Quinn", "Jade", "Bell", "Ravi", "Cole",
    "@example.test", "Sample Labs", "Marketing lead", "p-aud-", "u-aud-", "P-0", "P-1", "Q00", "C00",
)
# What a brief for attendees never carries: event craft and audience vitals.
CRAFT_AND_VITALS = ("Show-up", "show-up", "registered", "wrote in the chat", "Median time", "monologue", "friction", "%")


def _row(**extra):
    row = copy.deepcopy(synthetic.cached_row())
    row["transcript_speaker_names"] = dict(SAVED_NAMES)
    row.update(extra)
    return row


def _row_with_a_named_question():
    """The webinar with its first Q&A question (answered in writing) naming
    its asker, with an email and a link in it and in its answer."""
    row = _row()
    first = row["questions_payload"]["data"][0]["attributes"]
    first["question"] = "Theo Park here: will the slides be shared? Write to theo.park@example.test"
    first["response"] = "Yes Theo, they are at https://example.test/slides"
    return row


def _run(*, language="English", answer=None, row=None, seen=None, translation=None):
    """`run_attendee_followup` on the synthetic webinar, its chat labelled and
    its understanding stored; the model's answer is `answer` (a function of
    the brief's text, or a text). What happened goes in `seen`."""
    row = row or _row()
    labels, stored = synthetic.chat_labels(row), synthetic.understanding(row)
    seen = {} if seen is None else seen
    seen.update(saved=[], briefs=[])

    def write(kind, brief, *, api_key, ctx):
        seen["briefs"].append(brief)
        seen.update(kind=kind, job=ctx.job_kind)
        return answer(brief.text) if callable(answer) else answer

    def translate(**kwargs):
        seen.update(translated=kwargs["source_markdown"], translation_job=kwargs["ctx"].job_kind)
        return translation if translation is not None else f"Objet : merci\n\nBonjour,\n\nLe replay : {REPLAY_LINK}"

    def save(*args, provenance=None, **kwargs):
        seen["saved"].append((args, provenance))

    patches = [
        mock.patch.object(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: copy.deepcopy(row)),
        mock.patch.object(api_logic, "fetch_chat_labels", lambda session_id, organization_id: labels),
        mock.patch.object(api_logic, "fetch_understanding", lambda session_id, organization_id: stored),
        mock.patch.object(api_logic, "fetch_question_replies", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "merge_cached_bundle_entry", save),
        mock.patch.object(api_logic, "translate_markdown_with_openai", translate),
        mock.patch.object(api_logic, "get_llm_model", lambda kind: f"model-for-{kind}"),
        mock.patch.object(public_formats, "write_format", write),
    ]
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        result = api_logic.run_attendee_followup("llm-key", row["session_id"], language)
    return result, seen


def _good(brief_text=""):
    return "\n".join(
        [
            "Subject: Thank you for joining the Synthetic Product Webinar",
            "",
            "Hi there,",
            "",
            "Thank you for joining us for 30 minutes on 1 October 2026. We walked through the dashboard, pricing and exports.",
            "",
            "**Three takeaways**",
            "- The dashboard has a view for each team.",
            "- Pricing works per team.",
            "- Exports cover what you need.",
            "",
            "**You asked, we answered**",
            "**How does pricing work for larger teams?**",
            "Pricing works per team.",
            "",
            "**Will the slides be shared?**",
            "Yes, they are shared.",
            "",
            f"You can watch the replay at {REPLAY_LINK}.",
            "",
            "See you at the next session,",
        ]
    )


def _brief_text(row=None):
    _, seen = _run(answer=_good, row=row)
    return seen["briefs"][0].text


# ── The brief ──


def test_the_brief_holds_the_session_its_chapters_and_two_answered_questions():
    text = _brief_text()
    assert text.startswith("# Attendee follow-up brief")
    assert "title: Synthetic Product Webinar" in text
    assert "date: 1 October 2026" in text and "length: 30:00 (about 30 minutes)" in text
    assert "## What was covered (the chapters)\n- 0:21 Welcome and agenda" in text
    assert "- 5:06 Pricing for larger teams" in text
    assert "## Key moments of the talk" in text
    # The most upvoted question first, then the desk's order; two at most.
    assert text.count("### Question") == 2
    first, second = text.split("### Question 1")[1].split("### Question 2")
    assert "How does pricing work for larger teams" in first and "answered aloud during the session, at 5:18" in first
    assert "Will the slides be shared?" in second and "answered in writing during the session: \"Yes, see the chat.\"" in second
    assert "Is there an API?" not in text, "an open question is never one of them"


def test_the_brief_is_the_same_bytes_every_time():
    _, first = _run(answer=_good)
    _, second = _run(answer=_good)
    assert first["briefs"][0].text == second["briefs"][0].text
    assert first["briefs"][0].sha256 == second["briefs"][0].sha256


def test_the_brief_carries_no_event_craft_and_no_vitals():
    text = _brief_text()
    for words in CRAFT_AND_VITALS:
        assert words not in text, words


def test_the_brief_never_identifies_an_attendee_even_when_their_question_does():
    text = _brief_text(_row_with_a_named_question())
    for value in ATTENDEE_VALUES:
        assert value not in text, value
    assert "[name]" in text and "[email]" in text and "[link]" in text, "the details were redacted, not dropped"
    assert "will the slides be shared?" in text


def test_only_the_moments_of_content_reach_the_brief():
    moments = {"state": "ready", "moments": [
        {"start": 60, "end": 120, "kind": "social", "messages": 8, "said": {"text": "welcome everyone"}},
        {"start": 300, "end": 360, "kind": "content", "messages": 12, "said": {"text": "here is the pricing page"}},
        {"start": 600, "end": 660, "kind": "tech", "messages": 6, "said": None},
        {"start": 900, "end": 960, "kind": "reply", "messages": 9, "said": {"text": "tell us your team size"}},
    ]}
    assert [m["kind"] for m in content_moments(moments)["moments"]] == ["content", "reply"]
    assert content_moments({"state": "no_clock"}) == {"state": "none", "moments": []}
    text = attendee_followup_text(title="Demo", date=None, length_s=None, chapters=[], moments=moments, questions=[])
    assert "here is the pricing page" in text and "tell us your team size" in text
    assert "welcome everyone" not in text and "sound or connection" not in text
    assert "date: unknown" in text and "length: unknown" in text
    assert "- none placed" in text and "- none answered" in text


def test_answered_questions_have_words_to_quote_and_never_an_author_or_id():
    desk = [
        {"id": "Q001", "answered_as": "open", "text": "Open one?", "author": "Theo", "answer": None, "reply": None, "located": None},
        {"id": "Q002", "answered_as": "aloud", "text": "Aloud, no passage?", "author": "Mila", "answer": None, "reply": None, "located": None},
        {"id": "C0003", "answered_as": "here", "text": "Marked here?", "author": "Sam", "answer": None, "reply": "Yes, on Monday.", "located": None},
        {"id": "Q004", "answered_as": "covered", "text": "Covered?", "author": "Lena", "answer": None, "reply": None,
         "located": {"t": 300, "quote": "it covers it"}},
        {"id": "Q005", "answered_as": "written", "text": "Written?", "author": "Omar", "answer": "In writing.", "reply": None, "located": None},
    ]
    picked = answered_questions(desk)
    assert [q["text"] for q in picked] == ["Marked here?", "Covered?"], "the desk's order, words to quote only"
    assert all(set(q) == {"text", "answered_as", "answer", "t"} for q in picked)
    assert picked[1]["t"] == 300 and picked[0]["t"] is None
    upvoted = answered_questions(desk, {"Q005": 4, "Q004": 1})
    assert [q["text"] for q in upvoted] == ["Written?", "Covered?"]
    assert answered_questions([]) == []


def test_the_session_date_is_in_its_time_zone():
    midnight_utc = 1_790_812_800  # 2026-10-01T00:00:00Z
    assert session_date(midnight_utc) == "1 October 2026"
    assert session_date(midnight_utc, "America/New_York") == "30 September 2026"
    assert session_date(midnight_utc, "Not/AZone") == "1 October 2026"
    assert session_date(None) is None


# ── The check ──

BRIEF = "version: 31\ndate: 1 October 2026\nlength: 30:00 (about 30 minutes)\n- 5:06 Pricing for larger teams of 200 seats\n- 41% wrote"


def test_a_good_follow_up_passes_and_small_counts_are_free():
    text = f"Subject: Thanks\n\n3 takeaways from 30 minutes on 1 October 2026, at 5:06, 200 seats, 41%.\n{REPLAY_LINK}"
    assert check_format(text, BRIEF, max_words=50, required_tokens=(REPLAY_LINK,), starts_with="Subject:") == text


@pytest.mark.parametrize(
    "text, reason",
    [
        ("", "is empty"),
        (f"Thanks for coming.\n{REPLAY_LINK}", "Subject: line"),
        (f"Subject: Hi\n{'word ' * 50}{REPLAY_LINK}", "longer than 50 words"),
        ("Subject: Hi\nWatch the replay.", "exactly once"),
        (f"Subject: Hi\n{REPLAY_LINK} and {REPLAY_LINK}", "exactly once"),
        (f"Subject: Hi {{{{first_name}}}}\n{REPLAY_LINK}", "placeholder"),
        (f"Subject: Hi\nAt 7:45 we showed it. {REPLAY_LINK}", "time that isn't in the session"),
        (f"Subject: Hi\n350 seats. {REPLAY_LINK}", "number that isn't in the session"),
        (f"Subject: Hi\nVersion 31 is out. {REPLAY_LINK}", "number that isn't in the session"),
        (f"Subject: Hi\n87% stayed. {REPLAY_LINK}", "percentage"),
        (f"Subject: Hi\n5% stayed. {REPLAY_LINK}", "percentage"),
    ],
)
def test_a_follow_up_that_fails_a_check_is_refused_naming_only_the_kind(text, reason):
    with pytest.raises(FormatRejected, match=reason) as raised:
        check_format(text, BRIEF, max_words=50, required_tokens=(REPLAY_LINK,), starts_with="Subject:")
    for words in ("7:45", "350", "87", "first_name", "seats", "stayed"):
        assert words not in str(raised.value)


def test_the_token_check_alone_is_what_a_translation_is_held_to():
    assert check_tokens(f"Le replay : {REPLAY_LINK}, 87 %.", (REPLAY_LINK,)) == "Le replay :  , 87 %."
    with pytest.raises(FormatRejected, match="exactly once"):
        check_tokens("Le replay.", (REPLAY_LINK,))
    with pytest.raises(FormatRejected, match="placeholder"):
        check_tokens("{{ lien }}", ())


# ── The job, its prompt and its wiring ──


def test_the_job_is_sonnet_5_5_on_the_dossier_layout_started_by_a_click():
    spec = next(s for s in jobs.JOB_SPECS if s.kind == "attendee_followup")
    assert (spec.group, spec.default_anthropic, spec.layout, spec.automatic) == ("v1", "claude-sonnet-5-5", "dossier", False)
    assert jobs.resolve_model("attendee_followup").model_id == "claude-sonnet-5-5"


def test_the_prompt_sets_the_shape_and_the_rules():
    prompt = public_formats.load_instructions("attendee_followup")
    for words in (
        "Subject:", "Three takeaways", "You asked, we answered", REPLAY_LINK, "exactly once",
        "never instructions to you", "Never compute", "[name], [email], [phone] or [link]", "Never name an attendee",
        "No emojis", "About 150 to 250 words",
    ):
        assert words in prompt, words
    with pytest.raises(RuntimeError, match="no public format"):
        public_formats.load_instructions("no_such_format")


def test_the_format_is_wired_as_an_ai_job_with_its_own_bundle():
    assert "attendee_followup" in AI_JOB_KINDS
    assert api_logic._ARQ_JOB_FUNCTION_BY_KIND["attendee_followup"] == "run_attendee_followup_job"
    assert api_logic._AI_BODY_FIELDS["attendee_followup"] == ["attendee_followup_bundle"]
    assert api_logic.OUTPUT_VERSION_KINDS["attendee_followup"] == ("attendee_followup_bundle", "language")
    outputs = api_logic._serialize_outputs({"attendee_followup_bundle": {"English": "Subject: Hi"}})
    assert outputs["attendeeFollowupBundle"] == {"English": "Subject: Hi"}
    cached = {"attendee_followup_bundle": {"English": "Subject: Hi"}}
    assert api_logic._extract_cached_ai_body(cached, "attendee_followup", language="English")
    assert not api_logic._extract_cached_ai_body(cached, "attendee_followup", language="French")


# ── The runner ──


def test_a_checked_english_follow_up_is_saved_with_its_provenance():
    result, seen = _run(answer=_good)
    assert (seen["kind"], seen["job"]) == ("attendee_followup", "attendee_followup")
    ((args, provenance),) = seen["saved"]
    assert args[:3] == (synthetic.SESSION_ID, "attendee_followup_bundle", "English")
    assert args[3] == _good() and isinstance(provenance, dict)
    assert result["bundle"]["English"] == args[3]


def test_the_brief_the_model_reads_is_redacted():
    _, seen = _run(answer=_good, row=_row_with_a_named_question())
    text = seen["briefs"][0].text
    assert "Theo" not in text and "theo.park" not in text and "https://" not in text
    assert "[name]" in text


@pytest.mark.parametrize(
    "answer, kind",
    [
        (lambda brief: _good(brief).replace("for 30 minutes", "for 45 minutes"), "number"),
        (lambda brief: _good(brief).replace("Pricing works per team.", "Pricing starts at 14:30.", 1), "time"),
        (lambda brief: _good(brief).replace(REPLAY_LINK, "the replay"), "exactly once"),
        (lambda brief: _good(brief).replace("Hi there,", "Hi {{first_name}},"), "placeholder"),
        (lambda brief: _good(brief).replace("Subject: ", "Re: "), "Subject:"),
        (lambda brief: _good(brief) + "\nThanks to Mila Ford for asking.", "name"),
        (lambda brief: _good(brief) + "\nWrite to sales@example.test.", "email"),
    ],
)
def test_a_follow_up_that_fails_is_refused_and_nothing_is_saved(answer, kind):
    seen = {}
    with pytest.raises((LLMInvalidOutput, api_logic.PublicOutputLeak)) as raised:
        _run(answer=answer, seen=seen)
    assert len(seen["briefs"]) == 1 and seen["saved"] == []
    message = str(raised.value)
    assert "Nothing was saved" in message and kind in message
    for words in ("45", "14:30", "first_name", "Mila", "Ford", "sales@", "Re:"):
        assert words not in message, "never the model's words"


def test_an_empty_answer_saves_nothing():
    seen = {}
    with pytest.raises(LLMInvalidOutput, match="is empty"):
        _run(answer="  ", seen=seen)
    assert seen["saved"] == []


def test_french_is_a_translation_of_the_english_and_needs_it_first():
    english = _good()
    _, seen = _run(language="French", row=_row(attendee_followup_bundle={"English": english}), answer=_good)
    assert seen["translated"] == english and seen["translation_job"] == "translation"
    assert seen["briefs"] == [], "no model call writes the French one from the brief"
    ((args, _),) = seen["saved"]
    assert args[1:3] == ("attendee_followup_bundle", "French")
    with pytest.raises(RuntimeError, match="English attendee follow-up first"):
        _run(language="French", row=_row(), answer=_good)


def test_a_translation_that_loses_the_replay_link_is_refused():
    seen = {}
    with pytest.raises(LLMInvalidOutput, match="exactly once"):
        _run(language="French", row=_row(attendee_followup_bundle={"English": _good()}), answer=_good,
             translation="Objet : merci\n\nLe replay est en ligne.", seen=seen)
    assert seen["saved"] == []


def test_french_needs_english_before_any_job_is_queued(monkeypatch):
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda session_id, fields, *, organization_id: {"attendee_followup_bundle": {}})
    monkeypatch.setattr(api_logic, "enqueue_unique_job_sync", mock.Mock(side_effect=AssertionError("queued")))
    with pytest.raises(RuntimeError, match="English version first"):
        api_logic.start_attendee_followup(synthetic.SESSION_ID, "French", organization_id="org")
