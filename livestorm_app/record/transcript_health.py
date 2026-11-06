"""Transcript health: how far a session's transcript can be trusted.

`build_transcript_health(transcript, ...)` turns a `transcript.ParsedTranscript`
into the block the record route serves as `record.transcriptHealth`:

- `words`: how many words, how many carry a confidence, their mean
  confidence and the share below `LOW_CONFIDENCE` (word level, never
  utterance level), where the words came from, and how many utterances had
  their words re-based (a transcript saved before the merge fix);
- `coverage`: speech seconds (the union of the utterances' spans, so
  overlaps never count twice), over the recording and over the live session;
  when the first speech starts and how long the silent tail is;
- `speakers`: detected, substantial (`SUBSTANTIAL_MIN_S` and
  `SUBSTANTIAL_MIN_SHARE` of the speech), labelled by the host, expected
  (people on stage), the cap Gladia was sent, the chunks, and the split risk;
- `language`: detected against the event's language, with each language's
  share of the speech;
- `unclearClusters`: `CLUSTER_WINDOW_S` windows holding at least
  `CLUSTER_MIN_LOW_WORDS` low-confidence words, merged, the
  `CLUSTER_TOP` largest, in time order, with their utterance ids;
- `state` (`ok`, `degraded`, `poor`, `unavailable`) and its `reasons`
  (speech in which diarization found no speaker is degraded, never ok);
- `gates`: whether pace and speaker shares may be shown (speaker shares
  need at least one substantial speaker).

Times are recording seconds (`r`): the transcript isn't aligned with the
session clock here. Counts and ids only: no word, name or text. The
thresholds are provisional (`HEALTH_VERSION` goes up when one changes), and
confidence isn't comparable across Gladia models, so the model is kept with
the values. Formulas: docs/product.md §13, "Transcript health (v1 Report)".

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd

from livestorm_app.record.transcript import ParsedTranscript, speaker_key

HEALTH_VERSION = 1

OK = "ok"
DEGRADED = "degraded"
POOR = "poor"
UNAVAILABLE = "unavailable"

LOW_CONFIDENCE = 0.6  # a word below this is unclear
SUBSTANTIAL_MIN_S = 30.0
SUBSTANTIAL_MIN_SHARE = 0.02
CLUSTER_WINDOW_S = 30.0
CLUSTER_MIN_LOW_WORDS = 3
CLUSTER_TOP = 10
PACE_MIN_COVERAGE = 0.6  # pace is shown only above this share of the recording

# State thresholds. Provisional guesses, to tune on real transcripts.
POOR_MEAN_CONFIDENCE = 0.70
POOR_LOW_SHARE = 0.25
DEGRADED_MEAN_CONFIDENCE = 0.85
DEGRADED_LOW_SHARE = 0.10
DEGRADED_COVERAGE = 0.60

# Reasons, in the order they are checked.
NO_TRANSCRIPT = "no_transcript"
NO_SPEECH = "no_speech"
LOW_CONFIDENCE_REASON = "low_confidence"
UNCLEAR_WORDS = "unclear_words"
NO_WORD_DATA = "no_word_data"
LITTLE_SPEECH = "little_speech"
NO_SPEAKERS = "no_speakers"
SPLIT_RISK = "split_risk"
LANGUAGE_MISMATCH = "language_mismatch"

SHARE_PLACES = 4
SECONDS_PLACES = 3


# ── Small helpers ───────────────────────────────────────────────────────────


def _share(value: Optional[float]) -> Optional[float]:
    return None if value is None or not math.isfinite(value) else round(value, SHARE_PLACES) + 0.0


def _seconds(value: Optional[float]) -> Optional[float]:
    """Seconds for JSON: an int when whole, else rounded; None when unknown."""
    if value is None or not math.isfinite(value):
        return None
    rounded = round(value, SECONDS_PLACES) + 0.0
    return int(rounded) if rounded.is_integer() else rounded


def _ratio(num: float, den: Optional[float]) -> Optional[float]:
    return num / den if den else None


def primary_language(value: Any) -> Optional[str]:
    """The primary subtag of a language code, lower-cased: `fr-FR` → `fr`."""
    if not isinstance(value, str):
        return None
    primary = value.strip().lower().replace("_", "-").split("-")[0]
    return primary or None


def _spans(utterances: pd.DataFrame, audio_s: Optional[float]) -> List[Tuple[float, float]]:
    """Each utterance's (start, end) in recording seconds, clipped to the
    recording when its length is known. A missing or earlier end is the start."""
    spans = []
    for start, end in zip(utterances["r_start"], utterances["r_end"], strict=True):
        if math.isnan(start):
            continue
        stop = start if math.isnan(end) or end < start else end
        low = max(start, 0.0)
        high = max(stop, 0.0) if audio_s is None else min(max(stop, 0.0), audio_s)
        spans.append((low, max(low, high)))
    return spans


def union_seconds(spans: List[Tuple[float, float]]) -> float:
    """The length of the union of spans: overlapping speech counts once."""
    total = 0.0
    current_start: Optional[float] = None
    current_end = 0.0
    for start, end in sorted(spans):
        if current_start is None or start > current_end:
            if current_start is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_start is not None:
        total += current_end - current_start
    return total


# ── The parts ───────────────────────────────────────────────────────────────


def _words(transcript: ParsedTranscript) -> Dict[str, Any]:
    confidences = transcript.words["confidence"].dropna()
    with_confidence = int(len(confidences))
    low = int((confidences < LOW_CONFIDENCE).sum())
    return {
        "count": int(len(transcript.words)),
        "withConfidence": with_confidence,
        "meanConfidence": _share(float(confidences.mean())) if with_confidence else None,
        "lowConfidenceShare": _share(low / with_confidence) if with_confidence else None,
        "source": transcript.words_source,
        "rebasedUtterances": transcript.rebased_utterances,
    }


def _coverage(transcript: ParsedTranscript, live_s: Optional[float]) -> Dict[str, Any]:
    utterances = transcript.utterances
    audio_s = transcript.audio_s
    speech_s = union_seconds(_spans(utterances, audio_s))
    starts = utterances["r_start"].dropna()
    ends = pd.concat([utterances["r_end"], utterances["r_start"]]).dropna()
    first = float(starts.min()) if len(starts) else None
    last = float(ends.max()) if len(ends) else None
    tail = max(audio_s - last, 0.0) if audio_s is not None and last is not None else None
    return {
        "speechS": _seconds(speech_s),
        "audioS": _seconds(audio_s),
        "liveS": _seconds(live_s),
        "shareOfAudio": _share(_ratio(speech_s, audio_s)),
        "shareOfLive": _share(_ratio(speech_s, live_s)),
        "firstSpeechR": _seconds(first),
        "tailS": _seconds(tail),
    }


def _speaker_seconds(utterances: pd.DataFrame) -> Dict[str, float]:
    seconds: Dict[str, float] = {}
    for key, start, end in zip(utterances["speaker_key"], utterances["r_start"], utterances["r_end"], strict=True):
        if key is None:
            continue
        length = end - start if not (math.isnan(start) or math.isnan(end)) and end > start else 0.0
        seconds[key] = seconds.get(key, 0.0) + length
    return seconds


def _labelled(detected: List[str], speaker_names: Any) -> List[str]:
    if not isinstance(speaker_names, Mapping):
        return []
    named = {speaker_key(key) for key, label in speaker_names.items() if isinstance(label, str) and label.strip()}
    return [key for key in detected if key in named]


def _speakers(transcript: ParsedTranscript, people_on_stage: Optional[int], speaker_names: Any) -> Dict[str, Any]:
    seconds = _speaker_seconds(transcript.utterances)
    total = sum(seconds.values())
    substantial = sorted(
        key for key, value in seconds.items() if value >= SUBSTANTIAL_MIN_S and total > 0 and value / total >= SUBSTANTIAL_MIN_SHARE
    )
    labelled = _labelled(sorted(seconds), speaker_names)
    if transcript.people_on_stage is not None:
        expected, expected_source = transcript.people_on_stage, "transcript"
    elif people_on_stage is not None and people_on_stage > 0:
        expected, expected_source = int(people_on_stage), "roster"
    else:
        expected, expected_source = None, None
    split_risk = (expected is not None and len(substantial) > expected + 1) or (
        transcript.chunks is not None and transcript.chunks > 1
    )
    return {
        "detected": len(seconds),
        "substantial": len(substantial),
        "labelled": len(labelled),
        "substantialLabelled": sum(1 for key in substantial if key in set(labelled)),
        "expected": expected,
        "expectedSource": expected_source,
        "cap": transcript.max_speakers,
        "chunks": transcript.chunks,
        "splitRisk": bool(split_risk),
    }


def _language(transcript: ParsedTranscript, expected_language: Any) -> Dict[str, Any]:
    detected = primary_language(transcript.languages[0]) if transcript.languages else None
    expected = primary_language(expected_language)
    weights: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    utterances = transcript.utterances
    for language, start, end in zip(utterances["language"], utterances["r_start"], utterances["r_end"], strict=True):
        primary = primary_language(language)
        if primary is None:
            continue
        length = end - start if not (math.isnan(start) or math.isnan(end)) and end > start else 0.0
        weights[primary] = weights.get(primary, 0.0) + length
        counts[primary] = counts.get(primary, 0) + 1
    total = sum(weights.values())
    if total <= 0:  # no utterance has a length: share by count
        weights = {key: float(value) for key, value in counts.items()}
        total = sum(weights.values())
    shares = {key: _share(value / total) for key, value in sorted(weights.items(), key=lambda item: (-item[1], item[0]))} if total else {}
    return {
        "detected": detected,
        "expected": expected,
        "match": detected == expected if detected is not None and expected is not None else None,
        "shares": shares,
    }


def unclear_clusters(words: pd.DataFrame) -> List[Dict[str, Any]]:
    """Stretches of unclear words: every `CLUSTER_WINDOW_S` window, starting
    at a low-confidence word, that holds at least `CLUSTER_MIN_LOW_WORDS` of
    them; overlapping windows merge. The `CLUSTER_TOP` with the most low
    words (the earliest first on a tie), in time order: `{t0R, t1R, nLow, u}`
    (recording seconds and utterance ids)."""
    if words.empty:
        return []
    low = words[(words["confidence"] < LOW_CONFIDENCE) & words["r_start"].notna()].sort_values("r_start", kind="mergesort")
    starts = low["r_start"].tolist()
    ends = [end if not math.isnan(end) and end >= start else start for start, end in zip(starts, low["r_end"].tolist(), strict=True)]
    uids = low["uid"].tolist()
    windows: List[Tuple[int, int]] = []  # index ranges [first, last] of low words
    last = 0
    for first in range(len(starts)):
        last = max(last, first)
        while last + 1 < len(starts) and starts[last + 1] < starts[first] + CLUSTER_WINDOW_S:
            last += 1
        if last - first + 1 >= CLUSTER_MIN_LOW_WORDS:
            if windows and first <= windows[-1][1]:
                windows[-1] = (windows[-1][0], max(windows[-1][1], last))
            else:
                windows.append((first, last))
    clusters = [
        {
            "t0R": _seconds(starts[first]),
            "t1R": _seconds(max(ends[first : last + 1])),
            "nLow": last - first + 1,
            "u": sorted({uid for uid in uids[first : last + 1] if uid is not None}),
        }
        for first, last in windows
    ]
    top = sorted(clusters, key=lambda cluster: (-cluster["nLow"], cluster["t0R"]))[:CLUSTER_TOP]
    return sorted(top, key=lambda cluster: cluster["t0R"])


def _state(words: Mapping[str, Any], coverage: Mapping[str, Any], speakers: Mapping[str, Any], language: Mapping[str, Any]) -> Tuple[str, List[str]]:
    """The first state that applies, with every reason found: poor, then
    degraded, then ok. Called only for a transcript with speech."""
    poor: List[str] = []
    degraded: List[str] = []
    mean, low_share = words["meanConfidence"], words["lowConfidenceShare"]
    if mean is not None:
        if mean < POOR_MEAN_CONFIDENCE:
            poor.append(LOW_CONFIDENCE_REASON)
        elif mean < DEGRADED_MEAN_CONFIDENCE:
            degraded.append(LOW_CONFIDENCE_REASON)
    if low_share is not None:
        if low_share >= POOR_LOW_SHARE:
            poor.append(UNCLEAR_WORDS)
        elif low_share >= DEGRADED_LOW_SHARE:
            degraded.append(UNCLEAR_WORDS)
    if words["withConfidence"] == 0:
        degraded.append(NO_WORD_DATA)
    if coverage["shareOfAudio"] is not None and coverage["shareOfAudio"] < DEGRADED_COVERAGE:
        degraded.append(LITTLE_SPEECH)
    if speakers["detected"] == 0:  # speech, but diarization found no speaker
        degraded.append(NO_SPEAKERS)
    if speakers["splitRisk"]:
        degraded.append(SPLIT_RISK)
    if language["match"] is False:
        degraded.append(LANGUAGE_MISMATCH)
    if poor:
        return POOR, poor + degraded
    if degraded:
        return DEGRADED, degraded
    return OK, []


def _unavailable(reason: str) -> Dict[str, Any]:
    return {
        "version": HEALTH_VERSION,
        "state": UNAVAILABLE,
        "reasons": [reason],
        "model": None,
        "words": None,
        "coverage": None,
        "speakers": None,
        "language": None,
        "unclearClusters": [],
        "gates": {"pace": False, "speakerShares": False},
    }


def build_transcript_health(
    transcript: Optional[ParsedTranscript],
    *,
    live_s: Optional[float] = None,
    people_on_stage: Optional[int] = None,
    expected_language: Any = None,
    speaker_names: Any = None,
) -> Dict[str, Any]:
    """The `record.transcriptHealth` block (module docstring).

    - `transcript`: `transcript.parse_transcript(...)`; None gives
      `unavailable`;
    - `live_s`: the live session's length (`ClockBase.end_s`);
    - `people_on_stage`: the roster's count, used when the transcript doesn't
      record the count its speaker cap was built from;
    - `expected_language`: the event's language;
    - `speaker_names`: the host's speaker labels (`transcript_speaker_names`),
      only counted.
    """
    if transcript is None:
        return _unavailable(NO_TRANSCRIPT)
    words = _words(transcript)
    coverage = _coverage(transcript, live_s)
    speakers = _speakers(transcript, people_on_stage, speaker_names)
    language = _language(transcript, expected_language)
    if transcript.utterances.empty:
        state, reasons = POOR, [NO_SPEECH]
    else:
        state, reasons = _state(words, coverage, speakers, language)
    share = coverage["shareOfAudio"]
    has_speech = not transcript.utterances.empty
    return {
        "version": HEALTH_VERSION,
        "state": state,
        "reasons": reasons,
        "model": transcript.model,
        "words": words,
        "coverage": coverage,
        "speakers": speakers,
        "language": language,
        "unclearClusters": unclear_clusters(transcript.words),
        "gates": {
            "pace": bool(has_speech and share is not None and share >= PACE_MIN_COVERAGE),
            # Shares need someone to share: at least one substantial speaker
            # (none when diarization found no speaker at all).
            "speakerShares": bool(
                has_speech
                and speakers["substantial"] > 0
                and (not speakers["splitRisk"] or speakers["substantial"] == speakers["substantialLabelled"])
            ),
        },
    }
