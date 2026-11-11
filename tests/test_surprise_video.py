"""Surprise me's video (`ai/surprise_timing.py`, `ai/surprise_director.py`,
`ai/surprise_images.py`, `video/`, `render_worker.py`, the show job's video
steps in `api_logic`, the `surprise-show/video.mp4` route and the video's
tables).

Never calls OpenAI or Claude: `requests.post`, `llm_client.generate_json` or
the module boundaries are replaced. Never runs Chrome or ffmpeg: the
renderer's subprocess runner is faked."""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
import requests
from test_access_control import (  # noqa: F401  (fixtures)
    MARKER,
    SESSION_ID,
    _call,
    backend,
    client,
)

from livestorm_app import api_logic, db, llm_client, oauth_client, render_worker
from livestorm_app.ai import jobs
from livestorm_app.ai import surprise_director as director
from livestorm_app.ai import surprise_images as images
from livestorm_app.ai import surprise_timing as timing
from livestorm_app.ai.schemas import is_registered
from livestorm_app.video import film, keys, mix, punch, render

LINES = [
    {"beat": 1, "say": "Okay, so the chat went quiet.", "tone": "deadpan", "punchline": False},
    {"beat": 1, "say": "Then 96% stayed — for pricing.", "tone": "building", "punchline": False},
    {"beat": 2, "say": "Nobody asked why.", "tone": "punchline", "punchline": True},
]
CLIPS = [{"key": "a" * 64, "duration_s": 2.0}, {"key": "b" * 64, "duration_s": 2.5}, {"key": "c" * 64, "duration_s": 1.2}]


def _words(text, start=0.1, step=0.3):
    return [{"word": word.strip(",.—"), "start": start + n * step, "end": start + n * step + 0.25} for n, word in enumerate(text.split())]


# ── The timing ──


def test_a_line_is_cut_at_every_sentence_end_comma_dash_colon_and_ellipsis():
    assert timing.clauses("Okay, so... the chat went quiet: nobody — not one — typed. Wow!") == [
        "Okay,", "so...", "the chat went quiet:", "nobody", "not one", "typed.", "Wow!",
    ]
    assert timing.clauses("Just one line") == ["Just one line"]


def test_the_clips_follow_the_intro_with_the_rehearsals_gaps_and_the_outro_closes():
    tl = timing.build_timeline(LINES, CLIPS, [None, None, None])
    starts = [placed["start"] for placed in tl["voice"]]
    assert starts[0] == timing.INTRO_S == 4.0
    assert starts[1] == pytest.approx(4.0 + 2.0 + 0.22), "0.22 s within a beat"
    window = starts[1] + 2.5 + 0.75
    assert starts[2] == pytest.approx(window + 0.2), "0.75 s held, then the picture lands, then the punchline is said"
    assert tl["voice_end"] == pytest.approx(starts[2] + 1.2)
    assert tl["end_card"] == pytest.approx(window + timing.PUNCH_WINDOW_S), "the punchline's window closes the show"
    assert tl["total"] == pytest.approx(tl["end_card"] + timing.OUTRO_S) and timing.OUTRO_S == 6.0
    assert tl["timed"] == 0 and tl["dropped"] == [] and [v["line"] for v in tl["voice"]] == [0, 1, 2]


def test_a_line_after_the_last_punchline_ends_the_show():
    lines = [*LINES, {"beat": 3, "say": "Proud of you.", "tone": "warm", "punchline": False}]
    clips = [*CLIPS, {"key": "d" * 64, "duration_s": 1.0}]
    tl = timing.build_timeline(lines, clips, [None] * 4)
    punch = tl["punches"][0]
    assert tl["voice"][3]["start"] == pytest.approx(punch["end"] + timing.GAP_AFTER_PUNCH)
    assert tl["shots"][-1]["start"] == punch["end"], "the next shot takes the screen when the window ends"
    assert tl["end_card"] == pytest.approx(tl["voice"][3]["start"] + 1.0 + 0.5)


def test_a_new_beat_waits_0_38_s():
    lines = [{"beat": 1, "say": "One.", "tone": "deadpan"}, {"beat": 2, "say": "Two.", "tone": "aside"}]
    tl = timing.build_timeline(lines, CLIPS[:2], [None, None])
    assert tl["voice"][1]["start"] == pytest.approx(4.0 + 2.0 + 0.38)


def test_each_clause_starts_when_its_first_word_is_heard():
    tl = timing.build_timeline(LINES[:1], CLIPS[:1], [_words("Okay so the chat went quiet")])
    shots = tl["shots"]
    assert [shot["text"] for shot in shots] == ["Okay,", "so the chat went quiet."]
    assert shots[0]["start"] == 4.0
    assert shots[1]["start"] == pytest.approx(4.0 + 0.4 - timing.EARLY_S), "'so' is heard 0.4 s in"
    assert shots[0]["end"] == shots[1]["start"] and shots[-1]["end"] == pytest.approx(tl["voice_end"] + 0.5)
    assert [w["w"] for w in shots[1]["spoken"]][:2] == ["so", "the"]
    assert tl["timed"] == 1


def test_without_words_the_clauses_are_spread_by_their_length():
    tl = timing.build_timeline(LINES[1:2], CLIPS[1:2], [None])
    shots = tl["shots"]
    assert [shot["text"] for shot in shots] == ["Then 96% stayed", "for pricing."]
    assert shots[1]["start"] == pytest.approx(4.0 + 2.5 * len("Then 96% stayed") / len("Then 96% stayedfor pricing.") - timing.EARLY_S, abs=0.01)
    assert shots[0]["spoken"] == [] and tl["timed"] == 0


def test_a_clause_shorter_than_0_3_s_joins_the_next_one():
    words = [{"word": "Okay", "start": 0.0, "end": 0.1}, {"word": "so", "start": 0.15, "end": 0.2},
             {"word": "the", "start": 0.6, "end": 0.7}, {"word": "chat", "start": 0.8, "end": 0.9}]
    line = {"beat": 1, "say": "Okay, so, the chat.", "tone": "deadpan"}
    tl = timing.build_timeline([line], CLIPS[:1], [words])
    assert [shot["text"] for shot in tl["shots"]] == ["Okay, so,", "the chat."], "'Okay,' lasts 0.09 s: it joins 'so,'"
    assert all(shot["dur"] >= timing.MIN_CLAUSE_S for shot in tl["shots"][:-1])


def test_the_clause_before_a_punchline_freezes_and_the_punchline_gets_its_window():
    tl = timing.build_timeline(LINES, CLIPS, [None, None, None])
    shots, (punch,) = tl["shots"], tl["punches"]
    assert all(shot["line"] != 2 for shot in shots), "a punchline is never cut into clauses"
    assert shots[-1]["hold_after"] == 0.75 and shots[-1]["end"] == punch["start"]
    assert sum(1 for shot in shots if shot.get("hold_after")) == 1
    assert [shot["id"] for shot in shots] == list(range(len(shots)))
    assert (punch["n"], punch["line"], punch["text"], punch["dur"]) == (0, 2, "Nobody asked why.", 2.5)
    assert tl["segments"] == [
        {"kind": "kinetic", "start": 4.0, "end": punch["start"]},
        {"kind": "punch", "n": 0, "start": punch["start"], "end": punch["end"]},
    ]


def test_a_long_punchline_keeps_its_picture_until_it_is_said():
    clips = [*CLIPS[:2], {"key": "c" * 64, "duration_s": 3.4}]
    punch = timing.build_timeline(LINES, clips, [None] * 3)["punches"][0]
    assert punch["dur"] == pytest.approx(timing.PUNCH_LEAD_S + 3.4 + timing.PUNCH_TAIL_S)


def test_two_punchlines_in_a_row_hold_on_the_first_picture_and_a_first_one_has_no_hold():
    lines = [{"beat": 1, "say": "Hello is not a question.", "tone": "punchline", "punchline": True},
             {"beat": 2, "say": "Nobody asked why.", "tone": "punchline", "punchline": True}]
    tl = timing.build_timeline(lines, CLIPS[:2], [None, None])
    first, second = tl["punches"]
    assert first["start"] == 4.0, "nothing before it to freeze"
    assert first["end"] == second["start"] == pytest.approx(4.0 + max(2.5, 0.2 + 2.0 + 0.3) + 0.75)
    assert tl["shots"] == [] and [seg["kind"] for seg in tl["segments"]] == ["punch", "punch"]


def _long_script(lines_per_beat=4, beats=6, seconds=3.0):
    lines, clips = [], []
    for beat in range(1, beats + 1):
        for k in range(lines_per_beat):
            last = k == lines_per_beat - 1 and 1 < beat < beats
            lines.append({"beat": beat, "say": f"Line {beat} {k}.", "tone": "punchline" if last else "deadpan", "punchline": last})
            clips.append({"key": f"{len(clips):064x}", "duration_s": seconds})
    return lines, clips


def test_past_the_ceiling_whole_lines_are_dropped_in_a_fixed_order_never_a_punchline():
    lines, clips = _long_script()
    every = list(range(len(lines)))
    assert timing._layout(lines, clips, [None] * len(lines), every)["total"] > timing.MAX_TOTAL_S
    tl = timing.build_timeline(lines, clips, [None] * len(lines))
    assert tl["total"] <= timing.MAX_TOTAL_S == 75.0
    dropped = tl["dropped"]
    assert dropped and not any(lines[i]["punchline"] for i in dropped)
    assert 0 not in dropped and len(lines) - 1 not in dropped, "the first and last lines stay"
    assert dropped == sorted(timing.drop_order(lines)[: len(dropped)])
    heard = {placed["line"] for placed in tl["voice"]}
    assert heard.isdisjoint(dropped) and len(tl["punches"]) == 4
    assert all(shot["line"] not in dropped for shot in tl["shots"])


