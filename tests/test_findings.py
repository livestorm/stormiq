"""The Report's findings (`record/findings.py`) and their route
(`GET /api/sessions/{id}/record/findings`), on the synthetic webinar and on
busier copies of it."""

from __future__ import annotations

import dataclasses

import pandas as pd
import synthetic_session as synthetic

from livestorm_app.record.findings import (
    FOUND,
    NOT_CHECKED,
    NOTHING,
    chapter_index,
    dead_air,
    detect_chapters,
    detect_friction,
    findings,
    incidents,
)
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.understanding import chapter_list, current_reading


def _setup(row=None, *, labelled=True):
    row = row or synthetic.cached_row()
    inputs = load_record_inputs(row, stored_labels=synthetic.chat_labels(row) if labelled else None)
    stored = synthetic.understanding(row)
    result, _, _ = current_reading(stored, inputs)
    chapters, _ = chapter_list(stored, inputs)
    return inputs, chapters, result


def _with_chat(inputs, times, label="content", author=lambda i: f"P-{i % 7}"):
    """`inputs` with audience chat added at `times` (session seconds), each
    labelled `label`."""
    rows = [
        {
            "eid": f"X{i:04d}", "kind": "chat", "livestorm_id": f"x-{i}", "reply_to": None, "at_unix": None,
            "t": float(t), "phase": "live", "minute": int(t // 60), "is_team": False, "author_id": None,
            "author_ref": author(i), "text": "", "has_link": False,
        }
        for i, t in enumerate(times)
    ]
    events = pd.concat([inputs.events, pd.DataFrame(rows)], ignore_index=True)
    labels = {**(inputs.chat_labels or {}), **{r["eid"]: {"id": r["eid"], "class": label} for r in rows}}
    return dataclasses.replace(inputs, events=events, chat_labels=labels)


# ── #7: chapter by chapter ──


def test_the_index_puts_the_median_chapter_at_100_and_leaves_qa_and_short_ones_out():
    rows = chapter_index([
        {"seconds": 600, "unprompted": 10, "qa": False},
        {"seconds": 600, "unprompted": 30, "qa": False},
        {"seconds": 600, "unprompted": 20, "qa": False},
        {"seconds": 60, "unprompted": 9, "qa": False},
        {"seconds": 600, "unprompted": 50, "qa": True},
    ])
    assert [r["index"] for r in rows] == [50, 150, 100, None, None]
    assert rows[1]["perMinute"] == 3.0 and rows[4]["perMinute"] is None


def test_a_small_room_is_not_checked_and_says_why():
    inputs, chapters, result = _setup()
    answer = detect_chapters(inputs, chapters, result.get("prompts") or [])
    assert (answer["state"], answer["reason"]) == (NOT_CHECKED, "few_messages")
    assert detect_chapters(inputs, [], [])["reason"] == "no_chapters"
    unlabelled, _, _ = _setup(labelled=False)
    assert detect_chapters(unlabelled, chapters, [])["reason"] == "labels"


def test_a_chapter_that_drew_the_room_in_is_found():
    inputs, chapters, result = _setup()
    busy = _with_chat(inputs, [310 + 10 * i for i in range(40)] + [100 + 40 * i for i in range(5)] + [800 + 60 * i for i in range(10)])
    answer = detect_chapters(busy, chapters, result.get("prompts") or [])
    assert answer["state"] == FOUND
    assert answer["values"]["high"] == "Pricing for larger teams"
    pricing = next(c for c in answer["values"]["chapters"] if c["title"] == "Pricing for larger teams")
    assert pricing["index"] >= 150 and pricing["chatters"] == 7
    qa = next(c for c in answer["values"]["chapters"] if c["qa"])
    assert qa["index"] is None, "the Q&A chapter shows its questions, never an index"


def test_greetings_and_replies_to_a_prompt_are_not_unprompted():
    inputs, chapters, _ = _setup()
    social = _with_chat(inputs, [310 + 10 * i for i in range(40)], label="social")
    assert detect_chapters(social, chapters, [])["reason"] == "few_messages"
    replies = _with_chat(inputs, [330 + i for i in range(40)])
    prompt = [{"utterance": "U0028", "t": None}]
    assert detect_chapters(replies, chapters, prompt)["reason"] == "few_messages", "40 messages right after a prompt"


def test_an_even_room_is_checked_and_nothing_is_found():
    inputs, chapters, _ = _setup()
    even = _with_chat(inputs, [100 + 20 * i for i in range(80)])
    assert detect_chapters(even, chapters, [])["state"] == NOTHING


# ── #10: production friction ──


def test_tech_messages_close_together_make_an_incident_with_its_dead_air():
    tech = [{"t": 100, "author": "a"}, {"t": 150, "author": "b"}, {"t": 260, "author": "a"}, {"t": 900, "author": "c"}]
    assert incidents(tech, [(120.0, 140.0), (500.0, 600.0)]) == [
        {"start": 100, "end": 260, "messages": 3, "people": 2, "deadAirS": 20.0}
    ]
    assert dead_air([("U1", 0, 10), ("U2", 12, 20), ("U3", 30, 40), ("U4", 31, 50)]) == [(20, 30)]


def test_friction_finds_an_audio_incident_and_a_late_start():
    inputs, _, _ = _setup()
    assert detect_friction(inputs)["state"] == NOTHING, "the synthetic webinar started 95 s late"
    noisy = _with_chat(inputs, [400, 430, 470, 500], label="tech")
    answer = detect_friction(noisy)
    assert answer["state"] == FOUND and answer["values"]["incidents"][0]["messages"] == 4
    late = dataclasses.replace(inputs, clock=dataclasses.replace(inputs.clock, late_start_s=420.0))
    answer = detect_friction(late)
    assert answer["state"] == FOUND and answer["confidence"] == "measured"
    assert answer["values"]["lateStart"] == {"lateS": 420.0, "waitingMessages": 3}


def test_without_labels_only_the_late_start_is_looked_at():
    inputs, _, _ = _setup(labelled=False)
    assert detect_friction(inputs)["reason"] == "labels"


def test_findings_rank_the_found_ones_strongest_first():
    inputs, chapters, result = _setup()
    busy = _with_chat(inputs, [310 + 10 * i for i in range(40)] + [100 + 40 * i for i in range(5)] + [800 + 60 * i for i in range(10)])
    busy = _with_chat(busy, [400, 430, 470], label="tech")
    answer = findings(busy, chapters, result)
    assert [f["id"] for f in answer["found"]] == sorted((f["id"] for f in answer["found"]), key=lambda i: -next(
        f["strength"] for f in answer["found"] if f["id"] == i
    ))
    assert {f["id"] for f in answer["found"]} == {"chapters", "friction"}


# ── The route ──


def test_the_frontend_fixture_is_the_route_response():
    """`frontend/tests/fixtures/findings-v1.json` is what the route sends for
    the synthetic webinar. After a deliberate change, regenerate it:
    `.venv/bin/python tests/synthetic_session.py`."""
    assert synthetic.dump(synthetic.findings_response()) == synthetic.FINDINGS_FIXTURE.read_text(encoding="utf-8")
