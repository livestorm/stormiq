"""Surprise me's video timing: where each line of the performance script is
heard, and where each of its clauses starts, so the picture never lags the
voice (the owner's brief, from the November 2026 rehearsals).

- **The gaps** (`GAP_IN_BEAT`, `GAP_BETWEEN_BEATS`): the clips are laid
  end to end after the fixed intro (`INTRO_S`), 0.22 s apart within a beat,
  0.38 s between beats. The fixed outro (`OUTRO_S`) follows the end card.
- **The punchlines** (the script's four `punchline` lines, owner's brief of
  2026-11-12): before each, the picture holds `HOLD_BEFORE_PUNCHLINE`
  (0.75 s: the shot before it freezes; after another punchline, its picture
  stays); then a window (`punches`) of at least `PUNCH_WINDOW_S` (2.5 s),
  longer only when the line needs it: the punchline's picture and caption
  (`video/punch.py`) while the voice says it, `PUNCH_LEAD_S` after the
  picture lands, with `PUNCH_TAIL_S` after its last word. The next line's
  voice starts `GAP_AFTER_PUNCH` after the window, its first shot at the
  window's end. A punchline is never cut into clauses.
- **The segments** (`segments`): the middle, from the intro's end to the end
  card, cut into kinetic parts (the clauses' shots, drawn by Chrome) and
  punchline windows, in order, end to end.
- **The ceiling** (`MAX_TOTAL_S`, 75 s with the intro and outro; the script
  aims at about `TARGET_TOTAL_S`): when the voice still runs past it,
  whole lines are dropped, in a fixed order (`drop_order`), until it fits:
  never a punchline, nor the script's first or last line; first the lines
  of the middle beats (every beat but the first and the last), from the
  end; then the last beat's, from its end; then the first beat's. In each
  of the three, a line just before a punchline (its setup) goes last. A
  dropped line is never heard (`dropped`); its clip stays saved.
- **The clauses** (`clauses`): a line is cut at every sentence end, comma,
  semicolon, colon, dash and ellipsis; a clause shorter than
  `MIN_CLAUSE_S` once timed is merged into the next one of its line.
- **The words** (`transcribe_words`): each clip's word timestamps from
  OpenAI `whisper-1` (`verbose_json`, word granularity), the clip's own
  mp3 and nothing else. When a clip has none (no key, a failed call), its
  clauses are spread over the clip by their length (`build_timeline`'s
  estimate), so the show never fails on timing.
- **The key** (`timeline_key`): a hash of `TIMING_VERSION`, each line's
  clip key, beat, tone and punchline mark, in order: a new script or voice
  makes a new timeline on the next click.

`build_timeline` and `clauses` are pure; `transcribe_words` makes one
HTTP call per clip with a timeout.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import requests

# Bump when the timeline's shape, the gaps or the clause cut change.
TIMING_VERSION = 2

INTRO_S = 4.0  # the fixed intro, the same on every show
OUTRO_S = 6.0  # the sign-off and the end card, while the jingle rings out
GAP_IN_BEAT = 0.22
GAP_BETWEEN_BEATS = 0.38
HOLD_BEFORE_PUNCHLINE = 0.75
PUNCH_WINDOW_S = 2.5  # a punchline's picture and caption, at least this long
PUNCH_LEAD_S = 0.2  # the picture lands, then the voice says the punchline
PUNCH_TAIL_S = 0.3  # the picture stays this long after the punchline's last word
GAP_AFTER_PUNCH = 0.25
TARGET_TOTAL_S = 60.0  # what the script aims at (`surprise_performance.TARGET_WORDS`)
MAX_TOTAL_S = 75.0  # the whole video, intro and outro included: never longer
AFTER_VOICE = 0.5  # the last clause stays on screen this long after the voice
MIN_CLAUSE_S = 0.3
MIN_STEP_S = 0.15  # two clause starts are at least this far apart
EARLY_S = 0.06  # a clause appears just before its first word is heard

OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-1"
WHISPER_TIMEOUT = (10, 60)  # connect, read
WHISPER_ATTEMPTS = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class WordsFailed(RuntimeError):
    """A clip's word timestamps could not be read; the clip is timed by estimate."""


# ── The clauses ──

_SPLIT = re.compile(r"(?<=[.!?…,;:])\s+|\s+(?=[—–])|(?<=[—–])\s*|\s+-\s+")
_LETTER = re.compile(r"[A-Za-z0-9À-ÿ]")