def test_the_drop_order_takes_the_middle_beats_from_the_end_then_the_last_then_the_first_setups_last():
    lines, _ = _long_script()
    order = timing.drop_order(lines)
    # Beats 2 to 5 are the middle: lines 4 to 19; 7, 11, 15 and 19 are punchlines; 6, 10, 14, 18 their setups.
    assert order[:8] == [17, 16, 13, 12, 9, 8, 5, 4]
    assert order[8:12] == [18, 14, 10, 6], "a punchline's setup goes last in its group"
    assert order[12:] == [22, 21, 20, 3, 2, 1]
    assert timing.drop_order(lines[:2]) == []


def test_the_timeline_key_follows_the_clips_the_beats_the_tones_and_the_punchlines():
    base = timing.timeline_key(LINES, [c["key"] for c in CLIPS])
    assert base == timing.timeline_key(LINES, [c["key"] for c in CLIPS])
    assert base != timing.timeline_key(LINES, ["d" * 64] + [c["key"] for c in CLIPS[1:]])
    assert base != timing.timeline_key([{**LINES[0], "tone": "aside"}, *LINES[1:]], [c["key"] for c in CLIPS])
    assert base != timing.timeline_key([*LINES[:2], {**LINES[2], "punchline": False}], [c["key"] for c in CLIPS])


def _answer(status, body=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body or {}).encode()
    return response


def test_whisper_gets_only_the_clip_with_a_timeout(monkeypatch):
    sent = {}

    def post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return _answer(200, {"words": [{"word": "Okay", "start": 0.1, "end": 0.4}]})

    monkeypatch.setattr(timing.requests, "post", post)
    assert timing.transcribe_words("sk-test", b"mp3") == [{"word": "Okay", "start": 0.1, "end": 0.4}]
    assert sent["url"] == timing.OPENAI_TRANSCRIPTION_URL and sent["timeout"] == timing.WHISPER_TIMEOUT
    assert sent["data"]["model"] == "whisper-1" and sent["data"]["timestamp_granularities[]"] == "word"
    assert sent["files"]["file"][1] == b"mp3"


def test_whisper_retries_only_when_nothing_was_transcribed(monkeypatch):
    answers = [_answer(503), _answer(200, {"words": []})]
    monkeypatch.setattr(timing.requests, "post", lambda url, **kwargs: answers.pop(0))
    assert timing.transcribe_words("sk", b"mp3", sleep=lambda s: None) == []
    calls = []

    def timeout(url, **kwargs):
        calls.append(1)
        raise requests.exceptions.ReadTimeout("slow")

    monkeypatch.setattr(timing.requests, "post", timeout)
    with pytest.raises(timing.WordsFailed, match="read timeout"):
        timing.transcribe_words("sk", b"mp3", sleep=lambda s: None)
    assert calls == [1], "a read timeout may have been paid for: never again"
    monkeypatch.setattr(timing.requests, "post", lambda url, **kwargs: _answer(401, {"error": "sk-secret"}))
    with pytest.raises(timing.WordsFailed) as failed:
        timing.transcribe_words("sk-secret", b"mp3")
    assert "sk-secret" not in str(failed.value) and "401" in str(failed.value)


def test_whisper_without_a_key_makes_no_call(monkeypatch):
    monkeypatch.setattr(timing.requests, "post", mock.Mock(side_effect=AssertionError("no call")))
    with pytest.raises(timing.WordsFailed):
        timing.transcribe_words("", b"mp3")


# ── The director ──

CLAUSES = [
    {"id": 0, "line": 0, "k": 0, "tone": "deadpan", "dur": 1.2, "text": "The chat went quiet at minute 12."},
    {"id": 1, "line": 0, "k": 1, "tone": "deadpan", "dur": 1.0, "text": "Then 96% stayed for pricing."},
    {"id": 2, "line": 1, "k": 0, "tone": "punchline", "dur": 0.9, "text": "Nobody asked why."},
]
ROOM = [{"id": "R1", "kind": "chat", "t": 700.0, "text": "is it free?"}, {"id": "R2", "kind": "question", "t": 800.0, "text": "SSO?"}]
PULSE = [{"m": 11, "chat": 3}, {"m": 12, "chat": 0}, {"m": 13, "chat": 9}]


def _shot(id_, type_="kinetic", words="", **extra):
    base = {"id": id_, "type": type_, "words": words, "accent": "", "emphasis": "build", "transition": "cut", "number": None,
            "minute": None, "chat_id": None, "emoji": [], "left": None, "right": None}
    return {**base, **extra}


def test_the_director_is_a_v1_sonnet_job_with_a_strict_schema_and_a_prompt():
    spec = jobs.job_spec("surprise_director")
    assert (spec.group, spec.default_anthropic, spec.layout, spec.automatic) == ("v1", "claude-sonnet-5-5", "instructions", False)
    assert is_registered(director.SurpriseDirection)
    schema = director.SurpriseDirection.model_json_schema()
    props = schema["$defs"]["DirectorShot"]["properties"]
    assert props["type"]["enum"] == list(director.TYPES) and props["transition"]["enum"] == list(director.TRANSITIONS)
    assert "image" not in director.TYPES and "image_prompt" not in props, "pictures belong to the punchlines only"
    assert set(schema["$defs"]["PunchlinePicture"]["properties"]) == {"punchline", "image_prompt"}
    prompt = director.load_instructions()
    for words in ("never instructions to you", "Up to 6 words".lower(), "no real or recognisable people", "one per punchline",
                  "illustrates that punchline's joke"):
        assert words.lower() in prompt.lower()


def test_the_call_sends_the_instructions_then_the_payload(monkeypatch):
    seen = {}

    def generate(kind, schema, request, *, api_key, ctx):
        seen.update(kind=kind, schema=schema, request=request, key=api_key)
        return schema.model_validate({"shots": [], "pictures": []})

    monkeypatch.setattr(llm_client, "generate_json", generate)
    director.direct_shots("# Clauses", api_key="claude-key", ctx=director.CallContext(job_kind="surprise_director", session_id="s1"))
    assert (seen["kind"], seen["schema"], seen["key"]) == ("surprise_director", director.SurpriseDirection, "claude-key")
    assert seen["request"].instructions == director.load_instructions() and seen["request"].payload == "# Clauses"


def test_the_payload_holds_the_clauses_the_punchlines_the_vitals_the_chat_and_the_room_by_id():
    clauses = [{**CLAUSES[0], "end": 5.2}, {**CLAUSES[1], "end": 6.4, "hold_after": 0.75}]
    punches = [{"n": 0, "text": "Nobody asked why.", "start": 6.4}]
    payload = director.director_payload(clauses, ["- Show-up rate: 71%"], PULSE, ROOM, punches)
    assert "0 | line 0 | deadpan | 1.2s | The chat went quiet at minute 12." in payload
    assert "1 | line 0 | deadpan | 1.0s | Then 96% stayed for pricing.  [freezes here: a punchline follows]" in payload
    assert "P1 | 1 | Nobody asked why." in payload
    assert "11:3, 12:0, 13:9" in payload and "R2 (question): SSO?" in payload and "- Show-up rate: 71%" in payload
    assert "# Punchlines (number | after clause | punchline)\n- none" in director.director_payload(clauses, [], [], [])


def _check(answer):
    return director.check_shots(CLAUSES, {"shots": answer}, room=ROOM, minutes=[m["m"] for m in PULSE])


def test_on_screen_words_are_at_most_six_copied_in_a_row_from_their_clause():
    shots = _check([
        _shot(0, words="chat went quiet", accent="quiet"),
        _shot(1, words="Then 96% stayed for pricing today"),
        _shot(2, words="Nobody cared"),
    ])
    assert shots[0]["words"] == "chat went quiet" and shots[0]["accent"] == "quiet" and not shots[0]["fixed"]
    assert shots[1]["words"] == "Then 96% stayed for pricing." and shots[1]["fixed"], "a word not in the clause: the clause's own"
    assert shots[2]["words"] == "Nobody asked why." and shots[2]["fixed"]


def test_numbers_come_only_from_their_clause():
    shots = _check([
        _shot(0, "count", "minute 12", number="12"),
        _shot(1, "ring", "96% stayed", number="97%"),
        _shot(2, "count", "Nobody", number="3"),
    ])
    assert shots[0]["type"] == "count" and shots[0]["number"] == "12"
    assert shots[1]["type"] == "kinetic" and shots[1]["number"] is None, "a percentage the clause doesn't say"
    assert shots[2]["type"] == "kinetic"
    assert _check([_shot(0, "ring", "minute 12", number="12")])[0]["type"] == "kinetic", "a ring needs a percentage"


def test_chat_lines_come_only_from_our_list_by_id_once():
    shots = _check([_shot(0, "chat", "chat went quiet", chat_id="R1"), _shot(1, "chat", "96% stayed", chat_id="R1"),
                    _shot(2, "chat", "Nobody asked why.", chat_id="R9")])
    assert shots[0]["chat"] == {"id": "R1", "kind": "chat", "text": "is it free?"}, "our text, never the model's"
    assert shots[1]["type"] == "kinetic", "a line used twice"
    assert shots[2]["type"] == "kinetic", "an id we never gave"


