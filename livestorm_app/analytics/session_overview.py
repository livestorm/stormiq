from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from livestorm_app.analytics.audience import audience_chat_mask, is_internal_session


def extract_session_resource(session_payload: Dict[str, Any]) -> Dict[str, Any]:
    data = session_payload.get("data")
    return data if isinstance(data, dict) else {}


def extract_session_attributes(session_payload: Dict[str, Any]) -> Dict[str, Any]:
    session_resource = extract_session_resource(session_payload)
    attributes = session_resource.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def extract_session_people(session_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    included = session_payload.get("included")
    if not isinstance(included, list):
        return []
    return [
        item for item in included
        if isinstance(item, dict) and str(item.get("type") or "").strip() == "people"
    ]


def _format_unix_timestamp(value: Any) -> str:
    try:
        if value in (None, "", 0):
            return ""
        timestamp = pd.to_datetime(value, unit="s", utc=True, errors="coerce")
        if pd.isna(timestamp):
            return ""
        return timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError):
        return ""


def _format_duration_label(value: Any) -> str:
    try:
        if value in (None, ""):
            return ""
        total_seconds = int(float(value))
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: List[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def build_session_people_df(session_payload: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for person in extract_session_people(session_payload):
        attributes = person.get("attributes")
        if not isinstance(attributes, dict):
            continue
        registrant_detail = attributes.get("registrant_detail")
        registrant_detail = registrant_detail if isinstance(registrant_detail, dict) else {}
        fields = registrant_detail.get("fields")
        fields = fields if isinstance(fields, list) else []
        field_values = {
            str(field.get("id") or "").strip(): field.get("value")
            for field in fields
            if isinstance(field, dict)
        }

        first_name = str(attributes.get("first_name") or field_values.get("first_name") or "").strip()
        last_name = str(attributes.get("last_name") or field_values.get("last_name") or "").strip()
        full_name = f"{first_name} {last_name}".strip()

        row = {
            "person_id": str(person.get("id") or "").strip(),
            "role": attributes.get("role"),
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "email": attributes.get("email") or field_values.get("email"),
            "company": field_values.get("company"),
            "job_title": field_values.get("job_title"),
            "timezone": attributes.get("timezone") or registrant_detail.get("timezone"),
            "attended": registrant_detail.get("attended"),
            "attendance_rate": registrant_detail.get("attendance_rate"),
            "attendance_duration": registrant_detail.get("attendance_duration"),
            "attendance_duration_label": _format_duration_label(registrant_detail.get("attendance_duration")),
            "has_viewed_replay": registrant_detail.get("has_viewed_replay"),
            "registration_type": registrant_detail.get("registration_type"),
            "is_guest_speaker": registrant_detail.get("is_guest_speaker"),
            "session_role": registrant_detail.get("session_role"),
            "browser_name": registrant_detail.get("browser_name"),
            "os_name": registrant_detail.get("os_name"),
            "ip_city": registrant_detail.get("ip_city"),
            "ip_country_code": registrant_detail.get("ip_country_code"),
            "ip_country_name": registrant_detail.get("ip_country_name"),
            "messages_count": attributes.get("messages_count", 0),
            "questions_count": attributes.get("questions_count", 0),
            "votes_count": attributes.get("votes_count", 0),
            "up_votes_count": attributes.get("up_votes_count", 0),
        }
        row["engagement_score"] = (
            int(row["messages_count"] or 0)
            + (int(row["questions_count"] or 0) * 3)
            + (int(row["up_votes_count"] or 0) * 2)
        )
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    people_df = pd.DataFrame(rows)
    people_df["attendance_rate"] = pd.to_numeric(people_df["attendance_rate"], errors="coerce")
    people_df["attendance_duration"] = pd.to_numeric(people_df["attendance_duration"], errors="coerce")
    for column in ("messages_count", "questions_count", "votes_count", "up_votes_count", "engagement_score"):
        people_df[column] = pd.to_numeric(people_df[column], errors="coerce").fillna(0).astype(int)
    return people_df


def session_is_internal(session_payload: Dict[str, Any]) -> bool:
    """Whether the session is internal (`audience.is_internal_session`), from its people."""
    return is_internal_session(build_session_people_df(session_payload))


def audience_people_mask(people_df: pd.DataFrame, *, internal: bool = False) -> pd.Series:
    """True for audience members: not the host team (`role == "team_member"`)
    and not a guest speaker (`is_guest_speaker`). On an internal session
    (`audience.is_internal_session`) everyone is audience."""
    if people_df.empty or internal:
        return pd.Series(True, index=people_df.index, dtype=bool)
    is_team = people_df["role"].fillna("").astype(str).str.strip().eq("team_member")
    is_guest_speaker = people_df["is_guest_speaker"].apply(
        lambda value: value is True or str(value or "").strip().lower() == "true"
    )
    return ~is_team & ~is_guest_speaker


def build_session_stats(
    session_payload: Dict[str, Any],
    *,
    internal: Optional[bool] = None,
    chat_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """`internal` is the session's `is_internal_session` decision; when it is
    None it is decided here from the session's people.

    `chat_df` is the session's chat (`audience.build_chat_df_from_payload`,
    empty when the chat has no message), or None when no chat payload is
    cached. With a chat, the message counts count the chat itself; without
    one they fall back to the people's `messages_count`, which Livestorm
    sometimes reports as 0 for everyone even when the chat has messages."""
    attributes = extract_session_attributes(session_payload)
    people_df = build_session_people_df(session_payload)
    if internal is None:
        internal = is_internal_session(people_df)
    has_chat = isinstance(chat_df, pd.DataFrame)

    registrants_count = int(attributes.get("registrants_count") or 0)
    attendees_count = int(attributes.get("attendees_count") or 0)

    stats: Dict[str, Any] = {
        "status": attributes.get("status"),
        "timezone": attributes.get("timezone"),
        "duration_seconds": attributes.get("duration"),
        "duration_label": _format_duration_label(attributes.get("duration")),
        "registrants_count": registrants_count,
        "attendees_count": attendees_count,
        "attendance_rate_pct": round((attendees_count / registrants_count) * 100, 1) if registrants_count else None,
        "started_at_utc": _format_unix_timestamp(attributes.get("started_at")),
        "ended_at_utc": _format_unix_timestamp(attributes.get("ended_at")),
    }

    if not people_df.empty:
        attended_mask = people_df["attended"].fillna(False).astype(bool)
        stats["people_count"] = int(len(people_df.index))
        stats["attended_people_count"] = int(attended_mask.sum())
        stats["replay_viewers_count"] = int(people_df["has_viewed_replay"].fillna(False).astype(bool).sum())
        if not has_chat:
            # No chat cached: the people's own counts, split by audience member.
            is_audience = audience_people_mask(people_df, internal=internal)
            stats["total_messages_count"] = int(people_df.loc[is_audience, "messages_count"].sum())
            stats["team_messages_count"] = int(people_df.loc[~is_audience, "messages_count"].sum())
        stats["total_questions_count"] = int(people_df["questions_count"].sum())
        stats["total_up_votes_count"] = int(people_df["up_votes_count"].sum())
        stats["unique_countries_count"] = int(people_df["ip_country_name"].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique())

    if has_chat:
        # Chat messages are the audience's, as on the Chat page; the team's
        # and guest speakers' messages are counted apart (none on an
        # internal session, where everyone counts).
        is_audience_message = audience_chat_mask(chat_df, internal=internal)
        stats["total_messages_count"] = int(is_audience_message.sum())
        stats["team_messages_count"] = int((~is_audience_message).sum())

    return stats


def build_compact_session_payload_for_llm(
    session_payload: Dict[str, Any],
    max_people: int = 40,
    *,
    internal: Optional[bool] = None,
) -> Dict[str, Any]:
    """`internal`: as in `build_session_stats`."""
    attributes = extract_session_attributes(session_payload)
    compact_payload: Dict[str, Any] = {
        "session": {
            "session_id": str(extract_session_resource(session_payload).get("id") or "").strip(),
            "event_id": attributes.get("event_id"),
            "status": attributes.get("status"),
            "timezone": attributes.get("timezone"),
            "registrants_count": attributes.get("registrants_count"),
            "attendees_count": attributes.get("attendees_count"),
            "estimated_started_at": attributes.get("estimated_started_at"),
            "started_at": attributes.get("started_at"),
            "ended_at": attributes.get("ended_at"),
            "duration": attributes.get("duration"),
        },
    }

    people_df = build_session_people_df(session_payload)
    if internal is None:
        internal = is_internal_session(people_df)
    if not people_df.empty:
        attended_df = people_df.loc[people_df["attended"].fillna(False).astype(bool)].copy()
        compact_payload["participant_summary"] = {
            "countries": (
                people_df["ip_country_code"]
                .fillna("")
                .astype(str)
                .str.strip()
                .replace("", pd.NA)
                .dropna()
                .value_counts()
                .head(12)
                .to_dict()
            ),
            "roles": (
                people_df["role"]
                .fillna("unknown")
                .astype(str)
                .value_counts()
                .to_dict()
            ),
            "avg_attendance_rate": (
                round(float(attended_df["attendance_rate"].dropna().mean()), 2)
                if not attended_df.empty and attended_df["attendance_rate"].dropna().any()
                else None
            ),
            "avg_attendance_duration": (
                round(float(attended_df["attendance_duration"].dropna().mean()), 2)
                if not attended_df.empty and attended_df["attendance_duration"].dropna().any()
                else None
            ),
            "replay_viewers": int(people_df["has_viewed_replay"].fillna(False).astype(bool).sum()),
            # Audience members only, so the host is never read as the most
            # engaged attendee (everyone, on an internal session).
            "top_chatters": people_df.loc[audience_people_mask(people_df, internal=internal)].sort_values(
                by=["messages_count", "questions_count", "up_votes_count", "attendance_duration"],
                ascending=[False, False, False, False],
            )
            .head(max_people)
            .loc[:, [
                column for column in [
                    "full_name",
                    "role",
                    "attended",
                    "attendance_duration",
                    "attendance_rate",
                    "has_viewed_replay",
                    "messages_count",
                    "questions_count",
                    "up_votes_count",
                    "ip_country_code",
                    "browser_name",
                    "os_name",
                    "job_title",
                    "company",
                ] if column in people_df.columns
            ]]
            .to_dict("records"),
        }
    return compact_payload


def build_session_overview_data(
    session_payload: Dict[str, Any],
    *,
    chat_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Also returns `internal_session`, the session's `is_internal_session`
    decision, made once here so the workspace's other charts reuse it.
    `chat_df`: as in `build_session_stats`."""
    attributes = extract_session_attributes(session_payload)
    people_df = build_session_people_df(session_payload)
    internal = is_internal_session(people_df)
    stats = build_session_stats(session_payload, internal=internal, chat_df=chat_df)

    overview_rows = [
        {"label": "Status", "value": str(attributes.get("status") or "n/a")},
        {"label": "Timezone", "value": str(attributes.get("timezone") or "n/a")},
        {"label": "Session Name", "value": str(attributes.get("name") or "Untitled session")},
        {"label": "Started At", "value": _format_unix_timestamp(attributes.get("started_at")) or "n/a"},
        {"label": "Ended At", "value": _format_unix_timestamp(attributes.get("ended_at")) or "n/a"},
        {"label": "Duration", "value": _format_duration_label(attributes.get("duration")) or "n/a"},
        {"label": "Registrants", "value": str(attributes.get("registrants_count") or 0)},
        {"label": "Attendees", "value": str(attributes.get("attendees_count") or 0)},
        {
            "label": "Attendance Rate",
            "value": f"{stats['attendance_rate_pct']}%" if stats.get("attendance_rate_pct") is not None else "n/a",
        },
    ]
    overview_df = pd.DataFrame(overview_rows)

    if people_df.empty:
        empty_df = pd.DataFrame()
        return {
            "stats": stats,
            "overview_df": overview_df,
            "people_df": empty_df,
            "country_df": empty_df,
            "role_df": empty_df,
            "attendance_distribution_df": empty_df,
            "engagement_top_df": empty_df,
            "internal_session": internal,
        }

    # "Attendance by country" counts attendees; `people_count` (registrants)
    # is kept alongside for context.
    country_df = (
        people_df.assign(ip_country_name=people_df["ip_country_name"].fillna("").astype(str).str.strip())
        .loc[lambda df: df["ip_country_name"].ne("")]
        .groupby("ip_country_name", as_index=False)
        .agg(
            people_count=("person_id", "size"),
            attendees=("attended", lambda series: int(series.fillna(False).astype(bool).sum())),
        )
        .loc[lambda df: df["attendees"] > 0]
        .sort_values(by=["attendees", "people_count", "ip_country_name"], ascending=[False, False, True])
        .head(12)
    )

    role_df = (
        people_df.assign(role=people_df["role"].fillna("unknown").astype(str))
        .groupby("role", as_index=False)
        .agg(people_count=("person_id", "size"))
        .sort_values(by=["people_count", "role"], ascending=[False, True])
    )

    attendance_distribution_df = people_df.copy()
    attendance_distribution_df["attendance_band"] = pd.cut(
        attendance_distribution_df["attendance_rate"],
        bins=[-1, 0, 25, 50, 75, 100],
        labels=["0%", "1-25%", "26-50%", "51-75%", "76-100%"],
    )
    attendance_distribution_df = (
        attendance_distribution_df.dropna(subset=["attendance_band"])
        .groupby("attendance_band", as_index=False, observed=False)
        .agg(people_count=("person_id", "size"))
    )

    # Most engaged people are audience members: the host team and guest
    # speakers are left out, except on an internal session.
    engagement_top_df = people_df.loc[audience_people_mask(people_df, internal=internal)].sort_values(
        by=["engagement_score", "messages_count", "questions_count", "attendance_duration"],
        ascending=[False, False, False, False],
    ).head(12)

    return {
        "stats": stats,
        "overview_df": overview_df,
        "people_df": people_df,
        "country_df": country_df,
        "role_df": role_df,
        "attendance_distribution_df": attendance_distribution_df,
        "engagement_top_df": engagement_top_df,
        "internal_session": internal,
    }
