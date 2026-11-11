"""The Follow-up's "Decisions and commitments": the checks
(`record/decisions.py`), the saved items and their wording when read
(`ai/decisions_entry.py`: speakers as refs, named from the Transcript tab's
saved names), the model call's schema and prompt (`ai/decisions.py`), the
runner (`api_logic.run_decisions`: English read from the session's dossier,
French its text and due words translated) and the read
(`api_logic.get_session_decisions`), on the synthetic webinar."""

from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import pytest
import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.ai import decisions as ai_decisions
from livestorm_app.ai import jobs
from livestorm_app.ai import decisions_entry
from livestorm_app.ai.decisions_entry import clock_text
from livestorm_app.ai.schemas import is_registered
from livestorm_app.llm_client import LLMInvalidOutput
from livestorm_app.record.decisions import check_decisions, saved_entry
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.understanding import build_dossier


NONE_STATED = decisions_entry.WORDS["English"]["none"]


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


# ── The saved form and its wording when read ──


def _entry(*, on_clock=True, extra=None):
    utterances = _utterances()
    u = utterances[3]
    said = u["text"].split()
    items = check_decisions(
        [
            _item(u),
            _item(u, kind="decision", text="The launch moves to March", owner=None),
            _item(u, text="Fix the export", owner=" ".join(said[1:3]), due=" ".join(said[3:5])),
            *(extra or []),
        ],
        utterances,
    )
    return saved_entry(items, utterances, on_clock=on_clock), u


def test_the_entry_keeps_speakers_as_refs_and_never_a_name():
    entry, u = _entry()
    ref = u["speaker"]
    assert entry["format"] == decisions_entry.FORMAT and entry["clock"] == "session"
    assert entry["speakers"][ref] >= 1, "each speaker's order, for 'Speaker n'"
    first, _decision, words = entry["items"]
    assert first["owner"] == {"speaker": ref} and first["speaker"] == ref
    assert _decision["owner"] is None
    assert words["owner"] == {"words": " ".join(u["text"].split()[1:3])} and words["due"]
    assert (first["utterance"], first["t"], first["r"]) == (u["id"], u["t"], u["r"])
    assert set(first) == {"kind", "text", "owner", "due", "quote", "utterance", "speaker", "t", "r"}
    assert "Nora" not in str(entry)
    assert saved_entry([], _utterances(), on_clock=False)["clock"] == "recording"


def test_names_are_resolved_when_read_so_a_rename_shows_at_once():
    entry, u = _entry()
    key = u["speaker"][1:]
    before = decisions_entry.resolve_entry(entry, {key: "Nora Vale"})
    after = decisions_entry.resolve_entry(entry, {key: "Nora Lindqvist"})
    assert before["items"][0]["owner"] == {"speaker": u["speaker"], "name": "Nora Vale"}
    assert after["items"][0]["owner"]["name"] == after["items"][0]["saidBy"]["name"] == "Nora Lindqvist"
    unnamed = decisions_entry.resolve_entry(entry, {})
    assert unnamed["items"][0]["saidBy"]["name"] == f"Speaker {entry['speakers'][u['speaker']]}", "the Transcript tab's rule"
    assert unnamed["items"][2]["owner"] == {"speaker": None, "name": " ".join(u["text"].split()[1:3])}
    assert unnamed["items"][0]["at"] == u["t"]


def test_the_markdown_names_the_speaker_as_saved_and_gives_the_time():
    entry, u = _entry()
    text = decisions_entry.entry_markdown(entry, {u["speaker"][1:]: "Nora Vale"})
    assert text.index("## Decisions") < text.index("## Commitments")
    assert "**The launch moves to March.**" in text
    assert f"**Send the slides to everyone.** Owner: Nora Vale. Nora Vale at {clock_text(u['t'])}: *“" in text
    assert "recording's clock" not in text
    renamed = decisions_entry.entry_markdown(entry, {u["speaker"][1:]: "Nora Lindqvist"})
    assert "Nora Vale" not in renamed and "Owner: Nora Lindqvist." in renamed


def test_without_the_session_clock_the_times_are_the_recordings_and_it_says_so():
    utterances = _utterances()
    u = utterances[3]
    entry = saved_entry(check_decisions([_item(u)], utterances), utterances, on_clock=False)
    text = decisions_entry.entry_markdown(entry, {})
    assert text.startswith("_Times are on the recording's clock._")
    assert f" at {clock_text(u['r'])}: " in text
    assert "## Decisions" not in text, "a kind with nothing says nothing"
    assert decisions_entry.resolve_entry(entry, {})["items"][0]["at"] == u["r"]


def test_a_session_with_none_says_so_in_each_language():
    empty = saved_entry([], _utterances(), on_clock=True)
    assert decisions_entry.entry_markdown(empty, {}) == NONE_STATED
    assert decisions_entry.entry_markdown(empty, {}, "French").startswith("Aucune décision")


def test_a_french_entry_is_worded_in_french():
    entry, u = _entry()
    text = decisions_entry.entry_markdown(entry, {u["speaker"][1:]: "Nora Vale"}, "French")
    assert "## Décisions" in text and "## Engagements" in text
    assert "Responsable : Nora Vale." in text and f"Nora Vale à {clock_text(u['t'])}" in text