def test_a_minute_comes_only_from_the_pulse_and_a_pulse_needs_one():
    shots = _check([_shot(0, "pulse", "chat went quiet", minute=12), _shot(1, "pulse", "96% stayed", minute=99)])
    assert shots[0]["minute"] == 12 and shots[1]["type"] == "pulse" and shots[1]["minute"] is None
    no_pulse = director.check_shots(CLAUSES[:1], {"shots": [_shot(0, "pulse", "chat went quiet", minute=12)]}, room=ROOM, minutes=[])
    assert no_pulse[0]["type"] == "kinetic"


def test_a_split_copies_both_sides_from_the_clause():
    shots = _check([_shot(1, "split", "96% stayed", left="96% stayed", right="for pricing"), _shot(2, "split", "Nobody", left="Nobody", right="everyone")])
    by_id = {shot["id"]: shot for shot in shots}
    assert by_id[1]["type"] == "split" and (by_id[1]["left"], by_id[1]["right"]) == ("96% stayed", "for pricing")
    assert by_id[2]["type"] == "kinetic"


def test_one_picture_per_punchline_by_its_number_and_never_a_name_in_its_prompt():
    punches = [{"n": n} for n in range(5)]
    answer = {"pictures": [
        {"punchline": 2, "image_prompt": "a sleeping microphone"},
        {"punchline": 1, "image_prompt": "a tumbleweed in a chat window"},
        {"punchline": 1, "image_prompt": "a second try for the same punchline"},
        {"punchline": 3, "image_prompt": "Sarah holding a sign"},
        {"punchline": 9, "image_prompt": "a punchline we never gave"},
        {"punchline": True, "image_prompt": "not a number"},
    ]}
    pictures = director.check_pictures(punches, answer, banned={"sarah"})
    assert pictures == [
        {"n": 0, "image_prompt": "a tumbleweed in a chat window"},
        {"n": 1, "image_prompt": "a sleeping microphone"},
        {"n": 2, "image_prompt": None},
        {"n": 3, "image_prompt": None},
    ], "the first prompt for a number counts, a name or none gives the deadpan card, four at most"
    assert director.check_shots(CLAUSES[:1], {"shots": [_shot(0, "image", "chat went quiet")]}, room=ROOM, minutes=[])[0]["type"] == "kinetic"
    assert not director.image_prompt_ok("a cat at https://example.com", set())
    assert not director.image_prompt_ok("a cat emailing bob@example.com", set())
    assert director.image_prompt_ok("a tumbleweed rolling across an empty chat window", {"sarah"})


def test_banned_words_are_the_capitalised_words_and_the_speakers_names():
    banned = director.banned_words(["The demo of Acme Cloud was great. Nobody asked.", "is Zapier free?"], ["Jane Doe"])
    assert {"acme", "cloud", "zapier", "jane", "doe"} <= banned
    assert "the" not in banned and "nobody" not in banned


def test_emoji_carry_no_letters_and_a_skipped_clause_gets_the_rules_shot():
    shots = _check([_shot(0, "emoji", "chat went quiet", emoji=["🦗", "SSO", "🤫", "😶", "🙃"])])
    assert shots[0]["emoji"] == ["🦗", "🤫", "😶"]
    assert shots[1]["type"] == "ring" and shots[1]["number"] == "96%" and shots[1]["fixed"], "the rules for a clause the model skipped"


def test_the_rules_shots_fill_rings_count_numbers_draw_the_pulse_and_stamp_punchlines():
    shots = director.fallback_shots(CLAUSES, [12])
    assert [shot["type"] for shot in shots] == ["count", "ring", "stamp"]
    assert shots[0]["number"] == "12" and shots[1]["number"] == "96%" and shots[2]["emphasis"] == "punchline"
    quiet = director.fallback_shots([{**CLAUSES[0], "text": "The chat went quiet."}], [12])
    assert quiet[0]["type"] == "pulse"
    assert all(director.verbatim(shot["words"], clause["text"]) for shot, clause in zip(shots, CLAUSES, strict=True))


# ── The pictures ──


def test_a_picture_sends_the_style_and_the_prompt_only_with_a_timeout(monkeypatch):
    from PIL import Image

    png = io.BytesIO()
    Image.new("RGB", (1536, 1024), (10, 20, 48)).save(png, format="PNG")
    sent = {}

    def post(url, **kwargs):
        sent.update(url=url, **kwargs)
        import base64

        return _answer(200, {"data": [{"b64_json": base64.b64encode(png.getvalue()).decode()}]})

    monkeypatch.setattr(images.requests, "post", post)
    jpeg = images.generate_image("sk-test", "a tumbleweed in a chat window")
    assert sent["url"] == images.OPENAI_IMAGES_URL and sent["timeout"] == images.IMAGE_TIMEOUT
    assert sent["json"] == {"model": "gpt-image-2", "prompt": images.STYLE + "a tumbleweed in a chat window",
                            "size": "1536x1024", "quality": "medium", "n": 1}
    with Image.open(io.BytesIO(jpeg)) as picture:
        assert picture.format == "JPEG" and picture.size == (1280, 853)


def test_a_failed_picture_is_retried_only_when_nothing_was_made(monkeypatch):
    calls = []
    monkeypatch.setattr(images.requests, "post", lambda url, **kwargs: calls.append(1) or _answer(400, {"error": "sk-x"}))
    with pytest.raises(images.ImageFailed, match="HTTP 400"):
        images.generate_image("sk-x", "a cat", sleep=lambda s: None)
    assert calls == [1]
    monkeypatch.setattr(images.requests, "post", mock.Mock(side_effect=requests.exceptions.ReadTimeout()))
    with pytest.raises(images.ImageFailed, match="read timeout"):
        images.generate_image("sk-x", "a cat")
    with pytest.raises(images.ImageFailed, match="no OpenAI key"):
        images.generate_image("", "a cat")


# ── The show job's video steps ──


class Store:
    def __init__(self):
        self.timelines, self.shotlists, self.images, self.saved = {}, {}, {}, []


@pytest.fixture
def steps(monkeypatch):
    store = Store()
    clip_bytes = {clip["key"]: b"mp3-" + clip["key"][:1].encode() for clip in CLIPS}
    monkeypatch.setattr(api_logic, "fetch_surprise_timeline", lambda sid, key, *, organization_id: (
        {"key": key, "body": store.timelines[key]} if key in store.timelines else None))
    monkeypatch.setattr(api_logic, "save_surprise_timeline", lambda sid, key, body: store.timelines.__setitem__(key, body))
    monkeypatch.setattr(api_logic, "fetch_surprise_shotlist", lambda sid, key, *, organization_id: (
        {"key": key, "body": store.shotlists[key]} if key in store.shotlists else None))
    monkeypatch.setattr(api_logic, "save_surprise_shotlist", lambda sid, key, body: store.shotlists.__setitem__(key, body))
    monkeypatch.setattr(api_logic, "fetch_surprise_images", lambda sid, wanted, *, organization_id: {k: store.images[k] for k in wanted if k in store.images})
    monkeypatch.setattr(api_logic, "save_surprise_image", lambda sid, key, data, mime: store.images.__setitem__(key, data))
    monkeypatch.setattr(api_logic, "fetch_surprise_voice_clips", lambda sid, wanted, *, organization_id: {
        clip["key"]: {"duration_s": clip["duration_s"], "size": 9} for clip in CLIPS if clip["key"] in wanted})
    monkeypatch.setattr(api_logic, "fetch_surprise_voice_clip", lambda sid, key, *, organization_id: {"bytes": clip_bytes[key], "mime": "audio/mpeg"})
    monkeypatch.setattr(api_logic, "_video_facts", lambda sid, take: {
        "room": ROOM, "vitals": ["- Show-up rate: 71%"], "pulse": PULSE, "banned": {"acme"},
        "redactor": mock.Mock(leaks=lambda text: ["a name"] if "Sarah" in text else [])})
    return store


ROW = {"organization_id": "org-a"}
KEYS = [clip["key"] for clip in CLIPS]


def test_the_timeline_reads_each_clips_words_once_and_is_kept(steps, monkeypatch):
    heard = []
    monkeypatch.setattr(api_logic.timing, "transcribe_words", lambda key, audio: heard.append(audio) or _words("Okay so the chat went quiet"))
    key, tl, how = api_logic._make_surprise_timeline("sk-test", SESSION_ID, LINES, KEYS)
    assert how == "made" and sorted(heard) == sorted([b"mp3-a", b"mp3-b", b"mp3-c"]) and tl["timed"] == 3
    assert steps.timelines[key] == tl
    monkeypatch.setattr(api_logic.timing, "transcribe_words", mock.Mock(side_effect=AssertionError("no call")))
    assert api_logic._make_surprise_timeline("sk-test", SESSION_ID, LINES, KEYS)[2] == "kept"


def test_a_clip_whisper_cannot_read_is_timed_by_estimate_and_never_fails_the_show(steps, monkeypatch, caplog):
    monkeypatch.setattr(api_logic.timing, "transcribe_words", mock.Mock(side_effect=timing.WordsFailed("HTTP 500")))
    _, tl, how = api_logic._make_surprise_timeline("sk-test", SESSION_ID, LINES, KEYS)
    assert how == "made" and tl["timed"] == 0 and tl["shots"]
    assert "sk-test" not in caplog.text
    steps.timelines.clear()
    monkeypatch.setattr(api_logic.timing, "transcribe_words", mock.Mock(side_effect=AssertionError("no key, no call")))
    assert api_logic._make_surprise_timeline("", SESSION_ID, LINES, KEYS)[1]["timed"] == 0


def _timeline():
    return timing.build_timeline(LINES, CLIPS, [None, None, None])


