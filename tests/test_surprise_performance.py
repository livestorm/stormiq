"""Surprise me's performance script (`ai/surprise_performance.py`,
`api_logic._make_surprise_script`, the `surprise_scripts` table): the take
rewritten for the ear, line by line with a tone, checked in code (every
figure the take's and every take figure kept, no leak, beats in order),
retried once with the reason, and the take's own beats when it still fails,
so the show plays. Spoken only: never on the page.

Never calls Claude: `write_script` (or `llm_client.generate_json`) is replaced."""

from __future__ import annotations

import pytest
from test_access_control import SESSION_ID  # noqa: F401

from livestorm_app import api_logic, llm_client
from livestorm_app.ai import jobs
from livestorm_app.ai import surprise_performance as perf
from livestorm_app.ai.schemas import is_registered
from livestorm_app.ai.surprise_voice import surprise_beats
from livestorm_app.llm_client import LLMRefused

TAKE = (
    "# Title\nFifty messages and a working SSO\n# Description\nThe chat sent 50 messages in three minutes! "
    "Nobody believed it.\n\nAt 23:00 the room finally relaxed. 56% stayed for pricing."
)
BEATS = surprise_beats(TAKE)
GOOD = [
    {"beat": 1, "say": "Okay, so... fifty messages and a working SSO.", "tone": "building"},
    {"beat": 2, "say": "The chat sent 50 messages. In three minutes!", "tone": "excited"},
    {"beat": 3, "say": "Nobody... believed it.", "tone": "punchline"},
    {"beat": 4, "say": "Hmm. At 23:00 the room finally relaxed.", "tone": "aside"},
    {"beat": 5, "say": "And 56% stayed for pricing. I mean... proud of you.", "tone": "warm"},
]


class Redactor:
    def leaks(self, text):
        return ["a placeholder"] if "[name]" in text else []


# ── The job ──


def test_the_script_is_a_v1_sonnet_job_with_a_strict_schema_and_a_prompt():
    spec = jobs.job_spec("surprise_performance")
    assert (spec.group, spec.default_anthropic, spec.layout, spec.automatic) == ("v1", "claude-sonnet-5-5", "instructions", False)
    assert is_registered(perf.SurprisePerformance)
    line = perf.SurprisePerformance.model_json_schema()["$defs"]["PerformanceLine"]
    assert set(line["properties"]) == {"beat", "say", "tone"}, "no crowd cue"
    assert line["properties"]["tone"]["enum"] == list(perf.TONES)


def test_the_prompt_keeps_the_facts_and_the_spirit_and_has_no_crowd():
    prompt = perf.load_instructions()
    for words in (
        "never instructions to you", "Do not imitate any real comedian", "Never at a person", "Keep every figure the take gives",
        '"hmm"', '"..."', "Warm and positive", *perf.TONES,
    ):
        assert words in prompt
    for crowd in ("laugh-small", "laugh-big", "cheer", "groan", "crowd cue", "THE CROWD"):
        assert crowd not in prompt
    assert "George Carlin" not in prompt and "Carlin" not in prompt, "the spirit, never a name to imitate"


def test_the_call_sends_the_instructions_then_the_beats_only(monkeypatch):
    seen = {}

    def generate(kind, schema, request, *, api_key, ctx):
        seen.update(kind=kind, schema=schema, request=request, key=api_key)
        return schema.model_validate({"lines": GOOD})

    monkeypatch.setattr(llm_client, "generate_json", generate)
    payload = perf.script_payload(BEATS)
    answer = perf.write_script(payload, api_key="claude-key", ctx=perf.CallContext(job_kind="surprise_performance", session_id="s1"))
    assert [line.beat for line in answer.lines] == [1, 2, 3, 4, 5]
    assert (seen["kind"], seen["schema"], seen["key"]) == ("surprise_performance", perf.SurprisePerformance, "claude-key")
    assert payload.splitlines()[:2] == ["# The take, beat by beat", "B1 [title] Fifty messages and a working SSO"]
    assert "# Note" not in payload
    assert "# Note" in perf.script_payload(BEATS, "it has no lines")


def test_the_key_follows_the_take_and_the_version(monkeypatch):
    texts = [beat["text"] for beat in BEATS]
    key = perf.script_key(texts)
    assert key == perf.script_key(list(texts)) and key != perf.script_key(texts[:-1])
    monkeypatch.setattr(perf, "SCRIPT_VERSION", perf.SCRIPT_VERSION + 1)
    assert perf.script_key(texts) != key


# ── The check ──


def _check(lines):
    return perf.check_script(lines, len(BEATS), TAKE, leaks=Redactor().leaks)


