"""Surprise me, read aloud (`ai/surprise_voice.py`, `api_logic.run_surprise_voice`,
`worker.run_surprise_voice_job`, the `surprise-voice` routes): the beats cut on
the server exactly as the player cuts them (the shared fixture), the clips'
cache key, the OpenAI call (only the beat's text, a timeout, retried only when
nothing was made), each clip saved whole, the job queued with ids only from a
click, the Surprise me switch, and the clips stored in scope.

Never calls OpenAI: `requests.post` is replaced. To regenerate the shared
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
from livestorm_app.ai.surprise_voice import (
    SpeechFailed,
    clean_recap,
    clip_key,
    mp3_duration_s,
    resolve_voice,
    surprise_beats,
    synthesize_speech,
)

FIXTURE = Path(__file__).resolve().parents[1] / "frontend" / "tests" / "fixtures" / "surprise-beats-v1.json"
SETTINGS = {"model": "gpt-4o-mini-tts", "voice": "marin", "instructions": "Warm, upbeat presenter; lively but clear."}

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


# ── The voice and its cache key ──


def test_a_clip_key_changes_with_the_text_the_voice_the_model_and_the_notes():
    base = clip_key("Hello.", SETTINGS)
    assert base == clip_key("Hello.", dict(SETTINGS)) and len(base) == 64
    assert base != clip_key("Hello!", SETTINGS)
    for field, other in (("voice", "cedar"), ("model", "tts-1"), ("instructions", "Calm.")):
        assert base != clip_key("Hello.", {**SETTINGS, field: other})


def test_a_voice_gpt_4o_mini_tts_does_not_offer_becomes_cedar():
    assert resolve_voice(" Marin ") == "marin"
    assert resolve_voice("not-a-voice") == "cedar" and resolve_voice(None) == "cedar"


def test_the_voice_settings_read_the_setting_and_the_prompt_file(monkeypatch):
    monkeypatch.setattr("livestorm_app.db.get_setting", lambda key: "sage" if key == "tts_voice" else None)
    settings = voice.voice_settings()
    assert settings["voice"] == "sage" and settings["model"] == "gpt-4o-mini-tts"
    assert settings["instructions"] == Path("prompts/surprise_voice.txt").read_text(encoding="utf-8").strip()


def test_the_delivery_notes_ask_for_a_dry_wry_man_who_never_mocks_anyone():
    notes = Path("prompts/surprise_voice.txt").read_text(encoding="utf-8")
    for words in ("a man", "Dry, wry and a little sarcastic", "warm underneath", "deadpan pause", "Never mean", "never at a person"):
        assert words in notes
    assert len(notes) < 1500, "short notes: gpt-4o-mini-tts reads them with every sentence"


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


def test_only_the_beats_text_and_the_voice_go_to_openai_with_a_timeout(monkeypatch):
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
    row = {"organization_id": "org-a", "smart_recap_bundle": {"surprise": TAKE, "professional": "# Title\nX"}}
    spoken = []
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "fetch_surprise_enabled", lambda org: True)
    monkeypatch.setattr(api_logic, "voice_settings", lambda: dict(SETTINGS))
    monkeypatch.setattr(api_logic, "fetch_surprise_voice_clips", clips.fetch)
    monkeypatch.setattr(api_logic, "save_surprise_voice_clip", clips.save)
    monkeypatch.setattr(api_logic, "prune_surprise_voice_clips", clips.prune)
    # No storyboard yet, and no Claude key passed: the run skips it (tests/test_surprise_story.py).
    monkeypatch.setattr(api_logic, "fetch_surprise_storyboard", lambda sid, key, *, organization_id: None)

    def speak(api_key, text, settings):
        spoken.append(text)
        return _mp3(5)

    monkeypatch.setattr(api_logic, "synthesize_speech", speak)
    return {"clips": clips, "row": row, "spoken": spoken}


def test_the_run_reads_each_beat_once_saves_each_clip_whole_and_tidies_the_rest(run_env):
    published = []
    result = api_logic.run_surprise_voice("sk-test", SESSION_ID, publish=lambda stage, extra: published.append((stage, extra)))
    beats = surprise_beats(TAKE)
    assert sorted(run_env["spoken"]) == sorted(beat["text"] for beat in beats), "only the beats' text is read aloud"
    clips = run_env["clips"]
    assert [saved[3] for saved in clips.saved] == ["audio/mpeg"] * len(beats)
    assert all(saved[4] == pytest.approx(5 * 1152 / 44100, abs=0.001) for saved in clips.saved)
    keys = sorted(clip_key(beat["text"], SETTINGS) for beat in beats)
    assert clips.pruned == [(SESSION_ID, keys)]
    assert result == {"status": "ok", "made": len(beats), "beats": len(beats), "story": "skipped"}
    assert published[0] == ("storyboard", {})
    assert published[1] == ("speaking", {"done": 0, "total": len(beats), "percent": 10})
    assert published[-1] == ("speaking", {"done": len(beats), "total": len(beats), "percent": 95})


def test_a_replay_costs_nothing_and_a_new_take_makes_only_its_new_clips(run_env):
    api_logic.run_surprise_voice("sk-test", SESSION_ID)
    run_env["spoken"].clear()
    api_logic.run_surprise_voice("sk-test", SESSION_ID)
    assert run_env["spoken"] == [], "every clip was already there"
    run_env["row"]["smart_recap_bundle"]["surprise"] = TAKE + " One more line!"
    api_logic.run_surprise_voice("sk-test", SESSION_ID)
    # "56% stayed for pricing." is no longer the punchline, but it is the same sentence: its clip is kept.
    assert run_env["spoken"] == ["One more line!"]


def test_the_same_sentence_twice_is_read_once(run_env):
    run_env["row"]["smart_recap_bundle"]["surprise"] = "Again. Again.\n\nAgain. Again."
    api_logic.run_surprise_voice("sk-test", SESSION_ID)
    assert run_env["spoken"] == ["Again."]


def test_a_failed_clip_fails_the_run_and_keeps_only_whole_clips(run_env, monkeypatch):
    def speak(api_key, text, settings):
        if text == "Nobody believed it.":
            raise SpeechFailed("OpenAI refused to make the voice (HTTP 400).")
        return _mp3(2)

    monkeypatch.setattr(api_logic, "synthesize_speech", speak)
    monkeypatch.setattr(api_logic, "SURPRISE_VOICE_WORKERS", 1)
    with pytest.raises(SpeechFailed, match="HTTP 400"):
        api_logic.run_surprise_voice("sk-test", SESSION_ID)
    clips = run_env["clips"]
    assert clips.pruned == [], "nothing tidied after a failure"
    assert all(saved[2] == _mp3(2) for saved in clips.saved), "every saved clip is a whole answer"


def test_the_run_checks_the_switch_again_and_needs_a_take(run_env, monkeypatch):
    monkeypatch.setattr(api_logic, "fetch_surprise_enabled", lambda org: False)
    with pytest.raises(api_logic.SurpriseOffError):
        api_logic.run_surprise_voice("sk-test", SESSION_ID)
    monkeypatch.setattr(api_logic, "fetch_surprise_enabled", lambda org: True)
    run_env["row"]["smart_recap_bundle"].pop("surprise")
    with pytest.raises(api_logic.SurpriseVoiceNoTakeError):
        api_logic.run_surprise_voice("sk-test", SESSION_ID)
    assert run_env["spoken"] == []


def test_the_worker_job_reads_its_key_from_the_environment_and_clears_its_progress(monkeypatch):
    cleared, seen = [], {}
    monkeypatch.setattr(worker, "clear_progress_sync", lambda kind, key: cleared.append((kind, key)))
    monkeypatch.setattr(worker, "publish_progress_sync", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "get_runtime_secret", lambda name, default="": "")
    with pytest.raises(SpeechFailed, match="no OpenAI key"):
        asyncio.run(worker.run_surprise_voice_job({}, SESSION_ID))
    monkeypatch.setattr(worker, "get_runtime_secret", lambda name, default="": "sk-env" if name == "OPENAI_API_KEY" else default)

    def run(api_key, session_id, *, publish, llm_key):
        seen["key"], seen["llm_key"] = api_key, llm_key
        return {"status": "ok", "made": 0, "beats": 1, "story": "made"}

    monkeypatch.setattr(api_logic, "run_surprise_voice", run)
    monkeypatch.setenv("CLAUDE_API_KEY", "claude-env")
    assert asyncio.run(worker.run_surprise_voice_job({}, SESSION_ID))["status"] == "ok"
    assert seen["key"] == "sk-env" and seen["llm_key"] == "claude-env", "both keys from the worker's own environment"
    monkeypatch.delenv("CLAUDE_API_KEY")
    asyncio.run(worker.run_surprise_voice_job({}, SESSION_ID))
    assert seen["llm_key"] == "", "without a Claude key the voice is still made"
    assert cleared == [("surprise_voice", SESSION_ID)] * 3
    names = {getattr(item, "name", None) or getattr(item, "__name__", "") for item in worker.WorkerSettings.functions}
    assert "run_surprise_voice_job" in names


# ── The routes ──

VOICE = "/api/sessions/{session_id}/surprise-voice"


def test_play_queues_the_voice_with_the_session_id_only(client, backend):  # noqa: F811
    response = _call(client, "POST", VOICE, cookie="conn-a", body={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] in ("pending", "running") and body["total"] == 3
    assert [beat["text"] for beat in body["beats"]] == ["The take", "ORGACONFIDENTIALMARKER rocks.", "Truly."], "as the player shows it: no underscores"
    assert backend.enqueued == [("run_surprise_voice_job", f"surprise_voice:{SESSION_ID}", SESSION_ID)]


def test_reading_the_voice_starts_nothing(client, backend):  # noqa: F811
    response = _call(client, "GET", VOICE, cookie="conn-a")
    assert response.status_code == 200
    assert response.json()["state"] == "running" and backend.enqueued == []


def test_a_voice_already_made_is_not_queued_again(client, backend, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        api_logic,
        "fetch_surprise_voice_clips",
        lambda sid, keys, *, organization_id: {key: {"duration_s": 1.5, "size": 9} for key in keys},
    )
    scene = {"effect": "pop", "headline": "rocks", "number": None, "quote": None}
    monkeypatch.setattr(
        api_logic, "fetch_surprise_storyboard", lambda sid, key, *, organization_id: {"version": 1, "source": "model", "scenes": [None, scene]}
    )
    body = _call(client, "POST", VOICE, cookie="conn-a", body={}).json()
    assert body["state"] == "ready" and all(beat["ready"] and beat["durationS"] == 1.5 for beat in body["beats"])
    assert body["story"] == "ready" and [beat["scene"] for beat in body["beats"]] == [None, scene, None]
    assert backend.enqueued == []


def test_a_voice_made_without_its_storyboard_plays_and_play_tries_the_storyboard_again(client, backend, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        api_logic,
        "fetch_surprise_voice_clips",
        lambda sid, keys, *, organization_id: {key: {"duration_s": 1.5, "size": 9} for key in keys},
    )
    monkeypatch.setattr(api_logic, "read_job_status_sync", lambda job_id: {"jobStatus": "complete"})
    read = _call(client, "GET", VOICE, cookie="conn-a").json()
    assert read["state"] == "ready" and read["story"] == "missing", "the player draws its own scenes"
    assert all(beat["scene"] is None for beat in read["beats"]) and backend.enqueued == []
    _call(client, "POST", VOICE, cookie="conn-a", body={})
    assert backend.enqueued == [("run_surprise_voice_job", f"surprise_voice:{SESSION_ID}", SESSION_ID)]


@pytest.mark.parametrize(("method", "path"), [("POST", VOICE), ("GET", VOICE), ("GET", VOICE + "/clip")])
def test_the_voice_is_refused_when_surprise_me_is_off(client, backend, monkeypatch, method, path):  # noqa: F811
    monkeypatch.setattr(api_logic, "fetch_surprise_enabled", lambda org: False)
    response = _call(client, method, path, cookie="conn-a", body={})
    assert response.status_code == 403
    assert "turned off" in response.json()["detail"]["message"]
    assert backend.enqueued == []


def test_play_without_a_take_is_refused(client, backend, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: {"organization_id": "org-a", "smart_recap_bundle": {}}
    )
    response = _call(client, "POST", VOICE, cookie="conn-a", body={})
    assert response.status_code == 409 and backend.enqueued == []
    assert _call(client, "GET", VOICE, cookie="conn-a").json()["state"] == "none"


def test_a_clip_is_private_audio(client):  # noqa: F811
    response = _call(client, "GET", VOICE + "/clip", cookie="conn-a")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["cache-control"].startswith("private")


def test_a_malformed_clip_key_is_refused(client):  # noqa: F811
    client.cookies.set(oauth_client.LIVESTORM_OAUTH_COOKIE, "conn-a")
    response = client.get(f"/api/sessions/{SESSION_ID}/surprise-voice/clip", params={"key": "../etc"})
    assert response.status_code == 422


@pytest.mark.parametrize(("value", "status"), [(" Cedar ", 200), ("robot", 400)])
def test_admin_may_pick_one_of_the_voices_offered(client, saved_settings, value, status):  # noqa: F811
    response = _save_setting(client, "tts_voice", value)
    assert response.status_code == status
    assert saved_settings == ([("tts_voice", "cedar")] if status == 200 else [])


def test_admins_voice_list_is_the_servers():
    import re

    source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "views" / "AdminView.vue").read_text(encoding="utf-8")
    listed = re.search(r"const TTS_VOICES = \[(.*?)\];", source).group(1)
    assert tuple(re.findall(r'"([a-z]+)"', listed)) == voice.TTS_VOICES
    assert re.search(r'const DEFAULT_TTS_VOICE = "([a-z]+)";', source).group(1) == voice.DEFAULT_TTS_VOICE == "cedar"
    labels = re.search(r"const VOICE_LABELS = \{(.*?)\};", source, re.DOTALL).group(1)
    assert tuple(re.findall(r"^\s*([a-z]+):", labels, re.MULTILINE)) == voice.TTS_VOICES, "a label for every voice"


# ── Stored ──


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