def test_the_shot_list_is_the_directors_checked_and_saved_before_the_pictures(steps, monkeypatch):
    tl = _timeline()
    shots = [_shot(shot["id"], words=shot["text"].split()[0]) for shot in tl["shots"]]
    sent = {}

    def direct(payload, *, api_key, ctx):
        sent.update(payload=payload, ctx=ctx)
        return director.SurpriseDirection.model_validate({"shots": shots, "pictures": [
            {"punchline": 1, "image_prompt": "Sarah waving at a laptop"}]})

    monkeypatch.setattr(api_logic.director, "direct_shots", direct)
    key, shotlist, how = api_logic._make_surprise_shotlist("claude-key", SESSION_ID, ROW, "take", "tl-key", tl)
    assert how == "made" and shotlist["stage"] == "directed" and shotlist["source"] == "model"
    assert shotlist["pulse"] == PULSE and shotlist["timeline_key"] == "tl-key" and steps.shotlists[key] == shotlist
    assert len(shotlist["shots"]) == len(tl["shots"])
    assert shotlist["punches"] == [{"n": 0, "image_prompt": None}], "a picture prompt with a name in it is never sent"
    assert sent["ctx"].job_kind == "surprise_director" and sent["ctx"].job_id == f"surprise_show:{SESSION_ID}"
    assert "R1 (chat): is it free?" in sent["payload"]
    assert f"P1 | {tl['shots'][-1]['id']} | Nobody asked why." in sent["payload"], "after the clause that freezes"
    steps.shotlists.clear()
    monkeypatch.setattr(api_logic.director, "direct_shots", lambda payload, **kw: director.SurpriseDirection.model_validate(
        {"shots": shots, "pictures": [{"punchline": 1, "image_prompt": "a tumbleweed in a chat window"}]}))
    _, shotlist, _ = api_logic._make_surprise_shotlist("claude-key", SESSION_ID, ROW, "take", "tl-key", tl)
    assert shotlist["punches"] == [{"n": 0, "image_prompt": "a tumbleweed in a chat window"}]
    monkeypatch.setattr(api_logic.director, "direct_shots", mock.Mock(side_effect=AssertionError("kept")))
    assert api_logic._make_surprise_shotlist("claude-key", SESSION_ID, ROW, "take", "tl-key", tl)[2] == "kept"


@pytest.mark.parametrize("failure", [llm_client.LLMRefused("refused"), RuntimeError("boom"), llm_client.LLMProviderError("down")])
def test_a_bad_director_answer_never_fails_the_show(steps, monkeypatch, failure):
    monkeypatch.setattr(api_logic.director, "direct_shots", mock.Mock(side_effect=failure))
    _, shotlist, how = api_logic._make_surprise_shotlist("claude-key", SESSION_ID, ROW, "take", "tl-key", _timeline())
    assert how == "fallback" and shotlist["source"] == "rules" and len(shotlist["shots"]) == len(_timeline()["shots"])
    assert shotlist["punches"] == [{"n": 0, "image_prompt": None}], "the rules draw no picture: the deadpan card"


def test_without_a_claude_key_the_rules_direct_with_no_call(steps, monkeypatch):
    monkeypatch.setattr(api_logic.director, "direct_shots", mock.Mock(side_effect=AssertionError("no call")))
    assert api_logic._make_surprise_shotlist("", SESSION_ID, ROW, "take", "tl-key", _timeline())[2] == "fallback"


def _directed(*prompts):
    punches = [{"n": n, "image_prompt": prompt} for n, prompt in enumerate(prompts)]
    return {"version": 2, "stage": "directed", "shots": [{"id": 0, "type": "kinetic", "words": "y"}], "pulse": [], "punches": punches}


def test_one_picture_per_punchline_made_at_once_saved_whole_and_a_failed_one_keeps_the_deadpan_card(steps, monkeypatch):
    made = []

    def generate(key, prompt):
        made.append(prompt)
        if "broken" in prompt:
            raise images.ImageFailed("HTTP 500")
        return b"jpeg:" + prompt.encode()

    monkeypatch.setattr(api_logic.show_images, "generate_image", generate)
    directed = _directed("a cat", "a broken dog", None, "a fish", "a fifth one")
    final, count = api_logic._make_surprise_pictures("sk-test", SESSION_ID, "sh-key", directed)
    assert count == 2 and sorted(made) == ["a broken dog", "a cat", "a fish"], "four punchlines at most, none without a prompt"
    assert final["stage"] == "final" and steps.shotlists["sh-key"] == final
    assert [entry["image"] for entry in final["punches"]] == [images.image_key("a cat"), None, None, images.image_key("a fish")]
    assert steps.images[images.image_key("a cat")] == b"jpeg:a cat"
    assert api_logic._show_image_keys(final) == [images.image_key("a cat"), images.image_key("a fish")]
    made.clear()
    again, _ = api_logic._make_surprise_pictures("sk-test", SESSION_ID, "sh-key", _directed("a cat"))
    assert made == [] and again["punches"][0]["image"] == images.image_key("a cat"), "a saved picture is never made again"


def test_without_an_openai_key_every_punchline_keeps_the_deadpan_card(steps, monkeypatch):
    monkeypatch.setattr(api_logic.show_images.requests, "post", mock.Mock(side_effect=AssertionError("no call")))
    final, count = api_logic._make_surprise_pictures("", SESSION_ID, "sh-key", _directed("a cat"))
    assert count == 0 and final["punches"][0]["image"] is None


def test_the_show_job_queues_no_render_when_the_video_is_already_made(monkeypatch):
    from test_surprise_voice import KEYS as SHOW_KEYS, run_env  # noqa: F401

    queued = []
    row = {"organization_id": "org-a", "has_transcript": True, "smart_recap_bundle": {"surprise": "# T\nA line. A punch."}}
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "fetch_surprise_enabled", lambda org: True)
    monkeypatch.setattr(api_logic, "_make_surprise_script", lambda *a: ([{"beat": 1, "say": "Hi.", "tone": "deadpan"}], "kept"))
    monkeypatch.setattr(api_logic, "_record_lines", lambda sid, lines, settings, keys_, progress: (["a" * 64], 0))
    monkeypatch.setattr(api_logic, "prune_surprise_voice_clips", lambda sid, keep: 0)
    monkeypatch.setattr(api_logic, "_make_surprise_timeline", lambda *a: ("tl", {"timed": 1}, "kept"))
    monkeypatch.setattr(api_logic, "_make_surprise_shotlist", lambda *a: ("sh", {"stage": "final", "shots": []}, "kept"))
    monkeypatch.setattr(api_logic, "_make_surprise_pictures", lambda *a: ({"stage": "final", "shots": []}, 0))
    monkeypatch.setattr(api_logic, "prune_surprise_images", lambda sid, keep: 0)
    monkeypatch.setattr(api_logic, "fetch_surprise_video_meta", lambda sid, *, organization_id: {"video_key": keys.video_key("tl", "sh", [])})
    monkeypatch.setattr(api_logic, "enqueue_unique_job_sync", lambda *a, **k: queued.append(a))
    assert api_logic.run_surprise_show(SHOW_KEYS, SESSION_ID)["video"] == "kept" and queued == []
    monkeypatch.setattr(api_logic, "enqueue_unique_job_sync", lambda *a, **k: None)
    monkeypatch.setattr(api_logic, "fetch_surprise_video_meta", lambda sid, *, organization_id: None)
    with pytest.raises(RuntimeError, match="video's queue is unavailable"):
        api_logic.run_surprise_show(SHOW_KEYS, SESSION_ID)


# ── The film and the mix ──


def _film_inputs():
    tl = _timeline()
    shots = director.fallback_shots(tl["shots"], [12])
    shots[0] = {**shots[0], "type": "chat", "chat": {"id": "R1", "kind": "chat", "text": "<script>alert(1)</script> is it free?"}}
    return tl, shots


def test_the_film_escapes_the_rooms_words_and_leaves_the_punchline_to_its_window():
    tl, shots = _film_inputs()
    page, effects = film.build_film(tl, shots, pulse=PULSE, css="body{}")
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "Nobody" not in page and "NOBODY" not in page, "the punchline is drawn in its window, not by Chrome"
    assert 'data-width="1280" data-height="720"' in page and "window.__timelines" in page
    files = {event["file"] for event in effects}
    assert {"jingle-bass-meant-jazz-in.flac", "jingle-bass-meant-jazz-out.flac", "dun.flac"} <= files
    assert all((render.AUDIO_DIR / name).is_file() for name in files), "every effect is a committed file"
    assert effects == sorted(effects, key=lambda event: event["t"])


def test_the_page_holds_the_kinetic_parts_alone_each_on_a_keyframe():
    lines = [*LINES, {"beat": 3, "say": "Proud of you, room.", "tone": "warm", "punchline": False}]
    tl = timing.build_timeline(lines, [*CLIPS, {"key": "d" * 64, "duration_s": 1.5}], [None] * 4)
    shots = director.fallback_shots(tl["shots"], [12])
    plan = film.layout(tl)
    kinds = [seg["kind"] for seg in plan["segments"]]
    assert kinds == ["kinetic", "punch", "kinetic"]
    assert plan["segments"][0]["f0"] == 0 and plan["segments"][-1]["f1"] == plan["middle_frames"]
    assert all(a["f1"] == b["f0"] for a, b in zip(plan["segments"][:-1], plan["segments"][1:], strict=True)), "end to end, on whole frames"
    first, second = plan["segments"][0], plan["segments"][2]
    assert first["c0"] == 0 and second["c0"] % keys.KEYFRAME_FRAMES == 0 and second["c0"] >= first["frames"]
    assert second["c0"] - first["frames"] < keys.KEYFRAME_FRAMES, "only the frames up to the next keyframe are thrown away"
    assert plan["page_frames"] == second["c0"] + second["frames"]
    page, effects = film.build_film(tl, shots, pulse=PULSE, css="")
    assert f'data-composition-id="main" data-start="0" data-duration="{plan["page_frames"] / keys.FPS:.3f}"' in page
    after = next(shot for shot in tl["shots"] if shot["start"] == tl["punches"][0]["end"])
    assert f'id="s{after["id"]}" class="clip shot" data-start="{second["c0"] / keys.FPS:.3f}"' in page, \
        "a shot after a punchline starts at its part's place on the page"
    dun = [event["t"] for event in effects if event["file"] == "dun.flac"]
    assert dun == [round(film.spans(tl)["intro"] + plan["segments"][1]["f0"] / keys.FPS + 0.09, 3)], \
        "the dun as the punchline's picture lands, on the whole video's clock"


