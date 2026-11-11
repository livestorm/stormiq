"""The chapters the understanding job found (`record.understanding.
chapter_list`, `show_notes`): on the Transcript tab
(`GET /api/sessions/{id}/record/transcript`) and as show notes
(`GET /api/sessions/{id}/record/chapters`), on the synthetic webinar."""

from __future__ import annotations

from unittest import mock

import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.transcript_view import transcript_view
from livestorm_app.record.understanding import NONE, READY, STALE, chapter_list, show_notes


def _inputs(row=None, *, labelled=True):
    row = row or synthetic.cached_row()
    return load_record_inputs(row, stored_labels=synthetic.chat_labels(row) if labelled else None)


def _stored(row=None, **result):
    stored = synthetic.understanding(row or synthetic.cached_row())
    return {**stored, "result": {**stored["result"], **result}}


# ── The chapters ──


def test_chapters_come_in_order_on_the_recordings_clock():
    chapters, state = chapter_list(_stored(), _inputs())
    assert state == READY
    assert [(c["title"], c["first"], c["last"]) for c in chapters] == [
        ("Welcome and agenda", "U0001", "U0005"),
        ("The dashboard, view by view", "U0006", "U0027"),
        ("Pricing for larger teams", "U0028", "U0073"),
        ("Exports and questions", "U0074", "U0165"),
        ("Goodbye", "U0166", "U0166"),
    ]
    assert chapters[0]["r"] == 3.0 and chapters[1]["r"] == 60.0, "the first utterance's start, on the recording"


def test_chapters_are_used_only_while_read_from_the_session_as_it_is():
    stored = _stored()
    assert chapter_list(None, _inputs()) == ([], NONE)
    assert chapter_list({**stored, "dossier_hash": "0" * 64}, _inputs()) == ([], STALE)
    assert chapter_list(stored, _inputs(labelled=False)) == ([], STALE), "the chat's questions changed"
    row = synthetic.cached_row()
    assert chapter_list(stored, _inputs({**row, "transcript_payload": None})) == ([], NONE)


def test_a_stored_chapter_that_no_longer_fits_is_left_out():
    good = {"title": "Opening", "kind": "opening", "first": "U0001", "last": "U0005"}
    stored = _stored(chapters=[
        good,
        {"title": "Overlaps", "kind": "content", "first": "U0004", "last": "U0010"},
        {"title": "Unknown", "kind": "content", "first": "U9998", "last": "U9999"},
        {"title": "  ", "kind": "content", "first": "U0020", "last": "U0021"},
        {"title": "Odd kind", "kind": "keynote", "first": "U0030", "last": "U0031"},
        "not a chapter",
    ])
    chapters, _ = chapter_list(stored, _inputs())
    assert [(c["title"], c["kind"]) for c in chapters] == [("Opening", "opening"), ("Odd kind", "other")]


# ── Show notes ──


def _chapter(title, r, r_end):
    return {"title": title, "kind": "content", "first": "U", "last": "U", "r": r, "rEnd": r_end}


def test_show_notes_start_at_zero_and_say_whether_youtube_reads_them():
    notes = show_notes([_chapter("Opening", 3.0, 50), _chapter("Demo", 61.4, 600), _chapter("Q&A", 605, 900)])
    assert notes == {"text": "00:00 Opening\n01:01 Demo\n10:05 Q&A", "youtube": True}
    assert show_notes([_chapter("Opening", 0, 50), _chapter("Demo", 60, 600)])["youtube"] is False, "fewer than three"
    short_last = show_notes([_chapter("A", 0, 50), _chapter("B", 60, 600), _chapter("C", 600, 605)])
    assert short_last["youtube"] is False, "a chapter under ten seconds"
    assert show_notes([]) == {"text": "", "youtube": False}


def test_show_notes_show_hours_on_a_long_replay():
    notes = show_notes([_chapter("Opening", 0, 60), _chapter("Long part", 120, 3500), _chapter("Late", 3725, 4000)])
    assert notes["text"] == "0:00:00 Opening\n0:02:00 Long part\n1:02:05 Late"


# ── The routes ──


def _no_stored_roster():
    return (
        mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "fetch_session_roster", mock.Mock(side_effect=AssertionError("no stored roster"))),
    )


def _get(path, row=None, *, stored=True):
    row = row or synthetic.cached_row()
    status, roster = _no_stored_roster()
    understanding = synthetic.with_understanding(row) if stored else mock.patch.object(api_logic, "fetch_understanding", lambda s, o: None)
    with status, roster, understanding, synthetic.serving(row) as client:
        return client.get(path.format(id=row["session_id"]))


