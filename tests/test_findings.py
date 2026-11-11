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
    detect_moments,
    detect_monologue,
    detect_prompts,
    findings,
    incidents,
    longest_stretch,
    prompt_rows,
    surge_threshold,
    surges,
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
    assert {"chapters", "friction"} <= {f["id"] for f in answer["found"]}


# ── #6: monologue stretches ──


def test_the_longest_stretch_breaks_at_a_new_speaker_or_a_prompt():
    rows = [("U1", "0", 0, 60), ("U2", "0", 61, 300), ("U3", "1", 301, 320), ("U4", "1", 321, 700), ("U5", "1", 701, 760)]
    assert longest_stretch(rows, []) == {"start": 301, "end": 760, "speaker": "1"}
    assert longest_stretch(rows, ["U3"]) == {"start": 321, "end": 760, "speaker": "1"}, "a prompt ends the run it is in"
    assert longest_stretch([], []) is None


def test_a_long_stretch_where_the_chat_fell_is_found(monkeypatch):
    import livestorm_app.record.findings as module

    inputs, _, result = _setup()
    quiet = detect_monologue(inputs, [], result.get("answers") or [])
    assert quiet["state"] == NOTHING and quiet["values"]["seconds"] == 297.5, "under 8 minutes"
    monkeypatch.setattr(module, "MONOLOGUE_MIN_S", 200.0)
    busy_start = _with_chat(inputs, [625 + 10 * i for i in range(9)])
    answer = detect_monologue(busy_start, [], result.get("answers") or [])
    assert answer["state"] == FOUND
    assert (answer["values"]["first"], answer["values"]["last"]) == (9, 1)
    even = _with_chat(inputs, [625 + 30 * i for i in range(10)])
    assert detect_monologue(even, [], [])["state"] == NOTHING, "the chat kept up"
    unlabelled, _, _ = _setup(labelled=False)
    assert detect_monologue(unlabelled, [], [])["reason"] == "labels"


# ── #4: did the room have time to answer ──


def test_a_prompt_row_counts_the_wait_the_replies_over_the_usual_and_the_late_ones():
    rows = prompt_rows([("Any questions?", 100.0, 105.0)], [0.0, 100.0, 108.0, 200.0], [106.0, 110.0, 115.0, 120.0, 400.0], 0.01)
    assert rows == [
        {"t": 100.0, "quote": "Any questions?", "waitS": 3.0, "messages": 4, "expected": 1.2, "excess": 3, "late": 3, "medianDelayS": 7.5}
    ]


def test_a_prompt_the_room_answered_after_the_speaker_moved_on_is_found():
    inputs, _, _ = _setup()
    prompt = [{"utterance": "U0028", "t": 306.9, "quote": "pricing works per team"}]
    assert detect_prompts(inputs, [])["reason"] == "no_prompts"
    assert detect_prompts(inputs, prompt)["state"] == NOTHING, "nobody answered"
    answered = _with_chat(inputs, [330, 340, 355, 370, 390])
    answer = detect_prompts(answered, prompt)
    assert answer["state"] == FOUND and answer["values"]["missed"] == [306.9]
    row = answer["values"]["prompts"][0]
    assert row["waitS"] == 6.2 and row["late"] == 5 and row["excess"] >= 3


def test_prompts_need_a_verified_clock():
    inputs, _, _ = _setup()
    assumed = dataclasses.replace(inputs, alignment=dataclasses.replace(inputs.alignment, status="assumed"))
    assert detect_prompts(assumed, [{"utterance": "U0028"}])["reason"] == "clock"


# ── #2: where the room leaned in, unprompted ──


def test_a_surge_is_well_over_the_rooms_usual_minute_and_adjacent_ones_merge():
    assert surge_threshold([2, 2, 3, 2, 1, 2]) == 5.0, "never under 5"
    assert surge_threshold([10, 12, 10, 11, 10]) == 20.0, "twice the median"
    assert surges([1, 6, 7, 1, 9, 1], 5) == [(1, 2), (4, 4)]


def _busy(inputs, extra=(), label="content"):
    """A room writing two messages a minute, plus `extra`."""
    return _with_chat(inputs, [30 + 30 * i for i in range(58)] + list(extra), label=label)


def test_an_unprompted_surge_is_a_moment_with_the_words_being_said():
    inputs, _, _ = _setup()
    room = _busy(inputs, [605 + 4 * i for i in range(12)])
    answer = detect_moments(room, [], 0.0)
    assert answer["state"] == FOUND
    (moment,) = answer["values"]["moments"]
    assert (moment["start"], moment["end"], moment["n"]) == (600.0, 660.0, 1)
    assert moment["messages"] >= 12 and moment["quote"] and moment["utterance"].startswith("U")
    assert answer["values"]["quotes"] is True


def test_a_reply_to_a_prompt_and_a_wave_of_greetings_are_not_moments():
    inputs, _, _ = _setup()
    reply = _busy(inputs, [320 + 3 * i for i in range(12)])
    answer = detect_moments(reply, [{"utterance": "U0028"}], None)
    assert answer["state"] == NOTHING and answer["values"]["replies"] == 1
    wave = _with_chat(_busy(inputs), [605 + 4 * i for i in range(14)], label="social")
    answer = detect_moments(wave, [], None)
    assert answer["state"] == NOTHING and answer["values"]["waves"] == 2, "the webinar's opening greetings are a wave too"


def test_a_small_room_has_no_moments_to_find():
    inputs, _, _ = _setup()
    assert detect_moments(inputs, [], None)["reason"] == "few_messages"
    unlabelled, _, _ = _setup(labelled=False)
    assert detect_moments(unlabelled, [], None)["reason"] == "labels"


def test_authors_without_a_person_are_told_apart_by_their_livestorm_id():
    inputs, _, _ = _setup()
    noisy = _with_chat(inputs, [400, 430, 470], label="tech", author=lambda i: None)
    noisy = dataclasses.replace(
        noisy, events=noisy.events.assign(author_id=[a if a else f"ls-{i % 2}" for i, a in enumerate(noisy.events["author_id"])])
    )
    (incident,) = detect_friction(noisy)["values"]["incidents"]
    assert incident["messages"] == 3 and incident["people"] == 2, "a chat saved before the people links still counts its authors"


# ── The route ──


def test_the_frontend_fixture_is_the_route_response():
    """`frontend/tests/fixtures/findings-v1.json` is what the route sends for
    the synthetic webinar. After a deliberate change, regenerate it:
    `.venv/bin/python tests/synthetic_session.py`."""
    assert synthetic.dump(synthetic.findings_response()) == synthetic.FINDINGS_FIXTURE.read_text(encoding="utf-8")