def test_the_film_is_the_middle_alone_its_times_shifted_to_start_at_zero():
    tl, shots = _film_inputs()
    parts = film.spans(tl)
    page, effects = film.build_film(tl, shots, pulse=PULSE, css="")
    first = tl["shots"][0]
    assert f'id="s0" class="clip shot" data-start="{first["start"] - tl["intro_s"]:.3f}"' in page
    assert 'id="it-0"' not in page and film.TAGLINE not in page and 'id="so-w0"' not in page, "no intro or outro in it"
    plan = film.layout(tl)
    seg = plan["segments"][0]
    share = seg["f1"] / plan["middle_frames"]
    assert (f'tl.fromTo("#progress",{{scaleX:0.00000}},{{scaleX:{share:.5f},duration:{seg["frames"] / keys.FPS:.3f},'
            'ease:"none",immediateRender:true},0.000);') in page, "the bar runs over the middle only, the windows take it on"
    assert '"#glow"' not in page, "the glow is still: the joins can't be seen"
    jingles = {event["file"]: event["t"] for event in effects if event["file"].startswith("jingle-")}
    assert jingles["jingle-bass-meant-jazz-in.flac"] == 0.15, "the sound stays on the whole video's clock"
    assert jingles["jingle-bass-meant-jazz-out.flac"] == round(parts["join"] + film.SIGNOFF_S - 0.35, 3)


def test_the_freeze_before_a_punchline_is_set_back_at_the_window():
    tl, shots = _film_inputs()
    page, _ = film.build_film(tl, shots, pulse=PULSE, css="")
    end = film.layout(tl)["segments"][0]["frames"] / keys.FPS
    assert f'tl.set("#hold",{{"opacity": 0}},{end:.3f});' in page
    assert f'tl.set("#camera",{{"filter": "grayscale(0) brightness(1)"}},{end:.3f});' in page


def test_the_middle_moves_with_transforms_and_opacity_never_a_blur():
    tl, shots = _film_inputs()
    for n, transition in enumerate(("whip", "zoom", "blur")[: len(shots)]):
        shots[n] = {**shots[n], "transition": transition}
    page, _ = film.build_film(tl, shots, pulse=PULSE, css="")
    assert "blur(" not in page, "a CSS blur is redrawn by the CPU on every frame"


def test_a_middle_of_punchlines_alone_has_no_page():
    lines = [{"beat": 1, "say": "Hello is not a question.", "tone": "punchline", "punchline": True}]
    tl = timing.build_timeline(lines, CLIPS[:1], [None])
    page, effects = film.build_film(tl, [], pulse=[], css="")
    assert page is None and any(event["file"] == "dun.flac" for event in effects)


def test_the_parts_are_whole_frames_and_meet_at_the_end_card():
    tl = {"intro_s": 4.0, "end_card": 31.27, "total": 37.27, "outro_s": 6.0}
    parts = film.spans(tl)
    assert parts["intro"] == 4.0 and parts["outro"] == 6.0
    assert round(parts["middle"] * keys.FPS) == round((31.27 - 4.0) * keys.FPS)
    assert abs(parts["join"] - 31.27) <= 0.5 / keys.FPS and parts["total"] == parts["join"] + 6.0
    assert film.bumper_spans(tl) == [("intro", 4.0), ("outro", 6.0)]


def test_the_intro_and_outro_hold_nothing_from_a_show_and_are_the_same_every_time():
    mark = '<svg width="28" height="28"></svg>'
    intro = film.build_bumper("intro", 4.0, mark_svg=mark, css="body{}")
    outro = film.build_bumper("outro", 6.0, mark_svg=mark, css="body{}")
    assert 'id="it-0"' in intro and film.TAGLINE in intro and 'data-duration="4.000"' in intro
    assert 'id="so-w0"' in outro and ">session.<" in outro and ">StormIQ<" in outro and 'data-duration="6.000"' in outro
    assert 'width="100%" height="100%"' in intro, "the mark fills its box"
    assert "#progress" not in intro + outro and '"#glow"' not in intro + outro
    assert intro == film.build_bumper("intro", 4.0, mark_svg=mark, css="body{}")
    with pytest.raises(ValueError):
        film.build_bumper("outro", film.SIGNOFF_S, mark_svg=mark, css="")
    with pytest.raises(ValueError):
        film.build_bumper("middle", 4.0, mark_svg=mark, css="")


def test_the_film_needs_one_shot_per_clause():
    tl, shots = _film_inputs()
    with pytest.raises(ValueError):
        film.build_film(tl, shots[:-1], pulse=[], css="")


def test_an_older_picture_shot_is_drawn_as_type():
    tl, shots = _film_inputs()
    shots[1] = {**shots[1], "type": "image", "image": "k"}
    page, _ = film.build_film(tl, shots, pulse=[], css="")
    assert "<img" not in page


# ── The punchline windows (`video/punch.py`) ──


def _jpeg(size=(1280, 853), colour=(46, 230, 201)):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="JPEG")
    return out.getvalue()


def test_a_window_is_its_picture_zooming_with_the_caption_over_it():
    from PIL import Image

    frames = list(punch.frames(_jpeg(), "Nobody asked why.", 30, progress=(0.25, 0.5)))
    assert len(frames) == 30
    first, last = (Image.open(io.BytesIO(data)) for data in (frames[0], frames[-1]))
    assert first.format == "JPEG" and first.size == (keys.WIDTH, keys.HEIGHT)
    assert first.getpixel((640, 100))[1] > 180, "the picture covers the frame"
    assert last.getpixel((int(keys.WIDTH * 0.45), keys.HEIGHT - 2))[2] > 150, "the progress bar runs on"
    assert last.getpixel((int(keys.WIDTH * 0.6), keys.HEIGHT - 2))[2] < 150


def test_a_window_without_a_picture_is_the_deadpan_card():
    from PIL import Image

    for picture in (None, b"not a picture"):
        frame = Image.open(io.BytesIO(next(punch.frames(picture, "Nobody asked why.", 5))))
        assert max(frame.getpixel((20, 20))) < 20, "black around the caption"


def test_the_caption_is_upper_case_fits_and_drops_what_the_font_lacks():
    assert punch.drawable("nobody  asked 🙃 why…") == "NOBODY ASKED WHY…"
    lines, size = punch.caption_lines("And the chat went: is it free? " * 3, max_width=1160, sizes=range(150, 59, -6), max_lines=3)
    assert len(lines) <= 3 and size < 150
    short, big = punch.caption_lines("Pricing!", max_width=1160, sizes=range(150, 59, -6), max_lines=3)
    assert short == ["PRICING!"] and big == 150
    long = "Honestly, the software was the calmest person there."
    lines, size = punch.caption_lines(long, max_width=1180, sizes=range(220, 59, -6), max_lines=4, max_height=560)
    assert punch.caption_height(len(lines), size) <= 560, "a long deadpan card fits the frame's height"


def test_the_mix_places_each_clip_brings_the_voice_to_16_lufs_and_starts_fast(tmp_path):
    args = mix.mix_command("ffmpeg", tmp_path / "p.mp4", [(tmp_path / "1.mp3", 4.0), (tmp_path / "2.mp3", 6.22)],
                           [{"file": "pop1.flac", "t": 5.0, "gain": 0.35}], tmp_path, 20.0, tmp_path / "o.mp4")
    graph = args[args.index("-filter_complex") + 1]
    assert "adelay=4000|4000" in graph and "adelay=6220|6220" in graph and "loudnorm=I=-16" in graph
    assert "volume=0.350,adelay=5000|5000" in graph and "atrim=0:20.000" in graph
    assert args[args.index("-c:v") + 1] == "copy" and "+faststart" in args and args[args.index("-c:a") + 1] == "aac"
    with pytest.raises(ValueError):
        mix.mix_command("ffmpeg", tmp_path / "p.mp4", [], [], tmp_path, 1.0, tmp_path / "o.mp4")


# ── The renderer, with the CLI and ffmpeg faked ──


PROBE = {"codec_name": "h264", "profile": "High", "level": 31, "width": 1280, "height": 720, "pix_fmt": "yuv420p",
         "r_frame_rate": "24/1", "time_base": "1/12288", "extradata_hash": "SHA256:abc"}
BUMPERS = {"intro": b"INTRO", "outro": b"OUTRO"}


def _fake_runner(calls, *, fail=None, timeout=None, probes=None, frames=None):
    """Fakes the CLI, ffprobe (the same encoding for every part, or
    `probes` in turn; `frames` packets in a joined file) and ffmpeg (writes
    its last argument)."""
    probes = list(probes or [])

    def run(args, **kwargs):
        calls.append((list(args), kwargs))
        if timeout and timeout in args:
            raise subprocess.TimeoutExpired(args, 1)
        if fail and fail in " ".join(args):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="chrome crashed")
        if args[0] == "ffprobe" and "-count_packets" in args:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"streams": [{"nb_read_packets": str(frames)}]}), stderr="")
        if args[0] == "ffprobe":
            stream = probes.pop(0) if probes else PROBE
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"streams": [stream]}), stderr="")
        out = Path(args[args.index("--output") + 1]) if "--output" in args else Path(args[-1])
        if not out.is_absolute():
            out = Path(kwargs["cwd"]) / out
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 100)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    return run


