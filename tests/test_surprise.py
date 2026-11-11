"""Surprise Me! grounded in the record (`record/surprise_brief.py`,
`api_logic._surprise_facts`, `run_smart_recap` with the `surprise` tone):
what StormIQ measured and what the room wrote (redacted, never who) sent
with the transcript, and a take with a figure the session doesn't have
never saved, on the synthetic webinar."""

from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import pytest
import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.record.redaction import Redactor
from livestorm_app.record.surprise_brief import (
    MAX_ROOM_LINES,
    SurpriseRejected,
    build_surprise_brief,
    check_surprise,
    room_lines,
    spoken_numbers,
)

# ── The check ──

ALLOWED = "## Vitals\n- Show-up rate: 56% of those registered came live (estimated)\n- 23:00 to 26:00: 50 messages\nWe have 142 people."


def test_a_take_may_quote_the_sessions_figures_and_small_counts():
    take = "56% showed up, 142 people, and at 23:00 the chat sent 50 messages. For the 3rd time, someone asked."
    assert check_surprise(take, ALLOWED) == take


@pytest.mark.parametrize(
    ("take", "kind"),
    [
        ("At 12:34 it all went wrong.", "time"),
        ("A cool 87% of the room nodded.", "percentage"),
        ("All 400 attendees cheered.", "number"),
        ("", "empty"),
    ],
)
def test_a_take_with_a_figure_the_session_doesnt_have_is_refused(take, kind):
    with pytest.raises(SurpriseRejected, match=kind):
        check_surprise(take, ALLOWED)


def test_the_facts_list_the_vitals_moments_and_chapters():
    text = build_surprise_brief(
        title="Spring  release tour",
        length_s=2700,
        measures={"m.show_up_rate": {"state": "partial", "value": 0.5563}},
        moments={"state": "ready", "moments": [{"start": 1380, "end": 1560, "messages": 50, "kind": "content", "said": {"text": "watch, i connect okta"}}]},
        chapters=[{"title": "Single sign-on", "t": 1325}],
    )
    assert "title: Spring release tour" in text and "length: 45:00" in text
    assert "- Show-up rate: 56% of those registered came live (estimated)" in text
    assert '- 23:00 to 26:00: 50 messages, reacting to the talk; being said on stage: "watch, i connect okta"' in text
    assert "- 22:05 Single sign-on" in text
    empty = build_surprise_brief(title="", length_s=None, measures={}, moments={}, chapters=[])
    assert "- none measured" in empty and "- none found" in empty and "- none placed" in empty
    assert "- nothing quoted" in empty


# ── From the room ──


def _moment(kind, messages, start, written):
    return {"kind": kind, "messages": messages, "start": start, "written": [{"t": t, "text": text, "author": "Amara Diallo"} for t, text in written]}


MOMENTS = {
    "state": "ready",
    "moments": [
        _moment("social", 12, 60, [(61, "Hi from Lyon everyone"), (62, "hi")]),
        _moment("content", 30, 1380, [(1385, "Wait, it connected to Okta first try?"), (1390, "Amara wrote to amara@example.com about it")]),
        _moment("content", 30, 1380, [(1395, "wait, it connected to OKTA first try?")]),
    ],
}
QUESTIONS = [
    {"id": "Q001", "t": 300, "text": "Is SSO included?"},
    {"id": "Q002", "t": 900, "text": "Call me on +33 6 12 34 56 78 about pricing"},
    {"id": "C0042", "t": 1500, "text": "Can we export 1,200 rows?"},
    {"id": "Q003", "t": None, "text": "No time, so left out"},
]
REDACTOR = Redactor(full_names=("Amara Diallo",), words=("Amara", "Diallo"))


def test_the_room_gives_telling_messages_and_the_top_questions_redacted_without_who():
    rows = room_lines(MOMENTS, QUESTIONS, {"Q002": 7, "Q001": 2}, redact=REDACTOR.redact)
    texts = [row["text"] for row in rows]
    assert [row["id"] for row in rows] == [f"R{n}" for n in range(1, len(rows) + 1)]
    assert [row["t"] for row in rows] == sorted(row["t"] for row in rows), "in time order"
    assert "Wait, it connected to Okta first try?" in texts
    assert "wait, it connected to OKTA first try?" not in texts, "the same words count once"
    assert "hi" not in texts, "a one-word message says little"
    assert "[name] wrote to [email] about it" in texts
    assert "Call me on [phone] about pricing" in texts
    joined = " ".join(texts)
    assert "Amara" not in joined and "example.com" not in joined and "56 78" not in joined, "names and contacts never reach a model"
    assert all("author" not in row for row in rows), "never who wrote"
    asked = [row for row in rows if row["kind"] == "question"]
    assert [row["votes"] for row in asked] == [2, 7, None]
    assert "No time, so left out" not in texts, "a line with no time on the session clock is left out"
    assert room_lines(MOMENTS, QUESTIONS, {"Q002": 7, "Q001": 2}, redact=REDACTOR.redact) == rows, "the same lines every time"


def test_the_moments_of_content_come_first_and_the_room_is_capped():
    many = {"state": "ready", "moments": [_moment("social", 50, 0, [(n, f"Hello from city number {n}") for n in range(1, 9)]),
                                          _moment("content", 5, 600, [(600 + n, f"That export is so fast {n}") for n in range(3)])]}
    rows = room_lines(many, [{"id": f"Q{n:03d}", "t": 900 + n, "text": f"Question {n} please?"} for n in range(6)])
    chat = [row["text"] for row in rows if row["kind"] == "chat"]
    assert chat[-3:] == ["That export is so fast 0", "That export is so fast 1", "That export is so fast 2"]
    assert len(chat) == 5 and len([row for row in rows if row["kind"] == "question"]) == 3
    assert len(rows) <= MAX_ROOM_LINES
    assert room_lines({"state": "few_messages"}, []) == []