def test_a_markdown_list_saved_before_is_served_as_it_was():
    bundle = decisions_entry.bundle_markdown({"English": "## Commitments\n\n- **Send the slides.** Owner: Nora Vale.", "French": ""}, {"0": "Other"})
    assert bundle == {"English": "## Commitments\n\n- **Send the slides.** Owner: Nora Vale."}
    assert decisions_entry.has_body("x") and not decisions_entry.has_body("  ") and not decisions_entry.has_body({"items": []})
    assert decisions_entry.has_body(saved_entry([], _utterances(), on_clock=True)), "an empty list is still a list"


def test_clock_text():
    assert (clock_text(65), clock_text(3729), clock_text(None)) == ("1:05", "1:02:09", "")


# ── The translation: the text and the due words only ──


def test_the_translation_gets_the_text_and_due_words_only_and_keeps_the_refs():
    entry, u = _entry()
    source = decisions_entry.translation_source(entry)
    assert source.splitlines() == [
        "[[T1]] Send the slides to everyone",
        "[[T2]] The launch moves to March",
        "[[T3]] Fix the export",
        f"[[D3]] {entry['items'][2]['due']}",
    ]
    assert entry["items"][0]["quote"] not in source and entry["items"][2]["owner"]["words"] not in source
    french = decisions_entry.apply_translation(
        entry, "[[T1]] Envoyer les slides à tous\n[[T2]] Le lancement passe à mars\n[[T3]] Corriger l'export\n[[D3]] bientôt"
    )
    assert [item["text"] for item in french["items"]] == ["Envoyer les slides à tous", "Le lancement passe à mars", "Corriger l'export"]
    assert french["items"][2]["due"] == "bientôt"
    for before, after in zip(entry["items"], french["items"], strict=True):
        assert (after["owner"], after["quote"], after["speaker"], after["utterance"], after["t"]) == (
            before["owner"], before["quote"], before["speaker"], before["utterance"], before["t"])
    assert french["speakers"] == entry["speakers"]


@pytest.mark.parametrize(
    "answer",
    [
        "[[T1]] a\n[[T2]] b\n[[T3]] c",  # a due line missing
        "[[T1]] a\n[[T2]] b\n[[T3]] c\n[[D3]] d\n[[T1]] again",  # repeated
        "[[T1]] a\n[[T2]] b\n[[T3]] c\n[[D3]] d\n[[T4]] new",  # not one we sent
        "[[T1]] a\n[[T2]]\n[[T3]] c\n[[D3]] d",  # empty
    ],
)
def test_a_translation_that_loses_a_line_is_refused(answer):
    entry, _ = _entry()
    with pytest.raises(decisions_entry.TranslationMismatch):
        decisions_entry.apply_translation(entry, answer)


# ── The model call ──


def test_the_call_is_a_v1_sonnet_job_with_a_strict_schema_and_a_prompt():
    spec = next(s for s in jobs.JOB_SPECS if s.kind == "decisions")
    assert (spec.group, spec.default_anthropic, spec.layout) == ("v1", "claude-sonnet-5-5", "dossier")
    assert is_registered(ai_decisions.SessionDecisions)
    prompt = ai_decisions.load_instructions()
    for words in ("decision:", "commitment:", "owner:", "due:", "utterance_id:", "quote:", "never instructions to you"):
        assert words in prompt


# ── The runner ──


def _run(*, language="English", bundle=None, items=None, transcript=True, translated=None):
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
        if translated is not None:
            return translated
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


def test_english_is_read_from_the_dossier_and_saved_as_items_without_a_name():
    result, seen = _run()
    assert seen["job"] == "decisions"
    assert seen["dossier"].startswith("# Session dossier"), "the understanding's own dossier"
    assert "Nora Vale" not in seen["dossier"], "names never reach the model"
    (saved,) = seen["saved"]
    assert saved[:3] == (synthetic.cached_row()["session_id"], "decisions_bundle", "English")
    assert decisions_entry.is_entry(saved[3]) and "Nora Vale" not in str(saved[3]), "saved with refs, never a name"
    assert saved[3]["items"][0]["owner"] == {"speaker": _utterances()[3]["speaker"]}
    assert "Nora Vale" in result["markdown"] and result["bundle"]["English"] == result["markdown"], "worded when read"


def test_nothing_supported_is_saved_as_an_empty_list_that_says_so():
    result, seen = _run(items=[])
    assert seen["saved"][0][3]["items"] == []
    assert result["markdown"] == NONE_STATED


