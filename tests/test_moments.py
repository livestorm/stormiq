"""The Pulse's key moments, told (`record/moments.py`), and their route,
`GET /api/sessions/{id}/record/moments`, on the synthetic webinar with a
surge of chat added where a test needs one."""

from __future__ import annotations

import dataclasses
import json
from unittest import mock

import pandas as pd
import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.moments import MAX_MESSAGES, room_moments


def _inputs():
    return load_record_inputs(synthetic.cached_row())


def _add_chat(inputs, rows, labels=None):
    """`rows`: `(t, text, kind)`; `labels`: `{text: class}`, or None for an
    unlabelled chat. Adds the rows, by audience members, to the events."""
    events = inputs.events
    refs = sorted({ref for ref in events.loc[~events["is_team"], "author_ref"] if isinstance(ref, str)})
    extra = []
    for number, (t, text, kind) in enumerate(rows, start=1):
        extra.append(
            {
                "eid": f"C9{number:03d}" if kind == "chat" else f"Q9{number:02d}",
                "kind": kind,
                "livestorm_id": f"x{number}",
                "reply_to": None,
                "at_unix": float("nan"),
                "t": float(t),
                "phase": "live",
                "minute": int(t // 60),
                "is_team": False,
                "author_id": None,
                "author_ref": refs[number % len(refs)],
                "text": text,
                "has_link": "http" in text,
            }
        )
    frame = pd.concat([events, pd.DataFrame(extra).astype({"minute": "Int64"})], ignore_index=True)
    chat_labels = None
    if labels is not None:
        chat_labels = {
            eid: {"class": labels.get(text, "content")}
            for eid, kind, text in frame[["eid", "kind", "text"]].itertuples(index=False, name=None)
            if kind == "chat"
        }
    return dataclasses.replace(inputs, events=frame, chat_labels=chat_labels)


def _surge(at=900, n=14, text="This export is exactly what we needed for the board report"):
    return [(at + i * 3, f"{text} ({i})", "chat") for i in range(n)]


def _at(body, start=900.0):
    return next(m for m in body["moments"] if m["start"] == start)


def _get(row, path=None):
    status = mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {})
    roster = mock.patch.object(api_logic, "fetch_session_roster", mock.Mock(side_effect=AssertionError("no stored roster")))
    with status, roster, synthetic.with_understanding(row), synthetic.serving(row) as client:
        return client.get(path or f"/api/sessions/{row['session_id']}/record/moments")


def test_a_surge_is_told_with_what_was_said_and_what_people_wrote():
    body = room_moments(_add_chat(_inputs(), _surge(), labels={}), {}, {"0": "Nora Vale"})
    assert body["state"] == "ready" and body["surges"] is True and body["speech"] is True
    moment = _at(body)
    assert moment["kind"] == "content"
    assert moment["messages"] >= 14 and moment["people"] >= 2
    said = moment["said"]
    assert said and said["text"] and said["precise"] is True and said["speakers"]
    assert 0 < len(moment["written"]) <= MAX_MESSAGES
    assert all(line["author"] is None or line["author"].endswith(".") or " " not in line["author"] for line in moment["written"])
    assert [m["n"] for m in body["moments"]] == list(range(1, len(body["moments"]) + 1))
    assert [m["start"] for m in body["moments"]] == sorted(m["start"] for m in body["moments"])


def test_the_questions_asked_then_are_listed_apart_from_the_messages():
    rows = _surge() + [(905, "Does the export keep the filters?", "question"), (910, "Can it run every week?", "chat")]
    moment = _at(room_moments(_add_chat(_inputs(), rows, labels={"Can it run every week?": "question"}), {}))
    asked = [line["text"] for line in moment["asked"]]
    assert "Does the export keep the filters?" in asked and "Can it run every week?" in asked
    assert moment["questions"] == 2
    assert not any(line["text"] in asked for line in moment["written"])