def test_the_room_is_in_the_brief_and_its_figures_count_as_facts():
    room = room_lines(MOMENTS, QUESTIONS, {"Q002": 7}, redact=REDACTOR.redact)
    text = build_surprise_brief(title="T", length_s=1800, measures={}, moments={}, chapters=[], room=room)
    assert "## From the room" in text and "use it only when it makes the story better" in text
    assert '- 25:00 chat: "Can we export 1,200 rows?"' not in text
    assert '- 25:00 question: "Can we export 1,200 rows?"' in text
    assert '- 15:00 question (7 upvotes): "Call me on [phone] about pricing"' in text
    assert check_surprise("Someone at 25:00 wanted 1,200 rows.", text)


# ── The run ──


def _run(tone, answer):
    row = dict(synthetic.cached_row())
    seen = {"saved": []}

    answers = list(answer) if isinstance(answer, list) else None
    seen["calls"] = []

    def model(**kwargs):
        seen["sent"] = kwargs["transcript_text"]
        seen["calls"].append(kwargs["transcript_text"])
        return answers.pop(0) if answers is not None else answer

    with ExitStack() as stack:
        for name, value in (
            ("fetch_cached_session", lambda key, sid, *, organization_id: dict(row)),
            ("fetch_roster_status", lambda session_id, organization_id: {}),
            ("fetch_chat_labels", lambda session_id, organization_id: synthetic.chat_labels(row)),
            ("fetch_understanding", lambda session_id, organization_id: synthetic.understanding(row)),
            ("build_transcript_plain_text", lambda payload: "Welcome. Thanks Theo Park. We now have 142 people."),
            ("merge_cached_bundle_entry", lambda *args, **kwargs: seen["saved"].append(args)),
            ("get_llm_model", lambda kind: f"model-for-{kind}"),
            ("analyze_with_openai", model),
        ):
            stack.enter_context(mock.patch.object(api_logic, name, value))
        return api_logic.run_smart_recap("llm-key", row["session_id"], tone), seen


def test_the_surprise_take_reads_what_stormiq_measured_then_the_transcript_redacted():
    _, seen = _run("surprise", "# Title\nThe room\n# Description\n142 people, one plan.")
    sent = seen["sent"]
    assert sent.startswith("# What StormIQ measured in this session")
    assert sent.index("## When the chat was busiest") < sent.index("## From the room") < sent.index("# Transcript")
    room = sent[sent.index("## From the room"):sent.index("# Transcript")]
    assert '- 5:00 question (1 upvote): "How does pricing work for larger teams with several hundred seats?"' in room
    assert '- 1:02 chat: "Hi from Lyon"' in room
    assert "Thanks [name]." in sent and "Theo" not in sent
    assert len(seen["saved"]) == 1


def test_a_surprise_take_with_an_invented_figure_is_never_saved():
    with pytest.raises(api_logic.SurpriseCheckError, match="gives a number that isn't in the session. Nothing was saved") as raised:
        _run("surprise", "# Title\nWow\n# Description\nAll 999 attendees cheered.")
    assert "999" not in str(raised.value), "never the take's words"


def test_the_other_tones_read_the_transcript_only():
    _, seen = _run("professional", "# Title\nRecap\n# Description\nFine.")
    assert seen["sent"] == "Welcome. Thanks [name]. We now have 142 people."


# ── Numbers said aloud, and one retry ──


def test_numbers_said_aloud_are_read_in_english_and_french():
    assert spoken_numbers("we hit fifteen hundred users, twenty-five teams and two hundred and forty signups") == {"1500", "25", "240"}
    assert spoken_numbers("quatre-vingt-dix pour cent, soixante-dix, trois mille inscrits") == {"90", "70", "3000"}
    assert spoken_numbers("a hundred times") == {"100"}
    assert "15" in {v for v in spoken_numbers("fifteen")}


def test_a_number_said_aloud_may_be_written_as_digits():
    allowed = "We now have fifteen integrations and forty-two customers."
    assert check_surprise("Fifteen? No: 15 integrations, 42 customers.", allowed)
    with pytest.raises(SurpriseRejected, match="number"):
        check_surprise("All 43 customers clapped.", allowed)


def test_a_refused_take_is_written_once_more_told_why():
    bad = "# Title\nWow\n# Description\nAll 999 attendees cheered."
    good = "# Title\nThe room\n# Description\n142 people, one plan."
    _, seen = _run("surprise", [bad, good])
    assert len(seen["calls"]) == 2
    assert "A first take was refused: it gives a number that isn't in the session." in seen["calls"][1]
    assert "999" not in seen["calls"][1], "never the take's own words"
    assert len(seen["saved"]) == 1 and seen["saved"][0][3] == good


def test_two_refusals_save_nothing():
    bad = "# Title\nWow\n# Description\nAll 999 attendees cheered."
    with pytest.raises(api_logic.SurpriseCheckError):
        _run("surprise", [bad, bad])


def test_the_prompt_uses_the_room_only_when_it_makes_the_story_better():
    from livestorm_app.config import SMART_RECAP_SURPRISE_PROMPT_PATH

    prompt = SMART_RECAP_SURPRISE_PROMPT_PATH.read_text(encoding="utf-8")
    assert '"From the room"' in prompt and "only when they make the story better" in prompt
    assert "a take may quote none" in prompt
    assert "Never name or describe who wrote a chat message or asked a question" in prompt
