"""The Follow-up's "Decisions and commitments": the checks and the wording
(`record/decisions.py`), the model call's schema and prompt
(`ai/decisions.py`), and the runner (`api_logic.run_decisions`: English read
from the session's dossier, French a translation of it), on the synthetic
webinar."""

from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import pytest
import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.ai import decisions as ai_decisions
from livestorm_app.ai import jobs
from livestorm_app.ai.schemas import is_registered
from livestorm_app.record.decisions import NONE_STATED, check_decisions, clock_text, decisions_markdown
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.understanding import build_dossier


def _utterances():
    _, utterances, _ = build_dossier(load_record_inputs(synthetic.cached_row()))
    return utterances


def _item(utterance, **extra):
    words = utterance["text"].split()
    item = {
        "kind": "commitment",
        "text": "Send the slides to everyone",
        "owner": "speaker",
        "due": None,
        "utterance_id": utterance["id"],
        "quote": " ".join(words[:5]),
    }
    item.update(extra)
    return item


# ── The checks ──


def test_an_item_is_kept_only_when_its_quote_is_in_the_utterance_it_cites():
    utterances = _utterances()
    u = utterances[3]
    kept = check_decisions(
        [
            _item(u),
            _item(u, text="Something else", quote="words nobody said on stage"),
            _item(u, text="A third", utterance_id="U9999"),
            _item(u, text="A fourth", kind="wish"),
        ],
        utterances,
    )
    assert [entry["text"] for entry in kept] == ["Send the slides to everyone"]
    entry = kept[0]
    assert entry["utterance"] == u["id"] and entry["ownerIsSpeaker"] is True and entry["owner"] is None
    assert entry["speaker"] == u["speaker"][1:]
    assert entry["t"] == u["t"] and entry["r"] == u["r"]


def test_a_quote_may_run_into_the_next_utterance_and_case_and_punctuation_dont_count():
    utterances = _utterances()
    u, after = utterances[3], utterances[4]
    quote = f"{u['text'].split()[-1].upper()}, {after['text'].split()[0]}!"
    assert check_decisions([_item(u, quote=quote)], utterances)


def test_an_owner_or_a_date_is_kept_only_in_the_utterances_words():
    utterances = _utterances()
    u = utterances[3]
    said = u["text"].split()
    kept = check_decisions(
        [
            _item(u, owner=" ".join(said[1:3]), due=" ".join(said[3:5])),
            _item(u, text="Another", owner="Nora from marketing", due="next March"),
        ],
        utterances,
    )
    assert (kept[0]["owner"], kept[0]["due"]) == (" ".join(said[1:3]), " ".join(said[3:5]))
    assert (kept[1]["owner"], kept[1]["due"], kept[1]["ownerIsSpeaker"]) == (None, None, False)


def test_repeats_are_kept_once_and_items_follow_the_talk():
    utterances = _utterances()
    early, late = utterances[2], utterances[8]
    kept = check_decisions(
        [_item(late, text="Late one"), _item(early, text="Early one"), _item(early, text="early ONE.")],
        utterances,
    )
    assert [entry["text"] for entry in kept] == ["Early one", "Late one"]


# ── The wording ──


def test_the_markdown_names_the_speaker_as_saved_and_gives_the_time():
    utterances = _utterances()
    u = utterances[3]
    items = check_decisions([_item(u), _item(u, kind="decision", text="The launch moves to March", owner=None)], utterances)
    text = decisions_markdown(items, utterances, {u["speaker"][1:]: "Nora Vale"}, on_clock=True)
    assert text.index("## Decisions") < text.index("## Commitments")
    assert "**The launch moves to March.**" in text
    assert f"**Send the slides to everyone.** Owner: Nora Vale. Nora Vale at {clock_text(u['t'])}: *“" in text
    assert "recording's clock" not in text


