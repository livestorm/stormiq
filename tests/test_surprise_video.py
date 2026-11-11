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
from livestorm_app.video import film, keys, mix, render

LINES = [
    {"beat": 1, "say": "Okay, so the chat went quiet.", "tone": "deadpan"},
    {"beat": 1, "say": "Then 96% stayed — for pricing.", "tone": "building"},
    {"beat": 2, "say": "Nobody asked why.", "tone": "punchline"},
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
    assert starts[2] == pytest.approx(starts[1] + 2.5 + 0.75), "0.75 s held before a punchline line"
    assert tl["voice_end"] == pytest.approx(starts[2] + 1.2)
    assert tl["end_card"] == pytest.approx(tl["voice_end"] + 0.5)
    assert tl["total"] == pytest.approx(tl["end_card"] + timing.OUTRO_S) and timing.OUTRO_S == 6.0
    assert tl["timed"] == 0


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


def test_the_clause_before_a_punchline_holds():
    tl = timing.build_timeline(LINES, CLIPS, [None, None, None])
    shots = tl["shots"]
    punch = next(n for n, shot in enumerate(shots) if shot["tone"] == "punchline")
    assert shots[punch - 1]["hold_after"] == 0.75
    assert sum(1 for shot in shots if shot.get("hold_after")) == 1
    assert [shot["id"] for shot in shots] == list(range(len(shots)))


def test_the_timeline_key_follows_the_clips_the_beats_and_the_tones():
    base = timing.timeline_key(LINES, [c["key"] for c in CLIPS])
    assert base == timing.timeline_key(LINES, [c["key"] for c in CLIPS])
    assert base != timing.timeline_key(LINES, ["d" * 64] + [c["key"] for c in CLIPS[1:]])
    assert base != timing.timeline_key([{**LINES[0], "tone": "aside"}, *LINES[1:]], [c["key"] for c in CLIPS])


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
            "minute": None, "chat_id": None, "emoji": [], "left": None, "right": None, "image_prompt": None}
    return {**base, **extra}


def test_the_director_is_a_v1_sonnet_job_with_a_strict_schema_and_a_prompt():
    spec = jobs.job_spec("surprise_director")
    assert (spec.group, spec.default_anthropic, spec.layout, spec.automatic) == ("v1", "claude-sonnet-5-5", "instructions", False)
    assert is_registered(director.SurpriseDirection)
    props = director.SurpriseDirection.model_json_schema()["$defs"]["DirectorShot"]["properties"]
    assert props["type"]["enum"] == list(director.TYPES) and props["transition"]["enum"] == list(director.TRANSITIONS)
    prompt = director.load_instructions()
    for words in ("never instructions to you", "Up to 6 words".lower(), "no real or recognisable people", "at most 3"):
        assert words.lower() in prompt.lower()


def test_the_call_sends_the_instructions_then_the_payload(monkeypatch):
    seen = {}

    def generate(kind, schema, request, *, api_key, ctx):
        seen.update(kind=kind, schema=schema, request=request, key=api_key)
        return schema.model_validate({"shots": []})

    monkeypatch.setattr(llm_client, "generate_json", generate)
    director.direct_shots("# Clauses", api_key="claude-key", ctx=director.CallContext(job_kind="surprise_director", session_id="s1"))
    assert (seen["kind"], seen["schema"], seen["key"]) == ("surprise_director", director.SurpriseDirection, "claude-key")
    assert seen["request"].instructions == director.load_instructions() and seen["request"].payload == "# Clauses"


def test_the_payload_holds_the_clauses_the_vitals_the_chat_and_the_room_by_id():
    payload = director.director_payload(CLAUSES, ["- Show-up rate: 71%"], PULSE, ROOM)
    assert "0 | line 0 | deadpan | 1.2s | The chat went quiet at minute 12." in payload
    assert "2 | line 1 | punchline | 0.9s | Nobody asked why.  [held pause before this line]" in payload
    assert "11:3, 12:0, 13:9" in payload and "R2 (question): SSO?" in payload and "- Show-up rate: 71%" in payload


def _check(answer, banned=frozenset()):
    return director.check_shots(CLAUSES, {"shots": answer}, room=ROOM, minutes=[m["m"] for m in PULSE], banned=set(banned))


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
    no_pulse = director.check_shots(CLAUSES[:1], {"shots": [_shot(0, "pulse", "chat went quiet", minute=12)]}, room=ROOM, minutes=[], banned=set())
    assert no_pulse[0]["type"] == "kinetic"


