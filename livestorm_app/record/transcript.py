"""The v1 transcript parser: utterances with stable ids, and their words.

`parse_transcript(transcript_payload)` reads a stored Gladia transcript and
gives one `ParsedTranscript`:

- `utterances`: one row per utterance of `result.transcription.utterances`,
  with an id (`U0001`, `U0002`, ...) ordered by `(r_start, r_end, payload
  order)` with a stable sort, so two parses of the same payload give the
  same ids. Gladia's own utterance ids are never used: only merged payloads
  carry them, renumbered.
- `words`: one row per word, with its utterance's id. The words come from
  `result.transcription.words` when Gladia sent a non-empty list there, and
  otherwise from the utterances' own `words` (where Gladia puts them). An
  empty list never hides the utterances' words. The normalized top-level
  `words` list is a copy of `result.transcription.words` and is never read.
- the recording's length, the Gladia model, the languages, the chunk count
  and the speaker cap's provenance (`stormiq`), when the payload has them.

`r` is seconds into the recording. A transcript saved before the merge fix
(no `result.stormiq_chunks`, and the `result.transcription.words` list the
old merge always wrote) kept the words of its later chunks on their chunk's
own clock. Here, and only for those payloads, an utterance whose words lie
outside it has them moved by `utterance start − first word start`. Nothing
is written back. Times of every other payload are read as stored.

`parse_sentences` reads Gladia's sentences in their known shapes. Rules:
docs/product.md §13, "Transcript health (v1 Report)".

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import bisect
import itertools
import math
import numbers
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

UTTERANCE_COLUMNS = (
    "uid",  # U0001
    "order",  # position in the payload
    "r_start",  # seconds into the recording; NaN when unreadable
    "r_end",
    "speaker_key",  # the speaker as text ("0"), as speaker labels key it; None without one
    "language",  # the utterance's language, lower-cased; None without one
    "confidence",  # Gladia's utterance confidence; NaN without one
    "n_words",  # words of this utterance (`words`)
    "conf_mean",  # mean confidence of those words; NaN when none has one
    "text",
)
WORD_COLUMNS = (
    "uid",  # the utterance's id; None when a word of `transcription.words` falls in no utterance
    "r_start",
    "r_end",
    "confidence",  # NaN when missing or outside 0..1
    "speaker_key",
    "word",
)
SENTENCE_COLUMNS = ("r_start", "r_end", "speaker_key", "text")

# Where the words came from (`ParsedTranscript.words_source`).
WORDS_FROM_TRANSCRIPTION = "transcription"
WORDS_FROM_UTTERANCES = "utterances"
NO_WORDS = "none"

# A word this far outside its utterance (seconds) counts as outside it.
REBASE_TOLERANCE_S = 1.0
# Re-basing offsets closer than this belong to the same chunk.
CHUNK_OFFSET_GAP_S = 60.0


@dataclass(frozen=True)
class ParsedTranscript:
    """One stored transcript, parsed. The frames have the columns of
    `UTTERANCE_COLUMNS` and `WORD_COLUMNS`, in those orders.

    - `words_source`: `transcription`, `utterances` or `none`;
    - `rebased_utterances`: utterances whose words were moved onto them (a
      transcript saved before the merge fix, read from the utterances' words);
    - `chunks`: how many Gladia jobs made it, from `result.stormiq_chunks`;
      for an older transcript, worked out from the re-basing, or None when
      it can't be told;
    - `audio_s`: the recording's length in seconds;
    - `model`: the Gladia model (`request_params.model`);
    - `languages`: the detected languages, lower-cased, first one first;
    - `people_on_stage`, `max_speakers`: what the speaker cap was built from
      and the cap sent (the `stormiq` block), when the payload has them.
    """

    utterances: pd.DataFrame
    words: pd.DataFrame
    words_source: str
    rebased_utterances: int
    chunks: Optional[int]
    audio_s: Optional[float]
    model: Optional[str]
    languages: Tuple[str, ...]
    people_on_stage: Optional[int]
    max_speakers: Optional[int]


# ── Reading values ──────────────────────────────────────────────────────────


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _seconds(value: Any) -> Optional[float]:
    """A finite number of seconds, or None (booleans and text are not times)."""
    if value is None or isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive_seconds(value: Any) -> Optional[float]:
    number = _seconds(value)
    return number if number is not None and number > 0 else None


def _confidence(value: Any) -> float:
    """A confidence between 0 and 1, or NaN."""
    number = _seconds(value)
    return number if number is not None and 0.0 <= number <= 1.0 else math.nan


def _count(value: Any) -> Optional[int]:
    """A positive whole number, or None."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        return None
    return int(value) if value > 0 else None


def speaker_key(value: Any) -> Optional[str]:
    """A speaker as text, the way speaker labels key it (`str(speaker)`, as
    the classic transcript does). None when missing or blank."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _language(value: Any) -> Optional[str]:
    return value.strip().lower() or None if isinstance(value, str) else None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _nan(value: Optional[float]) -> float:
    return math.nan if value is None else value


def _items(value: Any) -> List[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def transcription_block(transcript_payload: Any) -> Optional[Mapping[str, Any]]:
    """`result.transcription` of a stored transcript, or None."""
    result = _mapping(transcript_payload).get("result")
    transcription = _mapping(result).get("transcription")
    return transcription if isinstance(transcription, Mapping) else None


# ── Utterances and words ────────────────────────────────────────────────────


def _word_span(words: Sequence[Mapping[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    starts = [s for s in (_seconds(word.get("start")) for word in words) if s is not None]
    ends = [e for e in (_seconds(word.get("end")) for word in words) if e is not None]
    if not starts:
        return None, None
    return min(starts), max(ends + starts)


def _rebase_delta(start: Optional[float], end: Optional[float], words: Sequence[Mapping[str, Any]]) -> float:
    """How far to move an old chunked utterance's words onto it: 0 when they
    already lie within it (give or take `REBASE_TOLERANCE_S`), otherwise
    `start − first word's start`."""
    if start is None or not words:
        return 0.0
    low, high = _word_span(words)
    if low is None:
        return 0.0
    stop = end if end is not None and end >= start else start
    if low >= start - REBASE_TOLERANCE_S and high <= stop + REBASE_TOLERANCE_S:
        return 0.0
    first = next(s for s in (_seconds(word.get("start")) for word in words) if s is not None)
    return start - first


def _chunks_from_offsets(offsets: List[float]) -> int:
    """Chunks of an old transcript: 1, plus one per group of re-basing
    offsets (offsets of one chunk agree within `CHUNK_OFFSET_GAP_S`)."""
    groups = 0
    last: Optional[float] = None
    for offset in sorted(offsets):
        if last is None or offset - last > CHUNK_OFFSET_GAP_S:
            groups += 1
        last = offset
    return 1 + groups


def _word_row(word: Mapping[str, Any], shift: float, uid: Optional[str], speaker: Optional[str]) -> Tuple[Any, ...]:
    start = _seconds(word.get("start"))
    end = _seconds(word.get("end"))
    return (
        uid,
        _nan(start + shift if start is not None else None),
        _nan(end + shift if end is not None else None),
        _confidence(word.get("confidence")),
        speaker,
        _text(word.get("word")),
    )


def _assign_by_time(rows: List[Tuple[Any, ...]], utterances: List[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
    """Give each word of `transcription.words` the latest-starting utterance
    that starts at or before its middle and reaches it (within
    `REBASE_TOLERANCE_S`), so a word inside a long utterance that a shorter
    one overlaps still finds it; None when no utterance does. `utterances`
    are in id order (by start). A word without a speaker takes its
    utterance's."""
    timed = [u for u in utterances if u["r_start"] is not None]
    starts = [u["r_start"] for u in timed]
    # Where each utterance reaches (its end, or its start when the end is
    # missing or earlier), and the furthest any of the first i + 1 reach:
    # the walk back stops as soon as no earlier utterance can reach the word.
    reaches = [u["r_end"] if u["r_end"] is not None and u["r_end"] >= u["r_start"] else u["r_start"] for u in timed]
    furthest = list(itertools.accumulate(reaches, max))
    assigned = []
    for _uid, r_start, r_end, confidence, speaker, word in rows:
        owner: Optional[Dict[str, Any]] = None
        if not math.isnan(r_start):
            middle = (r_start + r_end) / 2 if not math.isnan(r_end) and r_end >= r_start else r_start
            index = bisect.bisect_right(starts, middle) - 1
            while index >= 0 and middle <= furthest[index] + REBASE_TOLERANCE_S:
                if middle <= reaches[index] + REBASE_TOLERANCE_S:
                    owner = timed[index]
                    break
                index -= 1
        assigned.append(
            (
                owner["uid"] if owner else None,
                r_start,
                r_end,
                confidence,
                speaker if speaker is not None else (owner["speaker_key"] if owner else None),
                word,
            )
        )
    return assigned


def _frame(rows: List[Tuple[Any, ...]], columns: Sequence[str], floats: Sequence[str], ints: Sequence[str] = ()) -> pd.DataFrame:
    """A frame of `rows` (tuples in `columns` order). Built column by column,
    so a missing text value stays None rather than turning into NaN."""
    values = list(zip(*rows, strict=True)) if rows else [()] * len(columns)
    data = {}
    for column, column_values in zip(columns, values, strict=True):
        if column in floats:
            data[column] = pd.Series(column_values, dtype="float64")
        elif column in ints:
            data[column] = pd.Series(column_values, dtype="int64")
        else:
            data[column] = pd.Series(column_values, dtype="object")
    return pd.DataFrame(data, columns=list(columns))


def parse_transcript(transcript_payload: Any) -> Optional[ParsedTranscript]:
    """The parsed transcript, or None when the payload has no
    `result.transcription` (no transcript, or not a Gladia one). A
    transcription without utterances parses to empty frames."""
    transcription = transcription_block(transcript_payload)
    if transcription is None:
        return None
    payload = _mapping(transcript_payload)
    result = _mapping(payload.get("result"))
    chunk_map = result.get("stormiq_chunks")
    has_chunk_map = isinstance(chunk_map, list)
    # A transcript saved before the merge fix: no chunk map, and the
    # `transcription.words` list the old merge always wrote.
    legacy = not has_chunk_map and isinstance(transcription.get("words"), list)

    parsed: List[Dict[str, Any]] = []
    offsets: List[float] = []
    for order, raw in enumerate(_items(transcription.get("utterances"))):
        start, end = _seconds(raw.get("start")), _seconds(raw.get("end"))
        words = _items(raw.get("words"))
        shift = _rebase_delta(start, end, words) if legacy else 0.0
        if shift:
            offsets.append(shift)
        parsed.append(
            {
                "order": order,
                "r_start": start,
                "r_end": end,
                "speaker_key": speaker_key(raw.get("speaker")),
                "language": _language(raw.get("language")),
                "confidence": _confidence(raw.get("confidence")),
                "text": _text(raw.get("text")),
                "words": words,
                "shift": shift,
            }
        )

    parsed.sort(
        key=lambda u: (
            u["r_start"] is None,
            u["r_start"] if u["r_start"] is not None else 0.0,
            u["r_end"] is None,
            u["r_end"] if u["r_end"] is not None else 0.0,
            u["order"],
        )
    )
    for position, utterance in enumerate(parsed, start=1):
        utterance["uid"] = f"U{position:04d}"

    direct_words = _items(transcription.get("words"))
    if direct_words:
        source = WORDS_FROM_TRANSCRIPTION
        word_rows = _assign_by_time(
            [_word_row(word, 0.0, None, speaker_key(word.get("speaker"))) for word in direct_words], parsed
        )
    else:
        word_rows = [
            _word_row(word, utterance["shift"], utterance["uid"], utterance["speaker_key"])
            for utterance in parsed
            for word in utterance["words"]
        ]
        source = WORDS_FROM_UTTERANCES if word_rows else NO_WORDS
    words_frame = _frame(word_rows, WORD_COLUMNS, floats=("r_start", "r_end", "confidence"))

    counts: Dict[str, int] = {}
    confidences: Dict[str, List[float]] = {}
    for uid, confidence in zip(words_frame["uid"], words_frame["confidence"], strict=True):
        if uid is None:
            continue
        counts[uid] = counts.get(uid, 0) + 1
        if not math.isnan(confidence):
            confidences.setdefault(uid, []).append(confidence)
    utterance_rows = [
        (
            u["uid"],
            u["order"],
            _nan(u["r_start"]),
            _nan(u["r_end"]),
            u["speaker_key"],
            u["language"],
            u["confidence"],
            counts.get(u["uid"], 0),
            sum(confidences[u["uid"]]) / len(confidences[u["uid"]]) if u["uid"] in confidences else math.nan,
            u["text"],
        )
        for u in parsed
    ]
    utterances_frame = _frame(
        utterance_rows, UTTERANCE_COLUMNS, floats=("r_start", "r_end", "confidence", "conf_mean"), ints=("order", "n_words")
    )

    if has_chunk_map:
        chunks: Optional[int] = len(chunk_map) or None
    elif not legacy:
        chunks = 1  # the old merge always wrote `transcription.words`, so this was never chunked
    elif offsets:
        chunks = _chunks_from_offsets(offsets)
    elif any(u["words"] for u in parsed):
        chunks = 1
    else:
        chunks = None

    metadata = _mapping(result.get("metadata"))
    audio_s = (
        _positive_seconds(metadata.get("audio_duration"))
        or _positive_seconds(_mapping(payload.get("file")).get("audio_duration"))
        or _positive_seconds(payload.get("duration_seconds"))
    )
    languages: List[str] = []
    raw_languages = transcription.get("languages")
    for value in raw_languages if isinstance(raw_languages, list) else []:
        language = _language(value)
        if language and language not in languages:
            languages.append(language)
    if not languages:
        fallback = _language(metadata.get("language")) or _language(payload.get("language"))
        languages = [fallback] if fallback else []
    model = _mapping(payload.get("request_params")).get("model")
    provenance = _mapping(payload.get("stormiq"))

    return ParsedTranscript(
        utterances=utterances_frame,
        words=words_frame,
        words_source=source,
        rebased_utterances=len(offsets) if source == WORDS_FROM_UTTERANCES else 0,
        chunks=chunks,
        audio_s=audio_s,
        model=model.strip() or None if isinstance(model, str) else None,
        languages=tuple(languages),
        people_on_stage=_count(provenance.get("people_on_stage")),
        max_speakers=_count(provenance.get("max_speakers")),
    )


def _empty(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def parse_utterances(transcript_payload: Any) -> pd.DataFrame:
    """The utterances of `parse_transcript`, or an empty frame."""
    parsed = parse_transcript(transcript_payload)
    return parsed.utterances if parsed is not None else _empty(UTTERANCE_COLUMNS)


def parse_words(transcript_payload: Any) -> pd.DataFrame:
    """The words of `parse_transcript`, each with its utterance's id, or an empty frame."""
    parsed = parse_transcript(transcript_payload)
    return parsed.words if parsed is not None else _empty(WORD_COLUMNS)


# ── Sentences ───────────────────────────────────────────────────────────────


def _sentence_items(transcript_payload: Any) -> List[Any]:
    """`result.transcription.sentences` when it has items, else the
    `result.sentences` block (`{success, results}` or a plain list)."""
    transcription = transcription_block(transcript_payload) or {}
    direct = transcription.get("sentences")
    if isinstance(direct, list) and direct:
        return direct
    block = _mapping(_mapping(transcript_payload).get("result")).get("sentences")
    if isinstance(block, Mapping):
        block = block.get("results")
    return block if isinstance(block, list) else []


def parse_sentences(transcript_payload: Any) -> pd.DataFrame:
    """Gladia's sentences (`SENTENCE_COLUMNS`), in time order: items with
    their times and speaker, or plain strings (no times). Items without text
    are skipped."""
    rows = []
    for order, item in enumerate(_sentence_items(transcript_payload)):
        if isinstance(item, str):
            text, start, end, speaker = item.strip(), None, None, None
        elif isinstance(item, Mapping):
            text = _text(item.get("sentence")) or _text(item.get("text"))
            start, end, speaker = _seconds(item.get("start")), _seconds(item.get("end")), speaker_key(item.get("speaker"))
        else:
            continue
        if text:
            rows.append((start is None, start or 0.0, order, (_nan(start), _nan(end), speaker, text)))
    rows.sort(key=lambda row: row[:3])
    return _frame([row[3] for row in rows], SENTENCE_COLUMNS, floats=("r_start", "r_end"))