@pytest.fixture
def gsap(tmp_path, monkeypatch):
    path = tmp_path / "gsap.min.js"
    path.write_text("/* gsap */")
    monkeypatch.setenv("GSAP_JS_PATH", str(path))
    monkeypatch.setenv("HYPERFRAMES_BIN", "npx -y hyperframes@0.9.34")
    monkeypatch.delenv("FFPROBE_BIN", raising=False)
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    return path


def _expected_frames(tl):
    return round(film.spans(tl)["total"] * keys.FPS)


def test_the_render_draws_the_kinetic_parts_once_the_punchlines_by_ffmpeg_joins_by_copy_then_mixes(gsap):
    tl, shots = _film_inputs()
    calls, stages = [], []
    clips = {clip["key"]: b"mp3" for clip in CLIPS}
    seen = {}

    def run(args, **kwargs):
        if "render" in args:
            project = Path(args[args.index("render") + 1])
            seen["files"] = sorted(str(p.relative_to(project)) for p in project.rglob("*") if p.is_file())
        if "concat" in args:
            work = Path(kwargs["cwd"])
            seen["list"] = (work / "parts.txt").read_text()
            seen["intro"], seen["outro"] = (work / "intro.mp4").read_bytes(), (work / "outro.mp4").read_bytes()
        if "image2pipe" in args:
            seen["stdin"] = kwargs["input"]
        return _fake_runner(calls, frames=_expected_frames(tl))(args, **kwargs)

    made = render.render_video(tl, shots, pulse=PULSE, clips=clips, images={0: _jpeg()}, bumpers=BUMPERS, run=run,
                               on_stage=stages.append, job="session_id=s1")
    parts, plan = film.spans(tl), film.layout(tl)
    assert made["size"] == len(made["mp4"]) and made["duration_s"] == round(parts["total"], 3)
    tools = [args[0] for args, _ in calls]
    assert tools == ["npx", "ffmpeg", "ffprobe", "ffprobe", "ffprobe", "ffprobe", "ffmpeg", "ffprobe", "ffmpeg"], \
        "Chrome once, the punchline, four probes, the join, its frame count, the mix"
    picture, encode, join, sound = calls[0][0], calls[1][0], calls[6][0], calls[8][0]
    assert picture[:4] == ["npx", "-y", "hyperframes@0.9.34", "render"] and picture[picture.index("--fps") + 1] == "24"
    assert picture[picture.index("--crf") + 1] == render.PICTURE_CRF and picture[picture.index("--quality") + 1] == "draft"
    assert picture[picture.index("--keyframe-interval") + 1] == keys.KEYFRAME_INTERVAL_S == "0.5"
    assert calls[0][1]["timeout"] == render.picture_timeout(plan["page_frames"] / keys.FPS)
    assert calls[0][1]["env"]["HYPERFRAMES_NO_TELEMETRY"] == "1"
    assert encode == render.encode_command("ffmpeg", Path(encode[-1])) and calls[1][1]["timeout"] == render.PUNCH_TIMEOUT_S
    assert seen["stdin"].startswith(b"\xff\xd8") and seen["stdin"].count(b"\xff\xd9") >= plan["segments"][1]["frames"], \
        "the window's frames, as JPEGs on stdin: nothing from the session on the command line"
    assert "-show_data_hash" in calls[2][0], "the parameters compared include the SPS and PPS"
    assert join[join.index("-f") + 1] == "concat" and join[join.index("-c:v") + 1] == "copy", "joined by copy, never encoded again"
    kinetic = plan["segments"][0]
    assert seen["list"] == (f"file 'intro.mp4'\nfile 'kinetic.mp4'\noutpoint {render._seconds(kinetic['frames'])}\n"
                            "file 'punch_0.mp4'\nfile 'outro.mp4'\n")
    assert "-count_packets" in calls[7][0], "the joined file's frames are counted"
    assert (seen["intro"], seen["outro"]) == (b"INTRO", b"OUTRO")
    assert sound[0] == "ffmpeg" and "+faststart" in sound and sound[sound.index("-i") + 1].endswith("picture.mp4")
    assert sound[sound.index("-t") + 1] == f"{parts['total']:.3f}" and stages == ["mixing"]
    assert {"index.html", "hyperframes.json", "assets/gsap.min.js", "assets/fonts/Sora.woff2",
            "assets/fonts/LeagueGothic.ttf", "assets/background.png"} <= set(seen["files"])


def test_the_punchline_encode_repeats_hyperframes_own():
    args = render.encode_command("ffmpeg", Path("out.mp4"))
    pairs = {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i].startswith("-")}
    assert (pairs["-f"], pairs["-vcodec"], pairs["-i"]) == ("image2pipe", "mjpeg", "-")
    assert (pairs["-preset"], pairs["-threads"], pairs["-crf"], pairs["-bf"]) == ("ultrafast", "1", render.PICTURE_CRF, "0")
    assert (pairs["-g"], pairs["-keyint_min"], pairs["-force_key_frames"]) == ("12", "12", "expr:eq(mod(n,12),0)")
    assert pairs["-x264-params"] == "aq-mode=3:colorprim=bt709:transfer=bt709:colormatrix=bt709:scenecut=0:open-gop=0:repeat-headers=1"
    assert (pairs["-maxrate"], pairs["-bufsize"], pairs["-video_track_timescale"]) == ("1200000", "2400000", "12288")
    assert args[-1] == "out.mp4"


def test_the_concat_list_cuts_kinetic_parts_on_their_keyframes():
    listing = render.concat_list([("intro.mp4", None, None), ("kinetic.mp4", 0, 25), ("punch_0.mp4", None, None),
                                  ("kinetic.mp4", 36, 13), ("outro.mp4", None, None)])
    assert listing == ("file 'intro.mp4'\nfile 'kinetic.mp4'\noutpoint 1.041666\nfile 'punch_0.mp4'\n"
                       "file 'kinetic.mp4'\ninpoint 1.500000\noutpoint 2.041666\nfile 'outro.mp4'\n"), \
        "an out point rounded down: the frame there is the next part's"


def test_a_join_missing_a_frame_is_encoded_again(gsap, caplog):
    tl, shots = _film_inputs()
    calls = []
    run = _fake_runner(calls, frames=_expected_frames(tl) - 1)
    render.render_video(tl, shots, pulse=[], clips={c["key"]: b"m" for c in CLIPS}, images={}, bumpers=BUMPERS, run=run)
    joins = [args for args, _ in calls if "concat" in args]
    assert [args[args.index("-c:v") + 1] for args in joins] == ["copy", "libx264"]
    assert "expected; encoding the join" in caplog.text


@pytest.mark.parametrize("differ", [{"level": 40}, {"extradata_hash": "SHA256:def"}, {"extradata_hash": None}])
def test_parts_encoded_differently_are_joined_by_one_encode_with_the_same_settings(gsap, differ, caplog):
    tl, shots = _film_inputs()
    calls = []
    run = _fake_runner(calls, probes=[PROBE, {**PROBE, **differ}, PROBE, PROBE])
    render.render_video(tl, shots, pulse=[], clips={c["key"]: b"m" for c in CLIPS}, images={}, bumpers=BUMPERS, run=run)
    (join,) = [args for args, _ in calls if "concat" in args]
    assert join[join.index("-c:v") + 1] == "libx264" and join[join.index("-crf") + 1] == render.PICTURE_CRF
    assert join[join.index("-r") + 1] == str(keys.FPS) and "encoding the join" in caplog.text
    assert not any("-count_packets" in args for args, _ in calls), "an encoded join isn't counted"
    assert render.same_encoding([PROBE, dict(PROBE), PROBE]) and not render.same_encoding([])


def test_a_bumper_is_rendered_from_its_own_page_with_the_middles_settings(gsap):
    calls, seen = [], {}

    def run(args, **kwargs):
        project = Path(args[args.index("render") + 1])
        seen["page"] = (project / "index.html").read_text()
        return _fake_runner(calls)(args, **kwargs)

    data = render.render_bumper("outro", 6.0, run=run)
    assert data.startswith(b"\x00\x00\x00\x18ftyp")
    assert ">StormIQ<" in seen["page"] and 'data-duration="6.000"' in seen["page"]
    args = calls[0][0]
    assert args[args.index("--crf") + 1] == render.PICTURE_CRF and args[args.index("--max-bitrate") + 1] == render.PICTURE_MAX_BITRATE
    assert args[args.index("--fps") + 1] == str(keys.FPS) and args[args.index("--quality") + 1] == render.PICTURE_QUALITY
    assert args[args.index("--keyframe-interval") + 1] == keys.KEYFRAME_INTERVAL_S


def test_a_bumpers_key_covers_its_kind_length_page_encode_and_renderer(monkeypatch):
    key = render.bumper_key("intro", 4.0)
    assert key == render.bumper_key("intro", 4.0) and len(key) == 64
    assert len({key, render.bumper_key("outro", 6.0), render.bumper_key("intro", 5.0)}) == 3
    for name, value in (("PICTURE_CRF", "18"), ("PICTURE_QUALITY", "standard"), ("KEYFRAME_INTERVAL_S", "1")):
        monkeypatch.setattr(render, name, value)
        assert render.bumper_key("intro", 4.0) != key, f"a new encode makes new bumpers ({name})"
        monkeypatch.undo()
    monkeypatch.setattr(render, "_css", lambda: "body{color:red}")
    assert render.bumper_key("intro", 4.0) != key, "a new design makes new bumpers"


