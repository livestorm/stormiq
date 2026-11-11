"""Surprise me's performance script: the saved take rewritten for the ear, as
a short stand-up bit, line by line, each with a delivery tone (prompt:
`prompts/surprise_performance.txt`). The voice says these lines; the page
never shows them: the screen plays the take's beats (the storyboard) and
CC shows the take's own sentence.

- **When.** In the show job only (`api_logic.run_surprise_show`), after the
  take and before the storyboard; cached by `script_key` (the beats' text
  and `SCRIPT_VERSION`) in `surprise_scripts`, so a replay costs nothing.
- **The call** (`write_script`): a v1 job (`ai.jobs`, kind
  `surprise_performance`, Sonnet 5.5, the `instructions` layout), over the
  take's beats only (`script_payload`): the saved take was already checked
  (no attendee named, every figure the session's). Never a name or an email.
- **The check** (`check_script`, pure): at least one line and at most
  `MAX_LINES`; each line 1 to `MAX_SAY_CHARS` characters; each `beat` one
  of the take's, never going back; every time and number in the script is
  one of the take's, written the same way (`record.surprise_brief.check_surprise`
  against the take); every time and number of the take is still said (a
  small count, 0 to 10, may be said in words); and the redactor's leak
  check (`leaks`, the session's public redactor) finds nothing. A failure
  gives a reason that names the kind of problem, never the model's words.
- **The fallback** (`fallback_script`): the take's own beats, one line each,
  no fillers, so the show still plays.

No database, no web framework.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, Field

from livestorm_app.ai.request import build_request
from livestorm_app.ai.schemas import json_schema
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import SURPRISE_PERFORMANCE_PROMPT_PATH

KIND = "surprise_performance"

# Bump when the lines' shape, the check or the prompt's contract change:
# every script key changes with it, and the next show writes a new one.
SCRIPT_VERSION = 1

TONES = ("deadpan", "sarcastic", "curious", "building", "punchline", "aside", "warm", "excited")
MAX_LINES = 60
MAX_SAY_CHARS = 400

Tone = Literal["deadpan", "sarcastic", "curious", "building", "punchline", "aside", "warm", "excited"]

# The tone of a take's own beat, when the fallback reads the take as it is.
FALLBACK_TONES = {"title": "building", "line": "deadpan", "punch": "punchline"}


class PerformanceLine(BaseModel):
    beat: int = Field(description="The take beat this line performs: 1 for B1, 2 for B2, and so on.")
    say: str = Field(description="The spoken line: fillers and pauses written with punctuation only, no stage directions.")
    tone: Tone = Field(description="How the voice delivers it: deadpan, sarcastic, curious, building, punchline, aside, warm or excited.")


@json_schema
class SurprisePerformance(BaseModel):
    lines: List[PerformanceLine] = Field(description="The bit, line by line, in the take's order.")


# ── The key ──


def script_key(beat_texts: Sequence[str]) -> str:
    """The script's cache key: sha256 of `SCRIPT_VERSION` and the beats'
    text, in order."""
    parts = [f"script-{SCRIPT_VERSION}", *[str(text) for text in beat_texts]]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# ── The payload ──


def script_payload(beats: Sequence[Mapping[str, Any]], note: str = "") -> str:
    """What the model reads after its instructions: the take's beats
    (`{kind, text}`, numbered B1…), and, on the retry, why the first script
    was refused (our own words, never the model's)."""
    lines = ["# The take, beat by beat"]
    for number, beat in enumerate(beats, start=1):
        lines.append(f"B{number} [{beat.get('kind') or 'line'}] {' '.join(str(beat.get('text') or '').split())}")
    if note:
        lines += ["", "# Note", "", f"A first script was refused: {note}. Write it again, keeping every rule."]
    return "\n".join(lines)


# ── The check ──


def _small(value: str, free: int) -> bool:
    return value.isdigit() and int(value) <= free


def check_script(
    lines: Sequence[Mapping[str, Any]],
    beat_count: int,
    take: str,
    *,
    leaks: Callable[[str], Sequence[str]] = lambda text: [],
) -> Optional[str]:
    """Why the script can't be used (a short reason for the retry's note),
    or None when it passes every check."""
    # `record/` is loaded only here, never when classic code imports this module.
    from livestorm_app.record.digest_brief import figures
    from livestorm_app.record.surprise_brief import FREE_COUNT, SurpriseRejected, check_surprise, spoken_numbers

    if not lines:
        return "it has no lines"
    if len(lines) > MAX_LINES:
        return f"it has more than {MAX_LINES} lines"
    previous = 0
    for line in lines:
        beat = line.get("beat")
        say = " ".join(str(line.get("say") or "").split())
        if not isinstance(beat, int) or isinstance(beat, bool) or not 1 <= beat <= beat_count:
            return "a line names a beat the take doesn't have"
        if beat < previous:
            return "its beats go back: follow the take's order"
        previous = beat
        if not say or len(say) > MAX_SAY_CHARS:
            return "a line is empty or too long"
        if line.get("tone") not in TONES:
            return "a line has an unknown tone"
    text = "\n".join(" ".join(str(line.get("say") or "").split()) for line in lines)
    try:
        check_surprise(text, take)
    except SurpriseRejected as exc:
        return f"it {exc} (only the take's own figures may be said)"
    take_times, take_numbers = figures(take)
    said_times, said_numbers = figures(text)
    said = {value for value, _ in said_numbers}
    spelled = spoken_numbers(text.casefold())
    missing = [value for value, _ in take_numbers if value not in said and not (_small(value, FREE_COUNT) and value in spelled)]
    if take_times - said_times or missing:
        return "it dropped a time or a number the take gives: keep every figure of the take, written the same way"
    if leaks(text):
        return "it contains an attendee's details or a placeholder"
    return None


def clean_lines(lines: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The checked lines as saved: `{beat, say, tone}`, the spaces folded."""
    return [
        {"beat": int(line["beat"]), "say": " ".join(str(line["say"]).split()), "tone": str(line["tone"])}
        for line in lines
    ]


def fallback_script(beats: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The take's own beats as the lines, one each, no fillers: the show
    still plays when the script can't be written or fails its check."""
    return [
        {"beat": number, "say": str(beat.get("text") or ""), "tone": FALLBACK_TONES.get(str(beat.get("kind")), "deadpan")}
        for number, beat in enumerate(beats, start=1)
        if str(beat.get("text") or "").strip()
    ]


# ── The call ──


def load_instructions() -> str:
    text = SURPRISE_PERFORMANCE_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("The performance prompt is empty.")
    return text


def write_script(payload: str, *, api_key: str, ctx: CallContext) -> SurprisePerformance:
    """The model's script for one payload. Raises the `llm_client` errors."""
    from livestorm_app import llm_client

    request = build_request(KIND, instructions=load_instructions(), payload=payload)
    return llm_client.generate_json(KIND, SurprisePerformance, request, api_key=api_key, ctx=ctx)
