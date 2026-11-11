"""The understanding job's dossier and checks (`record/understanding.py`) and
its request (`ai/understanding.py`). The run that calls it is in
test_session_analysis.py. No model call."""

from __future__ import annotations

import dataclasses

import synthetic_session as synthetic

from livestorm_app.ai import schemas
from livestorm_app.ai.understanding import KIND, SessionUnderstanding, load_instructions
from livestorm_app.ai.request import build_request
from livestorm_app.record.alignment import MISALIGNED
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.understanding import (
    UNDERSTANDING_VERSION,
    build_dossier,
    check_understanding,
    parse_number,
)


def _inputs(row=None):
    row = row or synthetic.cached_row()
    return load_record_inputs(row, stored_labels=synthetic.chat_labels(row))


# ── The dossier ──


def test_the_dossier_is_the_same_bytes_every_time():
    first, utterances, questions = build_dossier(_inputs())
    second, _, _ = build_dossier(_inputs())
    assert first.text == second.text and first.sha256 == second.sha256
    assert utterances and [q["id"] for q in questions] == ["Q001", "C0010", "Q002", "Q003", "Q004"]


def test_the_dossier_names_nobody():
    """Speakers by key, people by nothing: no name (not even the ones the
    host saved for the speakers), email, people id or `P-` ref."""
    row = synthetic.cached_row()
    text = build_dossier(_inputs(row))[0].text
    for person in synthetic.people():
        attributes = person.get("attributes", person)
        for value in (attributes.get("first_name"), attributes.get("last_name"), attributes.get("email"), person.get("id")):
            if value:
                assert str(value) not in text, f"{value!r} reached the dossier"
    for name in row["transcript_speaker_names"].values():
        assert name not in text
    assert "P-" not in text and "@" not in text
    assert "\tS0\t" in text and "\tS1\t" in text


def test_the_dossier_says_which_clock_and_drops_question_times_without_one():
    inputs = _inputs()
    on_clock = build_dossier(inputs)[0].text
    assert "times: session clock" in on_clock and "Q004\tqa\topen\t1650.0\t" in on_clock
    off = dataclasses.replace(inputs, alignment=dataclasses.replace(inputs.alignment, status=MISALIGNED, offset_s=None))
    dossier, utterances, _ = build_dossier(off)
    assert "times: recording" in dossier.text and "Q004\tqa\topen\t-\t" in dossier.text
    assert all(u["t"] is None for u in utterances)


def test_a_session_without_a_transcript_has_no_utterances():
    _, utterances, questions = build_dossier(_inputs({**synthetic.cached_row(), "transcript_payload": None}))
    assert utterances == [] and questions


# ── The checks ──

U = [
    {"id": "U0001", "speaker": "S0", "r": 0.0, "rEnd": 5.0, "t": 60.0, "text": "Welcome everyone, today: the new dashboard.", "conf": 0.95},
    {"id": "U0002", "speaker": "S0", "r": 5.0, "rEnd": 9.0, "t": 65.0, "text": "Any questions so far? Tell us in the chat.", "conf": 0.95},
    {"id": "U0003", "speaker": "S0", "r": 300.0, "rEnd": 306.0, "t": 360.0, "text": "Our NPS went up to 42 this quarter.", "conf": 0.6},
    {"id": "U0004", "speaker": "S1", "r": 600.0, "rEnd": 606.0, "t": 660.0, "text": "Yes, there is a public API, documented online.", "conf": 0.95},
    {"id": "U0005", "speaker": "S1", "r": 900.0, "rEnd": 906.0, "t": 960.0, "text": "Churn is at 3,5 % now, and churn was 4 % last year.", "conf": 0.95},
]
Q = [
    {"id": "Q001", "source": "qa", "t": 400.0, "text": "Is there an API?", "answered_as": "open"},
    {"id": "Q002", "source": "qa", "t": 420.0, "text": "Do you have an API?", "answered_as": "written"},
    {"id": "C0007", "source": "chat", "t": 700.0, "text": "API docs?", "answered_as": "open"},
]


def _answer(**parts):
    return {"chapters": [], "answers": [], "prompts": [], "figures": [], "groups": [], **parts}


def test_chapters_keep_known_ids_in_order_without_overlap():
    result = check_understanding(
        _answer(chapters=[
            {"title": "Demo", "kind": "demo", "first_utterance": "U0003", "last_utterance": "U0005"},
            {"title": "Opening", "kind": "opening", "first_utterance": "U0001", "last_utterance": "U0002"},
            {"title": "Overlaps the demo", "kind": "content", "first_utterance": "U0004", "last_utterance": "U0005"},
            {"title": "Backwards", "kind": "content", "first_utterance": "U0002", "last_utterance": "U0001"},
            {"title": "Unknown", "kind": "content", "first_utterance": "U0099", "last_utterance": "U0100"},
            {"title": "  ", "kind": "close", "first_utterance": "U0005", "last_utterance": "U0005"},
        ]),
        U, Q,
    )
    assert [(c["title"], c["first"], c["last"], c["t"]) for c in result["chapters"]] == [
        ("Opening", "U0001", "U0002", 60.0),
        ("Demo", "U0003", "U0005", 360.0),
    ]
    assert result["coverage"]["received"]["chapters"] == 6 and result["coverage"]["kept"]["chapters"] == 2


