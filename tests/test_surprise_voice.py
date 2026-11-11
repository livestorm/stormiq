"""Surprise me's show (`ai/surprise_voice.py`, `ai/voice_elevenlabs.py`,
`api_logic.run_surprise_show`, `worker.run_surprise_show_job`, the
`surprise-show` routes): the beats cut on the server exactly as the player
cuts them (the shared fixture), the voices and their clips' cache key, the
OpenAI and ElevenLabs calls (only the line's text, a timeout, retried only
when nothing was made), the backup voice when ElevenLabs refuses the
account, one job from one click (the take, the script, the storyboard, the
voice), the script never sent to the page, the Surprise me switch, and the
clips stored in scope.

Never calls OpenAI, ElevenLabs or Claude: `requests.post` and the writers
are replaced. To regenerate the shared
fixture after changing the cut (and `surpriseBeats.js` with it):

    .venv/bin/python tests/test_surprise_voice.py
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from unittest import mock

import pytest
import requests
from test_access_control import (  # noqa: F401  (fixtures)
    SESSION_ID,
    _call,
    _save_setting,
    backend,
    client,
    saved_settings,
)

from livestorm_app import api_logic, db, oauth_client, worker
from livestorm_app.ai import surprise_voice as voice
from livestorm_app.ai import voice_elevenlabs as eleven
from livestorm_app.ai.surprise_voice import (
    SpeechFailed,
    clean_recap,
    clip_key,
    mp3_duration_s,
    resolve_voice,
    surprise_beats,
    synthesize_speech,
)
from livestorm_app.ai.surprise_performance import TONES, script_key
from livestorm_app.ai.voice_elevenlabs import VoiceUnavailable

FIXTURE = Path(__file__).resolve().parents[1] / "frontend" / "tests" / "fixtures" / "surprise-beats-v1.json"
SETTINGS = {"model": "gpt-4o-mini-tts", "voice": "marin", "instructions": "Warm, upbeat presenter; lively but clear."}  # an OpenAI request

# Takes as the Smart Recap saves them ("# Title" / "# Description"), with the
# marks the cut must survive: emphasis, quotes, ellipses, clock times,
# percentages, Windows line ends, a heading inside, a paragraph of one line.
TAKES = [
    "# Title\nFifty messages and a working SSO\n# Description\nAmara clicked connect. The chat sent 50 messages in three"
    " minutes! Nobody believed it.\n\nAt 23:00 the room finally relaxed. 56% stayed for pricing.",
    "# Title\n**Pricing**, live\n# Description\nThe host said \"one more slide.\" Then said it again… and again?! The"
    " room_count went up.\r\n\r\nOne sentence only\r\n\r\n## Act two\nAt 1:02:05 someone typed \"finally\". It was 1,200"
    " messages (or so). Wow!",
    "## Already clean\n\nNo punctuation at the end",
    "",
]


def fixture_cases():
    return [{"take": take, "beats": surprise_beats(take)} for take in TAKES]


# ── The beats, the same on both sides ──


def test_the_server_cuts_the_take_as_the_player_does():
    # The player's tests (`frontend/tests/surprise-beats.test.js`) read the
    # same file and check `surpriseBeats(cleanRecap(take))` gives these beats.
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == fixture_cases()


def test_the_beats_open_on_the_title_and_end_each_paragraph_on_its_punchline():
    beats = surprise_beats(TAKES[0])
    assert [beat["kind"] for beat in beats] == ["title", "line", "line", "punch", "line", "punch"]
    assert beats[0]["text"] == "Fifty messages and a working SSO"
    assert beats[3]["text"] == "Nobody believed it."
    second = surprise_beats(TAKES[1])
    assert second[0]["text"] == "Pricing, live", "emphasis is not read aloud"
    assert {"kind": "line", "text": "One sentence only"} in second, "a paragraph of one sentence has no punchline"
    assert {"kind": "title", "text": "Act two"} in second
    assert surprise_beats("") == [] and surprise_beats(None) == []


def test_the_take_is_cleaned_as_follow_up_shows_it():
    assert clean_recap("# Title\nThe room\n# Description\nIt was fine.") == "## The room\nIt was fine."
    assert clean_recap("## title\n\n# Description\nBody") == "Body"


# ── The voices and the cache key ──

LINE = {"beat": 2, "say": "Well... 50 messages. In three minutes.", "tone": "sarcastic"}
OPENAI = {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "cedar", "notes": "Dry."}
ELEVEN = {"provider": "elevenlabs", "model": "eleven_v3", "voice": eleven.DEFAULT_VOICE_ID, "notes": "stability=0.5"}


def test_a_clip_key_changes_with_the_line_the_provider_the_voice_the_model_and_the_notes():
    base = clip_key(LINE, OPENAI)
    assert base == clip_key(dict(LINE), dict(OPENAI)) and len(base) == 64
    assert base != clip_key({**LINE, "say": "Well."}, OPENAI)
    assert base != clip_key({**LINE, "tone": "warm"}, OPENAI), "the tone is part of what is sent"
    for field, other in (("voice", "marin"), ("model", "tts-1"), ("notes", "Calm.")):
        assert base != clip_key(LINE, {**OPENAI, field: other})
    assert clip_key(LINE, ELEVEN) != clip_key(LINE, {**ELEVEN, "voice": eleven.CHRIS_VOICE_ID})
    assert clip_key(LINE, ELEVEN) != base


def test_each_provider_gets_the_tone_its_own_way():
    assert voice.line_request(LINE, ELEVEN) == {"text": "[sarcastic] " + LINE["say"], "instructions": ""}
    sent = voice.line_request(LINE, OPENAI)
    assert sent["text"] == LINE["say"] and sent["instructions"] == "Dry.\n\n" + voice.TONE_NOTES["sarcastic"]
    assert set(eleven.TONE_TAGS) == set(voice.TONE_NOTES) == set(TONES)


def test_a_voice_gpt_4o_mini_tts_does_not_offer_becomes_cedar():
    assert resolve_voice(" Marin ") == "marin"
    assert resolve_voice("not-a-voice") == "cedar" and resolve_voice(None) == "cedar"


def _settings(monkeypatch, values):
    monkeypatch.setattr("livestorm_app.db.get_setting", lambda key: values.get(key))


def test_elevenlabs_with_finley_is_the_main_voice_and_openai_its_backup(monkeypatch):
    _settings(monkeypatch, {})
    main, backup = voice.voice_plan()
    assert (main["provider"], main["model"], main["voice"]) == ("elevenlabs", "eleven_v3", "fnYMz3F5gMEDGMWcH1ex")
    assert eleven.voice_label(main["voice"]) == "Finley (British, deep)"
    assert (backup["provider"], backup["voice"]) == ("openai", "cedar")
    assert backup["notes"] == Path("prompts/surprise_voice.txt").read_text(encoding="utf-8").strip()


def test_admin_picks_the_provider_and_each_voice(monkeypatch):
    _settings(monkeypatch, {"voice_provider": "elevenlabs", "elevenlabs_voice": eleven.CHRIS_VOICE_ID, "tts_voice": "sage"})
    main, backup = voice.voice_plan()
    assert main["voice"] == eleven.CHRIS_VOICE_ID and backup["voice"] == "sage"
    _settings(monkeypatch, {"voice_provider": "openai", "tts_voice": "sage"})
    main, backup = voice.voice_plan()
    assert (main["provider"], main["voice"], backup) == ("openai", "sage", None), "OpenAI has no backup"
    _settings(monkeypatch, {"voice_provider": "robot", "elevenlabs_voice": "../bad"})
    assert voice.voice_plan()[0]["voice"] == eleven.DEFAULT_VOICE_ID, "a bad saved id reads Finley"


def test_the_delivery_notes_ask_for_an_original_comedian_who_never_mocks_anyone():
    notes = Path("prompts/surprise_voice.txt").read_text(encoding="utf-8")
    for words in ("stand-up comedian", "Original, not an impression of anyone", "warm underneath", "beat of silence", "mocking anyone"):
        assert words in notes
    for crowd in ("laugh track", "applause", "crowd", "club"):
        assert crowd not in notes.lower()
    assert len(notes) < 1500, "short notes: gpt-4o-mini-tts reads them with every line"


# ── The call ──


def _response(status, content=b"mp3", headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = content
    response.headers.update(headers or {})
    return response


def _post(answers, sent):
    def post(url, headers, json, timeout):
        sent.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return post


def test_only_the_lines_text_and_the_voice_go_to_openai_with_a_timeout(monkeypatch):
    sent = []
    monkeypatch.setattr(voice.requests, "post", _post([_response(200, b"ID3audio")], sent))
    assert synthesize_speech("sk-test", "Nobody believed it.", SETTINGS) == b"ID3audio"
    (call,) = sent
    assert call["url"] == "https://api.openai.com/v1/audio/speech"
    assert call["json"] == {
        "model": "gpt-4o-mini-tts",
        "voice": "marin",
        "input": "Nobody believed it.",
        "instructions": SETTINGS["instructions"],
        "response_format": "mp3",
    }
    assert call["timeout"] == voice.SPEECH_TIMEOUT


@pytest.mark.parametrize(
    "first",
    [_response(429, headers={"Retry-After": "1"}), _response(503), requests.exceptions.ConnectTimeout("no route")],
    ids=["429", "503", "connect timeout"],
)
def test_a_failure_where_nothing_was_made_is_tried_again(monkeypatch, first):
    sent, slept = [], []
    monkeypatch.setattr(voice.requests, "post", _post([first, _response(200)], sent))
    assert synthesize_speech("sk-test", "Hi.", SETTINGS, sleep=slept.append) == b"mp3"
    assert len(sent) == 2 and len(slept) == 1 and slept[0] <= voice.RETRY_AFTER_CAP_S


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (requests.exceptions.ReadTimeout("slow"), "took too long"),
        (_response(400, b'{"error": "UPSTREAM_BODY"}'), "HTTP 400"),
        (_response(200, b""), "no audio"),
    ],
    ids=["read timeout", "400", "empty"],
)
def test_a_failure_that_may_have_been_paid_for_is_never_tried_again(monkeypatch, answer, message):
    sent = []
    monkeypatch.setattr(voice.requests, "post", _post([answer, _response(200)], sent))
    with pytest.raises(SpeechFailed, match=message) as raised:
        synthesize_speech("sk-test", "Hi.", SETTINGS, sleep=lambda s: None)
    assert len(sent) == 1
    assert "UPSTREAM_BODY" not in str(raised.value) and "sk-test" not in str(raised.value)


def test_retries_stop_after_the_last_attempt(monkeypatch):
    sent = []
    monkeypatch.setattr(voice.requests, "post", _post([_response(503)] * 3, sent))
    with pytest.raises(SpeechFailed, match="HTTP 503"):
        synthesize_speech("sk-test", "Hi.", SETTINGS, sleep=lambda s: None)
    assert len(sent) == voice.SPEECH_ATTEMPTS


def test_no_key_makes_no_call(monkeypatch):
    monkeypatch.setattr(voice.requests, "post", mock.Mock(side_effect=AssertionError("no call")))
    with pytest.raises(SpeechFailed, match="no OpenAI key"):
        synthesize_speech("  ", "Hi.", SETTINGS)


# ── The ElevenLabs call ──


def _post_eleven(answers, sent):
    def post(url, headers, params, json, timeout):
        sent.append({"url": url, "headers": headers, "params": params, "json": json, "timeout": timeout})
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return post


def test_only_the_tagged_line_goes_to_elevenlabs_with_a_timeout(monkeypatch):
    sent = []
    monkeypatch.setattr(eleven.requests, "post", _post_eleven([_response(200, b"ID3audio")], sent))
    assert eleven.synthesize("xi-test", "[dry] Nobody believed it.", ELEVEN) == b"ID3audio"
    (call,) = sent
    assert call["url"] == f"https://api.elevenlabs.io/v1/text-to-speech/{eleven.DEFAULT_VOICE_ID}"
    assert call["headers"]["xi-api-key"] == "xi-test"
    assert call["params"] == {"output_format": "mp3_44100_128"}
    assert call["json"] == {"text": "[dry] Nobody believed it.", "model_id": "eleven_v3", "voice_settings": {"stability": 0.5}}
    assert call["timeout"] == voice.SPEECH_TIMEOUT


@pytest.mark.parametrize(
    "first",
    [_response(429, headers={"Retry-After": "1"}), _response(503), requests.exceptions.ConnectTimeout("no route")],
    ids=["429", "503", "connect timeout"],
)
def test_elevenlabs_is_tried_again_only_when_nothing_was_made(monkeypatch, first):
    sent, slept = [], []
    monkeypatch.setattr(eleven.requests, "post", _post_eleven([first, _response(200)], sent))
    assert eleven.synthesize("xi-test", "Hi.", ELEVEN, sleep=slept.append) == b"mp3"
    assert len(sent) == 2 and len(slept) == 1


@pytest.mark.parametrize(
    ("answer", "error", "message"),
    [
        (requests.exceptions.ReadTimeout("slow"), SpeechFailed, "took too long"),
        (_response(400, b'{"detail": {"status": "UPSTREAM_BODY"}}'), SpeechFailed, "HTTP 400"),
        (_response(401, b'{"detail": {"status": "quota_exceeded", "message": "UPSTREAM_BODY"}}'), VoiceUnavailable, "HTTP 401"),
        (_response(401, b'{"detail": {"status": "invalid_api_key"}}'), VoiceUnavailable, "HTTP 401"),
        (_response(402, b"UPSTREAM_BODY"), VoiceUnavailable, "HTTP 402"),
        (_response(403, b"{}"), VoiceUnavailable, "HTTP 403"),
        (_response(400, b'{"detail": {"status": "payment_required"}}'), VoiceUnavailable, "HTTP 400"),
    ],
    ids=["read timeout", "400", "quota", "bad key", "402", "403", "payment"],
)
def test_elevenlabs_refusals_are_never_retried_and_the_account_ones_call_for_the_backup(monkeypatch, answer, error, message):
    sent = []
    monkeypatch.setattr(eleven.requests, "post", _post_eleven([answer, _response(200)], sent))
    with pytest.raises(error, match=message) as raised:
        eleven.synthesize("xi-test", "Hi.", ELEVEN, sleep=lambda s: None)
    assert len(sent) == 1 and type(raised.value) is error
    assert "UPSTREAM_BODY" not in str(raised.value) and "xi-test" not in str(raised.value)


def test_no_elevenlabs_key_makes_no_call_and_calls_for_the_backup(monkeypatch):
    monkeypatch.setattr(eleven.requests, "post", mock.Mock(side_effect=AssertionError("no call")))
    with pytest.raises(VoiceUnavailable, match="no ElevenLabs key"):
        eleven.synthesize("", "Hi.", ELEVEN)


def test_record_line_sends_each_provider_its_own_key(monkeypatch):
    seen = []
    monkeypatch.setattr(voice, "synthesize_speech", lambda key, text, request: seen.append(("openai", key, text, request)) or b"a")
    monkeypatch.setattr(eleven, "synthesize", lambda key, text, settings: seen.append(("eleven", key, text)) or b"b")
    keys = {"openai": "sk-1", "elevenlabs": "xi-1"}
    assert voice.record_line(LINE, ELEVEN, keys) == b"b" and voice.record_line(LINE, OPENAI, keys) == b"a"
    assert seen[0] == ("eleven", "xi-1", "[sarcastic] " + LINE["say"])
    assert seen[1][:3] == ("openai", "sk-1", LINE["say"]) and seen[1][3]["voice"] == "cedar"


# ── The clip's length ──


def _mp3(frames, id3=b""):
    # MPEG-1 Layer III, 128 kbit/s, 44.1 kHz, no padding: 417 bytes a frame.
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * 413
    return id3 + frame * frames


def test_an_mp3s_length_comes_from_its_frames():
    assert mp3_duration_s(_mp3(38)) == pytest.approx(38 * 1152 / 44100, abs=0.001)
    tag = b"ID3\x04\x00\x00" + struct.pack(">I", 20) + b"\xff" * 20  # a tag holding sync-like bytes
    assert mp3_duration_s(_mp3(10, tag)) == pytest.approx(10 * 1152 / 44100, abs=0.001)
    assert mp3_duration_s(b"not audio") is None and mp3_duration_s(b"") is None


# ── The worker's run ──

TAKE = TAKES[0]
BEATS = surprise_beats(TAKE)
SCRIPT = [
    {"beat": 1, "say": "Okay, so... fifty messages and a working SSO.", "tone": "building"},
    {"beat": 2, "say": "Amara clicked connect.", "tone": "deadpan"},
    {"beat": 3, "say": "And the chat? 50 messages in three minutes!", "tone": "excited"},
    {"beat": 4, "say": "Nobody... believed it.", "tone": "punchline"},
    {"beat": 5, "say": "Hmm. At 23:00 the room finally relaxed.", "tone": "aside"},
    {"beat": 6, "say": "And 56% stayed for pricing. Proud of you.", "tone": "warm"},
]


class Clips:
    def __init__(self, have=()):
        self.have = {key: {"duration_s": 1.0, "size": 3} for key in have}
        self.saved, self.pruned = [], []

    def fetch(self, session_id, keys, *, organization_id):
        return {key: self.have[key] for key in keys if key in self.have}

    def save(self, session_id, key, *, audio, mime, duration_s):
        self.saved.append((session_id, key, audio, mime, duration_s))
        self.have[key] = {"duration_s": duration_s, "size": len(audio)}

    def prune(self, session_id, keep):
        self.pruned.append((session_id, sorted(set(keep))))
        return 0


@pytest.fixture
def run_env(monkeypatch):
    clips = Clips()
    row = {"organization_id": "org-a", "has_transcript": True, "smart_recap_bundle": {"surprise": TAKE, "professional": "# Title\nX"}}
    spoken, scripts, boards = [], {}, {}
    env = {"clips": clips, "row": row, "spoken": spoken, "scripts": scripts, "refuse": set()}
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "fetch_surprise_enabled", lambda org: True)
    monkeypatch.setattr(api_logic, "voice_plan", lambda: (dict(ELEVEN), dict(OPENAI)))
    monkeypatch.setattr(api_logic, "fetch_surprise_voice_clips", clips.fetch)
    monkeypatch.setattr(api_logic, "save_surprise_voice_clip", clips.save)
    monkeypatch.setattr(api_logic, "prune_surprise_voice_clips", clips.prune)
    monkeypatch.setattr(api_logic, "fetch_surprise_script", lambda sid, key, *, organization_id: scripts.get(key))
    monkeypatch.setattr(api_logic, "save_surprise_script", lambda sid, key, script: scripts.__setitem__(key, script))
    monkeypatch.setattr(api_logic, "fetch_surprise_storyboard", lambda sid, key, *, organization_id: boards.get(key))
    monkeypatch.setattr(api_logic, "save_surprise_storyboard", lambda sid, key, board: boards.__setitem__(key, board))
    # The script and the storyboard are tested in their own files: here, a
    # script that passes, and a storyboard with no scene.
    monkeypatch.setattr(api_logic, "_make_surprise_script", lambda key, sid, r, take, beats: (
        api_logic.save_surprise_script(sid, script_key([b["text"] for b in beats]), {"version": 1, "source": "model", "lines": SCRIPT})
        or (SCRIPT, "made")
    ))
    monkeypatch.setattr(api_logic, "_make_surprise_storyboard", lambda key, sid, r, beats, k: (
        boards.__setitem__(k, {"version": 1, "source": "model", "scenes": [None] * len(beats)}) or "made"
    ))

    def speak(line, settings, keys):
        if settings["provider"] in env["refuse"]:
            raise VoiceUnavailable("ElevenLabs refused the account (HTTP 401).")
        spoken.append((settings["provider"], line["say"]))
        return _mp3(5)

    monkeypatch.setattr(api_logic, "record_line", speak)
    return env


KEYS = {"claude": "claude-key", "openai": "sk-test", "elevenlabs": "xi-test"}


def test_the_show_records_each_line_once_saves_each_clip_whole_and_tidies_the_rest(run_env):
    published = []
    result = api_logic.run_surprise_show(KEYS, SESSION_ID, publish=lambda stage, extra: published.append((stage, extra)))
    assert sorted(run_env["spoken"]) == sorted(("elevenlabs", line["say"]) for line in SCRIPT), "the script's lines, by the main voice"
    clips = run_env["clips"]
    assert [saved[3] for saved in clips.saved] == ["audio/mpeg"] * len(SCRIPT)
    assert all(saved[4] == pytest.approx(5 * 1152 / 44100, abs=0.001) for saved in clips.saved)
    assert clips.pruned == [(SESSION_ID, sorted(clip_key(line, ELEVEN) for line in SCRIPT))]
    assert result == {
        "status": "ok", "take": "kept", "script": "made", "story": "made", "lines": len(SCRIPT), "made": len(SCRIPT),
        "voice": "elevenlabs", "fellBack": False,
    }
    assert [stage for stage, _ in published][:2] == ["script", "storyboard"], "a saved take is not written again"
    assert published[2] == ("voice", {"done": 0, "total": len(SCRIPT), "percent": 55})
    assert published[-1] == ("voice", {"done": len(SCRIPT), "total": len(SCRIPT), "percent": 95})


def test_the_show_writes_the_take_first_when_there_is_none(run_env, monkeypatch):
    run_env["row"]["smart_recap_bundle"].pop("surprise")
    written = []

    def write_take(key, session_id, tone):
        written.append((key, session_id, tone))
        run_env["row"]["smart_recap_bundle"]["surprise"] = TAKE
        return {"markdown": TAKE}

    monkeypatch.setattr(api_logic, "run_smart_recap", write_take)
    published = []
    result = api_logic.run_surprise_show(KEYS, SESSION_ID, publish=lambda stage, extra: published.append(stage))
    assert written == [("claude-key", SESSION_ID, "surprise")] and result["take"] == "written"
    assert published[:3] == ["take", "script", "storyboard"] and published[3] == "voice"


def test_a_take_that_fails_its_check_fails_the_show_before_any_voice(run_env, monkeypatch):
    run_env["row"]["smart_recap_bundle"].pop("surprise")

    def refuse(key, session_id, tone):
        raise api_logic.SurpriseCheckError("The surprise take gives a number that isn't in the session.")

    monkeypatch.setattr(api_logic, "run_smart_recap", refuse)
    with pytest.raises(api_logic.SurpriseCheckError):
        api_logic.run_surprise_show(KEYS, SESSION_ID)
    assert run_env["spoken"] == [] and run_env["scripts"] == {}


def test_without_a_transcript_or_a_claude_key_no_take_is_written(run_env, monkeypatch):
    monkeypatch.setattr(api_logic, "run_smart_recap", mock.Mock(side_effect=AssertionError("no call")))
    run_env["row"]["smart_recap_bundle"].pop("surprise")
    with pytest.raises(RuntimeError, match="no Claude key"):
        api_logic.run_surprise_show({**KEYS, "claude": ""}, SESSION_ID)
    run_env["row"]["has_transcript"] = False
    with pytest.raises(api_logic.SurpriseVoiceNoTakeError, match="no transcript"):
        api_logic.run_surprise_show(KEYS, SESSION_ID)


def test_a_replay_costs_nothing(run_env):
    api_logic.run_surprise_show(KEYS, SESSION_ID)
    run_env["spoken"].clear()
    assert api_logic.run_surprise_show(KEYS, SESSION_ID)["made"] == 0
    assert run_env["spoken"] == [], "every clip was already there"


def test_when_elevenlabs_refuses_the_account_every_line_is_recorded_with_the_backup(run_env):
    run_env["refuse"].add("elevenlabs")
    result = api_logic.run_surprise_show(KEYS, SESSION_ID)
    assert result["voice"] == "openai" and result["fellBack"] is True
    assert sorted(run_env["spoken"]) == sorted(("openai", line["say"]) for line in SCRIPT), "one voice for the whole show"
    assert run_env["clips"].pruned == [(SESSION_ID, sorted(clip_key(line, OPENAI) for line in SCRIPT))]


def test_an_openai_main_voice_has_no_backup(run_env, monkeypatch):
    monkeypatch.setattr(api_logic, "voice_plan", lambda: (dict(OPENAI), None))
    run_env["refuse"].add("openai")
    with pytest.raises(VoiceUnavailable):
        api_logic.run_surprise_show(KEYS, SESSION_ID)


def test_a_failed_clip_fails_the_show_and_keeps_only_whole_clips(run_env, monkeypatch):
    def speak(line, settings, keys):
        if line["beat"] == 4:
            raise SpeechFailed("ElevenLabs refused to make the voice (HTTP 400).")
        return _mp3(2)

    monkeypatch.setattr(api_logic, "record_line", speak)
    monkeypatch.setattr(api_logic, "SURPRISE_VOICE_WORKERS", 1)
    with pytest.raises(SpeechFailed, match="HTTP 400"):
        api_logic.run_surprise_show(KEYS, SESSION_ID)
    clips = run_env["clips"]
    assert clips.pruned == [], "nothing tidied after a failure"
    assert all(saved[2] == _mp3(2) for saved in clips.saved), "every saved clip is a whole answer"


def test_the_show_checks_the_switch_again(run_env, monkeypatch):
    monkeypatch.setattr(api_logic, "fetch_surprise_enabled", lambda org: False)
    with pytest.raises(api_logic.SurpriseOffError):
        api_logic.run_surprise_show(KEYS, SESSION_ID)
    assert run_env["spoken"] == []


def test_the_worker_job_reads_its_keys_from_the_environment_and_clears_its_progress(monkeypatch):
    cleared, seen = [], {}
    monkeypatch.setattr(worker, "clear_progress_sync", lambda kind, key: cleared.append((kind, key)))
    monkeypatch.setattr(worker, "publish_progress_sync", lambda *args, **kwargs: None)
    env = {"OPENAI_API_KEY": "sk-env", "ELEVENLABS_API_KEY": "xi-env"}
    monkeypatch.setattr(worker, "get_runtime_secret", lambda name, default="": env.get(name, default))

    def run(keys, session_id, *, publish):
        seen["keys"] = dict(keys)
        return {"status": "ok", "made": 0, "lines": 1, "story": "made"}

    monkeypatch.setattr(api_logic, "run_surprise_show", run)
    monkeypatch.setenv("CLAUDE_API_KEY", "claude-env")
    assert asyncio.run(worker.run_surprise_show_job({}, SESSION_ID))["status"] == "ok"
    assert seen["keys"] == {"claude": "claude-env", "openai": "sk-env", "elevenlabs": "xi-env"}, "every key from the worker's env"
    monkeypatch.delenv("CLAUDE_API_KEY")
    asyncio.run(worker.run_surprise_show_job({}, SESSION_ID))
    assert seen["keys"]["claude"] == ""
    assert cleared == [("surprise_show", SESSION_ID)] * 2
    names = {getattr(item, "name", None) or getattr(item, "__name__", "") for item in worker.WorkerSettings.functions}
    assert "run_surprise_show_job" in names and "run_surprise_voice_job" not in names


# ── The routes ──

SHOW = "/api/sessions/{session_id}/surprise-show"
ROUTE_LINES = [
    {"beat": 1, "say": "SECRETSCRIPTWORDS one", "tone": "building"},
    {"beat": 2, "say": "SECRETSCRIPTWORDS two", "tone": "deadpan"},
    {"beat": 2, "say": "SECRETSCRIPTWORDS three", "tone": "sarcastic"},
    {"beat": 3, "say": "SECRETSCRIPTWORDS four", "tone": "punchline"},
]
ROUTE_MAIN = {"provider": "elevenlabs", "model": "eleven_v3", "voice": "fnYMz3F5gMEDGMWcH1ex", "notes": "stability=0.5"}
ROUTE_BACKUP = {"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "marin", "notes": "Warm."}


def _with_script(monkeypatch):
    monkeypatch.setattr(
        api_logic, "fetch_surprise_script", lambda sid, key, *, organization_id: {"version": 1, "source": "model", "lines": ROUTE_LINES}
    )


def _with_clips(monkeypatch, settings):
    keys = {clip_key(line, settings) for line in ROUTE_LINES}
    monkeypatch.setattr(
        api_logic,
        "fetch_surprise_voice_clips",
        lambda sid, wanted, *, organization_id: {key: {"duration_s": 1.5, "size": 9} for key in wanted if key in keys},
    )


def test_make_the_show_queues_one_job_with_the_session_id_only(client, backend, monkeypatch):  # noqa: F811
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: {"jobStatus": "not_found"})
    response = _call(client, "POST", SHOW, cookie="conn-a", body={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] in ("pending", "running") and body["script"] == "missing"
    assert [beat["text"] for beat in body["beats"]] == ["The take", "ORGACONFIDENTIALMARKER rocks.", "Truly."], "as the player shows it"
    assert backend.enqueued == [("run_surprise_show_job", f"surprise_show:{SESSION_ID}", SESSION_ID)]


def test_reading_the_show_starts_nothing(client, backend):  # noqa: F811
    response = _call(client, "GET", SHOW, cookie="conn-a")
    assert response.status_code == 200
    assert response.json()["state"] == "running" and backend.enqueued == []
    _call(client, "POST", SHOW, cookie="conn-a", body={})
    assert backend.enqueued == [], "a show being made is not queued twice"


def test_a_show_already_made_is_not_queued_again_and_never_sends_the_script(client, backend, monkeypatch):  # noqa: F811
    _with_script(monkeypatch)
    _with_clips(monkeypatch, ROUTE_MAIN)
    scene = {"effect": "pop", "headline": "rocks", "number": None, "quote": None}
    monkeypatch.setattr(
        api_logic, "fetch_surprise_storyboard", lambda sid, key, *, organization_id: {"version": 1, "source": "model", "scenes": [None, scene]}
    )
    response = _call(client, "POST", SHOW, cookie="conn-a", body={})
    body = response.json()
    assert body["state"] == "ready" and body["total"] == 4 and body["ready"] == 4
    assert [line["beat"] for line in body["lines"]] == [0, 1, 1, 2], "each line by its beat's index"
    assert all(line["ready"] and line["durationS"] == 1.5 for line in body["lines"])
    assert body["voice"] == {
        "provider": "elevenlabs", "model": "eleven_v3", "voice": "fnYMz3F5gMEDGMWcH1ex", "label": "Finley (British, deep)", "fellBack": False,
    }
    assert body["story"] == "ready" and [beat["scene"] for beat in body["beats"]] == [None, scene, None]
    assert "SECRETSCRIPTWORDS" not in response.text and "say" not in body["lines"][0], "the script is only ever heard"
    assert backend.enqueued == []


def test_a_show_recorded_with_the_backup_says_so(client, backend, monkeypatch):  # noqa: F811
    _with_script(monkeypatch)
    _with_clips(monkeypatch, ROUTE_BACKUP)
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: {"jobStatus": "complete"})
    body = _call(client, "GET", SHOW, cookie="conn-a").json()
    assert body["state"] == "ready" and body["voice"]["fellBack"] is True and body["voice"]["provider"] == "openai"
    assert body["voice"]["label"] == "Marin"
    assert all(line["clip"] == clip_key(ROUTE_LINES[i], ROUTE_BACKUP) for i, line in enumerate(body["lines"]))


def test_a_show_without_its_storyboard_plays_and_the_next_click_tries_again(client, backend, monkeypatch):  # noqa: F811
    _with_script(monkeypatch)
    _with_clips(monkeypatch, ROUTE_MAIN)
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: {"jobStatus": "complete"})
    read = _call(client, "GET", SHOW, cookie="conn-a").json()
    assert read["state"] == "ready" and read["story"] == "missing", "the player draws its own scenes"
    assert backend.enqueued == []


def test_a_failed_show_says_why(client, backend, monkeypatch):  # noqa: F811
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: {"jobStatus": "error", "error": "OpenAI could not be reached."})
    body = _call(client, "GET", SHOW, cookie="conn-a").json()
    assert body["state"] == "error" and body["error"] == "OpenAI could not be reached."
    _call(client, "POST", SHOW, cookie="conn-a", body={})
    assert len(backend.enqueued) == 1, "the user's retry queues the show again"


@pytest.mark.parametrize(("method", "path"), [("POST", SHOW), ("GET", SHOW), ("GET", SHOW + "/clip")])
def test_the_show_is_refused_when_surprise_me_is_off(client, backend, monkeypatch, method, path):  # noqa: F811
    monkeypatch.setattr(api_logic, "fetch_surprise_enabled", lambda org: False)
    response = _call(client, method, path, cookie="conn-a", body={})
    assert response.status_code == 403
    assert "turned off" in response.json()["detail"]["message"]
    assert backend.enqueued == []


def test_without_a_take_the_click_writes_it_and_without_a_transcript_it_is_refused(client, backend, monkeypatch):  # noqa: F811
    row = {"organization_id": "org-a", "smart_recap_bundle": {}, "has_transcript": True}
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: {"jobStatus": "not_found"})
    assert _call(client, "GET", SHOW, cookie="conn-a").json()["state"] == "none"
    assert _call(client, "POST", SHOW, cookie="conn-a", body={}).status_code == 200
    assert backend.enqueued == [("run_surprise_show_job", f"surprise_show:{SESSION_ID}", SESSION_ID)]
    row["has_transcript"] = False
    response = _call(client, "POST", SHOW, cookie="conn-a", body={})
    assert response.status_code == 409 and "no transcript" in response.json()["detail"]["message"]
    assert len(backend.enqueued) == 1


def test_a_clip_is_private_audio(client):  # noqa: F811
    response = _call(client, "GET", SHOW + "/clip", cookie="conn-a")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["cache-control"].startswith("private")


def test_a_malformed_clip_key_is_refused(client):  # noqa: F811
    client.cookies.set(oauth_client.LIVESTORM_OAUTH_COOKIE, "conn-a")
    response = client.get(f"/api/sessions/{SESSION_ID}/surprise-show/clip", params={"key": "../etc"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("key", "value", "status", "saved"),
    [
        ("tts_voice", " Cedar ", 200, "cedar"),
        ("tts_voice", "robot", 400, None),
        ("voice_provider", "OpenAI", 200, "openai"),
        ("voice_provider", "elevenlabs", 200, "elevenlabs"),
        ("voice_provider", "robot", 400, None),
        ("elevenlabs_voice", "iP95p4xoKVk53GoZ742B", 200, "iP95p4xoKVk53GoZ742B"),
        ("elevenlabs_voice", "../etc/passwd", 400, None),
    ],
)
def test_admin_may_pick_the_provider_and_the_voices(client, saved_settings, key, value, status, saved):  # noqa: F811
    response = _save_setting(client, key, value)
    assert response.status_code == status
    assert saved_settings == ([(key, saved)] if status == 200 else [])


def test_admins_voice_lists_are_the_servers():
    import re

    source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "views" / "AdminView.vue").read_text(encoding="utf-8")
    listed = re.search(r"const TTS_VOICES = \[(.*?)\];", source).group(1)
    assert tuple(re.findall(r'"([a-z]+)"', listed)) == voice.TTS_VOICES
    assert re.search(r'const DEFAULT_TTS_VOICE = "([a-z]+)";', source).group(1) == voice.DEFAULT_TTS_VOICE == "cedar"
    labels = re.search(r"const VOICE_LABELS = \{(.*?)\};", source, re.DOTALL).group(1)
    assert tuple(re.findall(r"^\s*([a-z]+):", labels, re.MULTILINE)) == voice.TTS_VOICES, "a label for every voice"
    providers = re.search(r"const VOICE_PROVIDERS = \[(.*?)\];", source, re.DOTALL).group(1)
    assert tuple(re.findall(r'value: "([a-z]+)"', providers)) == voice.VOICE_PROVIDERS
    assert re.search(r'const DEFAULT_VOICE_PROVIDER = "([a-z]+)";', source).group(1) == voice.DEFAULT_VOICE_PROVIDER
    presets = re.search(r"const ELEVENLABS_VOICES = \[(.*?)\];", source, re.DOTALL).group(1)
    assert re.findall(r'id: "([A-Za-z0-9]+)", label: "([^"]+)"', presets) == list(eleven.VOICE_LABELS.items()), "Finley first"


# ── Stored ──


def test_the_script_is_stored_whole_by_its_key_and_in_scope(test_database):
    db.upsert_cached_session("key-a", "sess-p", session_payload={"name": "A"}, organization_id="org-a")
    script = {"version": 1, "source": "model", "lines": [{"beat": 1, "say": "Well...", "tone": "deadpan"}]}
    db.save_surprise_script("sess-p", "k1", script)
    assert db.fetch_surprise_script("sess-p", "k1", organization_id="org-a") == script
    assert db.fetch_surprise_script("sess-p", "k1", organization_id="org-b") is None
    assert db.fetch_surprise_script("sess-p", "k1", organization_id="  ") is None
    db.save_surprise_script("sess-p", "k2", {**script, "source": "take"})
    assert db.fetch_surprise_script("sess-p", "k1", organization_id=db.ALL_ORGS) is None, "a new take replaces the old one"
    with pytest.raises(RuntimeError, match="Could not save the performance script"):
        db.save_surprise_script("sess-missing", "k1", script)


def test_clips_are_stored_whole_in_scope_and_tidied(test_database):
    db.upsert_cached_session("key-a", "sess-v", session_payload={"name": "A"}, organization_id="org-a")
    db.save_surprise_voice_clip("sess-v", "k1", audio=b"one", mime="audio/mpeg", duration_s=1.25)
    db.save_surprise_voice_clip("sess-v", "k1", audio=b"one!", mime="audio/mpeg", duration_s=1.5)
    db.save_surprise_voice_clip("sess-v", "k2", audio=b"two", mime="audio/mpeg", duration_s=None)
    assert db.fetch_surprise_voice_clips("sess-v", ["k1", "k2", "k3"], organization_id="org-a") == {
        "k1": {"duration_s": 1.5, "size": 4},
        "k2": {"duration_s": None, "size": 3},
    }
    assert db.fetch_surprise_voice_clips("sess-v", ["k1"], organization_id="org-b") == {}
    assert db.fetch_surprise_voice_clips("sess-v", ["k1"], organization_id="  ") == {}
    assert db.fetch_surprise_voice_clip("sess-v", "k1", organization_id="org-a") == {"bytes": b"one!", "mime": "audio/mpeg"}
    assert db.fetch_surprise_voice_clip("sess-v", "k1", organization_id="org-b") is None
    assert db.prune_surprise_voice_clips("sess-v", ["k2"]) == 1
    assert db.fetch_surprise_voice_clip("sess-v", "k1", organization_id=db.ALL_ORGS) is None
    with pytest.raises(RuntimeError, match="Could not save the voice clip"):
        db.save_surprise_voice_clip("sess-missing", "k1", audio=b"x", mime="audio/mpeg", duration_s=None)


if __name__ == "__main__":
    FIXTURE.write_text(json.dumps(fixture_cases(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE}")