def clauses(say: str) -> List[str]:
    """A line cut at every sentence end, comma, semicolon, colon, dash and
    ellipsis; a piece without a letter or digit joins the clause before."""
    out: List[str] = []
    for part in _SPLIT.split(" ".join(str(say or "").split())):
        piece = (part or "").strip()
        if not piece or piece in ("—", "–"):
            continue
        if out and not _LETTER.search(piece):
            out[-1] = f"{out[-1]} {piece}"
        elif not _LETTER.search(piece):
            continue
        else:
            out.append(piece)
    return out


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(word or "").lower())


# ── The key ──


def timeline_key(lines: Sequence[Mapping[str, Any]], clip_keys: Sequence[str]) -> str:
    """sha256 of `TIMING_VERSION` and, per line, its clip key, beat, tone
    and punchline mark."""
    parts = [str(TIMING_VERSION)]
    for line, key in zip(lines, clip_keys, strict=True):
        parts.append(f"{key}|{line.get('beat')}|{line.get('tone')}|{int(line.get('punchline') is True)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# ── The timeline ──


def _is_punchline(line: Mapping[str, Any]) -> bool:
    return line.get("punchline") is True


def drop_order(lines: Sequence[Mapping[str, Any]]) -> List[int]:
    """The lines the ceiling may drop, in the order it drops them: never a
    punchline, nor the first or last line; the middle beats' lines from the
    end, then the last beat's, then the first beat's; in each group, a line
    just before a punchline last."""
    n = len(lines)
    if n < 3:
        return []
    beats = [line.get("beat") for line in lines]
    first, last = beats[0], beats[-1]
    keep = {0, n - 1} | {i for i, line in enumerate(lines) if _is_punchline(line)}
    groups = (
        [i for i in range(n) if beats[i] not in (first, last)],
        [i for i in range(n) if beats[i] == last and beats[i] != first],
        [i for i in range(n) if beats[i] == first],
    )
    order: List[int] = []
    for group in groups:
        candidates = [i for i in reversed(group) if i not in keep]
        setups = [i for i in candidates if i + 1 < n and _is_punchline(lines[i + 1])]
        order += [i for i in candidates if i not in setups] + setups
    return order


def _clause_starts(
    pieces: List[str], words: Optional[Sequence[Mapping[str, Any]]], duration: float
) -> Tuple[List[float], List[Optional[int]]]:
    """Each clause's start within its clip (seconds) and the index of its
    first heard word (None when it could not be matched)."""
    tokens = [(norm(word), k) for k, piece in enumerate(pieces) for word in piece.split() if norm(word)]
    heard = [norm(item.get("word")) for item in words or []]
    mapped: List[Optional[int]] = [None] * len(tokens)
    if heard:
        matcher = SequenceMatcher(None, [token for token, _ in tokens], heard, autojunk=False)
        for a, b, size in matcher.get_matching_blocks():
            for j in range(size):
                mapped[a + j] = b + j
    starts: List[float] = []
    hits: List[Optional[int]] = []
    total_chars = sum(len(piece) for piece in pieces) or 1
    chars_before = 0
    for k, piece in enumerate(pieces):
        indexes = [j for j, (_, owner) in enumerate(tokens) if owner == k]
        first = next((j for j in indexes if mapped[j] is not None), None)
        if first is not None and words:
            # Unmatched words at the clause's start: back off by their count.
            hit = max(0, mapped[first] - (first - indexes[0]))
            hits.append(hit)
            starts.append(float(words[hit].get("start") or 0.0))
        else:
            hits.append(None)
            starts.append(duration * chars_before / total_chars)
        chars_before += len(piece)
    if starts:
        starts[0] = 0.0
    for k in range(1, len(starts)):
        starts[k] = max(starts[k], starts[k - 1] + MIN_STEP_S)
    return starts, hits


def _line_shots(i: int, line: Mapping[str, Any], t: float, duration: float,
                clip_words: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One spoken line's clause shots, its voice starting at `t`."""
    pieces = clauses(str(line.get("say") or "")) or [" ".join(str(line.get("say") or "").split()) or "…"]
    starts, hits = _clause_starts(pieces, clip_words, duration)
    line_shots: List[Dict[str, Any]] = []
    for k, piece in enumerate(pieces):
        first = hits[k]
        after = next((h for h in hits[k + 1:] if h is not None), len(clip_words))
        spoken = (
            [{"w": str(w.get("word")), "t": round(t + float(w.get("start") or 0.0), 3)} for w in clip_words[first:after]]
            if first is not None else []
        )
        line_shots.append({
            "line": i,
            "beat": line.get("beat"),
            "tone": str(line.get("tone") or ""),
            "text": piece,
            "start": round(t + starts[k] - (EARLY_S if k else 0.0), 3),
            "spoken": spoken,
        })
    # A clause shorter than MIN_CLAUSE_S joins the next one of its line.
    bounds = [shot["start"] for shot in line_shots[1:]] + [t + duration]
    merged: List[Dict[str, Any]] = []
    carry: Optional[Dict[str, Any]] = None
    for k, (shot, end) in enumerate(zip(line_shots, bounds, strict=True)):
        if carry is not None:
            shot = {**shot, "text": f"{carry['text']} {shot['text']}", "start": carry["start"],
                    "spoken": carry["spoken"] + shot["spoken"]}
            carry = None
        if end - shot["start"] < MIN_CLAUSE_S and k < len(line_shots) - 1:
            carry = shot
            continue
        merged.append(shot)
    for k, shot in enumerate(merged):
        shot["k"] = k
        shot["last_in_line"] = k == len(merged) - 1
    return merged


def _layout(
    lines: Sequence[Mapping[str, Any]],
    clips: Sequence[Mapping[str, Any]],
    words: Sequence[Optional[Sequence[Mapping[str, Any]]]],
    keep: Sequence[int],
) -> Dict[str, Any]:
    """The timeline of the lines in `keep` (indexes into `lines`, in order)."""
    t = INTRO_S
    voice: List[Dict[str, Any]] = []
    shots: List[Dict[str, Any]] = []
    punches: List[Dict[str, Any]] = []
    timed = 0
    previous: Optional[Tuple[str, Mapping[str, Any]]] = None  # ("shot" | "punch", the line)
    for i in keep:
        line = lines[i]
        duration = max(0.1, float(clips[i].get("duration_s") or 0.0))
        clip_words = [w for w in (words[i] or []) if isinstance(w, Mapping) and norm(w.get("word"))]
        if clip_words:
            timed += 1
        if _is_punchline(line):
            if previous is not None:
                # The hold: the shot before freezes, or the previous punchline's picture stays.
                t += HOLD_BEFORE_PUNCHLINE
                if previous[0] == "shot":
                    shots[-1]["end"] = round(t, 3)
                    shots[-1]["hold_after"] = HOLD_BEFORE_PUNCHLINE
                else:
                    punches[-1]["end"] = round(t, 3)
            start = t
            spoken_at = start + PUNCH_LEAD_S
            end = max(start + PUNCH_WINDOW_S, spoken_at + duration + PUNCH_TAIL_S)
            voice.append({"clip": str(clips[i]["key"]), "start": round(spoken_at, 3), "dur": round(duration, 3), "line": i})
            punches.append({
                "n": len(punches), "line": i, "beat": line.get("beat"), "text": " ".join(str(line.get("say") or "").split()),
                "start": round(start, 3), "end": round(end, 3), "voice_start": round(spoken_at, 3), "voice_dur": round(duration, 3),
            })
            t = end
            previous = ("punch", line)
            continue
        first_shot_at: Optional[float] = None
        if previous is not None and previous[0] == "punch":
            first_shot_at = t  # the window's end: the next shot takes the screen there
            t += GAP_AFTER_PUNCH
        elif previous is not None:
            t += GAP_BETWEEN_BEATS if line.get("beat") != previous[1].get("beat") else GAP_IN_BEAT
        voice.append({"clip": str(clips[i]["key"]), "start": round(t, 3), "dur": round(duration, 3), "line": i})
        line_shots = _line_shots(i, line, t, duration, clip_words)
        if first_shot_at is not None:
            line_shots[0]["start"] = round(first_shot_at, 3)
        shots += line_shots
        t += duration
        previous = ("shot", line)
    voice_end = max((placed["start"] + placed["dur"] for placed in voice), default=INTRO_S)
    # A show that ends on a punchline ends with its window (it covers the voice already).
    end_card = punches[-1]["end"] if previous is not None and previous[0] == "punch" else voice_end + AFTER_VOICE
    for n, shot in enumerate(shots):
        # A shot before a punchline already ends where the hold does.
        if "end" not in shot:
            shot["end"] = shots[n + 1]["start"] if n + 1 < len(shots) else end_card
        shot["id"] = n
        shot["end"] = round(float(shot["end"]), 3)
        shot["dur"] = round(shot["end"] - shot["start"], 3)
    for punch in punches:
        punch["dur"] = round(punch["end"] - punch["start"], 3)
    return {
        "version": TIMING_VERSION,
        "intro_s": INTRO_S,
        "outro_s": OUTRO_S,
        "voice": voice,
        "shots": shots,
        "punches": punches,
        "segments": _segments(punches, end_card),
        "voice_end": round(voice_end, 3),
        "end_card": round(end_card, 3),
        "total": round(end_card + OUTRO_S, 3),
        "timed": timed,
    }


def _segments(punches: Sequence[Mapping[str, Any]], end_card: float) -> List[Dict[str, Any]]:
    """The middle in order: kinetic parts and punchline windows, end to end."""
    out: List[Dict[str, Any]] = []
    cursor = INTRO_S
    for punch in punches:
        if punch["start"] > cursor + 1e-6:
            out.append({"kind": "kinetic", "start": round(cursor, 3), "end": punch["start"]})
        out.append({"kind": "punch", "n": punch["n"], "start": punch["start"], "end": punch["end"]})
        cursor = float(punch["end"])
    if end_card > cursor + 1e-6:
        out.append({"kind": "kinetic", "start": round(cursor, 3), "end": round(end_card, 3)})
    return out


def build_timeline(
    lines: Sequence[Mapping[str, Any]],
    clips: Sequence[Mapping[str, Any]],
    words: Sequence[Optional[Sequence[Mapping[str, Any]]]],
) -> Dict[str, Any]:
    """The show's timeline from the script's lines (`{beat, say, tone,
    punchline}`), their clips (`{key, duration_s}`, in the same order) and
    each clip's words (`[{word, start, end}]`, or None to estimate):

    `{version, intro_s, outro_s, voice: [{clip, start, dur, line}], shots:
    [{id, line, k, beat, tone, text, start, end, dur, last_in_line, spoken:
    [{w, t}], hold_after?}], punches: [{n, line, beat, text, start, end, dur,
    voice_start, voice_dur}], segments: [{kind, start, end, n?}], dropped,
    voice_end, end_card, total, timed}`, every time in seconds from the
    video's start. `shots` are the clauses of the lines that aren't
    punchlines; `hold_after` marks the one before a punchline: it freezes
    there. `dropped`: the lines left out to stay under `MAX_TOTAL_S`
    (`drop_order`). `timed` counts the clips placed by their words."""
    if not (len(lines) == len(clips) == len(words)):
        raise ValueError("build_timeline needs one clip and one word list per line.")
    keep = list(range(len(lines)))
    timeline = _layout(lines, clips, words, keep)
    dropped: List[int] = []
    for index in drop_order(lines):
        if timeline["total"] <= MAX_TOTAL_S:
            break
        keep.remove(index)
        dropped.append(index)
        timeline = _layout(lines, clips, words, keep)
    timeline["dropped"] = sorted(dropped)
    return timeline


# ── The words (OpenAI whisper-1) ──


def transcribe_words(
    api_key: str,
    audio: bytes,
    *,
    timeout: Tuple[float, float] = WHISPER_TIMEOUT,
    attempts: int = WHISPER_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> List[Dict[str, Any]]:
    """One clip's words with their times, `[{word, start, end}]`, from
    `whisper-1`. Sends the clip's mp3 only. Retries a 429, a 5xx and a
    connection error (nothing was transcribed), never a read timeout.
    Raises `WordsFailed` (its message never holds the key or OpenAI's answer)."""
    key = str(api_key or "").strip()
    if not key:
        raise WordsFailed("no OpenAI key")
    if not audio:
        raise WordsFailed("no audio")
    data = {
        "model": WHISPER_MODEL,
        "response_format": "verbose_json",
        "language": "en",
        "timestamp_granularities[]": "word",
    }
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            response = requests.post(
                OPENAI_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {key}"},
                data=data,
                files={"file": ("line.mp3", audio, "audio/mpeg")},
                timeout=timeout,
            )
        except requests.exceptions.ReadTimeout:
            raise WordsFailed("read timeout") from None
        except requests.exceptions.ConnectionError:
            if last:
                raise WordsFailed("connection error") from None
            sleep(float(2 ** attempt))
            continue
        except requests.RequestException:
            raise WordsFailed("request failed") from None
        if response.status_code in RETRY_STATUSES and not last:
            sleep(float(2 ** attempt))
            continue
        if not response.ok:
            raise WordsFailed(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            raise WordsFailed("not JSON") from None
        out = []
        for item in (body or {}).get("words") or []:
            if isinstance(item, Mapping) and isinstance(item.get("start"), (int, float)):
                out.append({"word": str(item.get("word") or ""), "start": float(item["start"]), "end": float(item.get("end") or item["start"])})
        return out
    raise WordsFailed("no answer")
