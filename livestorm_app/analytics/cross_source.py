"""Cross-source insights: the session-stage timeline and reaction moments across transcript, chat and questions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from livestorm_app.analytics.audience import split_audience_chat
from livestorm_app.analytics.transcript import build_transcript_insights


def _normalize_series_to_progress(series: pd.Series) -> pd.Series:
    timestamps = pd.to_datetime(series, utc=True, errors="coerce")
    valid = timestamps.dropna()
    if valid.empty:
        return pd.Series([None] * len(series), index=series.index, dtype="float64")
    min_ts = valid.min()
    max_ts = valid.max()
    duration_seconds = (max_ts - min_ts).total_seconds()
    if duration_seconds <= 0:
        return pd.Series([50.0 if pd.notna(value) else None for value in timestamps], index=series.index, dtype="float64")
    return timestamps.apply(
        lambda value: round(((value - min_ts).total_seconds() / duration_seconds) * 100, 2) if pd.notna(value) else None
    )


def _bucket_start(value: Any, bucket_size_pct: int) -> Optional[int]:
    """The bucket a progress percentage falls in. 100% (the last item of a
    series) belongs to the last bucket, not to a bucket past the end. The last
    bucket is the last start in range(0, 100, bucket_size_pct), which is also
    right when the size does not divide 100."""
    if pd.isna(value):
        return None
    bucket = int((float(value) // bucket_size_pct) * bucket_size_pct)
    last_bucket = ((100 - 1) // bucket_size_pct) * bucket_size_pct
    return min(bucket, last_bucket)


def _format_session_stage_label(start_pct: float, end_pct: float) -> str:
    midpoint = (float(start_pct) + float(end_pct)) / 2
    if midpoint < 15:
        stage = "Opening"
    elif midpoint < 35:
        stage = "Early"
    elif midpoint < 65:
        stage = "Middle"
    elif midpoint < 85:
        stage = "Late"
    else:
        stage = "Closing"
    return f"{stage} ({int(start_pct)}-{int(min(end_pct, 100))}%)"


def build_cross_source_insights(
    chat_df: Optional[pd.DataFrame],
    questions_df: Optional[pd.DataFrame],
    transcript_payload: Optional[Dict[str, Any]],
    bucket_size_pct: int = 10,
    *,
    transcript_insights: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """`transcript_insights` lets a caller that already built them skip a
    second (expensive) `build_transcript_insights` pass; frames are copied
    before being modified."""
    if not isinstance(chat_df, pd.DataFrame) or not isinstance(questions_df, pd.DataFrame) or not isinstance(transcript_payload, dict):
        return {"combined_timeline_df": pd.DataFrame(), "reaction_moments_df": pd.DataFrame()}

    if transcript_insights is None:
        transcript_insights = build_transcript_insights(transcript_payload)
    segments_df = transcript_insights.get("segments_df", pd.DataFrame())
    if segments_df.empty:
        return {"combined_timeline_df": pd.DataFrame(), "reaction_moments_df": pd.DataFrame()}

    transcript_duration = float(segments_df["end_seconds"].dropna().max()) if not segments_df["end_seconds"].dropna().empty else 0.0
    if transcript_duration <= 0:
        return {"combined_timeline_df": pd.DataFrame(), "reaction_moments_df": pd.DataFrame()}

    working_segments = segments_df.copy()
    working_segments["progress_pct"] = working_segments["start_seconds"].apply(
        lambda value: round((float(value) / transcript_duration) * 100, 2) if pd.notna(value) and transcript_duration > 0 else None
    )
    working_segments["bucket_start_pct"] = working_segments["progress_pct"].apply(
        lambda value: _bucket_start(value, bucket_size_pct)
    )

    combined_frames: List[pd.DataFrame] = []

    transcript_bins = (
        working_segments.dropna(subset=["bucket_start_pct"])
        .groupby("bucket_start_pct", as_index=False)
        .agg(
            transcript_words=("word_count", "sum"),
            transcript_segments=("text", "size"),
            transcript_pace=("words_per_second", "mean"),
        )
    )
    if not transcript_bins.empty:
        transcript_bins["transcript_wpm"] = transcript_bins["transcript_pace"].apply(
            lambda value: round(float(value) * 60, 1) if pd.notna(value) else 0.0
        )
        combined_frames.append(transcript_bins[["bucket_start_pct", "transcript_words", "transcript_segments", "transcript_wpm"]])

    # Reactions are the audience's: team and guest-speaker messages are left out.
    chat_df, _ = split_audience_chat(chat_df)
    if "created_at" in chat_df.columns:
        chat_progress = _normalize_series_to_progress(chat_df["created_at"])
        chat_working = chat_df.copy()
        chat_working["progress_pct"] = chat_progress
        chat_working["bucket_start_pct"] = chat_working["progress_pct"].apply(
            lambda value: _bucket_start(value, bucket_size_pct)
        )
        chat_bins = (
            chat_working.dropna(subset=["bucket_start_pct"])
            .groupby("bucket_start_pct", as_index=False)
            .agg(chat_messages=("text_content", "size"))
        )
        if not chat_bins.empty:
            combined_frames.append(chat_bins)
    else:
        chat_working = chat_df.copy()

    question_time_col = "asked_at" if "asked_at" in questions_df.columns else "created_at" if "created_at" in questions_df.columns else None
    if question_time_col is not None:
        question_progress = _normalize_series_to_progress(questions_df[question_time_col])
        question_working = questions_df.copy()
        question_working["progress_pct"] = question_progress
        question_working["bucket_start_pct"] = question_working["progress_pct"].apply(
            lambda value: _bucket_start(value, bucket_size_pct)
        )
        question_bins = (
            question_working.dropna(subset=["bucket_start_pct"])
            .groupby("bucket_start_pct", as_index=False)
            .agg(question_count=("question", "size"))
        )
        if not question_bins.empty:
            combined_frames.append(question_bins)
    else:
        question_working = questions_df.copy()

    if combined_frames:
        combined_timeline_df = combined_frames[0]
        for frame in combined_frames[1:]:
            combined_timeline_df = combined_timeline_df.merge(frame, on="bucket_start_pct", how="outer")
        all_buckets_df = pd.DataFrame({"bucket_start_pct": list(range(0, 100, bucket_size_pct))})
        combined_timeline_df = (
            all_buckets_df.merge(combined_timeline_df, on="bucket_start_pct", how="left")
            .fillna(0)
            .sort_values("bucket_start_pct")
            .reset_index(drop=True)
        )
        combined_timeline_df["bucket_end_pct"] = combined_timeline_df["bucket_start_pct"] + bucket_size_pct
        combined_timeline_df["progress_window"] = combined_timeline_df.apply(
            lambda row: f"{int(row['bucket_start_pct'])}-{int(min(row['bucket_end_pct'], 100))}%",
            axis=1,
        )
        combined_timeline_df["session_stage"] = combined_timeline_df.apply(
            lambda row: _format_session_stage_label(row["bucket_start_pct"], row["bucket_end_pct"]),
            axis=1,
        )
        for column in ("chat_messages", "question_count", "transcript_words", "transcript_segments"):
            if column in combined_timeline_df.columns:
                combined_timeline_df[column] = combined_timeline_df[column].astype(float)
        for column in ("chat_messages", "question_count", "transcript_words", "transcript_segments", "transcript_wpm"):
            if column not in combined_timeline_df.columns:
                combined_timeline_df[column] = 0.0
    else:
        combined_timeline_df = pd.DataFrame()

    if not combined_timeline_df.empty:
        grouped_segments = (
            working_segments.dropna(subset=["bucket_start_pct"])
            .groupby("bucket_start_pct", as_index=False)
            .agg(
                start_seconds=("start_seconds", "min"),
                start_label=("start_label", "first"),
                speaker=("speaker", lambda values: next((str(v).strip() for v in values if str(v).strip()), "")),
                transcript_excerpt=("text", lambda values: " ".join([str(v).strip() for v in values if str(v).strip()])),
                transcript_segments=("text", "size"),
                transcript_words=("word_count", "sum"),
            )
        )
        reaction_moments_df = combined_timeline_df.merge(grouped_segments, on="bucket_start_pct", how="left")
        reaction_moments_df["chat_messages"] = reaction_moments_df["chat_messages"].fillna(0.0)
        reaction_moments_df["question_count"] = reaction_moments_df["question_count"].fillna(0.0)
        reaction_moments_df = reaction_moments_df[
            ((reaction_moments_df["chat_messages"] > 0) | (reaction_moments_df["question_count"] > 0))
            & reaction_moments_df["transcript_excerpt"].fillna("").astype(str).str.strip().ne("")
        ].copy()
        if not reaction_moments_df.empty:
            reaction_moments_df["total_reactions"] = (
                reaction_moments_df["chat_messages"].astype(float)
                + reaction_moments_df["question_count"].astype(float)
            )
            reaction_moments_df["excerpt"] = reaction_moments_df["transcript_excerpt"].apply(
                lambda value: str(value)[:120] + ("..." if len(str(value)) > 120 else "")
            )
            reaction_moments_df = reaction_moments_df.sort_values(
                by=["total_reactions", "question_count", "chat_messages", "bucket_start_pct"],
                ascending=[False, False, False, True],
            ).head(8).reset_index(drop=True)
    else:
        reaction_moments_df = pd.DataFrame()

    return {"combined_timeline_df": combined_timeline_df, "reaction_moments_df": reaction_moments_df}