def test_a_split_copies_both_sides_from_the_clause():
    shots = _check([_shot(1, "split", "96% stayed", left="96% stayed", right="for pricing"), _shot(2, "split", "Nobody", left="Nobody", right="everyone")])
    by_id = {shot["id"]: shot for shot in shots}
    assert by_id[1]["type"] == "split" and (by_id[1]["left"], by_id[1]["right"]) == ("96% stayed", "for pricing")
    assert by_id[2]["type"] == "kinetic"


def test_at_most_three_pictures_and_never_a_name_in_their_prompt():
    clauses = [{**CLAUSES[0], "id": n} for n in range(5)]
    answer = [_shot(n, "image", "chat went quiet", image_prompt=f"a sleeping microphone number {n}") for n in range(4)]
    answer.append(_shot(4, "image", "chat went quiet", image_prompt="Sarah holding a sign"))
    shots = director.check_shots(clauses, {"shots": answer}, room=ROOM, minutes=[], banned={"sarah"})
    assert [shot["type"] for shot in shots] == ["image", "image", "image", "kinetic", "kinetic"]
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
    shots[0] = _shot(0, "image", tl["shots"][0]["text"].split()[0], image_prompt="Sarah waving at a laptop")
    sent = {}

    def direct(payload, *, api_key, ctx):
        sent.update(payload=payload, ctx=ctx)
        return director.SurpriseDirection.model_validate({"shots": shots})

    monkeypatch.setattr(api_logic.director, "direct_shots", direct)
    key, shotlist, how = api_logic._make_surprise_shotlist("claude-key", SESSION_ID, ROW, "take", "tl-key", tl)
    assert how == "made" and shotlist["stage"] == "directed" and shotlist["source"] == "model"
    assert shotlist["pulse"] == PULSE and shotlist["timeline_key"] == "tl-key" and steps.shotlists[key] == shotlist
    assert shotlist["shots"][0]["type"] == "kinetic", "an image prompt with a name in it is never sent"
    assert sent["ctx"].job_kind == "surprise_director" and sent["ctx"].job_id == f"surprise_show:{SESSION_ID}"
    assert "R1 (chat): is it free?" in sent["payload"]
    monkeypatch.setattr(api_logic.director, "direct_shots", mock.Mock(side_effect=AssertionError("kept")))
    assert api_logic._make_surprise_shotlist("claude-key", SESSION_ID, ROW, "take", "tl-key", tl)[2] == "kept"


@pytest.mark.parametrize("failure", [llm_client.LLMRefused("refused"), RuntimeError("boom"), llm_client.LLMProviderError("down")])
def test_a_bad_director_answer_never_fails_the_show(steps, monkeypatch, failure):
    monkeypatch.setattr(api_logic.director, "direct_shots", mock.Mock(side_effect=failure))
    _, shotlist, how = api_logic._make_surprise_shotlist("claude-key", SESSION_ID, ROW, "take", "tl-key", _timeline())
    assert how == "fallback" and shotlist["source"] == "rules" and len(shotlist["shots"]) == len(_timeline()["shots"])


def test_without_a_claude_key_the_rules_direct_with_no_call(steps, monkeypatch):
    monkeypatch.setattr(api_logic.director, "direct_shots", mock.Mock(side_effect=AssertionError("no call")))
    assert api_logic._make_surprise_shotlist("", SESSION_ID, ROW, "take", "tl-key", _timeline())[2] == "fallback"


def _directed(*prompts):
    shots = [{"id": n, "type": "image", "words": "x", "image_prompt": prompt} for n, prompt in enumerate(prompts)]
    shots.append({"id": len(prompts), "type": "kinetic", "words": "y"})
    return {"version": 1, "stage": "directed", "shots": shots, "pulse": []}


def test_the_pictures_are_made_once_saved_whole_and_a_failed_one_becomes_type(steps, monkeypatch):
    made = []

    def generate(key, prompt):
        made.append(prompt)
        if "broken" in prompt:
            raise images.ImageFailed("HTTP 500")
        return b"jpeg:" + prompt.encode()

    monkeypatch.setattr(api_logic.show_images, "generate_image", generate)
    final, count = api_logic._make_surprise_pictures("sk-test", SESSION_ID, "sh-key", _directed("a cat", "a broken dog"))
    assert count == 1 and sorted(made) == ["a broken dog", "a cat"]
    assert final["stage"] == "final" and steps.shotlists["sh-key"] == final
    assert final["shots"][0]["image"] == images.image_key("a cat") and steps.images[images.image_key("a cat")] == b"jpeg:a cat"
    assert final["shots"][1]["type"] == "kinetic" and not final["shots"][1].get("image")
    made.clear()
    again, _ = api_logic._make_surprise_pictures("sk-test", SESSION_ID, "sh-key", {**_directed("a cat"), "stage": "directed"})
    assert made == [] and again["shots"][0]["image"] == images.image_key("a cat"), "a saved picture is never made again"