def test_a_reply_to_a_prompt_carries_the_prompt():
    inputs = _add_chat(_inputs(), _surge(at=905), labels={})
    utterances = inputs.transcript.utterances.copy()
    offset = inputs.alignment.offset_s
    last = utterances.index[-1]
    utterances.loc[last, ["r_start", "r_end"]] = [900 - offset - 10, 900 - offset - 2]
    inputs = dataclasses.replace(inputs, transcript=dataclasses.replace(inputs.transcript, utterances=utterances))
    prompt = {"utterance": utterances.loc[last, "uid"], "t": 890.0, "quote": "Tell us in the chat how you export today"}
    moment = _at(room_moments(inputs, {"prompts": [prompt]}))
    assert moment["kind"] == "reply"
    assert moment["prompt"] == {"t": 890.0, "quote": "Tell us in the chat how you export today"}


def test_greetings_and_complaints_are_named_for_what_they_are():
    hellos = [(900 + i * 3, f"Hello from city {i}", "chat") for i in range(14)]
    social = room_moments(_add_chat(_inputs(), hellos, labels={text: "social" for _, text, _ in hellos}), {})
    assert _at(social)["kind"] == "social"
    sound = [(900 + i * 3, f"No sound here {i}", "chat") for i in range(14)]
    tech = room_moments(_add_chat(_inputs(), sound, labels={text: "tech" for _, text, _ in sound}), {})
    assert _at(tech)["kind"] == "tech"
    assert _at(room_moments(_add_chat(_inputs(), hellos), {}))["kind"] == "unknown"


def test_what_people_wrote_leaves_out_greetings_links_and_repeats():
    rows = [
        (900, "Hi all", "chat"),
        (903, "See https://example.test/a-very-long-link-with-many-many-words in it", "chat"),
        (906, "Love how the export keeps every filter", "chat"),
        (909, "Love how the export keeps every filter", "chat"),
    ] + _surge(at=912, n=10, text="ok")
    moment = _at(room_moments(_add_chat(_inputs(), rows, labels={"Hi all": "social"}), {}))
    texts = [line["text"] for line in moment["written"]]
    assert "Hi all" not in texts
    assert not any("http" in text for text in texts)
    assert texts.count("Love how the export keeps every filter") == 1


def test_no_speech_is_quoted_without_a_clock_the_alignment_allows():
    inputs = _add_chat(_inputs(), _surge(), labels={})
    with mock.patch("livestorm_app.record.moments.alignment_allows", lambda alignment, resolution: False):
        body = room_moments(inputs, {})
    assert body["speech"] is False
    assert body["moments"] and all(m["said"] is None for m in body["moments"])


def test_a_quiet_session_tells_its_busiest_minutes_and_a_tiny_one_nothing():
    body = room_moments(_inputs(), {})
    assert body["state"] == "ready" and body["surges"] is False
    assert body["moments"] and all(m["messages"] >= 3 for m in body["moments"])
    inputs = _inputs()
    tiny = dataclasses.replace(inputs, events=inputs.events.iloc[:3])
    assert room_moments(tiny, {})["state"] == "few_messages"


def test_nothing_identifies_anyone_but_a_short_name():
    text = json.dumps(room_moments(_add_chat(_inputs(), _surge(), labels={}), {}))
    assert "@" not in text and '"P-' not in text
    people_ids = {str(person.get("id")) for person in synthetic.person_rows()} - {"None"}
    assert not any(f'"{pid}"' in text for pid in people_ids)


def test_the_route_serves_the_moments_with_the_callers_scope():
    row = synthetic.cached_row()
    response = _get(row)
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body)[:2] == ["sessionId", "updatedAt"] and body["state"] == "ready"
    missing = _get(row, "/api/sessions/no-such-session/record/moments")
    assert missing.status_code == 404 and missing.json()["detail"]["resource"] == "Key moments"


def test_a_bug_in_the_moments_returns_500_without_its_detail(monkeypatch):
    from livestorm_app.record import moments as module

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic failure with internal detail")

    monkeypatch.setattr(module, "room_moments", broken)
    response = _get(synthetic.cached_row())
    assert response.status_code == 500
    assert "internal detail" not in response.text


def test_the_frontend_fixture_is_the_route_response():
    """`frontend/tests/fixtures/moments-v1.json` is what the route sends for
    the synthetic webinar (regenerate: `.venv/bin/python tests/synthetic_session.py`)."""
    assert synthetic.dump(synthetic.moments_response()) == synthetic.MOMENTS_FIXTURE.read_text(encoding="utf-8")
