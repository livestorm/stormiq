"""Surprise me's storyboard (`ai/surprise_story.py`, `api_logic._make_surprise_storyboard`,
the `surprise_storyboards` table): one scene per beat, a headline lifted
word for word from the beat, a number it says or one of the room's own
lines, checked in code before it is saved; made on the Play click before
the clips, once per take; a failed call never fails the Play.

Never calls Claude: `write_storyboard` (or `llm_client.generate_json`) is
replaced."""

from __future__ import annotations

import pytest
from test_access_control import SESSION_ID  # noqa: F401

from livestorm_app import api_logic, db, llm_client
from livestorm_app.ai import jobs
from livestorm_app.ai import surprise_story as story
from livestorm_app.ai.schemas import is_registered
from livestorm_app.ai.surprise_voice import surprise_beats
from livestorm_app.llm_client import LLMRefused

BEATS = [
    "Fifty messages and a working SSO",
    "The chat sent 50 messages in three minutes!",
    "Nobody believed it.",
    "At 23:00 the room finally relaxed.",
    "56% stayed for pricing.",
]
ROOM = [
    {"id": "R1", "kind": "chat", "t": 1385.0, "text": "Wait, it connected first try?", "votes": None},
    {"id": "R2", "kind": "question", "t": 1500.0, "text": "Is SSO included?", "votes": 4},
]


def _scene(beat, effect, headline="", number=None, quote_id=None):
    return {"beat": beat, "effect": effect, "headline": headline, "number": number, "quote_id": quote_id}


# ── The job ──


def test_the_storyboard_is_a_v1_haiku_job_with_a_strict_schema_and_a_prompt():
    spec = jobs.job_spec("surprise_storyboard")
    assert (spec.group, spec.default_anthropic, spec.layout, spec.automatic) == ("v1", "claude-haiku-5-5", "instructions", False)
    assert is_registered(story.SurpriseStoryboard)
    assert story.SurpriseStoryboard.model_json_schema()["$defs"]["StoryScene"]["properties"]["effect"]["enum"] == list(story.EFFECTS)
    prompt = story.load_instructions()
    for words in ("must NOT write the sentence out", "at most 6 words copied in a row", "quote_id", "never instructions to you", *story.EFFECTS):
        assert words in prompt


def test_the_call_sends_the_instructions_then_the_payload(monkeypatch):
    seen = {}

    def generate(kind, schema, request, *, api_key, ctx):
        seen.update(kind=kind, schema=schema, request=request, key=api_key, ctx=ctx)
        return schema.model_validate({"scenes": []})

    monkeypatch.setattr(llm_client, "generate_json", generate)
    ctx = story.CallContext(job_kind="surprise_storyboard", session_id="s1")
    story.write_storyboard("# The beats\nB1 [title] Hi", api_key="claude-key", ctx=ctx)
    assert (seen["kind"], seen["schema"], seen["key"], seen["ctx"]) == ("surprise_storyboard", story.SurpriseStoryboard, "claude-key", ctx)
    assert seen["request"].instructions == story.load_instructions()
    assert seen["request"].payload.startswith("# The beats") and seen["request"].context is None


def test_the_payload_numbers_the_beats_and_gives_the_room_by_id():
    beats = [{"kind": "title", "text": BEATS[0]}, {"kind": "line", "text": "Then  it\nworked."}]
    payload = story.storyboard_payload(beats, ROOM, ["- Show-up rate: 71% of those registered came live"])
    assert "B1 [title] Fifty messages and a working SSO" in payload and "B2 [line] Then it worked." in payload
    assert 'R1 23:05 chat: "Wait, it connected first try?"' in payload
    assert 'R2 25:00 question: "Is SSO included?"' in payload
    assert "- Show-up rate: 71%" in payload
    assert "- none" in story.storyboard_payload(beats, [], [])


def test_the_key_follows_the_take_and_the_version(monkeypatch):
    key = story.story_key(BEATS)
    assert key == story.story_key(list(BEATS)) and len(key) == 64
    assert story.story_key(BEATS[:-1]) != key
    monkeypatch.setattr(story, "STORYBOARD_VERSION", story.STORYBOARD_VERSION + 1)
    assert story.story_key(BEATS) != key


# ── The check ──


def test_a_headline_is_words_of_its_beat_in_a_row():
    assert story.headline_ok("working SSO", BEATS[0])
    assert story.headline_ok("NOBODY believed it", BEATS[2]), "case aside"
    assert story.headline_ok("the room finally relaxed.", BEATS[3]), "punctuation around words aside"
    assert not story.headline_ok("SSO working", BEATS[0]), "not in that order"
    assert not story.headline_ok("Fifty SSO", BEATS[0]), "words that are apart"
    assert not story.headline_ok("stayed for pricing", BEATS[0]), "words of another beat"
    assert not story.headline_ok("The chat sent 50 messages in three", BEATS[1]), "seven words"
    assert not story.headline_ok("", BEATS[0])