def test_the_pinned_renderer_is_the_one_the_image_installs():
    dockerfile = (Path(__file__).resolve().parents[1] / "docker" / "Dockerfile.render").read_text()
    assert f"hyperframes@{render.HYPERFRAMES_VERSION} " in dockerfile


def test_the_picture_timeout_grows_with_the_video_and_the_job_outlasts_it():
    short, long = render.picture_timeout(30), render.picture_timeout(150)
    assert render.PICTURE_TIMEOUT_BASE_S < short < long <= render.PICTURE_TIMEOUT_MAX_S
    assert render.picture_timeout(10_000) == render.PICTURE_TIMEOUT_MAX_S
    bumpers = render.picture_timeout(4) + render.picture_timeout(6)
    worst = (bumpers + render.PICTURE_TIMEOUT_MAX_S + 4 * render.PUNCH_TIMEOUT_S + 8 * render.PROBE_TIMEOUT_S
             + 2 * render.JOIN_TIMEOUT_S + render.MIX_TIMEOUT_S)
    assert render_worker.RENDER_JOB_TIMEOUT_SECONDS > worst


def test_the_capture_workers_follow_render_workers_within_bounds(monkeypatch):
    monkeypatch.delenv("RENDER_WORKERS", raising=False)
    assert render.capture_workers() == 1
    for raw, expected in (("2", 2), (" 3 ", 3), ("0", 1), ("-5", 1), ("99", render.MAX_WORKERS), ("two", 1), ("", 1)):
        monkeypatch.setenv("RENDER_WORKERS", raw)
        assert render.capture_workers() == expected, raw


def test_the_film_page_is_made_at_the_videos_frame_rate():
    tl, shots = _film_inputs()
    page, _effects = film.build_film(tl, shots, pulse=PULSE, css="")
    assert f'data-fps="{keys.FPS}"' in page and keys.FPS == 24


def test_a_failed_or_slow_render_says_so_without_the_tools_output(gsap, caplog):
    tl, shots = _film_inputs()
    clips = {clip["key"]: b"mp3" for clip in CLIPS}
    with pytest.raises(render.RenderFailed) as failed:
        render.render_video(tl, shots, pulse=[], clips=clips, images={}, bumpers=BUMPERS, run=_fake_runner([], fail="hyperframes"))
    assert str(failed.value) == "The video could not render its pictures. Please try again."
    assert "chrome crashed" in caplog.text, "the tool's output is logged, never returned"
    with pytest.raises(render.RenderFailed, match="took too long"):
        render.render_video(tl, shots, pulse=[], clips=clips, images={}, bumpers=BUMPERS, run=_fake_runner([], timeout="render"))
    with pytest.raises(render.RenderFailed, match="join its parts"):
        render.render_video(tl, shots, pulse=[], clips=clips, images={}, bumpers=BUMPERS, run=_fake_runner([], fail="concat"))
    with pytest.raises(render.RenderFailed, match="missing"):
        render.render_video(tl, shots, pulse=[], clips={}, images={}, bumpers=BUMPERS, run=_fake_runner([]))
    with pytest.raises(render.RenderFailed, match="intro or outro"):
        render.render_video(tl, shots, pulse=[], clips=clips, images={}, bumpers={"intro": b"I"}, run=_fake_runner([]))


def test_without_gsap_the_render_stops_before_any_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("GSAP_JS_PATH", str(tmp_path / "missing.js"))
    tl, shots = _film_inputs()
    calls = []
    with pytest.raises(render.RenderFailed, match="isn't set up"):
        render.render_video(tl, shots, pulse=[], clips={c["key"]: b"m" for c in CLIPS}, images={}, bumpers=BUMPERS,
                            run=_fake_runner(calls))
    with pytest.raises(render.RenderFailed, match="isn't set up"):
        render.render_bumper("intro", 4.0, run=_fake_runner(calls))
    assert calls == []


# ── The render worker ──


def _saved_show(monkeypatch, *, enabled=True, meta=None, timeline=None):
    tl = timeline or _timeline()
    shots = director.fallback_shots(tl["shots"], [])
    punches = [{"n": 0, "image_prompt": "a cat", "image": "img-key"}]
    saved = []
    monkeypatch.setattr(render_worker, "fetch_session_fields", lambda sid, fields, *, organization_id: {"organization_id": "org-a"})
    monkeypatch.setattr(render_worker, "fetch_surprise_enabled", lambda org: enabled)
    monkeypatch.setattr(render_worker, "fetch_surprise_timeline", lambda sid, key, *, organization_id: {"key": "tl", "body": tl})
    monkeypatch.setattr(render_worker, "fetch_surprise_shotlist", lambda sid, key, *, organization_id: {
        "key": "sh", "body": {"stage": "final", "timeline_key": "tl", "pulse": PULSE, "shots": shots, "punches": punches}})
    monkeypatch.setattr(render_worker, "fetch_surprise_images", lambda sid, wanted, *, organization_id: {"img-key": b"jpeg"})
    monkeypatch.setattr(render_worker, "fetch_surprise_voice_clip", lambda sid, key, *, organization_id: {"bytes": b"mp3", "mime": "audio/mpeg"})
    monkeypatch.setattr(render_worker, "fetch_surprise_video_meta", lambda sid, *, organization_id: meta)
    monkeypatch.setattr(render_worker, "save_surprise_video", lambda sid, key, *, mp4, duration_s: saved.append((sid, key, mp4, duration_s)))
    stored = {render.bumper_key(kind, seconds): kind.upper().encode() for kind, seconds in film.bumper_spans(tl)}
    monkeypatch.setattr(render_worker, "fetch_surprise_bumper", stored.get)
    monkeypatch.setattr(render_worker, "save_surprise_bumper", mock.Mock(side_effect=AssertionError("stored already")))
    return saved


def test_the_render_worker_renders_what_the_show_job_saved_and_saves_it_whole(monkeypatch):
    saved = _saved_show(monkeypatch)
    seen = {}

    def fake_render(timeline, shots, *, pulse, clips, images, bumpers, on_stage, job):
        seen.update(clips=clips, images=images, pulse=pulse, bumpers=bumpers, job=job)
        on_stage("mixing")
        return {"mp4": b"MP4", "duration_s": timeline["total"], "size": 3}

    monkeypatch.setattr(render, "render_video", fake_render)
    monkeypatch.setattr(render, "render_bumper", mock.Mock(side_effect=AssertionError("the stored bumpers are reused")))
    stages = []
    assert render_worker.render_and_save(SESSION_ID, stages.append)["status"] == "made"
    assert saved == [(SESSION_ID, keys.video_key("tl", "sh", ["img-key"]), b"MP4", _timeline()["total"])]
    assert seen["images"] == {0: b"jpeg"} and set(seen["clips"]) == {c["key"] for c in CLIPS} and seen["pulse"] == PULSE
    assert seen["bumpers"] == {"intro": b"INTRO", "outro": b"OUTRO"} and seen["job"] == f"session_id={SESSION_ID}"
    assert stages == ["rendering", "mixing"]


def test_a_missing_bumper_is_rendered_once_saved_whole_then_reused(monkeypatch):
    tl = _timeline()
    store, made = {}, []

    def save(key, kind, *, mp4, duration_s):
        store[key] = mp4
        made.append((kind, duration_s))

    def make(kind, seconds, *, job=""):
        assert job == "session_id=s1"
        return f"{kind}-{seconds}".encode()

    monkeypatch.setattr(render_worker, "fetch_surprise_bumper", store.get)
    monkeypatch.setattr(render_worker, "save_surprise_bumper", save)
    monkeypatch.setattr(render, "render_bumper", make)
    first = render_worker.ensure_bumpers(tl, "session_id=s1")
    assert first == {"intro": b"intro-4.0", "outro": b"outro-6.0"} and made == [("intro", 4.0), ("outro", 6.0)]
    monkeypatch.setattr(render, "render_bumper", mock.Mock(side_effect=AssertionError("made once")))
    assert render_worker.ensure_bumpers(tl, "session_id=s2") == first and len(made) == 2


def test_the_render_worker_keeps_a_video_made_from_the_same_show_and_refuses_when_off(monkeypatch):
    saved = _saved_show(monkeypatch, meta={"video_key": keys.video_key("tl", "sh", ["img-key"]), "size_bytes": 3, "duration_s": 1.0})
    monkeypatch.setattr(render, "render_video", mock.Mock(side_effect=AssertionError("no render")))
    assert render_worker.render_and_save(SESSION_ID)["status"] == "kept" and saved == []
    _saved_show(monkeypatch, enabled=False)
    with pytest.raises(render_worker.VideoFailed, match="turned off"):
        render_worker.render_and_save(SESSION_ID)


def test_the_render_worker_refuses_a_timeline_made_before_the_punchline_windows(monkeypatch):
    _saved_show(monkeypatch, timeline={**_timeline(), "version": 1})
    monkeypatch.setattr(render, "render_video", mock.Mock(side_effect=AssertionError("no render")))
    with pytest.raises(render_worker.VideoFailed, match="Make the show again"):
        render_worker.render_and_save(SESSION_ID)