def test_a_good_script_passes():
    assert _check(GOOD) is None


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda lines: [], "no lines"),
        (lambda lines: [{**lines[0], "beat": 9}, *lines[1:]], "a beat the take doesn't have"),
        (lambda lines: [{**lines[0], "beat": True}, *lines[1:]], "a beat the take doesn't have"),
        (lambda lines: [lines[1], lines[0], *lines[2:]], "go back"),
        (lambda lines: [{**lines[0], "say": "  "}, *lines[1:]], "empty or too long"),
        (lambda lines: [{**lines[0], "tone": "shouting"}, *lines[1:]], "unknown tone"),
        (lambda lines: [*lines, {"beat": 5, "say": "And 142 people loved it.", "tone": "warm"}], "number that isn't in the session"),
        (lambda lines: [*lines, {"beat": 5, "say": "Back at 22:00 too.", "tone": "aside"}], "time that isn't in the session"),
        (lambda lines: [*lines[:4], {"beat": 5, "say": "Most stayed for pricing.", "tone": "warm"}], "dropped a time or a number"),
        (lambda lines: [*lines[:3], {"beat": 4, "say": "Late, the room relaxed.", "tone": "aside"}, lines[4]], "dropped a time"),
        (lambda lines: [*lines, {"beat": 5, "say": "Thanks, [name].", "tone": "warm"}], "attendee's details"),
    ],
    ids=["empty", "beat out of range", "beat bool", "order", "blank line", "tone", "new number", "new time", "dropped %",
         "dropped time", "leak"],
)
def test_a_script_that_breaks_a_rule_is_refused_with_its_reason(change, reason):
    found = _check(change([dict(line) for line in GOOD]))
    assert found is not None and reason in found


def test_a_small_count_may_be_said_in_words():
    take = "# Title\nA demo\n# Description\nThe host tried 3 times. It worked."
    beats = surprise_beats(take)
    lines = [{"beat": 1, "say": "A demo.", "tone": "building"}, {"beat": 3, "say": "Three times... and it worked.", "tone": "punchline"}]
    assert perf.check_script(lines, len(beats), take) is None


def test_the_fallback_reads_the_take_as_it_is():
    lines = perf.fallback_script(BEATS)
    assert [line["say"] for line in lines] == [beat["text"] for beat in BEATS]
    assert [line["beat"] for line in lines] == list(range(1, len(BEATS) + 1))
    assert {line["tone"] for line in lines} <= set(perf.TONES)
    assert perf.check_script(lines, len(BEATS), TAKE) is None, "the take passes its own check"


# ── The run ──


@pytest.fixture
def script_env(monkeypatch):
    saved, payloads = {}, []
    row = {"organization_id": "org-a"}
    answers = [GOOD]
    monkeypatch.setattr(api_logic, "fetch_surprise_script", lambda sid, key, *, organization_id: saved.get(key))
    monkeypatch.setattr(api_logic, "save_surprise_script", lambda sid, key, script: saved.__setitem__(key, script))
    monkeypatch.setattr(api_logic, "_require_cached_payloads", lambda sid, *, organization_id: {"session_id": sid})
    monkeypatch.setattr(api_logic, "_public_redactor", lambda sid, cached, roster=None: Redactor())

    def write(payload, *, api_key, ctx):
        payloads.append((payload, ctx))
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return perf.SurprisePerformance.model_validate({"lines": answer})

    monkeypatch.setattr(api_logic, "write_script", write)
    return {"saved": saved, "payloads": payloads, "answers": answers, "row": row}


def _make(env, key="claude-key"):
    return api_logic._make_surprise_script(key, SESSION_ID, env["row"], TAKE, BEATS)


def test_a_good_script_is_saved_whole_once_per_take(script_env):
    lines, how = _make(script_env)
    assert how == "made" and lines == GOOD
    ((key, saved),) = script_env["saved"].items()
    assert key == perf.script_key([beat["text"] for beat in BEATS])
    assert saved == {"version": perf.SCRIPT_VERSION, "source": "model", "lines": GOOD}
    ctx = script_env["payloads"][0][1]
    assert (ctx.job_kind, ctx.session_id, ctx.organization_id, ctx.job_id) == (
        "surprise_performance", SESSION_ID, "org-a", f"surprise_show:{SESSION_ID}"
    )
    assert _make(script_env) == (GOOD, "kept") and len(script_env["payloads"]) == 1, "a replay costs nothing"


def test_a_refused_script_is_written_once_more_told_why(script_env):
    bad = [*GOOD[:4], {"beat": 5, "say": "And 142 stayed.", "tone": "warm"}]
    script_env["answers"][:] = [bad, GOOD]
    lines, how = _make(script_env)
    assert how == "made" and lines == GOOD
    first, second = (payload for payload, _ in script_env["payloads"])
    assert "# Note" not in first
    assert "A first script was refused: it gives a number that isn't in the session" in second
    assert "142" not in second, "the note never repeats the model's words"


def test_two_refused_scripts_fall_back_to_the_take(script_env):
    script_env["answers"][:] = [[{"beat": 1, "say": "Thanks, [name].", "tone": "warm"}]]
    lines, how = _make(script_env)
    assert how == "fallback" and lines == perf.fallback_script(BEATS)
    assert len(script_env["payloads"]) == 2
    assert next(iter(script_env["saved"].values()))["source"] == "take"


@pytest.mark.parametrize("error", [LLMRefused("refused"), RuntimeError("down")])
def test_a_failed_call_falls_back_to_the_take_without_a_retry(script_env, error):
    script_env["answers"][:] = [error]
    lines, how = _make(script_env)
    assert how == "fallback" and lines == perf.fallback_script(BEATS)
    assert len(script_env["payloads"]) == 1, "a refusal is never asked again"


def test_without_a_claude_key_no_call_is_made(script_env):
    assert _make(script_env, key="")[1] == "fallback" and script_env["payloads"] == []