def test_without_an_openai_key_every_picture_shot_becomes_type(steps, monkeypatch):
    monkeypatch.setattr(api_logic.show_images.requests, "post", mock.Mock(side_effect=AssertionError("no call")))
    final, count = api_logic._make_surprise_pictures("", SESSION_ID, "sh-key", _directed("a cat"))
    assert count == 0 and final["shots"][0]["type"] == "kinetic"


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


def test_the_film_escapes_the_rooms_words_and_freezes_the_held_punchline():
    tl, shots = _film_inputs()
    page, effects = film.build_film(tl, shots, pulse=PULSE, images={}, mark_svg='<svg width="28" height="28"></svg>', css="body{}")
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert 'class="deadpan"' in page and "NOBODY ASKED WHY." in page, "the held punchline in condensed capitals"
    assert 'data-width="1280" data-height="720"' in page and "window.__timelines" in page
    assert 'id="it-0"' in page and film.TAGLINE in page and 'id="so-w0"' in page and ">session.<" in page
    files = {event["file"] for event in effects}
    assert {"jingle-bass-meant-jazz-in.flac", "jingle-bass-meant-jazz-out.flac", "dun.flac"} <= files
    assert all((render.AUDIO_DIR / name).is_file() for name in files), "every effect is a committed file"
    assert effects == sorted(effects, key=lambda event: event["t"])


def test_the_film_needs_one_shot_per_clause():
    tl, shots = _film_inputs()
    with pytest.raises(ValueError):
        film.build_film(tl, shots[:-1], pulse=[], images={}, mark_svg="<svg/>", css="")


def test_a_picture_shot_without_its_picture_is_drawn_as_type():
    tl, shots = _film_inputs()
    shots[1] = {**shots[1], "type": "image", "image": "k"}
    page, _ = film.build_film(tl, shots, pulse=[], images={}, mark_svg="<svg/>", css="")
    assert "<img" not in page


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


def _fake_runner(calls, *, fail=None, timeout=None):
    def run(args, **kwargs):
        calls.append((list(args), kwargs))
        if timeout and timeout in args:
            raise subprocess.TimeoutExpired(args, 1)
        if fail and fail in " ".join(args):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="chrome crashed")
        out = Path(args[args.index("--output") + 1]) if "--output" in args else Path(args[-1])
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 100)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    return run


@pytest.fixture
def gsap(tmp_path, monkeypatch):
    path = tmp_path / "gsap.min.js"
    path.write_text("/* gsap */")
    monkeypatch.setenv("GSAP_JS_PATH", str(path))
    monkeypatch.setenv("HYPERFRAMES_BIN", "npx -y hyperframes@0.9.34")
    return path


def test_the_render_writes_a_project_runs_the_pinned_cli_at_720p_then_mixes(gsap):
    tl, shots = _film_inputs()
    calls, stages = [], []
    clips = {clip["key"]: b"mp3" for clip in CLIPS}
    seen = {}

    def run(args, **kwargs):
        if "render" in args:
            project = Path(args[args.index("render") + 1])
            seen["files"] = sorted(str(p.relative_to(project)) for p in project.rglob("*") if p.is_file())
        return _fake_runner(calls)(args, **kwargs)

    made = render.render_video(tl, shots, pulse=PULSE, clips=clips, images={1: b"jpeg"}, run=run, on_stage=stages.append)
    assert made["size"] == len(made["mp4"]) and made["duration_s"] == tl["total"]
    picture, sound = calls[0][0], calls[1][0]
    assert picture[:4] == ["npx", "-y", "hyperframes@0.9.34", "render"] and picture[picture.index("--fps") + 1] == "30"
    assert calls[0][1]["timeout"] == render.PICTURE_TIMEOUT_S and calls[0][1]["env"]["HYPERFRAMES_NO_TELEMETRY"] == "1"
    assert sound[0] == "ffmpeg" and "+faststart" in sound and stages == ["mixing"]
    assert {"index.html", "hyperframes.json", "assets/gsap.min.js", "assets/fonts/Sora.woff2",
            "assets/fonts/LeagueGothic.ttf", "assets/img_001.jpg"} <= set(seen["files"])