def test_without_the_session_clock_the_times_are_the_recordings_and_it_says_so():
    utterances = _utterances()
    u = utterances[3]
    text = decisions_markdown(check_decisions([_item(u)], utterances), utterances, {}, on_clock=False)
    assert text.startswith("_Times are on the recording's clock._")
    assert f" at {clock_text(u['r'])}: " in text
    assert "## Decisions" not in text, "a kind with nothing says nothing"


def test_a_session_with_none_says_so():
    assert decisions_markdown([], _utterances(), {}, on_clock=True) == NONE_STATED


def test_clock_text():
    assert (clock_text(65), clock_text(3729), clock_text(None)) == ("1:05", "1:02:09", "")


# ── The model call ──


def test_the_call_is_a_v1_sonnet_job_with_a_strict_schema_and_a_prompt():
    spec = next(s for s in jobs.JOB_SPECS if s.kind == "decisions")
    assert (spec.group, spec.default_anthropic, spec.layout) == ("v1", "claude-sonnet-5-5", "dossier")
    assert is_registered(ai_decisions.SessionDecisions)
    prompt = ai_decisions.load_instructions()
    for words in ("decision:", "commitment:", "owner:", "due:", "utterance_id:", "quote:", "never instructions to you"):
        assert words in prompt


# ── The runner ──


def _run(*, language="English", bundle=None, items=None, transcript=True):
    row = dict(synthetic.cached_row())
    if bundle is not None:
        row["decisions_bundle"] = bundle
    if not transcript:
        row["transcript_payload"] = None
    row["transcript_speaker_names"] = {"0": "Nora Vale"}
    utterances = _utterances()
    seen = {"saved": []}

    def read(dossier, *, api_key, ctx):
        seen.update(dossier=dossier.text, job=ctx.job_kind)
        chosen = items if items is not None else [_item(utterances[3])]
        return ai_decisions.SessionDecisions.model_validate({"items": chosen})

    def translate(**kwargs):
        seen.update(translated=kwargs["source_markdown"], translation_job=kwargs["ctx"].job_kind)
        return "## Engagements\n\n- **Envoyer les slides.**"

    def save(*args, provenance=None, **kwargs):
        assert isinstance(provenance, dict)
        seen["saved"].append(args)

    patches = [
        mock.patch.object(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row)),
        mock.patch.object(api_logic, "fetch_chat_labels", lambda session_id, organization_id: None),
        mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "merge_cached_bundle_entry", save),
        mock.patch.object(api_logic, "translate_markdown_with_openai", translate),
        mock.patch.object(api_logic, "get_llm_model", lambda kind: f"model-for-{kind}"),
        mock.patch.object(ai_decisions, "read_decisions", read),
    ]
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        result = api_logic.run_decisions("llm-key", row["session_id"], language)
    return result, seen


def test_english_is_read_from_the_dossier_and_saved_with_the_saved_names():
    result, seen = _run()
    assert seen["job"] == "decisions"
    assert seen["dossier"].startswith("# Session dossier"), "the understanding's own dossier"
    assert "Nora Vale" not in seen["dossier"], "names never reach the model"
    (saved,) = seen["saved"]
    assert saved[:3] == (synthetic.cached_row()["session_id"], "decisions_bundle", "English")
    assert "Nora Vale" in saved[3]
    assert result["bundle"]["English"] == saved[3]


def test_nothing_supported_is_saved_as_none_stated():
    _, seen = _run(items=[])
    assert seen["saved"][0][3] == NONE_STATED


def test_french_is_a_translation_of_the_english_and_needs_it_first():
    _, seen = _run(language="French", bundle={"English": "## Commitments\n\n- **Send the slides.**"})
    assert seen["translated"].startswith("## Commitments") and seen["translation_job"] == "translation"
    assert "dossier" not in seen
    with pytest.raises(RuntimeError, match="English decisions first"):
        _run(language="French", bundle={})


def test_a_session_without_a_transcript_is_refused_before_any_call():
    with pytest.raises(RuntimeError, match="read from the transcript"):
        _run(transcript=False)