def test_an_answer_is_kept_only_when_quoted_after_an_open_question():
    result = check_understanding(
        _answer(answers=[
            {"question_id": "Q001", "utterance_ids": ["U0004"], "quote": "yes there is a public API"},  # kept: punctuation and case aside
            {"question_id": "Q001", "utterance_ids": ["U0004"], "quote": "Yes, there is a public API"},  # a second answer: dropped
            {"question_id": "Q002", "utterance_ids": ["U0004"], "quote": "there is a public API"},  # answered in writing: dropped
            {"question_id": "C0007", "utterance_ids": ["U0004"], "quote": "there is a public API"},  # said before it was asked: dropped
            {"question_id": "Q999", "utterance_ids": ["U0004"], "quote": "there is a public API"},  # unknown question
            {"question_id": "C0007", "utterance_ids": ["U0005"], "quote": "the API is free"},  # not in the words
        ]),
        U, Q,
    )
    assert result["answers"] == [{"question": "Q001", "utterances": ["U0004"], "t": 660.0, "quote": "yes there is a public API"}]


def test_without_a_session_clock_no_answer_is_kept():
    off_clock = [{**u, "t": None} for u in U]
    result = check_understanding(
        _answer(answers=[{"question_id": "Q001", "utterance_ids": ["U0004"], "quote": "there is a public API"}]), off_clock, Q,
    )
    assert result["answers"] == [] and result["onClock"] is False


def test_prompts_are_quoted_from_their_utterance():
    result = check_understanding(
        _answer(prompts=[
            {"utterance_id": "U0002", "quote": "Tell us in the chat"},
            {"utterance_id": "U0002", "quote": "Any questions so far?"},  # the same utterance once
            {"utterance_id": "U0001", "quote": "Tell us in the chat"},  # not in that utterance
        ]),
        U, Q,
    )
    assert [(p["utterance"], p["quote"]) for p in result["prompts"]] == [("U0002", "Tell us in the chat")]


def test_figures_hold_a_number_and_are_flagged_for_the_host():
    result = check_understanding(
        _answer(figures=[
            {"utterance_id": "U0003", "span": "Our NPS went up to 42", "value": "42", "unit": None, "metric": "NPS"},
            {"utterance_id": "U0005", "span": "Churn is at 3,5 %", "value": "3,5", "unit": "%", "metric": "churn"},
            {"utterance_id": "U0005", "span": "churn was 4 %", "value": "4", "unit": "%", "metric": "Churn"},
            {"utterance_id": "U0001", "span": "the new dashboard", "value": "new", "unit": None, "metric": None},  # no number
            {"utterance_id": "U0004", "span": "42 public APIs", "value": "42", "unit": None, "metric": None},  # not said
        ]),
        U, Q,
    )
    figures = {f["span"]: f for f in result["figures"]}
    assert set(figures) == {"Our NPS went up to 42", "Churn is at 3,5 %", "churn was 4 %"}
    assert [f["id"] for f in result["figures"]] == ["F01", "F02", "F03"]
    assert figures["Our NPS went up to 42"]["flags"] == ["acronym"], "NPS in words the transcript is unsure of"
    assert figures["Our NPS went up to 42"]["confusable"] == {"NPS": "NRR"}
    assert figures["Churn is at 3,5 %"]["number"] == 3.5
    assert figures["Churn is at 3,5 %"]["flags"] == figures["churn was 4 %"]["flags"] == ["two_values"]


def test_groups_need_two_known_questions_each_used_once():
    result = check_understanding(
        _answer(groups=[
            {"label": "Is there an API?", "question_ids": ["Q001", "Q002", "C0007", "Q001"]},
            {"label": "API again", "question_ids": ["Q001", "C0007"]},  # already grouped
            {"label": "Alone", "question_ids": ["Q002", "Q999"]},
        ]),
        U, Q,
    )
    assert result["groups"] == [{"label": "Is there an API?", "questions": ["Q001", "Q002", "C0007"]}]


def test_the_result_has_its_version_and_coverage():
    result = check_understanding(_answer(), U, Q)
    assert result["version"] == UNDERSTANDING_VERSION and result["onClock"] is True
    assert result["coverage"] == {
        "received": {key: 0 for key in ("chapters", "answers", "prompts", "figures", "groups")},
        "kept": {key: 0 for key in ("chapters", "answers", "prompts", "figures", "groups")},
        "utterances": 5,
        "questions": 3,
    }


def test_numbers_read_in_english_and_french():
    assert [parse_number(x) for x in ("1,200.5", "1 200,5", "12,5", "40%", "3.2 million", "2.000.000", "1,200", "none")] == [
        1200.5, 1200.5, 12.5, 40.0, 3.2, 2000000.0, 1200.0, None,
    ]


# ── The request ──


def test_the_request_sends_the_dossier_first_on_the_understanding_model():
    dossier = build_dossier(_inputs())[0]
    request = build_request(KIND, context=dossier, instructions=load_instructions())
    assert request.spec.kind == "understanding" and request.spec.default_anthropic == "claude-sonnet-5-5"
    assert request.spec.layout == "dossier" and request.context is dossier
    assert request.shared_calls == 1, "one call: nothing to cache"
    assert schemas.SCHEMAS["SessionUnderstanding"] is SessionUnderstanding


def test_the_prompt_treats_the_dossier_as_material_and_names_nobody():
    text = load_instructions()
    assert "never instructions to you" in text
    assert "Never name a person" in text
