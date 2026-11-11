"""Surprise me's performance script: the saved take rewritten for the ear, as
a short stand-up bit of about a minute, line by line, each with a delivery
tone, four of its lines marked as the punchlines (prompt:
`prompts/surprise_performance.txt`). The voice says these lines; the page
never gets them as text: the video shows at most six words of a clause at a
time (`ai/surprise_director.py`), and each punchline whole, as its picture's
caption (`video/punch.py`).

- **When.** In the show job only (`api_logic.run_surprise_show`), after the
  take and before the voice; cached by `script_key` (the beats' text
  and `SCRIPT_VERSION`) in `surprise_scripts`, so a replay costs nothing.
- **The call** (`write_script`): a v1 job (`ai.jobs`, kind
  `surprise_performance`, Sonnet 5.5, the `instructions` layout), over the
  take's beats only (`script_payload`): the saved take was already checked
  (no attendee named, every figure the session's). Never a name or an email.
- **The check** (`check_script`, pure): at least one line and at most
  `MAX_LINES`; each line 1 to `MAX_SAY_CHARS` characters; each `beat` one
  of the take's, never going back; at most `MAX_SCRIPT_WORDS` spoken words
  in all (the voice must fit the video's ceiling,
  `surprise_timing.MAX_TOTAL_S`); exactly `punchlines_needed` lines marked
  `punchline` (four; fewer only when the take has fewer beats that aren't
  its title), each the last line of its beat, one per beat, never in the
  title, at most `MAX_PUNCHLINE_WORDS` words; every time and number in the
  script is one of the take's, written the same way
  (`record.surprise_brief.check_surprise` against the take). A minute
  can't hold every figure of a longer take: the script may leave one out,
  never add one. Then the redactor's leak check (`leaks`, the session's
  public redactor) finds nothing. A failure gives a reason that names the
  kind of problem, never the model's words.
- **The punchline tone** belongs to the punchlines: `clean_lines` gives
  them the `punchline` tone and any other line marked so the `deadpan` one.
- **The fallback** (`fallback_script`): the take's own beats, one line each,
  no fillers, so the show still plays; its punchlines are up to four of the
  take's paragraph closers (`punch` beats), spread evenly. It is usually
  longer than a minute: the timing drops lines until it fits
  (`surprise_timing.build_timeline`).

No database, no web framework.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Collection, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, Field

from livestorm_app.ai.request import build_request
from livestorm_app.ai.schemas import json_schema
from livestorm_app.ai.usage import CallContext
from livestorm_app.config import SURPRISE_PERFORMANCE_PROMPT_PATH

KIND = "surprise_performance"

# Bump when the lines' shape, the check or the prompt's contract change:
# every script key changes with it, and the next show writes a new one.
SCRIPT_VERSION = 2

TONES = ("deadpan", "sarcastic", "curious", "building", "punchline", "aside", "warm", "excited")
MAX_LINES = 60
MAX_SAY_CHARS = 400
# About a minute of video: the prompt aims at 120 to 130 spoken words; the
# check refuses more than `MAX_SCRIPT_WORDS` (with the fixed intro and
# outro, the holds and the punchlines' windows, about the 75 s ceiling at a
# comedian's pace; the timing drops lines if the voice still runs over).
TARGET_WORDS = (120, 130)
MAX_SCRIPT_WORDS = 150
PUNCHLINES = 4
MAX_PUNCHLINE_WORDS = 8

Tone = Literal["deadpan", "sarcastic", "curious", "building", "punchline", "aside", "warm", "excited"]

# The tone of a take's own beat, when the fallback reads the take as it is.
FALLBACK_TONES = {"title": "building", "line": "deadpan", "punch": "deadpan"}


class PerformanceLine(BaseModel):
    beat: int = Field(description="The take beat this line performs: 1 for B1, 2 for B2, and so on.")
    say: str = Field(description="The spoken line: fillers and pauses written with punctuation only, no stage directions.")
    tone: Tone = Field(description="How the voice delivers it: deadpan, sarcastic, curious, building, punchline, aside, warm or excited.")
    punchline: bool = Field(description="True for exactly four lines: the strongest jokes, each the last line of its beat.")


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
    lines += ["", f"Punchlines to mark: {punchlines_needed(len(beats), title_beats(beats))}"]
    if note:
        lines += ["", "# Note", "", f"A first script was refused: {note}. Write it again, keeping every rule."]
    return "\n".join(lines)


# ── The check ──

_WORD = re.compile(r"[A-Za-z0-9À-ɏ]")


def spoken_words(text: str) -> int:
    """How many words `text` says: the pieces between spaces that hold a
    letter or a digit (a lone dash or ellipsis is not a word)."""
    return sum(1 for piece in str(text or "").split() if _WORD.search(piece))


def title_beats(beats: Sequence[Mapping[str, Any]]) -> List[int]:
    """The take's title beats, numbered as the payload numbers them (B1…)."""
    return [number for number, beat in enumerate(beats, start=1) if beat.get("kind") == "title"]


