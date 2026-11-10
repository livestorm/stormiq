"""Seed a LOCAL database with synthetic sessions for performance benchmarks.

Creates, in organization `bench-org`:
    bench-short     10-minute session, 60 attendees, full outputs
    bench-long      2-hour session, 400 attendees, full outputs, and its
                    people stored as a synced roster (the v1 record reads it)
    bench-pending   2-hour session whose transcript job is still running
    bench-card-NN   40 one-hour sessions for the card list (half with a cover)
plus an OAuth connection `bench-conn` for that org (expires in a year).

All content is generated (fixed random seed): no customer data is involved.
Payload shapes follow what the app stores: a raw Gladia v2 result run
through the transcriber's own normalisation, and Livestorm JSON:API
payloads for the session, its event, chat and questions. Like a public
webinar's, the people come from many UTM sources, referrers and
registration types, registered days or hours ahead (a few after the
start), and answered select fields, one with 200 options; the event
carries those registration fields. So the v1 record's channel, segment
and lead-time measures do their full work on these sessions.

Usage (refuses any non-local database):
    DATABASE_URL=postgresql://stormiq:stormiq@localhost:5433/stormiq \\
        python scripts/seed_benchmark_sessions.py [--reset]

`--reset` deletes previously seeded rows (session ids starting with
`bench-`, the `bench-conn` connection and their transcript jobs) first.
See scripts/benchmark_routes.py for the measurements.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from psycopg.types.json import Jsonb  # noqa: E402

from livestorm_app.db import (  # noqa: E402
    create_transcript_job,
    ensure_database_schema,
    get_database_url,
    get_db_connection,
    replace_session_roster,
    upsert_cached_session,
)
from livestorm_app.gladia.transcriber import _normalize_transcription  # noqa: E402

ORG = "bench-org"
CONNECTION_ID = "bench-conn"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}

WORDS = (
    "we the and to of a in that is for it on with as this you are our be at have from your can "
    "will by about more product team customers webinar session question audience data growth "
    "launch roadmap feature platform engagement pipeline revenue marketing sales strategy plan "
    "today next quarter really great point thanks everyone think going so just like know right "
    "integration analytics dashboard workflow onboarding feedback demo pricing renewal adoption"
).split()
ENTITIES = [
    ("ORGANIZATION", "Livestorm"), ("ORGANIZATION", "HubSpot"), ("LOCATION", "Paris"),
    ("PERSON", "Claire"), ("DATE", "Q3"), ("PERCENT", "25%"), ("MONEY", "$40,000"),
]
COUNTRIES = [("FR", "France"), ("US", "United States"), ("DE", "Germany"), ("GB", "United Kingdom"), ("ES", "Spain")]

# How people registered, and what they answered (all made up). Choices are
# skewed (`_skewed`), so a few channels and answers are large and the long
# tail folds into Other.
UTM_SOURCES = tuple(f"source-{i:02d}" for i in range(1, 41))
UTM_MEDIUMS = ("email", "social", "cpc", "partner")
REFERRER_HOSTS = tuple(f"https://www.site-{i:02d}.example/page" for i in range(1, 16))
TEAM_SIZES = ("1-10", "11-50", "51-200", "201-1000", "1000+")
INDUSTRIES = tuple(f"industry-{i:03d}" for i in range(1, 201))  # a 200-option select
TOPICS = tuple(f"topic-{i:02d}" for i in range(1, 31))
# Days registered ahead of the start; negative is after the start.
LEAD_DAYS = (0.1, 0.5, 2, 3, 5, 7, 10, 14, 20, 40, -0.01)
EVENT_FIELDS = [
    {"id": "email", "type": "text", "order": 0, "required": True},
    {"id": "first_name", "type": "text", "order": 1, "required": True},
    {"id": "last_name", "type": "text", "order": 2, "required": True},
    {"id": "company", "type": "text", "order": 3, "required": False},
    {"id": "job_title", "type": "text", "order": 4, "required": False},
    {"id": "team_size", "type": "unique_select", "order": 5, "required": False},
    {"id": "industry", "type": "unique_select", "order": 6, "required": False},
    {"id": "topics", "type": "multiple_select", "order": 7, "required": False},
]


def _require_local_database() -> None:
    host = urlparse(get_database_url()).hostname or ""
    if host not in LOCAL_HOSTS:
        sys.exit(f"Refusing to seed: DATABASE_URL points at {host!r}, not a local database.")


# ── Transcript ──────────────────────────────────────────────────────────────


def _srt_time(seconds: float, comma: bool) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{',' if comma else '.'}{ms:03d}"


def build_transcript(rng: random.Random, session_id: str, duration_s: int) -> Dict[str, Any]:
    utterances: List[Dict[str, Any]] = []
    sentences: List[Dict[str, Any]] = []
    entities: List[Dict[str, Any]] = []
    t = 1.0
    speakers = [0] * 7 + [1] * 2 + [2]
    while t < duration_s - 5:
        speaker = rng.choice(speakers)
        n_words = rng.randint(6, 45)
        words = []
        for _ in range(n_words):
            if rng.random() < 0.02:
                entity_type, token = rng.choice(ENTITIES)
                entities.append({"entity_type": entity_type, "text": token, "start": round(t, 2), "end": round(t + 0.4, 2)})
            else:
                token = rng.choice(WORDS)
            length = rng.uniform(0.18, 0.55)
            words.append({"word": f" {token}", "start": round(t, 3), "end": round(t + length, 3), "confidence": round(rng.uniform(0.8, 1.0), 3)})
            t += length + rng.uniform(0.02, 0.12)
        text = "".join(w["word"] for w in words).strip() + "."
        utterance = {
            "words": words, "text": text, "language": "en", "start": words[0]["start"], "end": words[-1]["end"],
            "confidence": round(rng.uniform(0.85, 0.99), 3), "channel": 0, "speaker": speaker,
        }
        utterances.append(utterance)
        for i in range(0, len(words), 12):
            chunk = words[i:i + 12]
            sentences.append({
                "sentence": "".join(w["word"] for w in chunk).strip(), "start": chunk[0]["start"], "end": chunk[-1]["end"],
                "words": chunk, "speaker": speaker, "confidence": utterance["confidence"], "language": "en", "channel": 0,
            })
        t += rng.choice([0.2, 0.4, 0.8, 1.3, 2.5])

    srt = "\n\n".join(
        f"{i}\n{_srt_time(u['start'], True)} --> {_srt_time(u['end'], True)}\n{u['text']}" for i, u in enumerate(utterances, 1)
    )
    vtt = "WEBVTT\n\n" + "\n\n".join(f"{_srt_time(u['start'], False)} --> {_srt_time(u['end'], False)}\n{u['text']}" for u in utterances)
    raw = {
        "id": str(uuid.UUID(int=rng.getrandbits(128))), "request_id": f"G-{rng.getrandbits(32):08x}", "kind": "pre-recorded",
        "status": "done", "created_at": "2026-10-01T10:00:00Z", "completed_at": "2026-10-01T10:20:00Z",
        "file": {"id": "f", "filename": f"{session_id}.mp3", "source": None, "audio_duration": duration_s, "number_of_channels": 1},
        "request_params": {"model": "solaria-1", "diarization": True, "named_entity_recognition": True, "sentences": True},
        "result": {
            "metadata": {"audio_duration": duration_s, "number_of_distinct_channels": 1, "billing_time": duration_s, "transcription_time": 300},
            "transcription": {
                "languages": ["en"],
                "full_transcript": " ".join(u["text"] for u in utterances),
                "utterances": utterances,
                "subtitles": [{"format": "srt", "subtitles": srt}, {"format": "vtt", "subtitles": vtt}],
            },
            "sentences": {"success": True, "is_empty": False, "results": sentences},
            "named_entity_recognition": {"success": True, "is_empty": False, "results": entities},
        },
    }
    return _normalize_transcription(
        raw, source_video=Path(f"/tmp/{session_id}.mp4"), extracted_audio=None,
        requested_model="gladia-v2-pre-recorded", actual_model="gladia-v2-pre-recorded", session_id=session_id,
    )


# ── Livestorm payloads ──────────────────────────────────────────────────────


def _skewed(rng: random.Random, options: tuple) -> Any:
    """One option, the k-th about 1/k as likely as the first."""
    return rng.choices(options, weights=[1 / k for k in range(1, len(options) + 1)])[0]


def _channel(rng: random.Random) -> Dict[str, Any]:
    """How a person registered: a UTM, a referrer, a registration type, or nothing."""
    draw = rng.random()
    if draw < 0.5:
        return {"utm_source": _skewed(rng, UTM_SOURCES), "utm_medium": rng.choice(UTM_MEDIUMS), "referrer": None, "registration_type": None}
    if draw < 0.7:
        return {"utm_source": None, "utm_medium": None, "referrer": _skewed(rng, REFERRER_HOSTS), "registration_type": None}
    if draw < 0.8:
        return {"utm_source": None, "utm_medium": None, "referrer": None, "registration_type": "live"}
    return {"utm_source": None, "utm_medium": None, "referrer": None, "registration_type": None}


def _select_answers(rng: random.Random) -> List[Dict[str, Any]]:
    """Answers to the select fields; about one person in ten skips each."""
    answers = []
    if rng.random() < 0.9:
        answers.append({"id": "team_size", "type": "unique_select", "value": _skewed(rng, TEAM_SIZES), "required": False})
    if rng.random() < 0.9:
        answers.append({"id": "industry", "type": "unique_select", "value": _skewed(rng, INDUSTRIES), "required": False})
    if rng.random() < 0.9:
        topics = sorted({_skewed(rng, TOPICS) for _ in range(rng.randint(1, 4))})
        answers.append({"id": "topics", "type": "multiple_select", "value": topics, "required": False})
    return answers


def build_people(rng: random.Random, attendees: int, started_at: int = 1759312800) -> List[Dict[str, Any]]:
    people = []
    for i in range(attendees):
        code, country = rng.choice(COUNTRIES)
        attended = rng.random() < 0.7
        registered_at = started_at - int(_skewed(rng, LEAD_DAYS) * 86400)
        people.append({
            "id": f"p{i}", "type": "people",
            "attributes": {
                "role": "team_member" if i < 3 else "participant",
                "first_name": f"First{i}", "last_name": f"Last{i}", "email": f"person{i}@example.com",
                "created_at": 1759312800, "updated_at": 1759312800, "timezone": "Europe/Paris",
                "messages_count": rng.randint(0, 6), "questions_count": rng.randint(0, 2),
                "votes_count": rng.randint(0, 3), "up_votes_count": rng.randint(0, 3),
                "registrant_detail": {
                    "event_id": "evt", "session_id": "s", "attended": attended,
                    "attendance_rate": rng.randint(10, 100) if attended else 0,
                    "attendance_duration": rng.randint(300, 7000) if attended else 0,
                    "has_viewed_replay": rng.random() < 0.2,
                    "is_guest_speaker": i < 2, "session_role": "participant", "browser_name": "Chrome",
                    "os_name": "Mac OS", "ip_city": "Paris", "ip_country_code": code, "ip_country_name": country,
                    "created_at": registered_at, **_channel(rng),
                    "fields": [
                        {"id": "email", "type": "text", "value": f"person{i}@example.com", "required": True},
                        {"id": "first_name", "type": "text", "value": f"First{i}", "required": True},
                        {"id": "last_name", "type": "text", "value": f"Last{i}", "required": True},
                        {"id": "company", "type": "text", "value": f"Company {i % 37}", "required": False},
                        {"id": "job_title", "type": "text", "value": "Manager", "required": False},
                        *_select_answers(rng),
                    ],
                },
            },
        })
    return people


def build_session_payload(session_id: str, event_id: str, duration_s: int, people: List[Dict[str, Any]], started_at: int) -> Dict[str, Any]:
    return {
        "data": {
            "id": session_id, "type": "sessions",
            "attributes": {
                "event_id": event_id, "status": "past", "timezone": "Europe/Paris", "room_link": "https://example.com/room",
                "attendees_count": sum(1 for p in people if p["attributes"]["registrant_detail"]["attended"]),
                "duration": duration_s, "estimated_started_at": started_at, "started_at": started_at,
                "ended_at": started_at + duration_s, "canceled_at": None, "created_at": started_at - 86400,
                "updated_at": started_at + duration_s, "registrants_count": len(people), "name": None,
            },
        },
        "included": people,
    }


def build_chat(rng: random.Random, n: int, people: List[Dict[str, Any]], started_at: int, duration_s: int) -> Dict[str, Any]:
    """Chat messages in the shape the fetcher stores (`client.fetch_chat_messages`).

    As in real payloads, a message's author is its people link
    (`relationships.people.data`), and its `author_id` is another id: here a
    random one per writer, drawn again in each session, unrelated to the
    people ids. The included people are not stored with the chat.
    """
    writer_ids = {p["id"]: str(uuid.UUID(int=rng.getrandbits(128), version=4)) for p in people}
    data = []
    for i in range(n):
        writer = rng.choice(people)["id"]
        data.append({
            "id": f"m{i}", "type": "chat_messages",
            "attributes": {
                "text_content": " ".join(rng.choice(WORDS) for _ in range(rng.randint(3, 25))),
                "html_content": "<p>…</p>", "created_at": started_at + rng.randint(0, duration_s),
                "from_guest_speaker": False, "from_team_member": False,
                "author_id": writer_ids[writer], "session_id": "s", "event_id": "evt",
            },
            "relationships": {"people": {"data": {"type": "people", "id": writer}}},
        })
    return {"data": data, "meta": {"record_count": n}, "pages_fetched": 1, "requested_page_size": 50}


def build_questions(rng: random.Random, n: int, people: List[Dict[str, Any]], started_at: int, duration_s: int) -> Dict[str, Any]:
    data = []
    for i in range(n):
        asked = started_at + rng.randint(0, duration_s)
        answered = rng.random() < 0.6
        data.append({
            "id": f"q{i}", "type": "questions",
            "attributes": {
                "question": " ".join(rng.choice(WORDS) for _ in range(rng.randint(6, 20))) + "?",
                "response": "Thanks, " + " ".join(rng.choice(WORDS) for _ in range(12)) if answered else None,
                "created_at": asked, "updated_at": asked, "responded_at": asked + 120 if answered else None,
                "question_author_id": rng.choice(people)["id"],
                "response_author_id": people[0]["id"] if answered else None,
                "response_orally": False, "session_id": "s", "event_id": "evt",
            },
        })
    return {"data": data, "included": people[:3], "pages_fetched": 1, "requested_page_size": 100}


def build_outputs(rng: random.Random) -> Dict[str, Any]:
    def markdown(sections: int, words: int) -> str:
        return "\n\n".join(
            f"## Section {s}\n" + " ".join(rng.choice(WORDS) for _ in range(words)) for s in range(1, sections + 1)
        )

    content = {key: markdown(3, 250) for key in ("summary", "blog", "email", "social_media")}
    return {
        "analysis_bundle": {"English": markdown(5, 150), "French": markdown(5, 150)},
        "deep_analysis_bundle": {"English": markdown(10, 200), "French": markdown(10, 200)},
        "smart_recap_bundle": {"professional": markdown(2, 120), "hype": markdown(2, 120)},
        "content_repurpose_bundle": {"English": content, "French": content},
    }


# ── Seeding ─────────────────────────────────────────────────────────────────


def seed_roster(session_id: str, people: List[Dict[str, Any]], event_payload: Dict[str, Any]) -> None:
    """Store `people` as the session's roster, as a completed people sync
    leaves it (`people_sync.run_people_sync`): one row per person from
    `parse_person` with its `P-` ref, the event's field definitions, and a
    complete paginated header. The v1 record then reads these rows."""
    from livestorm_app.livestorm.payloads import parse_person
    from livestorm_app.record.roster import assign_person_refs, event_fields, resolve_field_definitions

    rows = [row for row in (parse_person(record) for record in people) if row]
    refs = assign_person_refs(session_id, [row["person_id"] for row in rows])
    definitions, fields_source = resolve_field_definitions(event_fields(event_payload))
    replace_session_roster(
        session_id,
        [{**row, "person_ref": refs[row["person_id"]]} for row in rows],
        source="paginated",
        complete=True,
        record_count=len(rows),
        reason="initial",
        fields=definitions,
        fields_source=fields_source,
        refresh_due_at=None,
    )


def seed_session(rng: random.Random, session_id: str, *, duration_s: int, attendees: int, chat: int, questions: int,
                 with_transcript: bool = True, with_outputs: bool = True, cover: bool = False,
                 stored_roster: bool = False, started_at: int) -> None:
    event_id = f"evt-{session_id}"
    people = build_people(rng, attendees, started_at)
    event_attributes = {"title": f"Benchmark webinar {session_id}", "fields": EVENT_FIELDS}
    fields: Dict[str, Any] = {
        "session_payload": build_session_payload(session_id, event_id, duration_s, people, started_at),
        "event_payload": {"data": {"id": event_id, "type": "events", "attributes": event_attributes}},
        "chat_payload": build_chat(rng, chat, people, started_at, duration_s),
        "questions_payload": build_questions(rng, questions, people, started_at, duration_s),
        "organization_id": ORG,
        "created_by_user_id": "bench-user", "created_by_email": "bench@example.com", "created_by_name": "Bench User",
    }
    if with_transcript:
        fields["transcript_payload"] = build_transcript(rng, session_id, duration_s)
    if with_outputs:
        outputs = build_outputs(rng)
        fields.update(outputs)
        fields["analysis_md"] = outputs["analysis_bundle"]["English"]
        fields["deep_analysis_md"] = outputs["deep_analysis_bundle"]["English"]
    if cover:
        fields["cover_image_bytes"] = rng.randbytes(1_500_000)
        fields["cover_image_mime"] = "image/png"
        fields["cover_image_generated_at"] = datetime.now(timezone.utc)
    upsert_cached_session("bench-credential", session_id, **fields)
    if stored_roster:
        seed_roster(session_id, people, fields["event_payload"])


def seed_connection() -> None:
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO oauth_connections (connection_id, provider, user_id, email, organization_id, access_token,
                                           token_type, expires_at, profile)
            VALUES (%s, 'livestorm', 'bench-user', 'bench@example.com', %s, 'bench-token', 'Bearer', %s, %s)
            ON CONFLICT (connection_id) DO UPDATE SET organization_id = EXCLUDED.organization_id, expires_at = EXCLUDED.expires_at
            """,
            (CONNECTION_ID, ORG, datetime.now(timezone.utc) + timedelta(days=365), Jsonb({"full_name": "Bench User"})),
        )
        conn.commit()