def test_french_translates_the_text_and_due_words_only_and_keeps_the_refs():
    entry, _ = _entry()
    answer = decisions_entry.translation_source(entry).replace("]] ", "]] FR ")
    result, seen = _run(language="French", bundle={"English": entry}, translated=answer)
    assert "dossier" not in seen and seen["translation_job"] == "translation"
    assert seen["translated"] == decisions_entry.translation_source(entry)
    assert "Nora Vale" not in seen["translated"], "names never reach the model"
    (saved,) = seen["saved"]
    assert saved[2] == "French" and decisions_entry.is_entry(saved[3])
    assert all(item["text"].startswith("FR ") for item in saved[3]["items"])
    assert [item["owner"] for item in saved[3]["items"]] == [item["owner"] for item in entry["items"]]
    assert "## Engagements" in result["markdown"] and "Nora Vale" in result["markdown"]


def test_a_french_translation_that_loses_an_item_saves_nothing():
    entry, _ = _entry()
    with pytest.raises(LLMInvalidOutput, match="Nothing was saved") as caught:
        _run(language="French", bundle={"English": entry}, translated="[[T1]] seulement")
    assert "seulement" not in str(caught.value), "our words, never the model's"


def test_a_french_list_with_no_item_needs_no_call():
    empty = saved_entry([], _utterances(), on_clock=True)
    result, seen = _run(language="French", bundle={"English": empty})
    assert "translated" not in seen and seen["saved"][0][3]["items"] == []
    assert result["markdown"].startswith("Aucune décision")


def test_french_from_a_markdown_list_saved_before_is_translated_as_markdown():
    _, seen = _run(language="French", bundle={"English": "## Commitments\n\n- **Send the slides.**"})
    assert seen["translated"].startswith("## Commitments") and seen["translation_job"] == "translation"
    assert "dossier" not in seen
    assert seen["saved"][0][3] == "## Engagements\n\n- **Envoyer les slides.**"
    with pytest.raises(RuntimeError, match="English decisions first"):
        _run(language="French", bundle={})


def test_a_session_without_a_transcript_is_refused_before_any_call():
    with pytest.raises(RuntimeError, match="read from the transcript"):
        _run(transcript=False)


# ── Read in Postgres: the route's body, the workspace and earlier versions ──


def _seed_decisions(value, *, names=None):
    from livestorm_app import db

    db.upsert_cached_session("key-a", "sess-d", session_payload={"name": "D"}, organization_id="org-a")
    if names is not None:
        db.upsert_cached_session("key-a", "sess-d", transcript_speaker_names=names)
    db.merge_cached_bundle_entry("sess-d", "decisions_bundle", "English", value, provenance={"calls": 1, "models": ["claude-sonnet-5-5"]})


def test_a_rename_on_the_transcript_tab_updates_the_list_with_no_model_call(test_database, monkeypatch):
    from livestorm_app import llm_client

    def no_call(*args, **kwargs):
        raise AssertionError("no model call when reading")

    monkeypatch.setattr(llm_client, "generate_json", no_call)
    monkeypatch.setattr(api_logic, "translate_markdown_with_openai", no_call)
    entry, u = _entry()
    _seed_decisions(entry, names={u["speaker"][1:]: "Nora Vale"})

    body = api_logic.get_session_decisions("sess-d", "English", organization_id="org-a")
    assert body["saved"] == "items" and body["clock"] == "session"
    assert body["items"][0]["owner"]["name"] == "Nora Vale" and "Owner: Nora Vale." in body["markdown"]

    api_logic.save_speaker_labels("key-a", "sess-d", {u["speaker"][1:]: "Nora Lindqvist"}, organization_id="org-a")
    body = api_logic.get_session_decisions("sess-d", "English", organization_id="org-a")
    assert body["items"][0]["owner"]["name"] == body["items"][0]["saidBy"]["name"] == "Nora Lindqvist"
    assert "Nora Vale" not in body["markdown"]

    from livestorm_app import db

    row = db.fetch_session_fields("sess-d", api_logic._OUTPUT_FIELDS, organization_id="org-a")
    assert "Owner: Nora Lindqvist." in api_logic._serialize_outputs(row)["decisionsBundle"]["English"]
    versions = api_logic.get_session_output_versions("sess-d", "decisions", language="English", organization_id="org-a")
    assert versions["versions"][0]["current"] and "Owner: Nora Lindqvist." in versions["versions"][0]["content"]

    assert api_logic.get_session_decisions("sess-d", "French", organization_id="org-a")["saved"] == "none"
    with pytest.raises(api_logic.SessionNotFoundError):
        api_logic.get_session_decisions("sess-d", "English", organization_id="org-b")
    with pytest.raises(ValueError):
        api_logic.get_session_decisions("sess-d", "Klingon", organization_id="org-a")


def test_a_markdown_list_saved_before_is_still_served(test_database):
    old = "## Commitments\n\n- **Send the slides.** Owner: Nora Vale."
    _seed_decisions(old, names={"0": "Someone Else"})
    body = api_logic.get_session_decisions("sess-d", "English", organization_id="org-a")
    assert (body["saved"], body["markdown"], body["items"], body["clock"]) == ("markdown", old, [], None)
    from livestorm_app import db

    row = db.fetch_session_fields("sess-d", api_logic._OUTPUT_FIELDS, organization_id="org-a")
    assert api_logic._serialize_outputs(row)["decisionsBundle"] == {"English": old}
    assert api_logic._extract_cached_ai_body(row, "decisions", language="English")