def test_the_transcript_route_places_chapters_on_its_speech_lines():
    body = _get("/api/sessions/{id}/record/transcript").json()
    chapters = body["chapters"]
    assert chapters["state"] == READY and [c["first"] for c in chapters["items"]] == ["U0001", "U0006", "U0028", "U0074", "U0166"]
    lines = {line["id"]: line for line in body["speech"]}
    assert all(c["t"] == lines[c["first"]]["t"] and c["r"] == lines[c["first"]]["r"] for c in chapters["items"])


def test_the_transcript_view_leaves_out_a_chapter_whose_line_isnt_shown():
    inputs = _inputs()
    chapters, state = chapter_list(_stored(), inputs)
    view = transcript_view(inputs, {}, [*chapters, {"title": "Ghost", "kind": "other", "first": "U9999"}], state)
    assert "Ghost" not in [c["title"] for c in view["chapters"]["items"]]
    assert transcript_view(inputs, {})["chapters"] == {"state": NONE, "items": []}


def test_the_chapters_route_serves_the_show_notes():
    response = _get("/api/sessions/{id}/record/chapters")
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["sessionId", "updatedAt", "state", "placed", "chapters", "showNotes"]
    assert body["placed"] is True and {"t", "tEnd", "audienceChat", "questions", "chatPerMinute"} <= set(body["chapters"][0])
    assert body["showNotes"]["text"].splitlines()[0] == "00:00 Welcome and agenda"
    assert body["showNotes"]["youtube"] is False, "the synthetic goodbye is three seconds long"
    empty = _get("/api/sessions/{id}/record/chapters", stored=False).json()
    assert (empty["state"], empty["chapters"], empty["showNotes"]) == (NONE, [], {"text": "", "youtube": False})


def test_another_session_looks_missing_and_a_failure_hides_its_detail(monkeypatch):
    assert _get("/api/sessions/someone-elses/record/chapters").status_code == 404
    from livestorm_app.record import understanding as module

    monkeypatch.setattr(module, "show_notes", mock.Mock(side_effect=RuntimeError("secret internal detail")))
    broken = _get("/api/sessions/{id}/record/chapters")
    assert broken.status_code == 500 and "secret internal detail" not in broken.text


def test_the_frontend_fixture_is_the_route_response():
    """`frontend/tests/fixtures/chapters-v1.json` is what the chapters route
    sends for the synthetic webinar. After a deliberate change, regenerate
    it: `.venv/bin/python tests/synthetic_session.py`."""
    committed = synthetic.CHAPTERS_FIXTURE.read_text(encoding="utf-8")
    assert synthetic.dump(synthetic.chapters_response()) == committed


# ── The chapters on the session clock (the Report's cover) ──


def test_each_chapter_is_placed_on_the_session_clock_with_what_the_audience_did_in_it():
    from livestorm_app.record.assemble import chapter_marks
    from livestorm_app.record.clock import event_lane, phase

    inputs = _inputs()
    chapters, _ = chapter_list(_stored(), inputs)
    marks = chapter_marks(inputs, chapters)
    assert marks["placed"] is True and len(marks["marks"]) == len(chapters)
    offset = inputs.alignment.offset_s
    first = marks["marks"][0]
    assert first["t"] == round(chapters[0]["r"] + offset, 3), "t = r + offsetS"
    assert [m["t"] for m in marks["marks"]] == sorted(m["t"] for m in marks["marks"])
    live = [
        lane
        for kind, is_team, t in inputs.events[["kind", "is_team", "t"]].itertuples(index=False, name=None)
        if phase(t, inputs.clock) == "live" and (lane := event_lane(kind, is_team)) in ("audience_chat", "questions")
    ]
    assert sum(m["audienceChat"] for m in marks["marks"]) == live.count("audience_chat"), "every live message in one chapter"
    assert sum(m["questions"] for m in marks["marks"]) == live.count("questions")
    second, third = marks["marks"][1], marks["marks"][2]
    assert second["chatPerMinute"] == round(second["audienceChat"] * 60 / (third["t"] - second["t"]), 1)
    assert first["chatPerMinute"] is None, "a 57-second welcome has no rate to compare"


def test_chapters_stay_off_the_session_clock_without_speech_on_it():
    from livestorm_app.record.assemble import chapter_marks

    inputs = _inputs()
    chapters, _ = chapter_list(_stored(), inputs)
    assert chapter_marks(inputs, []) == {"placed": False, "marks": []}
    with mock.patch("livestorm_app.record.assemble.alignment_allows", lambda alignment, resolution: False):
        assert chapter_marks(inputs, chapters) == {"placed": False, "marks": []}