def reset() -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM transcript_jobs WHERE session_id LIKE 'bench-%'")
        conn.execute("DELETE FROM session_cache WHERE session_id LIKE 'bench-%'")
        conn.execute("DELETE FROM oauth_connections WHERE connection_id = %s", (CONNECTION_ID,))
        conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete previously seeded rows first")
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        sys.exit("Set DATABASE_URL explicitly (a local database); .env is not read by this script.")
    _require_local_database()
    ensure_database_schema()
    if args.reset:
        reset()

    rng = random.Random(20261104)
    base = int(datetime(2026, 10, 1, 9, tzinfo=timezone.utc).timestamp())
    seed_connection()
    seed_session(rng, "bench-short", duration_s=600, attendees=60, chat=40, questions=10, started_at=base)
    seed_session(rng, "bench-long", duration_s=7200, attendees=400, chat=600, questions=80, cover=True,
                 stored_roster=True, started_at=base)
    seed_session(rng, "bench-pending", duration_s=7200, attendees=400, chat=600, questions=80,
                 with_transcript=False, with_outputs=False, started_at=base)
    create_transcript_job("bench-pending")
    for i in range(40):
        seed_session(rng, f"bench-card-{i:02d}", duration_s=3600, attendees=150, chat=150, questions=25,
                     cover=i % 2 == 0, started_at=base - i * 86400)
    print("Seeded bench-short, bench-long, bench-pending and 40 card sessions in organization", ORG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
