"""Surprise me's video timing: where each line of the performance script is
heard, and where each of its clauses starts, so the picture never lags the
voice (the owner's brief, from the November 2026 rehearsals).

- **The gaps** (`GAP_IN_BEAT`, `GAP_BETWEEN_BEATS`, `HOLD_BEFORE_PUNCHLINE`):
  the clips are laid end to end after the fixed intro (`INTRO_S`), 0.22 s
  apart within a beat, 0.38 s between beats, and 0.75 s held before a
  punchline line. The fixed outro (`OUTRO_S`) follows the last clip.
- **The clauses** (`clauses`): a line is cut at every sentence end, comma,
  semicolon, colon, dash and ellipsis; a clause shorter than
  `MIN_CLAUSE_S` once timed is merged into the next one of its line.
- **The words** (`transcribe_words`): each clip's word timestamps from
  OpenAI `whisper-1` (`verbose_json`, word granularity), the clip's own
  mp3 and nothing else. When a clip has none (no key, a failed call), its
  clauses are spread over the clip by their length (`build_timeline`'s
  estimate), so the show never fails on timing.
- **The key** (`timeline_key`): a hash of `TIMING_VERSION`, each line's
  clip key, beat and tone, in order: a new script or voice makes a new
  timeline on the next click.

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
TIMING_VERSION = 1

INTRO_S = 4.0  # the fixed intro, the same on every show
OUTRO_S = 6.0  # the sign-off and the end card, while the jingle rings out
GAP_IN_BEAT = 0.22
GAP_BETWEEN_BEATS = 0.38
HOLD_BEFORE_PUNCHLINE = 0.75
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
    """sha256 of `TIMING_VERSION` and, per line, its clip key, beat and tone."""
    parts = [str(TIMING_VERSION)]
    for line, key in zip(lines, clip_keys, strict=True):
        parts.append(f"{key}|{line.get('beat')}|{line.get('tone')}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# ── The timeline ──


def _gap(line: Mapping[str, Any], previous: Mapping[str, Any]) -> float:
    if line.get("tone") == "punchline":
        return HOLD_BEFORE_PUNCHLINE
    return GAP_BETWEEN_BEATS if line.get("beat") != previous.get("beat") else GAP_IN_BEAT


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


def build_timeline(
    lines: Sequence[Mapping[str, Any]],
    clips: Sequence[Mapping[str, Any]],
    words: Sequence[Optional[Sequence[Mapping[str, Any]]]],
) -> Dict[str, Any]:
    """The show's timeline from the script's lines (`{beat, say, tone}`),
    their clips (`{key, duration_s}`, in the same order) and each clip's
    words (`[{word, start, end}]`, or None to estimate):

    `{version, intro_s, outro_s, voice: [{clip, start, dur}], shots: [{id,
    line, k, beat, tone, text, start, end, dur, last_in_line, spoken: [{w,
    t}], hold_after?}], voice_end, end_card, total, timed}`, every time in
    seconds from the video's start. `hold_after` marks the clause before a
    punchline line: the voice holds there. `timed` counts the clips placed
    by their words."""
    if not (len(lines) == len(clips) == len(words)):
        raise ValueError("build_timeline needs one clip and one word list per line.")
    t = INTRO_S
    voice: List[Dict[str, Any]] = []
    shots: List[Dict[str, Any]] = []
    timed = 0
    for i, line in enumerate(lines):
        if i:
            t += _gap(line, lines[i - 1])
        duration = max(0.1, float(clips[i].get("duration_s") or 0.0))
        voice.append({"clip": str(clips[i]["key"]), "start": round(t, 3), "dur": round(duration, 3)})
        clip_words = [w for w in (words[i] or []) if isinstance(w, Mapping) and norm(w.get("word"))]
        if clip_words:
            timed += 1
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
        shots += merged
        t += duration
    voice_end = t
    for n, shot in enumerate(shots):
        shot["id"] = n
        shot["end"] = round(shots[n + 1]["start"] if n + 1 < len(shots) else voice_end + AFTER_VOICE, 3)
        shot["dur"] = round(shot["end"] - shot["start"], 3)
    for n, shot in enumerate(shots):
        if n and shot["k"] == 0 and shot["tone"] == "punchline":
            shots[n - 1]["hold_after"] = HOLD_BEFORE_PUNCHLINE
    end_card = voice_end + AFTER_VOICE
    return {
        "version": TIMING_VERSION,
        "intro_s": INTRO_S,
        "outro_s": OUTRO_S,
        "voice": voice,
        "shots": shots,
        "voice_end": round(voice_end, 3),
        "end_card": round(end_card, 3),
        "total": round(end_card + OUTRO_S, 3),
        "timed": timed,
    }


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
