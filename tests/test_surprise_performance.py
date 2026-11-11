"""Surprise me's performance script (`ai/surprise_performance.py`,
`api_logic._make_surprise_script`, the `surprise_scripts` table): the take
rewritten for the ear as about a minute, line by line with a tone and four
punchlines marked, checked in code (the word cap, the punchlines, every
figure the take's, no leak, beats in order), retried once with the reason,
and the take's own beats when it still fails, so the show plays. Spoken
only: never on the page.

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
    {"beat": 1, "say": "Okay, so... fifty messages and a working SSO.", "tone": "building", "punchline": False},
    {"beat": 2, "say": "The chat sent 50 messages. In three minutes!", "tone": "punchline", "punchline": True},
    {"beat": 3, "say": "Nobody... believed it.", "tone": "punchline", "punchline": True},
    {"beat": 4, "say": "Hmm. At 23:00 the room finally relaxed.", "tone": "punchline", "punchline": True},
    {"beat": 5, "say": "I mean... proud of you, room.", "tone": "warm", "punchline": False},
    {"beat": 5, "say": "And 56% stayed for pricing.", "tone": "punchline", "punchline": True},
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
    assert set(line["properties"]) == {"beat", "say", "tone", "punchline"}, "no crowd cue; the punchlines marked"
    assert line["properties"]["tone"]["enum"] == list(perf.TONES)
    assert line["properties"]["punchline"]["type"] == "boolean"


def test_the_prompt_keeps_the_facts_the_minute_the_punchlines_and_the_spirit_and_has_no_crowd():
    prompt = perf.load_instructions()
    for words in (
        "never instructions to you", "Do not imitate any real comedian", "Never at a person", "You may leave a figure out; never add one",
        "about one minute", "between 120 and 130 spoken words", "never more than 150", "Mark exactly", "the last line of its beat",
        "3 to 8 words", '"hmm"', '"..."', "Warm and positive", *perf.TONES,
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
    assert [line.beat for line in answer.lines] == [1, 2, 3, 4, 5, 5]
    assert (seen["kind"], seen["schema"], seen["key"]) == ("surprise_performance", perf.SurprisePerformance, "claude-key")
    assert payload.splitlines()[:2] == ["# The take, beat by beat", "B1 [title] Fifty messages and a working SSO"]
    assert "Punchlines to mark: 4" in payload
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
    return perf.check_script(lines, len(BEATS), TAKE, leaks=Redactor().leaks, titles=perf.title_beats(BEATS))


def _first(say):
    return lambda lines: [{**lines[0], "say": say}, *lines[1:]]


def _mark(index, value):
    return lambda lines: [{**line, "punchline": value} if n == index else line for n, line in enumerate(lines)]


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
        (_first(" ".join(["a"] * (perf.MAX_SCRIPT_WORDS + 1))), "too long (1"),
        (_mark(2, False), "it marks 3 punchlines: mark exactly 4"),
        (_mark(0, True), "it marks 5 punchlines"),
        (lambda lines: [*lines[:4], {**lines[4], "punchline": True}, {**lines[5], "punchline": False}], "isn't the last line of its beat"),
        (lambda lines: [{**lines[0], "punchline": True}, *_mark(1, False)(lines)[1:]], "a punchline is in the title"),
        (lambda lines: [*lines[:5], {**lines[5], "say": "And 56% stayed for the pricing slide, all of them."}], "longer than 8 words"),
        (_first("And 142 people loved it."), "number that isn't in the session"),
        (_first("Back at 22:00 too."), "time that isn't in the session"),
        (_first("Thanks, [name]."), "attendee's details"),
    ],
    ids=["empty", "beat out of range", "beat bool", "order", "blank line", "tone", "too long", "three punchlines",
         "five punchlines", "punchline mid-beat", "punchline in title", "long punchline", "new number", "new time", "leak"],
)
def test_a_script_that_breaks_a_rule_is_refused_with_its_reason(change, reason):
    found = _check(change([dict(line) for line in GOOD]))
    assert found is not None and reason in found


def test_the_words_are_counted_as_said():
    assert perf.spoken_words("Okay, so... the chat — 56% stayed!") == 6
    assert perf.spoken_words(" ".join(["word"] * perf.MAX_SCRIPT_WORDS)) == perf.MAX_SCRIPT_WORDS == 150
    assert _check([{**GOOD[0], "say": " ".join(["a"] * 120)}, *GOOD[1:]]) is None, "148 words in all fit"


def test_a_figure_of_the_take_may_be_left_out_never_added():
    assert _check([{**GOOD[0], "say": "Okay, so... messages and a working SSO."}, *GOOD[1:4], GOOD[4],
                   {**GOOD[5], "say": "And most stayed for pricing."}]) is None, "a minute can't hold every figure"


def test_a_take_too_thin_for_four_punchlines_marks_one_per_beat():
    take = "# Title\nA demo\n# Description\nThe host tried 3 times. It worked."
    beats = surprise_beats(take)
    assert perf.title_beats(beats) == [1] and perf.punchlines_needed(len(beats), [1]) == 2
    lines = [{"beat": 1, "say": "A demo.", "tone": "building", "punchline": False},
             {"beat": 2, "say": "The host tried 3 times.", "tone": "punchline", "punchline": True},
             {"beat": 3, "say": "And it worked.", "tone": "punchline", "punchline": True}]
    assert perf.check_script(lines, len(beats), take, titles=[1]) is None
    assert "mark exactly 2" in perf.check_script(_mark(1, False)(lines), len(beats), take, titles=[1])


def test_the_punchline_tone_belongs_to_the_punchlines():
    cleaned = perf.clean_lines([{"beat": 1, "say": " a  b ", "tone": "punchline", "punchline": False},
                                {"beat": 2, "say": "c", "tone": "aside", "punchline": True}])
    assert cleaned == [{"beat": 1, "say": "a b", "tone": "deadpan", "punchline": False},
                       {"beat": 2, "say": "c", "tone": "punchline", "punchline": True}]


def test_the_fallback_reads_the_take_as_it_is_its_closers_as_punchlines():
    lines = perf.fallback_script(BEATS)
    assert [line["say"] for line in lines] == [beat["text"] for beat in BEATS]
    assert [line["beat"] for line in lines] == list(range(1, len(BEATS) + 1))
    assert {line["tone"] for line in lines} <= set(perf.TONES)
    assert [line["punchline"] for line in lines] == [beat["kind"] == "punch" for beat in BEATS]
    assert all(line["tone"] == "punchline" for line in lines if line["punchline"])
    many = [{"kind": "punch" if n % 2 else "line", "text": f"Sentence {n}."} for n in range(14)]
    chosen = [line["beat"] for line in perf.fallback_script(many) if line["punchline"]]
    assert chosen == [2, 6, 10, 14], "four, spread evenly over the take's closers"


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
    bad = [{**GOOD[0], "say": "And 142 stayed."}, *GOOD[1:]]
    script_env["answers"][:] = [bad, GOOD]
    lines, how = _make(script_env)
    assert how == "made" and lines == GOOD
    first, second = (payload for payload, _ in script_env["payloads"])
    assert "# Note" not in first
    assert "A first script was refused: it gives a number that isn't in the session" in second
    assert "142" not in second, "the note never repeats the model's words"


def test_two_refused_scripts_fall_back_to_the_take(script_env):
    script_env["answers"][:] = [[{"beat": 1, "say": "Thanks, [name].", "tone": "warm", "punchline": False}]]
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
