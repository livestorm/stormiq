"""Public formats: what attendees wrote or said, redacted before a writer of
the FAQ, Smart Recap or the repurposed content sees it, and the output
checked after (`record/redaction.py`, `api_logic._public_redactor`), on the
synthetic webinar."""

from __future__ import annotations

import copy
import dataclasses
from contextlib import ExitStack
from unittest import mock

import pytest
import synthetic_session as synthetic
import test_faq

from livestorm_app import api_logic
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.redaction import COMMON_WORDS, public_redactor, redactor_for, saved_speaker_names

AUDIENCE = {"Theo Park", "Mila Ford", "Sam Reed", "Lena Hart", "Omar Quinn", "Jade Bell", "Ravi Cole"}

# ── Contact details ──


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mail me at marie.dupont+webinar@acme.co.uk please", "Mail me at [email] please"),
        ("Slides: https://acme.example/deck?id=4. Thanks", "Slides: [link]. Thanks"),
        ("see www.acme.example/pricing)", "see [link])"),
        ("Call +33 6 12 34 56 78 tomorrow", "Call [phone] tomorrow"),
        ("US line (555) 123-4567.", "US line [phone]."),
    ],
)
def test_emails_links_and_phone_numbers_are_replaced(text, expected):
    assert redactor_for(()).redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "On 2026-11-11 at 23:00, 45 questions",
        "Revenue grew to 10.000.000 or 12 500 000 euros, 1,200 people",
        "Order 12345 and room 4 at 14:30",
        "Le 11/11/2026, 73% ont écrit",
    ],
)
def test_dates_times_and_numbers_stay(text):
    assert redactor_for(()).redact(text) == text


# ── Names ──


def test_a_full_name_goes_whatever_its_case_and_a_capitalised_word_of_it_too():
    r = redactor_for(["Marie Dupont"])
    assert r.redact("Thanks MARIE DUPONT and marie dupont!") == "Thanks [name] and [name]!"
    assert r.redact("Great question from Marie, and Dupont agrees.") == "Great question from [name], and [name] agrees."
    assert r.redact("hi im marie from Acme") == "hi im marie from Acme", "lower case is left to the prompt; companies stay"


def test_a_common_word_starting_a_sentence_stays_but_not_elsewhere():
    assert "will" in COMMON_WORDS
    r = redactor_for(["Will Turner"])
    assert r.redact("Will it work? Thanks Will. Turner asked too.") == "Will it work? Thanks [name]. [name] asked too."


def test_speakers_keep_their_names_and_repeats_merge():
    r = redactor_for(["Rhea Stone", "Theo Park"], kept_names=["Rhea Stone"])
    assert r.full_names == ("Theo Park",)
    assert r.redact("Rhea Stone answered Theo Park") == "Rhea Stone answered [name]"
    assert redactor_for(["Anne Marie Roche"]).redact("Hello Anne Marie Roche") == "Hello [name]"


def _roster(internal=False):
    roster = load_record_inputs(dict(synthetic.cached_row())).roster
    return dataclasses.replace(roster, meta=dataclasses.replace(roster.meta, internal=True)) if internal else roster


def test_a_public_session_hides_its_audience_never_its_team_or_speakers():
    r = public_redactor(_roster(), saved_speaker_names(synthetic.cached_row()))
    assert set(r.full_names) == AUDIENCE
    assert not {"Nora", "Vale", "Ilan", "Moss", "Rhea", "Stone"} & set(r.words)


def test_an_internal_session_hides_everyone_but_the_saved_speakers():
    r = public_redactor(_roster(internal=True), saved_speaker_names(synthetic.cached_row()))
    assert set(r.full_names) == AUDIENCE | {"Ilan Moss"}, "Nora Vale and Rhea Stone are saved speakers"


def test_without_people_only_contact_details_go():
    r = public_redactor(None, [])
    assert (r.full_names, r.words) == ((), ())


# ── The check ──


def test_the_check_names_each_kind_found_never_the_text():
    r = redactor_for(["Theo Park"])
    assert r.leaks("All clear: 45 questions on 2026-11-11.") == []
    assert r.leaks("Write to a@b.example or +33 6 12 34 56 78; theo park asked; [name]") == [
        "an email",
        "a phone number",
        "an attendee's name",
        "a placeholder",
    ]


# ── The FAQ ──


def _row_with_personal_question():
    row = copy.deepcopy(dict(synthetic.cached_row()))
    first = row["questions_payload"]["data"][0]["attributes"]
    first["question"] = "Theo Park here (theo@park.example): will the slides be shared?"
    first["response"] = "Yes Theo, see https://acme.example/slides"
    return row


def test_the_faq_brief_never_holds_what_identifies_an_attendee():
    _, seen = test_faq._run(_row_with_personal_question())
    text = seen["brief"].text
    assert "[name] here ([email]): will the slides be shared?" in text
    assert "Yes [name], see [link]" in text
    for leaked in ("Theo", "Park", "theo@park.example", "https://acme.example"):
        assert leaked not in text


def test_a_faq_that_names_an_attendee_is_never_saved():
    with pytest.raises(api_logic.PublicOutputLeak, match="FAQ contained an attendee's name. Nothing was saved") as raised:
        test_faq._run(answer="## Questions and answers\n### Slides?\nYes, as Mila Ford asked.")
    assert "Mila" not in str(raised.value), "never the output's words"


# ── Smart Recap and the repurposed content ──


def _speech_run(runner, answer):
    row = dict(synthetic.cached_row())
    seen = {"saved": []}

    def model(**kwargs):
        seen["transcript"] = kwargs["transcript_text"]
        return answer

    speech = "Welcome. Thanks Theo Park, mail theo@park.example. Rhea Stone shows SSO."
    patches = [
        mock.patch.object(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row)),
        mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "build_transcript_plain_text", lambda payload: speech),
        mock.patch.object(api_logic, "merge_cached_bundle_entry", lambda *args, **kwargs: seen["saved"].append(args)),
        mock.patch.object(api_logic, "get_llm_model", lambda kind: f"model-for-{kind}"),
        mock.patch.object(api_logic, "analyze_with_openai", model),
        mock.patch.object(api_logic, "generate_content_repurpose_bundle_with_openai", model),
    ]
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        return runner(row), seen


def _recap(row):
    return api_logic.run_smart_recap("llm-key", row["session_id"], "professional")


def _content(row):
    return api_logic.run_content_repurposing("llm-key", row["session_id"], "English")


CONTENT = {"summary": "S.", "blog": "# B", "email": "E.", "social_media": "## LinkedIn\nL."}


@pytest.mark.parametrize(("runner", "answer"), [(_recap, "# Recap\nRhea Stone showed SSO."), (_content, CONTENT)])
def test_the_recap_and_the_content_read_the_speech_redacted_with_speakers_named(runner, answer):
    _, seen = _speech_run(runner, answer)
    assert seen["transcript"] == "Welcome. Thanks [name], mail [email]. Rhea Stone shows SSO."
    assert len(seen["saved"]) == 1


def test_a_recap_or_content_holding_an_attendee_s_details_is_never_saved():
    with pytest.raises(api_logic.PublicOutputLeak, match="smart recap contained an attendee's name"):
        _speech_run(_recap, "# Recap\nOmar Quinn loved it.")
    with pytest.raises(api_logic.PublicOutputLeak, match="content contained a placeholder"):
        _speech_run(_content, {**CONTENT, "email": "Thanks [name]!"})
