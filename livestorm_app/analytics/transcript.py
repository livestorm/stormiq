"""Transcript analytics: text, segments, pace, airtime, pauses, entities, engagement scoring and key moments.

The entry point is `build_transcript_insights`; formulas are documented in docs/product.md section 13.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

COMMON_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "do", "does", "did",
    "for", "from", "had", "has", "have", "hello", "how", "i", "if", "in", "into", "is", "it",
    "it's", "its", "just", "let", "let's", "ll", "look", "me", "my", "not", "now", "of", "okay",
    "on", "one", "or", "our", "ours", "please", "right", "so", "some", "still", "such", "than",
    "that", "that's", "the", "their", "them", "then", "there", "thing", "things", "think",
    "these", "they", "this", "those", "to", "too", "up", "us", "very", "was", "we", "well",
    "what", "when", "where", "which", "who", "why", "with", "would", "yeah", "you", "your",
    "yours", "ah", "eh", "first", "go", "going", "get", "got", "know", "like", "through", "back", "live",
    "will", "work", "working", "really", "also", "make", "made", "want", "need", "maybe",
    "s", "t", "re", "ve", "m", "d",
    "bonjour", "merci", "pour", "avec", "dans", "tout", "tous", "les", "des", "une", "est",
    "que", "qui", "sur", "pas", "oui", "franchement", "ca", "ça", "va",
}


def build_transcript_stats(transcript_payload: Dict[str, Any]) -> Dict[str, Any]:
    transcript = _extract_transcript_object(transcript_payload)
    text = str(transcript.get("text") or "")
    stats: Dict[str, Any] = {
        "word_count": len(re.findall(r"\S+", text)),
        "character_count": len(text),
        "model": transcript.get("model"),
        "requested_model": transcript.get("requested_model"),
        "language": transcript.get("language"),
        "timestamped": bool(transcript.get("timestamped")),
        "created_at": transcript.get("created_at"),
        "duration_seconds": transcript.get("duration_seconds"),
    }

    usage = transcript.get("usage")
    if isinstance(usage, dict):
        stats["usage"] = usage
    recording = transcript.get("recording")
    if isinstance(recording, dict):
        stats["recording"] = {
            "id": recording.get("id"),
            "event_id": recording.get("event_id"),
            "file_type": recording.get("file_type"),
            "mime_type": recording.get("mime_type"),
            "file_size": recording.get("file_size"),
            "file_name": recording.get("file_name"),
        }
    return stats


def _coerce_seconds(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_seconds_label(value: Optional[float]) -> str:
    if value is None:
        return ""
    total_seconds = max(int(value), 0)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _extract_transcript_object(transcript_payload: Dict[str, Any]) -> Dict[str, Any]:
    transcript = transcript_payload.get("transcript")
    if isinstance(transcript, dict):
        return transcript
    result = transcript_payload.get("result")
    if isinstance(result, dict):
        nested_transcript = result.get("transcript")
        if isinstance(nested_transcript, dict):
            return nested_transcript
        transcription = result.get("transcription")
        if isinstance(transcription, dict):
            utterances = transcription.get("utterances")
            language = None
            if isinstance(utterances, list):
                for utterance in utterances:
                    if isinstance(utterance, dict) and utterance.get("language"):
                        language = utterance.get("language")
                        break
            metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            return {
                **transcription,
                "timestamped": bool(utterances),
                "created_at": transcript_payload.get("completed_at") or transcript_payload.get("created_at"),
                "duration_seconds": metadata.get("audio_duration")
                or transcript_payload.get("audio_duration")
                or (transcript_payload.get("file") or {}).get("audio_duration"),
                "language": language,
                "model": (transcript_payload.get("request_params") or {}).get("model"),
                "requested_model": (transcript_payload.get("request_params") or {}).get("model"),
                "usage": metadata,
                "recording": transcript_payload.get("file"),
            }
    return transcript_payload if isinstance(transcript_payload, dict) else {}


def _extract_transcript_segments(transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("segments", "timestamps", "utterances", "chunks"):
        value = transcript.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _extract_speaker_value(segment: Dict[str, Any]) -> Any:
    for key in ("speaker", "speaker_name", "speaker_label", "speaker_id", "participant", "author"):
        if key in segment and segment.get(key) not in (None, ""):
            return segment.get(key)
    return None


def _extract_transcript_words(transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
    direct_words = transcript.get("words")
    if isinstance(direct_words, list):
        return [item for item in direct_words if isinstance(item, dict)]

    segments = _extract_transcript_segments(transcript)
    words: List[Dict[str, Any]] = []
    for segment in segments:
        segment_words = segment.get("words")
        if not isinstance(segment_words, list):
            continue
        speaker = _extract_speaker_value(segment)
        for word in segment_words:
            if not isinstance(word, dict):
                continue
            words.append({**word, "_speaker": speaker})
    if words:
        return words

    nested_result = transcript.get("result")
    if isinstance(nested_result, dict):
        nested_sentences = nested_result.get("sentences")
        if isinstance(nested_sentences, list):
            for sentence in nested_sentences:
                if not isinstance(sentence, dict):
                    continue
                sentence_words = sentence.get("words")
                if not isinstance(sentence_words, list):
                    continue
                speaker = _extract_speaker_value(sentence)
                for word in sentence_words:
                    if not isinstance(word, dict):
                        continue
                    words.append({**word, "_speaker": speaker})
    return words


def _get_segment_start_value(segment: Dict[str, Any]) -> Any:
    start_value = segment.get("start")
    if start_value is None:
        start_value = segment.get("start_time")
    if start_value is None:
        start_value = segment.get("offset")
    if start_value is None and segment.get("start_ms") is not None:
        start_seconds = _coerce_seconds(segment.get("start_ms"))
        start_value = start_seconds / 1000 if start_seconds is not None else None
    return start_value


def _get_segment_end_value(segment: Dict[str, Any]) -> Any:
    end_value = segment.get("end")
    if end_value is None:
        end_value = segment.get("end_time")
    if end_value is None:
        end_value = segment.get("offset_end")
    if end_value is None and segment.get("end_ms") is not None:
        end_seconds = _coerce_seconds(segment.get("end_ms"))
        end_value = end_seconds / 1000 if end_seconds is not None else None
    return end_value


def build_transcript_display_text(transcript_payload: Dict[str, Any]) -> str:
    transcript = _extract_transcript_object(transcript_payload)
    segments = _extract_transcript_segments(transcript)
    if segments:
        lines: List[str] = []
        for segment in segments:
            segment_text = str(segment.get("text") or "").strip()
            if not segment_text:
                continue
            start_value = _get_segment_start_value(segment)
            timestamp_label = format_seconds_label(_coerce_seconds(start_value))
            lines.append(f"[{timestamp_label}] {segment_text}" if timestamp_label else segment_text)
        if lines:
            return "\n".join(lines)
    if isinstance(segments, list) and segments:
        combined_text = " ".join(str(segment.get("text") or "").strip() for segment in segments).strip()
        if combined_text:
            return combined_text
    full_transcript = transcript.get("full_transcript")
    if isinstance(full_transcript, str) and full_transcript.strip():
        return full_transcript.strip()
    sentence_items = _extract_sentence_items(transcript_payload, transcript)
    if sentence_items:
        sentence_lines: List[str] = []
        for sentence in sentence_items:
            sentence_text = str(sentence.get("sentence") or sentence.get("text") or "").strip()
            if not sentence_text:
                continue
            start_value = sentence.get("start") or sentence.get("start_time")
            timestamp_label = format_seconds_label(_coerce_seconds(start_value))
            sentence_lines.append(f"[{timestamp_label}] {sentence_text}" if timestamp_label else sentence_text)
        if sentence_lines:
            return "\n".join(sentence_lines)
    words = _extract_transcript_words(transcript)
    if words:
        joined_words = " ".join(str(word.get("word") or word.get("text") or "").strip() for word in words).strip()
        if joined_words:
            return re.sub(r"\s+", " ", joined_words).strip()
    return str(transcript.get("text") or "").strip()


def build_transcript_plain_text(transcript_payload: Dict[str, Any]) -> str:
    transcript = _extract_transcript_object(transcript_payload)
    direct_text = str(transcript.get("text") or "").strip()
    if direct_text:
        return direct_text
    full_transcript = str(transcript.get("full_transcript") or "").strip()
    if full_transcript:
        return full_transcript

    segments = _extract_transcript_segments(transcript)
    if segments:
        combined_segments = " ".join(str(segment.get("text") or "").strip() for segment in segments).strip()
        if combined_segments:
            return re.sub(r"\s+", " ", combined_segments).strip()

    sentence_items = _extract_sentence_items(transcript_payload, transcript)
    if sentence_items:
        combined_sentences = " ".join(
            str(sentence.get("sentence") or sentence.get("text") or "").strip()
            for sentence in sentence_items
        ).strip()
        if combined_sentences:
            return re.sub(r"\s+", " ", combined_sentences).strip()

    words = _extract_transcript_words(transcript)
    if words:
        joined_words = " ".join(str(word.get("word") or word.get("text") or "").strip() for word in words).strip()
        if joined_words:
            return re.sub(r"\s+", " ", joined_words).strip()
    return ""


def build_transcript_segments_df(transcript_payload: Dict[str, Any]) -> pd.DataFrame:
    transcript = _extract_transcript_object(transcript_payload)
    segments = _extract_transcript_segments(transcript)
    if not segments:
        return pd.DataFrame()

    rows: List[Dict[str, Any]] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        start_value = _get_segment_start_value(segment)
        end_value = _get_segment_end_value(segment)
        speaker = _extract_speaker_value(segment)

        start_seconds = _coerce_seconds(start_value)
        end_seconds = _coerce_seconds(end_value)
        duration_seconds = max(end_seconds - start_seconds, 0.0) if start_seconds is not None and end_seconds is not None else None
        word_count = len(re.findall(r"\S+", text))
        rows.append(
            {
                "segment_id": segment.get("id"),
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "start_label": format_seconds_label(start_seconds),
                "duration_seconds": duration_seconds,
                "text": text,
                "speaker": str(speaker).strip() if speaker not in (None, "") else "Unknown speaker",
                "word_count": word_count,
                "words_per_second": round(word_count / duration_seconds, 2) if duration_seconds and duration_seconds > 0 else None,
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty and "start_seconds" in df.columns:
        df["minute_bucket"] = df["start_seconds"].fillna(0).astype(float).floordiv(60).astype(int)
        df["minute_label"] = df["minute_bucket"].apply(lambda value: format_seconds_label(value * 60))
    return df


def extract_meaningful_terms_from_series(text_series: pd.Series, top_n: int = 10) -> pd.DataFrame:
    normalized_segments = text_series.fillna("").astype(str).str.lower().str.replace("’", "'", regex=False)
    token_pattern = re.compile(r"\b[\w']+\b")

    term_counts: Counter[str] = Counter()
    segment_counts: Counter[str] = Counter()
    total_segments = 0

    for segment_text in normalized_segments:
        tokens = [token.strip("'") for token in token_pattern.findall(segment_text)]
        filtered_tokens = [
            token for token in tokens
            if token
            and len(token) >= 3
            and not token.isdigit()
            and token not in COMMON_STOPWORDS
            and "'" not in token
        ]
        if not filtered_tokens:
            continue
        total_segments += 1
        term_counts.update(filtered_tokens)
        segment_counts.update(set(filtered_tokens))

    if not term_counts or total_segments == 0:
        return pd.DataFrame(columns=["term", "count", "segment_count", "score"])

    scored_rows: List[Dict[str, Any]] = []
    for term, count in term_counts.items():
        doc_freq = int(segment_counts.get(term, 0))
        if doc_freq <= 0:
            continue
        coverage_ratio = doc_freq / total_segments
        if coverage_ratio >= 0.8:
            continue
        score = count * math.log1p(total_segments / doc_freq)
        scored_rows.append(
            {
                "term": term,
                "count": int(count),
                "segment_count": doc_freq,
                "score": round(score, 3),
            }
        )

    if not scored_rows:
        return pd.DataFrame(columns=["term", "count", "segment_count", "score"])

    scored_df = pd.DataFrame(scored_rows).sort_values(
        by=["score", "count", "segment_count", "term"],
        ascending=[False, False, True, True],
    )
    return scored_df.head(top_n).reset_index(drop=True)


def _extract_sentence_items(transcript_payload: Dict[str, Any], transcript: Dict[str, Any]) -> List[Dict[str, Any]]:
    direct_sentences = transcript.get("sentences")
    if isinstance(direct_sentences, list):
        return [item for item in direct_sentences if isinstance(item, dict)]

    result = transcript_payload.get("result")
    if isinstance(result, dict):
        transcription = result.get("transcription")
        if isinstance(transcription, dict) and isinstance(transcription.get("sentences"), list):
            return [item for item in transcription.get("sentences") if isinstance(item, dict)]
        sentences_payload = result.get("sentences")
        if isinstance(sentences_payload, dict) and isinstance(sentences_payload.get("results"), list):
            return [item for item in sentences_payload.get("results") if isinstance(item, dict)]
    return []


def _extract_named_entity_items(transcript_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    transcript = _extract_transcript_object(transcript_payload)
    candidate_containers: List[Dict[str, Any]] = []

    if isinstance(transcript_payload, dict):
        candidate_containers.append(transcript_payload)

    result = transcript_payload.get("result")
    if isinstance(result, dict):
        candidate_containers.append(result)

    if isinstance(transcript, dict):
        candidate_containers.append(transcript)
        nested_result = transcript.get("result")
        if isinstance(nested_result, dict):
            candidate_containers.append(nested_result)

    for container in candidate_containers:
        ner_payload = container.get("named_entity_recognition")
        if isinstance(ner_payload, dict) and isinstance(ner_payload.get("results"), list):
            return [item for item in ner_payload.get("results") if isinstance(item, dict)]
        if isinstance(ner_payload, list):
            return [item for item in ner_payload if isinstance(item, dict)]

    direct_entities = transcript_payload.get("named_entities")
    if isinstance(direct_entities, list):
        return [item for item in direct_entities if isinstance(item, dict)]
    return []


def _bucket_seconds(value: Optional[float], bucket_size_seconds: int) -> Optional[int]:
    if value is None or pd.isna(value):
        return None
    return int(float(value) // bucket_size_seconds) * bucket_size_seconds


def _safe_quantile(series: pd.Series, q: float, default: float = 0.0) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return default
    return float(clean.quantile(q))


def _min_max_scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.5
    return min(max((float(value) - low) / (high - low), 0.0), 1.0)


class _EntityContextFinder:
    """Pick the sentence that gives context to a named-entity mention.

    Rules, in order:
      1. the first sentence that contains the entity text and spans its time;
      2. otherwise sentences containing the text beat those that don't, then
         sentences spanning the time beat those that don't, then the nearest
         start wins, then the earliest sentence.

    Sentence texts and times are read once, and the sentences containing each
    entity text are memoised, so a 2-hour transcript with hundreds of
    mentions no longer costs mentions × sentences `iterrows` calls (that
    scan was most of a 2-hour workspace read).

    A sentence without a start time counts as infinitely far away. (The old
    scan gave it a NaN distance, which made the first such sentence
    unbeatable; Gladia sentences always have times, so this changes nothing
    for real transcripts.)
    """

    def __init__(self, source_df: pd.DataFrame) -> None:
        self.records: List[Dict[str, Any]] = source_df.to_dict("records") if not source_df.empty else []
        self.texts = [str(r.get("sentence") or r.get("text") or "").strip().lower() for r in self.records]
        self.starts = np.array([_seconds_or_nan(r.get("start_seconds")) for r in self.records], dtype=float)
        self.ends = np.array([_seconds_or_nan(r.get("end_seconds")) for r in self.records], dtype=float)
        self._containing: Dict[str, np.ndarray] = {}

    def _sentences_containing(self, entity_text_lower: str) -> np.ndarray:
        found = self._containing.get(entity_text_lower)
        if found is None:
            found = np.array([i for i, text in enumerate(self.texts) if text and entity_text_lower in text], dtype=int)
            self._containing[entity_text_lower] = found
        return found

    def find(self, entity_text: str, entity_start_seconds: Optional[float]) -> Dict[str, Any]:
        if not self.records:
            return {}
        if entity_start_seconds is None:
            spans = np.zeros(len(self.records), dtype=bool)
            distance = np.full(len(self.records), np.inf)
        else:
            moment = float(entity_start_seconds)
            with np.errstate(invalid="ignore"):
                spans = (self.starts <= moment) & (moment <= self.ends)
            distance = np.abs(self.starts - moment)
            distance[np.isnan(distance)] = np.inf

        containing = self._sentences_containing(entity_text.lower())
        if containing.size:
            spanning = containing[spans[containing]]
            best = spanning[0] if spanning.size else containing[np.argmin(distance[containing])]
        else:
            spanning = np.flatnonzero(spans)
            best = spanning[np.argmin(distance[spanning])] if spanning.size else int(np.argmin(distance))
        return dict(self.records[int(best)])


def _seconds_or_nan(value: Any) -> float:
    seconds = _coerce_seconds(value)
    return float(seconds) if seconds is not None else float("nan")


def build_transcript_insights(transcript_payload: Dict[str, Any]) -> Dict[str, Any]:
    segments_df = build_transcript_segments_df(transcript_payload)
    if segments_df.empty:
        return {
            "segments_df": segments_df,
            "words_df": pd.DataFrame(),
            "sentence_df": pd.DataFrame(),
            "timeline_df": pd.DataFrame(),
            "pace_df": pd.DataFrame(),
            "silence_df": pd.DataFrame(),
            "pause_type_df": pd.DataFrame(),
            "pause_distribution_df": pd.DataFrame(),
            "speaker_df": pd.DataFrame(),
            "speaker_turns_df": pd.DataFrame(),
            "filler_df": pd.DataFrame(),
            "sentence_length_df": pd.DataFrame(),
            "confidence_distribution_df": pd.DataFrame(),
            "engagement_df": pd.DataFrame(),
            "low_energy_df": pd.DataFrame(),
            "burst_df": pd.DataFrame(),
            "burst_distribution_df": pd.DataFrame(),
            "utterance_duration_distribution_df": pd.DataFrame(),
            "topic_timeline_df": pd.DataFrame(),
            "named_entities_df": pd.DataFrame(),
            "numbers_df": pd.DataFrame(),
            "interruptions_df": pd.DataFrame(),
            "response_time_df": pd.DataFrame(),
            "key_moments_df": pd.DataFrame(),
            "replay_navigation_df": pd.DataFrame(),
            "segment_mix_df": pd.DataFrame(),
            "terms_df": pd.DataFrame(),
            "summary": {},
        }

    transcript = _extract_transcript_object(transcript_payload)
    ordered_df = segments_df.sort_values(by=["start_seconds", "end_seconds"], na_position="last").reset_index(drop=True)
    transcript_duration_seconds = _coerce_seconds(transcript.get("duration_seconds"))
    bucket_size_seconds = 60

    word_rows: List[Dict[str, Any]] = []
    for word in _extract_transcript_words(transcript):
        word_text = str(word.get("word") or word.get("text") or "").strip()
        start_seconds = _coerce_seconds(word.get("start") or word.get("start_time"))
        end_seconds = _coerce_seconds(word.get("end") or word.get("end_time"))
        speaker = word.get("_speaker")
        if speaker in (None, ""):
            for key in ("speaker", "speaker_id", "speaker_name"):
                if key in word and word.get(key) not in (None, ""):
                    speaker = word.get(key)
                    break
        if not word_text or start_seconds is None or end_seconds is None:
            continue
        word_rows.append(
            {
                "word": word_text,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "duration_seconds": max(end_seconds - start_seconds, 0.0),
                "speaker": str(speaker).strip() if speaker not in (None, "") else "Unknown speaker",
                "confidence": _coerce_seconds(word.get("confidence")),
            }
        )
    words_df = (
        pd.DataFrame(word_rows).sort_values(by=["start_seconds", "end_seconds"], na_position="last").reset_index(drop=True)
        if word_rows else pd.DataFrame(columns=["word", "start_seconds", "end_seconds", "duration_seconds", "speaker", "confidence"])
    )
    if not words_df.empty:
        words_df["bucket_start_seconds"] = words_df["start_seconds"].apply(lambda value: _bucket_seconds(value, bucket_size_seconds))
        words_df["bucket_label"] = words_df["bucket_start_seconds"].apply(format_seconds_label)

    sentence_rows: List[Dict[str, Any]] = []
    for sentence in _extract_sentence_items(transcript_payload, transcript):
        sentence_text = str(sentence.get("sentence") or sentence.get("text") or "").strip()
        start_seconds = _coerce_seconds(sentence.get("start") or sentence.get("start_time"))
        end_seconds = _coerce_seconds(sentence.get("end") or sentence.get("end_time"))
        speaker = sentence.get("speaker")
        if speaker in (None, ""):
            speaker = _extract_speaker_value(sentence)
        sentence_words = sentence.get("words") if isinstance(sentence.get("words"), list) else []
        if not sentence_words and sentence_text:
            sentence_word_count = len(re.findall(r"\S+", sentence_text))
            avg_confidence = _coerce_seconds(sentence.get("confidence"))
        else:
            sentence_word_count = len([word for word in sentence_words if isinstance(word, dict)])
            confidences = [
                _coerce_seconds(word.get("confidence"))
                for word in sentence_words
                if isinstance(word, dict) and _coerce_seconds(word.get("confidence")) is not None
            ]
            avg_confidence = round(sum(confidences) / len(confidences), 3) if confidences else _coerce_seconds(sentence.get("confidence"))
        sentence_rows.append(
            {
                "sentence": sentence_text,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "speaker": str(speaker).strip() if speaker not in (None, "") else "Unknown speaker",
                "word_count": sentence_word_count,
                "duration_seconds": max(float(end_seconds) - float(start_seconds), 0.0) if start_seconds is not None and end_seconds is not None else None,
                "confidence": avg_confidence,
            }
        )
    sentence_df = pd.DataFrame(sentence_rows)
    if not sentence_df.empty:
        sentence_df = sentence_df.sort_values(by=["start_seconds", "end_seconds"], na_position="last").reset_index(drop=True)
        sentence_df["bucket_start_seconds"] = sentence_df["start_seconds"].apply(lambda value: _bucket_seconds(value, bucket_size_seconds))
        sentence_df["bucket_label"] = sentence_df["bucket_start_seconds"].apply(format_seconds_label)

    timeline_rows: List[Dict[str, Any]] = []
    for _, row in ordered_df.iterrows():
        start_seconds = row.get("start_seconds")
        end_seconds = row.get("end_seconds")
        duration_seconds = row.get("duration_seconds")
        word_count = int(row.get("word_count") or 0)

        if pd.isna(start_seconds):
            continue

        if pd.isna(end_seconds) or pd.isna(duration_seconds) or float(duration_seconds) <= 0:
            minute_bucket = int(float(start_seconds) // 60)
            timeline_rows.append(
                {
                    "minute_bucket": minute_bucket,
                    "word_count": word_count,
                    "speaking_seconds": 0.0,
                    "segment_count": 1,
                }
            )
            continue

        start_seconds = float(start_seconds)
        end_seconds = float(end_seconds)
        duration_seconds = float(duration_seconds)
        current_minute = int(start_seconds // 60)
        last_minute = int(max(end_seconds - 1e-9, start_seconds) // 60)

        for minute_bucket in range(current_minute, last_minute + 1):
            bucket_start = minute_bucket * 60
            bucket_end = bucket_start + 60
            overlap_seconds = max(0.0, min(end_seconds, bucket_end) - max(start_seconds, bucket_start))
            if overlap_seconds <= 0:
                continue
            timeline_rows.append(
                {
                    "minute_bucket": minute_bucket,
                    "word_count": (word_count * overlap_seconds) / duration_seconds if duration_seconds > 0 else word_count,
                    "speaking_seconds": overlap_seconds,
                    "segment_count": 1,
                }
            )

    timeline_df = pd.DataFrame(timeline_rows)
    if not timeline_df.empty:
        timeline_df = (
            timeline_df.groupby("minute_bucket", as_index=False)
            .agg(
                word_count=("word_count", "sum"),
                speaking_seconds=("speaking_seconds", "sum"),
                segment_count=("segment_count", "sum"),
            )
        )
        timeline_df["word_count"] = timeline_df["word_count"].round(1)
        timeline_df["minute_label"] = timeline_df["minute_bucket"].apply(lambda value: format_seconds_label(value * 60))
    else:
        timeline_df = pd.DataFrame(columns=["minute_bucket", "word_count", "speaking_seconds", "segment_count", "minute_label"])

    timeline_df["words_per_minute"] = timeline_df.apply(
        lambda row: round((row["word_count"] / row["speaking_seconds"]) * 60, 1)
        if pd.notna(row["speaking_seconds"]) and row["speaking_seconds"] and row["speaking_seconds"] > 0
        else 0.0,
        axis=1,
    )

    pace_df = ordered_df.copy()
    pace_df["time_seconds"] = pace_df["start_seconds"]
    pace_df["time_label"] = pace_df["time_seconds"].apply(format_seconds_label)
    pace_df["segment_wpm"] = pace_df["words_per_second"].apply(
        lambda value: round(float(value) * 60, 1) if pd.notna(value) else 0.0
    )

    silence_rows: List[Dict[str, Any]] = []
    pause_threshold_seconds = 0.75
    silence_source_df = words_df.copy() if not words_df.empty else ordered_df.copy()
    first_start = silence_source_df["start_seconds"].dropna().min() if not silence_source_df.empty else None
    last_end = silence_source_df["end_seconds"].dropna().max() if not silence_source_df.empty else None

    def classify_pause(gap_seconds: float) -> str:
        if gap_seconds < 0.3:
            return "Natural flow"
        if gap_seconds < 1.0:
            return "Thinking pause"
        if gap_seconds < 2.0:
            return "Hesitation"
        return "Strong silence"

    if first_start is not None and not pd.isna(first_start) and float(first_start) >= pause_threshold_seconds:
        silence_rows.append(
            {
                "silence_start_seconds": 0.0,
                "silence_end_seconds": float(first_start),
                "silence_start_label": format_seconds_label(0.0),
                "silence_end_label": format_seconds_label(float(first_start)),
                "gap_seconds": round(float(first_start), 2),
                "pause_type": classify_pause(float(first_start)),
                "speaker_transition": False,
                "bucket_start_seconds": _bucket_seconds(float(first_start), bucket_size_seconds),
            }
        )

    for idx in range(1, len(silence_source_df.index)):
        previous_end = silence_source_df.loc[idx - 1, "end_seconds"]
        current_start = silence_source_df.loc[idx, "start_seconds"]
        if pd.isna(previous_end) or pd.isna(current_start):
            continue
        gap_seconds = float(current_start) - float(previous_end)
        if gap_seconds >= pause_threshold_seconds:
            previous_speaker = silence_source_df.loc[idx - 1, "speaker"]
            current_speaker = silence_source_df.loc[idx, "speaker"]
            silence_rows.append(
                {
                    "silence_start_seconds": float(previous_end),
                    "silence_end_seconds": float(current_start),
                    "silence_start_label": format_seconds_label(float(previous_end)),
                    "silence_end_label": format_seconds_label(float(current_start)),
                    "gap_seconds": round(gap_seconds, 2),
                    "pause_type": classify_pause(gap_seconds),
                    "speaker_transition": (
                        previous_speaker not in (None, "")
                        and current_speaker not in (None, "")
                        and previous_speaker != current_speaker
                    ),
                    "bucket_start_seconds": _bucket_seconds(float(previous_end), bucket_size_seconds),
                }
            )
    if (
        transcript_duration_seconds is not None
        and last_end is not None
        and not pd.isna(last_end)
        and float(transcript_duration_seconds) - float(last_end) >= pause_threshold_seconds
    ):
        silence_rows.append(
            {
                "silence_start_seconds": float(last_end),
                "silence_end_seconds": float(transcript_duration_seconds),
                "silence_start_label": format_seconds_label(float(last_end)),
                "silence_end_label": format_seconds_label(float(transcript_duration_seconds)),
                "gap_seconds": round(float(transcript_duration_seconds) - float(last_end), 2),
                "pause_type": classify_pause(float(transcript_duration_seconds) - float(last_end)),
                "speaker_transition": False,
                "bucket_start_seconds": _bucket_seconds(float(last_end), bucket_size_seconds),
            }
        )
    silence_df = pd.DataFrame(silence_rows)
    if not silence_df.empty:
        silence_df["bucket_label"] = silence_df["bucket_start_seconds"].apply(format_seconds_label)

    pause_type_df = pd.DataFrame(columns=["pause_type", "count", "total_gap_seconds"])
    if not silence_df.empty:
        pause_type_df = (
            silence_df.groupby("pause_type", as_index=False)
            .agg(
                count=("gap_seconds", "size"),
                total_gap_seconds=("gap_seconds", "sum"),
            )
        )
        pause_order = ["Thinking pause", "Hesitation", "Strong silence"]
        pause_type_df["pause_type"] = pd.Categorical(
            pause_type_df["pause_type"],
            categories=pause_order,
            ordered=True,
        )
        pause_type_df = pause_type_df.sort_values("pause_type").reset_index(drop=True)

    pause_distribution_df = pd.DataFrame(columns=["duration_bin", "count"])
    if not silence_df.empty:
        histogram_source = silence_df.copy()
        histogram_source["duration_bin"] = pd.cut(
            histogram_source["gap_seconds"],
            bins=[0.75, 1.0, 2.0, 3.0, float("inf")],
            labels=["0.75-1s", "1-2s", "2-3s", "3s+"],
            include_lowest=True,
            right=False,
        )
        pause_distribution_df = (
            histogram_source.groupby("duration_bin", as_index=False, observed=False)
            .agg(count=("gap_seconds", "size"))
        )

    speaker_df = (
        ordered_df.groupby("speaker", as_index=False)
        .agg(
            speaking_seconds=("duration_seconds", "sum"),
            segments=("text", "size"),
            words=("word_count", "sum"),
        )
        if "speaker" in ordered_df.columns
        else pd.DataFrame(columns=["speaker", "speaking_seconds", "segments", "words"])
    )
    if not speaker_df.empty:
        total_speaking = float(speaker_df["speaking_seconds"].fillna(0).sum())
        speaker_df["speaking_seconds"] = speaker_df["speaking_seconds"].fillna(0.0)
        speaker_df["speaking_label"] = speaker_df["speaking_seconds"].apply(format_seconds_label)
        speaker_df["share_pct"] = speaker_df["speaking_seconds"].apply(
            lambda value: round((float(value) / total_speaking) * 100, 1) if total_speaking > 0 else 0.0
        )
        speaker_df = speaker_df.sort_values(by=["speaking_seconds", "words"], ascending=[False, False]).reset_index(drop=True)

    speaker_turns_df = ordered_df.copy()
    if not speaker_turns_df.empty:
        speaker_turns_df["end_seconds"] = speaker_turns_df["end_seconds"].fillna(speaker_turns_df["start_seconds"])
        speaker_turns_df["duration_seconds"] = speaker_turns_df["duration_seconds"].fillna(0.0)
        speaker_turns_df["excerpt"] = speaker_turns_df["text"].fillna("").astype(str).str.slice(0, 90)

    normalized_text = " ".join(words_df["word"].astype(str).tolist()).lower() if not words_df.empty else " ".join(ordered_df["text"].fillna("").astype(str).tolist()).lower()
    normalized_text = normalized_text.replace("’", "'")
    filler_patterns = {
        "uh": r"\buh+\b",
        "um": r"\bum+\b",
        "you know": r"\byou know\b",
        "so": r"\bso\b",
    }
    filler_rows = []
    total_words = int(ordered_df["word_count"].sum())
    for filler, pattern in filler_patterns.items():
        count = len(re.findall(pattern, normalized_text))
        filler_rows.append(
            {
                "filler": filler,
                "count": count,
                "per_1000_words": round((count / total_words) * 1000, 2) if total_words > 0 else 0.0,
            }
        )
    filler_df = pd.DataFrame(filler_rows).sort_values(by=["count", "filler"], ascending=[False, True]).reset_index(drop=True)

    sentence_length_df = pd.DataFrame(columns=["sentence_length_bin", "count"])
    if not sentence_df.empty:
        sentence_length_working = sentence_df.copy()
        sentence_length_working["sentence_length_bin"] = pd.cut(
            sentence_length_working["word_count"],
            bins=[0, 5, 10, 20, 30, float("inf")],
            labels=["1-5", "6-10", "11-20", "21-30", "31+"],
            include_lowest=True,
            right=True,
        )
        sentence_length_df = (
            sentence_length_working.groupby("sentence_length_bin", as_index=False, observed=False)
            .agg(count=("word_count", "size"))
        )

    confidence_distribution_df = pd.DataFrame(columns=["confidence_bin", "count"])
    if not words_df.empty and "confidence" in words_df.columns:
        confidence_working = words_df.dropna(subset=["confidence"]).copy()
        if not confidence_working.empty:
            confidence_working["confidence_bin"] = pd.cut(
                confidence_working["confidence"],
                bins=[0.0, 0.2, 0.4, 0.6, 0.8, 1.01],
                labels=["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"],
                include_lowest=True,
                right=False,
            )
            confidence_distribution_df = (
                confidence_working.groupby("confidence_bin", as_index=False, observed=False)
                .agg(count=("confidence", "size"))
            )

    interruption_rows: List[Dict[str, Any]] = []
    response_rows: List[Dict[str, Any]] = []
    for idx in range(1, len(ordered_df.index)):
        prev_row = ordered_df.loc[idx - 1]
        curr_row = ordered_df.loc[idx]
        if prev_row.get("speaker") == curr_row.get("speaker"):
            continue
        prev_end = prev_row.get("end_seconds")
        curr_start = curr_row.get("start_seconds")
        if pd.isna(prev_end) or pd.isna(curr_start):
            continue
        gap_seconds = round(float(curr_start) - float(prev_end), 3)
        response_rows.append(
            {
                "from_speaker": prev_row.get("speaker"),
                "to_speaker": curr_row.get("speaker"),
                "gap_seconds": gap_seconds,
                "from_end_label": format_seconds_label(float(prev_end)),
                "to_start_label": format_seconds_label(float(curr_start)),
            }
        )
        if gap_seconds <= 0.2:
            interruption_rows.append(
                {
                    "from_speaker": prev_row.get("speaker"),
                    "to_speaker": curr_row.get("speaker"),
                    "gap_seconds": gap_seconds,
                    "kind": "Overlap" if gap_seconds < 0 else "Rapid handoff",
                    "time_seconds": float(curr_start),
                    "time_label": format_seconds_label(float(curr_start)),
                }
            )
    interruptions_df = pd.DataFrame(interruption_rows)
    response_time_df = pd.DataFrame(response_rows)
    if not response_time_df.empty:
        response_time_df["speaker_pair"] = response_time_df["from_speaker"].astype(str) + " -> " + response_time_df["to_speaker"].astype(str)
    if not interruptions_df.empty:
        interruptions_df["speaker_pair"] = interruptions_df["from_speaker"].astype(str) + " -> " + interruptions_df["to_speaker"].astype(str)

    burst_rows: List[Dict[str, Any]] = []
    if not ordered_df.empty:
        current_burst = None
        for _, row in ordered_df.iterrows():
            start_seconds = row.get("start_seconds")
            end_seconds = row.get("end_seconds")
            if pd.isna(start_seconds) or pd.isna(end_seconds):
                continue
            if current_burst is None:
                current_burst = {
                    "speaker": row.get("speaker"),
                    "start_seconds": float(start_seconds),
                    "end_seconds": float(end_seconds),
                    "word_count": int(row.get("word_count") or 0),
                }
                continue
            gap_from_previous = float(start_seconds) - float(current_burst["end_seconds"])
            if row.get("speaker") == current_burst["speaker"] and gap_from_previous < pause_threshold_seconds:
                current_burst["end_seconds"] = float(end_seconds)
                current_burst["word_count"] += int(row.get("word_count") or 0)
            else:
                burst_rows.append(current_burst)
                current_burst = {
                    "speaker": row.get("speaker"),
                    "start_seconds": float(start_seconds),
                    "end_seconds": float(end_seconds),
                    "word_count": int(row.get("word_count") or 0),
                }
        if current_burst is not None:
            burst_rows.append(current_burst)
    burst_df = pd.DataFrame(burst_rows)
    burst_distribution_df = pd.DataFrame(columns=["burst_duration_bin", "count"])
    if not burst_df.empty:
        burst_df["duration_seconds"] = (burst_df["end_seconds"] - burst_df["start_seconds"]).round(2)
        burst_df["start_label"] = burst_df["start_seconds"].apply(format_seconds_label)
        burst_df["burst_duration_bin"] = pd.cut(
            burst_df["duration_seconds"],
            bins=[0, 10, 30, 60, float("inf")],
            labels=["0-10s", "10-30s", "30-60s", "60s+"],
            include_lowest=True,
            right=False,
        )
        burst_distribution_df = burst_df.groupby("burst_duration_bin", as_index=False, observed=False).agg(count=("speaker", "size"))

    utterance_duration_distribution_df = pd.DataFrame(columns=["duration_bin", "count"])
    if not ordered_df.empty:
        utterance_duration_working = ordered_df.dropna(subset=["duration_seconds"]).copy()
        if not utterance_duration_working.empty:
            utterance_duration_working["duration_bin"] = pd.cut(
                utterance_duration_working["duration_seconds"],
                bins=[0, 5, 10, 20, 30, float("inf")],
                labels=["0-5s", "5-10s", "10-20s", "20-30s", "30s+"],
                include_lowest=True,
                right=False,
            )
            utterance_duration_distribution_df = (
                utterance_duration_working.groupby("duration_bin", as_index=False, observed=False)
                .agg(count=("duration_seconds", "size"))
            )

    segment_mix_df = ordered_df.copy()
    segment_mix_df["segment_style"] = segment_mix_df["duration_seconds"].apply(
        lambda value: "Quick hits" if pd.notna(value) and float(value) < 5
        else "Steady beats" if pd.notna(value) and float(value) < 10
        else "Long stretches"
    )
    segment_mix_df = (
        segment_mix_df.groupby("segment_style", as_index=False)
        .agg(
            segments=("text", "size"),
            words=("word_count", "sum"),
            speaking_seconds=("duration_seconds", "sum"),
        )
    )
    segment_style_order = ["Quick hits", "Steady beats", "Long stretches"]
    if not segment_mix_df.empty:
        segment_mix_df["segment_style"] = pd.Categorical(
            segment_mix_df["segment_style"],
            categories=segment_style_order,
            ordered=True,
        )
        segment_mix_df = segment_mix_df.sort_values("segment_style").reset_index(drop=True)

    total_words = int(ordered_df["word_count"].sum())
    if not segment_mix_df.empty:
        segment_mix_df["word_share_pct"] = segment_mix_df["words"].apply(
            lambda value: round((float(value) / total_words) * 100, 1) if total_words > 0 else 0.0
        )
        segment_mix_df["speaking_seconds"] = segment_mix_df["speaking_seconds"].fillna(0.0)
        segment_mix_df["speaking_time_label"] = segment_mix_df["speaking_seconds"].apply(format_seconds_label)

    terms_df = extract_meaningful_terms_from_series(ordered_df["text"], top_n=10)

    duration_values = ordered_df["duration_seconds"].dropna()
    total_speaking_seconds = float(duration_values.sum()) if not duration_values.empty else 0.0
    full_span_seconds = 0.0
    valid_starts = ordered_df["start_seconds"].dropna()
    valid_ends = ordered_df["end_seconds"].dropna()
    if not valid_starts.empty and not valid_ends.empty:
        full_span_seconds = max(float(valid_ends.max()) - float(valid_starts.min()), 0.0)

    raw_named_entity_rows = []
    for entity in _extract_named_entity_items(transcript_payload):
        entity_text = str(entity.get("text") or "").strip()
        entity_type = str(entity.get("entity_type") or entity.get("type") or "Unknown").strip()
        start_seconds = _coerce_seconds(entity.get("start"))
        if not entity_text:
            continue
        raw_named_entity_rows.append(
            {
                "entity": entity_text,
                "entity_type": entity_type,
                "start_seconds": start_seconds,
                "time_label": format_seconds_label(start_seconds),
            }
        )
    named_entities_df = pd.DataFrame(raw_named_entity_rows)
    if not named_entities_df.empty:
        named_entities_df = (
            named_entities_df.groupby(["entity", "entity_type"], as_index=False)
            .agg(
                count=("entity", "size"),
                first_seen_seconds=("start_seconds", "min"),
            )
            .sort_values(by=["count", "first_seen_seconds", "entity"], ascending=[False, True, True])
            .reset_index(drop=True)
        )
        named_entities_df["first_seen_label"] = named_entities_df["first_seen_seconds"].apply(format_seconds_label)

    numbers_rows = []
    source_for_entities = sentence_df if not sentence_df.empty else ordered_df
    entity_type_priority = {
        "MONEY": 0,
        "DATE_INTERVAL": 1,
        "EVENT": 2,
        "ORGANIZATION": 3,
        "LOCATION_CITY": 4,
        "LOCATION_COUNTRY": 5,
        "NAME": 6,
        "NAME_GIVEN": 7,
        "OCCUPATION": 8,
        "DURATION": 9,
        "FILENAME": 10,
    }

    entity_contexts = _EntityContextFinder(source_for_entities)
    for entity_row in raw_named_entity_rows:
        entity_text = str(entity_row.get("entity") or "").strip()
        entity_type = str(entity_row.get("entity_type") or "Unknown").strip()
        start_seconds = _coerce_seconds(entity_row.get("start_seconds"))
        if not entity_text:
            continue
        context_row = entity_contexts.find(entity_text, start_seconds)
        numbers_rows.append(
            {
                "mention": entity_text,
                "kind": entity_type,
                "speaker": context_row.get("speaker"),
                "time_seconds": start_seconds if start_seconds is not None else context_row.get("start_seconds"),
                "time_label": format_seconds_label(start_seconds if start_seconds is not None else _coerce_seconds(context_row.get("start_seconds"))),
                "context": str(context_row.get("sentence") or context_row.get("text") or "").strip()[:180],
                "ner_context": "",
                "_priority": entity_type_priority.get(entity_type, 99),
            }
        )
    numbers_df = pd.DataFrame(numbers_rows)
    if not numbers_df.empty:
        numbers_df = (
            numbers_df.drop_duplicates(subset=["mention", "kind", "time_label", "context"])
            .sort_values(by=["_priority", "time_seconds", "mention"], ascending=[True, True, True], na_position="last")
            .drop(columns=["_priority"], errors="ignore")
            .reset_index(drop=True)
        )

    topic_rows = []
    if not ordered_df.empty:
        topic_working = ordered_df.copy()
        topic_working["bucket_start_seconds"] = topic_working["start_seconds"].apply(lambda value: _bucket_seconds(value, bucket_size_seconds))
        for bucket_start, bucket_df in topic_working.groupby("bucket_start_seconds"):
            bucket_terms = extract_meaningful_terms_from_series(bucket_df["text"], top_n=1)
            top_topic = bucket_terms.iloc[0]["term"] if not bucket_terms.empty else None
            dominant_speaker = (
                bucket_df.groupby("speaker")["duration_seconds"].sum().sort_values(ascending=False).index[0]
                if "speaker" in bucket_df.columns and not bucket_df.empty else None
            )
            topic_rows.append(
                {
                    "bucket_start_seconds": bucket_start,
                    "bucket_label": format_seconds_label(bucket_start),
                    "topic": top_topic or "General discussion",
                    "speaker": dominant_speaker,
                }
            )
    topic_timeline_df = pd.DataFrame(topic_rows)

    engagement_df = timeline_df.copy()
    if not engagement_df.empty:
        pause_by_bucket = (
            silence_df.groupby("bucket_start_seconds", as_index=False)
            .agg(
                pause_seconds=("gap_seconds", "sum"),
                pause_count=("gap_seconds", "size"),
            )
            if not silence_df.empty else pd.DataFrame(columns=["bucket_start_seconds", "pause_seconds", "pause_count"])
        )
        if not interruptions_df.empty:
            interruptions_by_bucket = interruptions_df.copy()
            interruptions_by_bucket["bucket_start_seconds"] = interruptions_by_bucket["time_seconds"].apply(
                lambda value: _bucket_seconds(_coerce_seconds(value), bucket_size_seconds)
            )
            interruptions_by_bucket = interruptions_by_bucket.groupby("bucket_start_seconds", as_index=False).agg(
                interruption_count=("kind", "size")
            )
        else:
            interruptions_by_bucket = pd.DataFrame(columns=["bucket_start_seconds", "interruption_count"])

        engagement_df = engagement_df.rename(columns={"minute_bucket": "bucket_index"})
        engagement_df["bucket_start_seconds"] = engagement_df["bucket_index"] * 60
        engagement_df = engagement_df.merge(pause_by_bucket, on="bucket_start_seconds", how="left")
        engagement_df = engagement_df.merge(interruptions_by_bucket, on="bucket_start_seconds", how="left")
        engagement_df["pause_seconds"] = engagement_df["pause_seconds"].fillna(0.0)
        engagement_df["pause_count"] = engagement_df["pause_count"].fillna(0).astype(int)
        engagement_df["interruption_count"] = engagement_df["interruption_count"].fillna(0).astype(int)
        engagement_df["bucket_label"] = engagement_df["bucket_start_seconds"].apply(format_seconds_label)
        engagement_df["silence_ratio"] = engagement_df["pause_seconds"].apply(lambda value: min(float(value) / bucket_size_seconds, 1.0))
        engagement_df["pace_delta"] = engagement_df["words_per_minute"].diff().abs().fillna(0.0)

        wpm_low = _safe_quantile(engagement_df["words_per_minute"], 0.1, 0.0)
        wpm_high = _safe_quantile(engagement_df["words_per_minute"], 0.9, 1.0)
        delta_high = _safe_quantile(engagement_df["pace_delta"], 0.9, 1.0)
        pause_high = _safe_quantile(engagement_df["pause_seconds"], 0.9, 1.0)
        interrupt_high = max(float(engagement_df["interruption_count"].max()), 1.0)

        engagement_scores = []
        low_energy_labels = []
        clarity_scores = []
        cognitive_load_values = []
        for _, row in engagement_df.iterrows():
            pace_score = _min_max_scale(float(row["words_per_minute"]), wpm_low, wpm_high)
            silence_penalty = _min_max_scale(float(row["pause_seconds"]), 0.0, pause_high)
            interruption_score = _min_max_scale(float(row["interruption_count"]), 0.0, interrupt_high)
            variation_score = _min_max_scale(float(row["pace_delta"]), 0.0, delta_high)

            engagement_score = round(((pace_score * 0.45) + ((1 - silence_penalty) * 0.35) + (interruption_score * 0.2)) * 100, 1)
            cognitive_load = round(((silence_penalty * 0.45) + ((1 - pace_score) * 0.2) + (variation_score * 0.2)) * 100, 1)
            clarity_score = round((((1 - silence_penalty) * 0.3) + pace_score * 0.35 + ((1 - min(variation_score, 1.0)) * 0.1)) * 100, 1)
            engagement_scores.append(engagement_score)
            cognitive_load_values.append(cognitive_load)
            clarity_scores.append(clarity_score)

            if row["pause_seconds"] >= 8 or (row["words_per_minute"] <= wpm_low and row["pause_seconds"] >= 4):
                low_energy_labels.append("Low energy")
            elif row["pause_seconds"] >= 4:
                low_energy_labels.append("Watch")
            else:
                low_energy_labels.append("")

        engagement_df["engagement_score"] = engagement_scores
        engagement_df["cognitive_load_index"] = cognitive_load_values
        engagement_df["clarity_score"] = clarity_scores
        engagement_df["energy_label"] = low_energy_labels

        filler_total = float(filler_df["count"].sum()) if not filler_df.empty else 0.0
        filler_per_bucket = pd.DataFrame(columns=["bucket_start_seconds", "filler_count"])
        if not words_df.empty:
            filler_words = {"uh", "um", "so"}
            filler_phrase_counts = []
            filler_word_working = words_df.copy()
            filler_word_working["normalized_word"] = filler_word_working["word"].str.lower().str.replace(r"[^\w']+", "", regex=True)
            filler_hits = filler_word_working[filler_word_working["normalized_word"].isin(filler_words)]
            if not filler_hits.empty:
                filler_phrase_counts.append(
                    filler_hits.groupby("bucket_start_seconds", as_index=False).agg(filler_count=("normalized_word", "size"))
                )
            if not sentence_df.empty:
                sentence_you_know = sentence_df[sentence_df["sentence"].str.lower().str.contains(r"\byou know\b", regex=True, na=False)]
                if not sentence_you_know.empty:
                    filler_phrase_counts.append(
                        sentence_you_know.groupby("bucket_start_seconds", as_index=False).agg(filler_count=("sentence", "size"))
                    )
            if filler_phrase_counts:
                filler_per_bucket = pd.concat(filler_phrase_counts, ignore_index=True).groupby("bucket_start_seconds", as_index=False).agg(
                    filler_count=("filler_count", "sum")
                )
        engagement_df = engagement_df.merge(filler_per_bucket, on="bucket_start_seconds", how="left")
        engagement_df["filler_count"] = engagement_df["filler_count"].fillna(0).astype(int)
        engagement_df["clarity_score"] = (
            engagement_df["clarity_score"] - engagement_df["filler_count"].apply(lambda value: min(value * 3, 20))
        ).clip(lower=0, upper=100)
        engagement_df["cognitive_load_index"] = (
            engagement_df["cognitive_load_index"] + engagement_df["filler_count"].apply(lambda value: min(value * 3, 20))
        ).clip(lower=0, upper=100)

    low_energy_df = engagement_df[engagement_df["energy_label"] != ""].copy() if not engagement_df.empty else pd.DataFrame()

    key_moment_rows = []
    strong_statement_pattern = re.compile(r"\b(?:important|key|must|need to|significant|critical|huge)\b", re.IGNORECASE)
    high_pace_threshold = _safe_quantile(pace_df["segment_wpm"], 0.9, 0.0) if not pace_df.empty else 0.0
    number_lookup = set()
    numeric_signal_kinds = {"MONEY", "DATE_INTERVAL", "DURATION", "EVENT"}
    if not numbers_df.empty:
        for _, number_row in numbers_df.iterrows():
            row_kind = str(number_row.get("kind") or "").strip().upper()
            if row_kind in numeric_signal_kinds:
                number_lookup.add((number_row.get("time_label"), number_row.get("context")))
    entity_texts = set(named_entities_df["entity"].head(25).astype(str).tolist()) if not named_entities_df.empty else set()
    source_for_moments = sentence_df if not sentence_df.empty else ordered_df
    for _, row in source_for_moments.iterrows():
        text = str(row.get("sentence") or row.get("text") or "").strip()
        if not text:
            continue
        normalized_text = re.sub(r"\s+", " ", text).strip()
        word_count = len(re.findall(r"\b\w+\b", normalized_text))
        alpha_word_count = len(re.findall(r"\b[a-zA-Z]{2,}\b", normalized_text))
        if len(normalized_text) < 24 or word_count < 4 or alpha_word_count < 3:
            continue

        score = 0
        reasons = []
        signal_count = 0

        has_numbers_signal = (format_seconds_label(_coerce_seconds(row.get("start_seconds"))), text[:180]) in number_lookup
        if has_numbers_signal:
            score += 2
            reasons.append("numbers")
            signal_count += 1

        has_strong_statement_signal = bool(strong_statement_pattern.search(text))
        if has_strong_statement_signal:
            score += 2
            reasons.append("strong statement")
            signal_count += 1

        has_named_entity_signal = entity_texts and any(entity.lower() in text.lower() for entity in list(entity_texts)[:10])
        if has_named_entity_signal:
            score += 1
            reasons.append("named entity")

        pace_value = 0.0
        if row.get("duration_seconds") and row.get("word_count"):
            pace_value = (float(row.get("word_count")) / float(row.get("duration_seconds"))) * 60 if float(row.get("duration_seconds")) > 0 else 0.0
        has_pace_signal = pace_value >= high_pace_threshold and high_pace_threshold > 0
        if has_pace_signal:
            score += 1
            reasons.append("pace spike")
            signal_count += 1

        if has_named_entity_signal and not (has_numbers_signal or has_strong_statement_signal or has_pace_signal):
            continue

        if signal_count < 2 and score < 3:
            continue

        if score > 0:
            key_moment_rows.append(
                {
                    "time_seconds": row.get("start_seconds"),
                    "time_label": format_seconds_label(_coerce_seconds(row.get("start_seconds"))),
                    "speaker": row.get("speaker"),
                    "score": score,
                    "reasons": ", ".join(reasons),
                    "excerpt": text[:160],
                }
            )
    key_moments_df = pd.DataFrame(key_moment_rows)
    if not key_moments_df.empty:
        key_moments_df = key_moments_df.sort_values(by=["score", "time_seconds"], ascending=[False, True]).head(20).reset_index(drop=True)

    replay_navigation_df = pd.DataFrame()
    if not engagement_df.empty:
        dominant_speakers_by_bucket = (
            ordered_df.assign(bucket_start_seconds=ordered_df["start_seconds"].apply(lambda value: _bucket_seconds(value, bucket_size_seconds)))
            .groupby(["bucket_start_seconds", "speaker"], as_index=False)
            .agg(speaking_seconds=("duration_seconds", "sum"))
        )
        dominant_speaker_df = pd.DataFrame(columns=["bucket_start_seconds", "dominant_speaker"])
        if not dominant_speakers_by_bucket.empty:
            dominant_speaker_df = dominant_speakers_by_bucket.sort_values(
                by=["bucket_start_seconds", "speaking_seconds"], ascending=[True, False]
            ).drop_duplicates(subset=["bucket_start_seconds"]).rename(columns={"speaker": "dominant_speaker"})[
                ["bucket_start_seconds", "dominant_speaker"]
            ]
        key_counts = pd.DataFrame(columns=["bucket_start_seconds", "key_moments"])
        if not key_moments_df.empty:
            key_counts = key_moments_df.copy()
            key_counts["bucket_start_seconds"] = key_counts["time_seconds"].apply(lambda value: _bucket_seconds(_coerce_seconds(value), bucket_size_seconds))
            key_counts = key_counts.groupby("bucket_start_seconds", as_index=False).agg(key_moments=("score", "size"))
        replay_navigation_df = engagement_df[[
            "bucket_start_seconds",
            "bucket_label",
            "engagement_score",
            "clarity_score",
            "cognitive_load_index",
            "pause_seconds",
            "words_per_minute",
            "energy_label",
        ]].merge(topic_timeline_df[["bucket_start_seconds", "topic"]], on="bucket_start_seconds", how="left")
        replay_navigation_df = replay_navigation_df.merge(dominant_speaker_df, on="bucket_start_seconds", how="left")
        replay_navigation_df = replay_navigation_df.merge(key_counts, on="bucket_start_seconds", how="left")
        replay_navigation_df["key_moments"] = replay_navigation_df["key_moments"].fillna(0).astype(int)
        replay_navigation_df["highlight"] = replay_navigation_df.apply(
            lambda row: "Key moment" if row["key_moments"] > 0
            else "Low energy" if str(row["energy_label"]).strip()
            else "Steady",
            axis=1,
        )

    avg_pause_seconds = round(float(silence_df["gap_seconds"].mean()), 2) if not silence_df.empty else 0.0
    pause_count_over_1s = int((silence_df["gap_seconds"] > 1.0).sum()) if not silence_df.empty else 0
    avg_response_seconds = round(float(response_time_df["gap_seconds"].mean()), 2) if not response_time_df.empty else 0.0
    interruption_count = int(len(interruptions_df.index)) if not interruptions_df.empty else 0
    filler_total = int(filler_df["count"].sum()) if not filler_df.empty else 0
    pace_wpm_values = pace_df["segment_wpm"].dropna() if not pace_df.empty else pd.Series(dtype=float)

    summary = {
        "total_segments": int(len(ordered_df.index)),
        "total_words": total_words,
        "avg_words_per_minute": round((total_words / total_speaking_seconds) * 60, 1) if total_speaking_seconds > 0 else 0.0,
        "total_speaking_seconds": round(total_speaking_seconds, 2),
        "full_span_seconds": round(full_span_seconds, 2),
        "silence_count": int(len(silence_df.index)),
        "total_silence_seconds": round(float(silence_df["gap_seconds"].sum()), 2) if not silence_df.empty else 0.0,
        "longest_silence_seconds": round(float(silence_df["gap_seconds"].max()), 2) if not silence_df.empty else 0.0,
        "avg_pause_seconds": avg_pause_seconds,
        "pause_count_over_1s": pause_count_over_1s,
        "pause_timing_source": "word" if not words_df.empty else "segment",
        "global_wpm": round((total_words / total_speaking_seconds) * 60, 1) if total_speaking_seconds > 0 else 0.0,
        "min_wpm": round(float(pace_wpm_values.min()), 1) if not pace_wpm_values.empty else 0.0,
        "max_wpm": round(float(pace_wpm_values.max()), 1) if not pace_wpm_values.empty else 0.0,
        "avg_response_seconds": avg_response_seconds,
        "interruption_count": interruption_count,
        "filler_total": filler_total,
        "entity_count": int(named_entities_df["count"].sum()) if not named_entities_df.empty else 0,
        "kpi_mentions": int(len(numbers_df.index)) if not numbers_df.empty else 0,
        "avg_confidence": round(float(words_df["confidence"].dropna().mean()), 3) if not words_df.empty and not words_df["confidence"].dropna().empty else 0.0,
        "avg_sentence_length": round(float(sentence_df["word_count"].mean()), 1) if not sentence_df.empty else 0.0,
        "avg_engagement_score": round(float(engagement_df["engagement_score"].mean()), 1) if not engagement_df.empty else 0.0,
        "avg_clarity_score": round(float(engagement_df["clarity_score"].mean()), 1) if not engagement_df.empty else 0.0,
        "avg_cognitive_load_index": round(float(engagement_df["cognitive_load_index"].mean()), 1) if not engagement_df.empty else 0.0,
        "low_energy_zone_count": int(len(low_energy_df.index)) if not low_energy_df.empty else 0,
        "top_term": terms_df.iloc[0]["term"] if not terms_df.empty else None,
        "dominant_segment_style": segment_mix_df.iloc[0]["segment_style"] if not segment_mix_df.empty else None,
    }

    return {
        "segments_df": ordered_df,
        "words_df": words_df,
        "sentence_df": sentence_df,
        "timeline_df": timeline_df,
        "pace_df": pace_df,
        "silence_df": silence_df,
        "pause_type_df": pause_type_df,
        "pause_distribution_df": pause_distribution_df,
        "speaker_df": speaker_df,
        "speaker_turns_df": speaker_turns_df,
        "filler_df": filler_df,
        "sentence_length_df": sentence_length_df,
        "confidence_distribution_df": confidence_distribution_df,
        "engagement_df": engagement_df,
        "low_energy_df": low_energy_df,
        "burst_df": burst_df,
        "burst_distribution_df": burst_distribution_df,
        "utterance_duration_distribution_df": utterance_duration_distribution_df,
        "topic_timeline_df": topic_timeline_df,
        "named_entities_df": named_entities_df,
        "numbers_df": numbers_df,
        "interruptions_df": interruptions_df,
        "response_time_df": response_time_df,
        "key_moments_df": key_moments_df,
        "replay_navigation_df": replay_navigation_df,
        "segment_mix_df": segment_mix_df,
        "terms_df": terms_df,
        "summary": summary,
    }