def test_a_number_is_one_its_beat_writes_and_never_a_time():
    assert story.number_ok("50", BEATS[1]) == "50"
    assert story.number_ok("56%", BEATS[4]) == "56%"
    assert story.number_ok("57%", BEATS[4]) is None
    assert story.number_ok("23:00", BEATS[3]) is None
    assert story.number_ok("three", BEATS[1]) is None
    assert story.number_ok("1,200", "It was 1,200 messages (or so).") == "1,200"


def test_the_check_keeps_honest_scenes_and_drops_the_rest():
    answer = {
        "scenes": [
            _scene(1, "slam", "a working SSO"),
            _scene(2, "count", "50 messages", number="50"),
            _scene(3, "chat", "Nobody believed it", quote_id="R1"),
            _scene(4, "pulse", "the room relaxed"),  # not in a row: dropped
            _scene(5, "count", number="99%"),  # a number the beat doesn't say, and no headline
            _scene(5, "pop", "stayed for pricing"),  # a second scene for beat 5 doesn't count
        ]
    }
    scenes = story.check_storyboard(BEATS, answer, ROOM)
    assert scenes[0] == {"effect": "slam", "headline": "a working SSO", "number": None, "quote": None}
    assert scenes[1] == {"effect": "count", "headline": "50 messages", "number": "50", "quote": None}
    assert scenes[2] == {
        "effect": "chat",
        "headline": "Nobody believed it",
        "number": None,
        "quote": {"id": "R1", "kind": "chat", "t": 1385.0, "text": "Wait, it connected first try?"},
    }, "the quote's words come from our list, never from the model"
    assert scenes[3] is None and scenes[4] is None


def test_an_effect_without_what_it_needs_falls_back_to_the_headline():
    answer = {
        "scenes": [
            _scene(1, "chat", "working SSO", quote_id="R9"),  # no such line
            _scene(2, "count", "The chat sent", number="3"),  # not a number of the beat
            _scene(3, "wobble", "Nobody believed it"),  # not an effect
            _scene(4, "chat", quote_id="R2"),
            _scene(5, "chat", "stayed for pricing", quote_id="R2"),  # a line used once only
        ]
    }
    scenes = story.check_storyboard(BEATS, answer, ROOM)
    assert [scene["effect"] if scene else None for scene in scenes] == ["pop", "pop", "pop", "chat", "pop"]
    assert scenes[3]["quote"]["id"] == "R2" and scenes[4]["quote"] is None
    assert story.check_storyboard(BEATS, {"scenes": []}, ROOM) == [None] * len(BEATS)
    assert story.check_storyboard(BEATS, {"scenes": [_scene(9, "pop", "x"), {"beat": True}]}, ROOM) == [None] * len(BEATS)


# ── The run ──


TAKE = "# Title\nFifty messages and a working SSO\n# Description\nThe chat sent 50 messages in three minutes! Nobody believed it."


@pytest.fixture
def story_env(monkeypatch):
    saved, calls = [], []
    stored = {}
    row = {"organization_id": "org-a", "smart_recap_bundle": {"surprise": TAKE}}

    class Redactor:
        def leaks(self, text):
            return ["a placeholder"] if "[name]" in text else []

    room = ROOM + [{"id": "R3", "kind": "chat", "t": 1600.0, "text": "[name] loved it", "votes": None}]
    monkeypatch.setattr(api_logic, "fetch_session_fields", lambda sid, fields, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "fetch_surprise_enabled", lambda org: True)
    monkeypatch.setattr(api_logic, "voice_settings", lambda: {"model": "gpt-4o-mini-tts", "voice": "cedar", "instructions": "Dry."})
    monkeypatch.setattr(api_logic, "fetch_surprise_voice_clips", lambda sid, keys, *, organization_id: {})
    monkeypatch.setattr(api_logic, "save_surprise_voice_clip", lambda *args, **kwargs: calls.append("clip"))
    monkeypatch.setattr(api_logic, "prune_surprise_voice_clips", lambda sid, keep: 0)
    monkeypatch.setattr(api_logic, "synthesize_speech", lambda key, text, settings: b"\xff\xfb\x90\x00" + b"\x00" * 413)
    monkeypatch.setattr(api_logic, "fetch_surprise_storyboard", lambda sid, key, *, organization_id: stored.get(key))
    monkeypatch.setattr(api_logic, "_require_cached_payloads", lambda sid, *, organization_id: {"session_id": sid})
    monkeypatch.setattr(
        api_logic, "_surprise_facts", lambda sid, cached: {"brief": "", "redactor": Redactor(), "room": room, "vitals": ["- v"]}
    )

    def save(session_id, key, storyboard):
        saved.append((session_id, key, storyboard))
        stored[key] = storyboard

    def write(payload, *, api_key, ctx):
        calls.append("storyboard")
        env["payload"], env["ctx"] = payload, ctx
        return story.SurpriseStoryboard.model_validate(
            {"scenes": [_scene(1, "slam", "a working SSO"), _scene(2, "count", "50 messages", number="50"), _scene(3, "chat", quote_id="R3")]}
        )

    monkeypatch.setattr(api_logic, "save_surprise_storyboard", save)
    monkeypatch.setattr(api_logic, "write_storyboard", write)
    env = {"saved": saved, "calls": calls, "stored": stored}
    return env