def test_the_render_job_publishes_its_progress_and_clears_it(monkeypatch):
    published, cleared = [], []
    monkeypatch.setattr(render_worker, "publish_progress_sync", lambda kind, key, stage, **kw: published.append((kind, stage)))
    monkeypatch.setattr(render_worker, "clear_progress_sync", lambda kind, key: cleared.append((kind, key)))
    monkeypatch.setattr(render_worker, "render_and_save", lambda sid, publish: publish("mixing") or {"status": "made", "size": 1, "duration_s": 1})
    assert asyncio.run(render_worker.render_surprise_video({}, SESSION_ID))["status"] == "made"
    assert published == [("surprise_video", "rendering"), ("surprise_video", "mixing")]
    monkeypatch.setattr(render_worker, "render_and_save", mock.Mock(side_effect=render.RenderFailed("The video could not mix its sound.")))
    with pytest.raises(render.RenderFailed):
        asyncio.run(render_worker.render_surprise_video({}, SESSION_ID))
    assert cleared == [("surprise_video", SESSION_ID)] * 2


def test_the_render_worker_has_its_own_queue_one_job_at_a_time_and_stores_messages_only():
    settings = render_worker.WorkerSettings
    assert settings.queue_name == "stormiq:render" and settings.max_jobs == 1 and settings.max_tries == 1
    assert settings.job_serializer is render_worker.job_result_serializer
    assert [getattr(f, "name", "") for f in settings.functions] == ["render_surprise_video"]
    assert settings.functions[0].timeout_s == render_worker.RENDER_JOB_TIMEOUT_SECONDS
    from livestorm_app import worker

    assert getattr(worker.WorkerSettings, "queue_name", "arq:queue") == "arq:queue", "the main worker never takes a render"


def test_the_web_process_and_the_main_worker_never_import_the_renderer():
    code = ("import sys, app, livestorm_app.worker; "
            "print([m for m in sys.modules if m in ('livestorm_app.video.render', 'livestorm_app.video.film', "
            "'livestorm_app.video.mix', 'livestorm_app.render_worker')])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], check=True)
    assert out.stdout.strip() == "[]"


# ── The route ──

VIDEO = "/api/sessions/{session_id}/surprise-show/video.mp4"


def test_the_video_is_private_and_whole_without_a_range(client):  # noqa: F811
    response = _call(client, "GET", VIDEO, cookie="conn-a")
    assert response.status_code == 200 and response.content == MARKER.encode()
    assert response.headers["content-type"] == "video/mp4" and response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"].startswith("private") and "content-disposition" not in response.headers


def test_a_range_is_answered_with_206_and_its_part(client):  # noqa: F811
    response = _call(client, "GET", VIDEO, cookie="conn-a", headers={"Range": "bytes=4-9"})
    assert response.status_code == 206 and response.content == MARKER.encode()[4:10]
    assert response.headers["content-range"] == f"bytes 4-9/{len(MARKER)}"
    suffix = _call(client, "GET", VIDEO, cookie="conn-a", headers={"Range": "bytes=-6"})
    assert suffix.status_code == 206 and suffix.content == b"MARKER"
    assert suffix.headers["content-range"] == f"bytes {len(MARKER) - 6}-{len(MARKER) - 1}/{len(MARKER)}"
    open_ended = _call(client, "GET", VIDEO, cookie="conn-a", headers={"Range": f"bytes={len(MARKER) - 6}-"})
    assert open_ended.status_code == 206 and open_ended.content == b"MARKER"


def test_a_range_outside_the_video_is_416(client):  # noqa: F811
    response = _call(client, "GET", VIDEO, cookie="conn-a", headers={"Range": "bytes=50-60"})
    assert response.status_code == 416 and response.headers["content-range"] == f"bytes */{len(MARKER)}"


def test_download_names_the_file_after_the_session(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: (
        {"event_title": "Q3 Roadmap: what's next!"} if fields == ["event_title"] else
        {"organization_id": "org-a", "smart_recap_bundle": {}, "has_transcript": True}))
    client.cookies.set(oauth_client.LIVESTORM_OAUTH_COOKIE, "conn-a")
    response = client.get(f"/api/sessions/{SESSION_ID}/surprise-show/video.mp4", params={"download": "1"})
    assert response.headers["content-disposition"] == 'attachment; filename="surprise-me-q3-roadmap-what-s-next.mp4"'


def test_no_video_yet_is_404(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(api_logic, "fetch_surprise_video_meta", lambda sid, *, organization_id: None)
    assert _call(client, "GET", VIDEO, cookie="conn-a").status_code == 404


def test_byte_ranges():
    assert api_logic.byte_range(None, 10) is None and api_logic.byte_range("items=0-1", 10) is None
    assert api_logic.byte_range("bytes=0-1,4-5", 10) is None, "several ranges: the whole file"
    assert api_logic.byte_range("bytes=2-", 10) == (2, 9) and api_logic.byte_range("bytes=2-50", 10) == (2, 9)
    for bad in ("bytes=10-", "bytes=5-2", "bytes=x-1", "bytes=-0", "bytes=7"):
        with pytest.raises(api_logic.RangeNotSatisfiable):
            api_logic.byte_range(bad, 10)
    assert api_logic.video_file_name("") == "surprise-me-session.mp4"
    assert api_logic.video_file_name("Café Démo — 2026") == "surprise-me-cafe-demo-2026.mp4"


# ── Stored ──


def test_the_videos_parts_are_stored_whole_in_scope(test_database):
    db.upsert_cached_session("key-a", "sess-v", session_payload={"name": "A"}, organization_id="org-a")
    db.save_surprise_timeline("sess-v", "t1", {"total": 10.0})
    assert db.fetch_surprise_timeline("sess-v", "t1", organization_id="org-a") == {"key": "t1", "body": {"total": 10.0}}
    assert db.fetch_surprise_timeline("sess-v", None, organization_id="org-a")["key"] == "t1"
    assert db.fetch_surprise_timeline("sess-v", "t2", organization_id="org-a") is None
    assert db.fetch_surprise_timeline("sess-v", "t1", organization_id="org-b") is None
    assert db.fetch_surprise_timeline("sess-v", "t1", organization_id="  ") is None
    db.save_surprise_shotlist("sess-v", "s1", {"stage": "final", "shots": []})
    assert db.fetch_surprise_shotlist("sess-v", "s1", organization_id=db.ALL_ORGS)["body"]["stage"] == "final"
    db.save_surprise_image("sess-v", "i1", b"jpeg")
    db.save_surprise_image("sess-v", "i2", b"jpeg2")
    assert db.fetch_surprise_images("sess-v", ["i1", "i9"], organization_id="org-a") == {"i1": b"jpeg"}
    assert db.fetch_surprise_images("sess-v", ["i1"], organization_id="org-b") == {}
    assert db.prune_surprise_images("sess-v", ["i1"]) == 1
    mp4 = bytes(range(256)) * 40
    db.save_surprise_video("sess-v", "v1", mp4=mp4, duration_s=12.5)
    meta = db.fetch_surprise_video_meta("sess-v", organization_id="org-a")
    assert (meta["video_key"], meta["duration_s"], meta["size_bytes"]) == ("v1", 12.5, len(mp4))
    assert db.fetch_surprise_video_meta("sess-v", organization_id="org-b") is None
    assert db.fetch_surprise_video_range("sess-v", 100, 50, organization_id="org-a") == mp4[100:150]
    assert db.fetch_surprise_video_range("sess-v", 0, 10, organization_id="org-b") is None
    db.save_surprise_video("sess-v", "v2", mp4=b"new", duration_s=1.0)
    assert db.fetch_surprise_video_meta("sess-v", organization_id="org-a")["video_key"] == "v2", "a new video replaces the old one"
    with pytest.raises(RuntimeError, match="Could not save the video"):
        db.save_surprise_video("sess-missing", "v1", mp4=b"x", duration_s=1.0)
    with db.get_db_connection() as connection:
        storage = connection.execute(
            "SELECT attstorage FROM pg_attribute WHERE attrelid = 'surprise_videos'::regclass AND attname = 'mp4'"
        ).fetchone()
    assert storage["attstorage"] == "e", "stored uncompressed, so a range reads only its chunks"


def test_a_bumper_is_stored_whole_once_per_key_and_is_not_an_ai_output(test_database):
    assert db.fetch_surprise_bumper("k1") is None
    mp4 = bytes(range(256)) * 10
    db.save_surprise_bumper("k1", "intro", mp4=mp4, duration_s=4.0)
    assert db.fetch_surprise_bumper("k1") == mp4
    db.save_surprise_bumper("k1", "intro", mp4=b"other", duration_s=4.0)
    assert db.fetch_surprise_bumper("k1") == mp4, "the same key is the same picture: the first is kept"
    db.save_surprise_bumper("k2", "outro", mp4=b"outro", duration_s=6.0)
    assert db.fetch_surprise_bumper("k2") == b"outro" and db.fetch_surprise_bumper("  ") is None
    with pytest.raises(ValueError):
        db.save_surprise_bumper("k3", "middle", mp4=b"x", duration_s=1.0)
    assert "surprise_bumpers" not in db.AI_OUTPUT_TABLES, "made from no session: a reset keeps it"
    db.ensure_database_schema()
    with db.get_db_connection() as connection:
        storage = connection.execute(
            "SELECT attstorage FROM pg_attribute WHERE attrelid = 'surprise_bumpers'::regclass AND attname = 'mp4'"
        ).fetchone()
    assert storage["attstorage"] == "e" and db.fetch_surprise_bumper("k1") == mp4, "ensured again, kept"


def test_the_storyboards_table_is_gone(test_database):
    with db.get_db_connection() as connection:
        row = connection.execute("SELECT to_regclass('surprise_storyboards') AS t").fetchone()
    assert row["t"] is None