def test_a_failed_or_slow_render_says_so_without_the_tools_output(gsap, caplog):
    tl, shots = _film_inputs()
    clips = {clip["key"]: b"mp3" for clip in CLIPS}
    with pytest.raises(render.RenderFailed) as failed:
        render.render_video(tl, shots, pulse=[], clips=clips, images={}, run=_fake_runner([], fail="hyperframes"))
    assert str(failed.value) == "The video could not render its pictures. Please try again."
    assert "chrome crashed" in caplog.text, "the tool's output is logged, never returned"
    with pytest.raises(render.RenderFailed, match="took too long"):
        render.render_video(tl, shots, pulse=[], clips=clips, images={}, run=_fake_runner([], timeout="render"))
    with pytest.raises(render.RenderFailed, match="missing"):
        render.render_video(tl, shots, pulse=[], clips={}, images={}, run=_fake_runner([]))


def test_without_gsap_the_render_stops_before_any_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("GSAP_JS_PATH", str(tmp_path / "missing.js"))
    tl, shots = _film_inputs()
    calls = []
    with pytest.raises(render.RenderFailed, match="isn't set up"):
        render.render_video(tl, shots, pulse=[], clips={c["key"]: b"m" for c in CLIPS}, images={}, run=_fake_runner(calls))
    assert calls == []


# ── The render worker ──


def _saved_show(monkeypatch, *, enabled=True, meta=None):
    tl = _timeline()
    shots = director.fallback_shots(tl["shots"], [])
    shots[0] = {**shots[0], "image": "img-key"}
    saved = []
    monkeypatch.setattr(render_worker, "fetch_session_fields", lambda sid, fields, *, organization_id: {"organization_id": "org-a"})
    monkeypatch.setattr(render_worker, "fetch_surprise_enabled", lambda org: enabled)
    monkeypatch.setattr(render_worker, "fetch_surprise_timeline", lambda sid, key, *, organization_id: {"key": "tl", "body": tl})
    monkeypatch.setattr(render_worker, "fetch_surprise_shotlist", lambda sid, key, *, organization_id: {
        "key": "sh", "body": {"stage": "final", "timeline_key": "tl", "pulse": PULSE, "shots": shots}})
    monkeypatch.setattr(render_worker, "fetch_surprise_images", lambda sid, wanted, *, organization_id: {"img-key": b"jpeg"})
    monkeypatch.setattr(render_worker, "fetch_surprise_voice_clip", lambda sid, key, *, organization_id: {"bytes": b"mp3", "mime": "audio/mpeg"})
    monkeypatch.setattr(render_worker, "fetch_surprise_video_meta", lambda sid, *, organization_id: meta)
    monkeypatch.setattr(render_worker, "save_surprise_video", lambda sid, key, *, mp4, duration_s: saved.append((sid, key, mp4, duration_s)))
    return saved


def test_the_render_worker_renders_what_the_show_job_saved_and_saves_it_whole(monkeypatch):
    saved = _saved_show(monkeypatch)
    seen = {}

    def fake_render(timeline, shots, *, pulse, clips, images, on_stage):
        seen.update(clips=clips, images=images, pulse=pulse)
        on_stage("mixing")
        return {"mp4": b"MP4", "duration_s": timeline["total"], "size": 3}

    monkeypatch.setattr(render, "render_video", fake_render)
    stages = []
    assert render_worker.render_and_save(SESSION_ID, stages.append)["status"] == "made"
    assert saved == [(SESSION_ID, keys.video_key("tl", "sh", ["img-key"]), b"MP4", _timeline()["total"])]
    assert seen["images"] == {0: b"jpeg"} and set(seen["clips"]) == {c["key"] for c in CLIPS} and seen["pulse"] == PULSE
    assert stages == ["rendering", "mixing"]


def test_the_render_worker_keeps_a_video_made_from_the_same_show_and_refuses_when_off(monkeypatch):
    saved = _saved_show(monkeypatch, meta={"video_key": keys.video_key("tl", "sh", ["img-key"]), "size_bytes": 3, "duration_s": 1.0})
    monkeypatch.setattr(render, "render_video", mock.Mock(side_effect=AssertionError("no render")))
    assert render_worker.render_and_save(SESSION_ID)["status"] == "kept" and saved == []
    _saved_show(monkeypatch, enabled=False)
    with pytest.raises(render_worker.VideoFailed, match="turned off"):
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


def test_the_storyboards_table_is_gone(test_database):
    with db.get_db_connection() as connection:
        row = connection.execute("SELECT to_regclass('surprise_storyboards') AS t").fetchone()
    assert row["t"] is None