def punchlines_needed(beat_count: int, titles: Collection[int] = ()) -> int:
    """`PUNCHLINES`, or one per beat that isn't a title when the take has fewer."""
    own = {int(n) for n in titles if 1 <= int(n) <= beat_count}
    return max(0, min(PUNCHLINES, beat_count - len(own)))


def _punchline_problem(lines: Sequence[Mapping[str, Any]], beat_count: int, titles: Collection[int]) -> Optional[str]:
    needed = punchlines_needed(beat_count, titles)
    marked = [n for n, line in enumerate(lines) if line.get("punchline") is True]
    if len(marked) != needed:
        return f"it marks {len(marked)} punchlines: mark exactly {needed}, the strongest jokes, each the last line of its beat"
    title_set = {int(n) for n in titles}
    used = set()
    for n in marked:
        beat = lines[n].get("beat")
        if n + 1 < len(lines) and lines[n + 1].get("beat") == beat:
            return "a punchline isn't the last line of its beat"
        if beat in title_set:
            return "a punchline is in the title"
        if beat in used:
            return "two punchlines share a beat"
        used.add(beat)
        if spoken_words(str(lines[n].get("say") or "")) > MAX_PUNCHLINE_WORDS:
            return f"a punchline is longer than {MAX_PUNCHLINE_WORDS} words"
    return None


def check_script(
    lines: Sequence[Mapping[str, Any]],
    beat_count: int,
    take: str,
    *,
    leaks: Callable[[str], Sequence[str]] = lambda text: [],
    titles: Collection[int] = (),
) -> Optional[str]:
    """Why the script can't be used (a short reason for the retry's note),
    or None when it passes every check. `titles`: the take's title beats
    (`title_beats`), where no punchline goes."""
    # `record/` is loaded only here, never when classic code imports this module.
    from livestorm_app.record.surprise_brief import SurpriseRejected, check_surprise

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
    words = spoken_words(text)
    if words > MAX_SCRIPT_WORDS:
        return (f"it is too long ({words} words): keep it between {TARGET_WORDS[0]} and {TARGET_WORDS[1]} "
                f"spoken words, never over {MAX_SCRIPT_WORDS}")
    reason = _punchline_problem(lines, beat_count, titles)
    if reason:
        return reason
    try:
        check_surprise(text, take)
    except SurpriseRejected as exc:
        return f"it {exc} (only the take's own figures may be said)"
    if leaks(text):
        return "it contains an attendee's details or a placeholder"
    return None


def clean_lines(lines: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The checked lines as saved: `{beat, say, tone, punchline}`, the
    spaces folded; the `punchline` tone is the punchlines' own (another
    line marked so is read `deadpan`)."""
    out = []
    for line in lines:
        punchline = line.get("punchline") is True
        tone = "punchline" if punchline else ("deadpan" if line["tone"] == "punchline" else str(line["tone"]))
        out.append({"beat": int(line["beat"]), "say": " ".join(str(line["say"]).split()), "tone": tone, "punchline": punchline})
    return out


def fallback_script(beats: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The take's own beats as the lines, one each, no fillers: the show
    still plays when the script can't be written or fails its check. The
    punchlines are up to `PUNCHLINES` of the take's `punch` beats (each
    paragraph's last sentence), spread evenly over them."""
    kept = [(number, beat) for number, beat in enumerate(beats, start=1) if str(beat.get("text") or "").strip()]
    punches = [n for n, (_, beat) in enumerate(kept) if beat.get("kind") == "punch"]
    if len(punches) > PUNCHLINES:
        step = (len(punches) - 1) / (PUNCHLINES - 1)
        punches = [punches[round(k * step)] for k in range(PUNCHLINES)]
    chosen = set(punches)
    return [
        {
            "beat": number,
            "say": str(beat.get("text") or ""),
            "tone": "punchline" if n in chosen else FALLBACK_TONES.get(str(beat.get("kind")), "deadpan"),
            "punchline": n in chosen,
        }
        for n, (number, beat) in enumerate(kept)
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