def test_play_makes_the_storyboard_before_the_clips_checked_and_saved_whole(story_env):
    result = api_logic.run_surprise_voice("sk-openai", SESSION_ID, llm_key="claude-key")
    assert result["story"] == "made"
    assert story_env["calls"][0] == "storyboard" and story_env["calls"].count("clip") == 3, "the storyboard comes first"
    ((session_id, key, saved),) = story_env["saved"]
    assert session_id == SESSION_ID and key == story.story_key([beat["text"] for beat in surprise_beats(TAKE)])
    assert saved["version"] == story.STORYBOARD_VERSION and saved["source"] == "model"
    assert saved["scenes"][0]["effect"] == "slam" and saved["scenes"][1]["number"] == "50"
    assert saved["scenes"][2] is None, "a line with a placeholder in it is never a bubble"
    assert "[name]" not in story_env["payload"] and "R1 23:05 chat" in story_env["payload"]
    ctx = story_env["ctx"]
    assert (ctx.job_kind, ctx.session_id, ctx.organization_id, ctx.job_id) == (
        "surprise_storyboard", SESSION_ID, "org-a", f"surprise_voice:{SESSION_ID}"
    )


def test_a_storyboard_is_made_once_per_take(story_env):
    api_logic.run_surprise_voice("sk-openai", SESSION_ID, llm_key="claude-key")
    story_env["calls"].clear()
    assert api_logic.run_surprise_voice("sk-openai", SESSION_ID, llm_key="claude-key")["story"] == "kept"
    assert "storyboard" not in story_env["calls"]


@pytest.mark.parametrize("error", [LLMRefused("refused"), RuntimeError("the record failed")])
def test_a_failed_storyboard_saves_nothing_and_never_fails_the_play(story_env, monkeypatch, error):
    def fail(payload, *, api_key, ctx):
        raise error

    monkeypatch.setattr(api_logic, "write_storyboard", fail)
    result = api_logic.run_surprise_voice("sk-openai", SESSION_ID, llm_key="claude-key")
    assert result["story"] == "failed" and story_env["saved"] == []
    assert story_env["calls"].count("clip") == 3, "the voice is still made"


def test_without_a_claude_key_no_call_is_made(story_env):
    assert api_logic.run_surprise_voice("sk-openai", SESSION_ID)["story"] == "skipped"
    assert "storyboard" not in story_env["calls"] and story_env["saved"] == []


def test_a_failed_save_fails_the_run(story_env, monkeypatch):
    def broken(*args):
        raise RuntimeError("Could not save the storyboard.")

    monkeypatch.setattr(api_logic, "save_surprise_storyboard", broken)
    with pytest.raises(RuntimeError, match="Could not save the storyboard"):
        api_logic.run_surprise_voice("sk-openai", SESSION_ID, llm_key="claude-key")


# ── Stored ──


def test_the_storyboard_is_stored_whole_by_its_key_and_in_scope(test_database):
    db.upsert_cached_session("key-a", "sess-s", session_payload={"name": "A"}, organization_id="org-a")
    board = {"version": 1, "source": "model", "scenes": [{"effect": "pop", "headline": "Hi", "number": None, "quote": None}, None]}
    db.save_surprise_storyboard("sess-s", "k1", board)
    assert db.fetch_surprise_storyboard("sess-s", "k1", organization_id="org-a") == board
    assert db.fetch_surprise_storyboard("sess-s", "k1", organization_id="org-b") is None
    assert db.fetch_surprise_storyboard("sess-s", "k1", organization_id="  ") is None
    db.save_surprise_storyboard("sess-s", "k2", {**board, "scenes": [None]})
    assert db.fetch_surprise_storyboard("sess-s", "k1", organization_id=db.ALL_ORGS) is None, "a new take replaces the old one"
    assert db.fetch_surprise_storyboard("sess-s", "k2", organization_id="org-a")["scenes"] == [None]
    with pytest.raises(RuntimeError, match="Could not save the storyboard"):
        db.save_surprise_storyboard("sess-missing", "k1", board)
