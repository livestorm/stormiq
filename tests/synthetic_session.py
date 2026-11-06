"""A synthetic public webinar, shaped like a `session_cache` row.

Every name, email, company, id and message below is made up. The row is
what `db.fetch_cached_session` returns for a 30-minute session that started
95 seconds late: a team host (organizer) and co-host, a guest speaker,
seven audience members with UTMs, a referrer and a registration type, chat
in the waiting room, live and after the end (each message linked to its
author's people record), questions answered in writing, orally or not at
all, a transcript of the whole session (with speaker labels, a stretch of
unclear words and a phantom speaker) and a few AI outputs. The recording
started about 20 seconds after `started_at`: the host announces the slides
link (posted in the chat), calls the room to the chat (three people answer)
and a guest reads a question aloud, so the alignment probe verifies an
offset of about +19 s.

Three committed files come from it:

- `frontend/tests/fixtures/record-v1.json`: the v1 record route's response
  for this row, built by the real code, and `record-v1-crowd.json`, its
  response for a larger made-up webinar (`crowd_row`), whose grouped
  measures pass their gates. `tests/test_record_route.py` fails when
  either drifts. Regenerate both after a deliberate change to the record:

      .venv/bin/python tests/synthetic_session.py

- `tests/fixtures/classic-workspace.json`: a frozen copy of this row and the
  classic workspace it serializes to. It keeps its own copy of the row, so
  changing this module doesn't touch it. Regenerate it only when a change
  to the classic workspace is intended, and say so in the PR:

      .venv/bin/python tests/synthetic_session.py --classic
"""

from __future__ import annotations

import copy
import json
import sys
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SESSION_ID = "synthetic-webinar"
ORGANIZATION_ID = "synthetic-org"
EVENT_ID = "synthetic-event"
T0 = int(datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).timestamp())
LATE_START_S = 95
DURATION_S = 1800
DAY = 86400
UPDATED_AT = datetime(2026, 10, 2, 8, 30, tzinfo=timezone.utc)
CREATED_AT = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)

RECORD_FIXTURE = REPO_ROOT / "frontend" / "tests" / "fixtures" / "record-v1.json"
RECORD_CROWD_FIXTURE = REPO_ROOT / "frontend" / "tests" / "fixtures" / "record-v1-crowd.json"
CLASSIC_SNAPSHOT = REPO_ROOT / "tests" / "fixtures" / "classic-workspace.json"

# Values a v1 record response must never carry: names, emails and their
# domains, companies, job titles, Livestorm ids, chat and question text,
# and the join-link keys an old row may still hold.
PRIVATE_VALUES = (
    "Nora", "Vale", "Ilan", "Moss", "Rhea", "Stone", "Theo", "Park", "Mila", "Ford",
    "Sam", "Reed", "Lena", "Hart", "Omar", "Quinn", "Jade", "Bell", "Ravi", "Cole",
    "@example.test", "example.test",
    "Example Co", "Sample Labs", "Head of Events", "Marketing lead",
    "p-host", "p-aud-", "u-aud-", "u-team-", "msg-", "qst-",
    "Welcome", "pricing", "slides", "replay link", "Will the", "Is there an API", "Lyon", "Berlin",
    "password_key", "connection_link", "synthetic-password-key", "synthetic-gladia-job",
    # Transcript text: words, never sent.
    "joining from", "larger teams", "walk through", "share my screen",
)


def _person(
    person_id: str,
    first: str,
    last: str,
    *,
    role: str = "participant",
    attended: bool = True,
    guest: bool = False,
    session_role: Optional[str] = None,
    registered_s_before: int = 7 * DAY,
    company: Optional[str] = None,
    job_title: Optional[str] = None,
    team_size: Optional[str] = None,
    messages: int = 0,
    questions: int = 0,
    up_votes: int = 0,
    attendance_rate: int = 0,
    country: tuple = ("FR", "France"),
    **detail: Any,
) -> Dict[str, Any]:
    email = f"{first}.{last}@example.test".lower()
    fields: List[Dict[str, Any]] = [
        {"id": "email", "type": "text", "value": email, "required": True},
        {"id": "first_name", "type": "text", "value": first, "required": True},
        {"id": "last_name", "type": "text", "value": last, "required": True},
    ]
    if company:
        fields.append({"id": "company", "type": "text", "value": company, "required": False})
    if job_title:
        fields.append({"id": "job_title", "type": "text", "value": job_title, "required": False})
    if team_size:
        fields.append({"id": "team_size", "type": "unique_select", "value": team_size, "required": False})
    registrant_detail = {
        "event_id": EVENT_ID,
        "session_id": SESSION_ID,
        "created_at": T0 - registered_s_before,
        "updated_at": T0 - registered_s_before,
        "fields": fields,
        "referrer": None,
        "utm_source": None,
        "utm_medium": None,
        "utm_campaign": None,
        "utm_term": None,
        "utm_content": None,
        "browser_name": "Firefox",
        "os_name": "Linux",
        "ip_city": "Exampleville",
        "ip_country_code": country[0],
        "ip_country_name": country[1],
        "attended": attended,
        "attendance_rate": attendance_rate if attended else 0,
        "attendance_duration": round(DURATION_S * attendance_rate / 100) if attended else 0,
        "has_viewed_replay": False,
        "is_highlighted": False,
        "is_guest_speaker": guest,
        "registration_type": None,
        "session_role": session_role,
    }
    registrant_detail.update(detail)
    return {
        "id": person_id,
        "type": "people",
        "attributes": {
            "created_at": T0 - registered_s_before,
            "updated_at": T0 + DURATION_S,
            "timezone": "Europe/Paris",
            "first_name": first,
            "last_name": last,
            "email": email,
            "messages_count": messages,
            "questions_count": questions,
            "votes_count": 0,
            "up_votes_count": up_votes,
            "role": role,
            "avatar_link": None,
            "registrant_detail": registrant_detail,
        },
    }


def people() -> List[Dict[str, Any]]:
    """Ten people: two team members (one organizer), a guest speaker, five
    audience members who attended and two who didn't (one watched the replay)."""
    return [
        _person("p-host", "Nora", "Vale", role="team_member", session_role="organizer", registered_s_before=30 * DAY,
                company="Example Co", job_title="Head of Events", messages=2, attendance_rate=100,
                password_key="synthetic-password-key",
                connection_link="https://app.livestorm.test/p/x/live?key=synthetic-password-key"),
        _person("p-cohost", "Ilan", "Moss", role="team_member", registered_s_before=30 * DAY, company="Example Co",
                messages=1, attendance_rate=98),
        _person("p-guest", "Rhea", "Stone", guest=True, registered_s_before=10 * DAY, job_title="Analyst",
                messages=1, attendance_rate=95),
        _person("p-aud-1", "Theo", "Park", registered_s_before=12 * DAY, company="Sample Labs", job_title="Marketing lead",
                team_size="11-50", messages=3, questions=1, up_votes=2, attendance_rate=90,
                utm_source="Newsletter", utm_medium="Email", utm_campaign="oct-launch",
                password_key="synthetic-password-key",
                connection_link="https://app.livestorm.test/p/x/live?key=synthetic-password-key"),
        _person("p-aud-2", "Mila", "Ford", registered_s_before=2 * DAY, team_size="1-10", messages=3, questions=1,
                attendance_rate=80, referrer="https://www.linkedin.example/feed/?trk=synthetic", country=("DE", "Germany")),
        _person("p-aud-3", "Sam", "Reed", registered_s_before=5 * 3600, messages=2, questions=1, attendance_rate=75,
                registration_type="detailed"),
        _person("p-aud-4", "Lena", "Hart", registered_s_before=6 * DAY, team_size="51-200", messages=2, attendance_rate=60,
                utm_source="partner", country=("US", "United States")),
        _person("p-aud-5", "Omar", "Quinn", registered_s_before=-120, questions=1, attendance_rate=40,
                company="Sample Labs"),
        _person("p-aud-6", "Jade", "Bell", attended=False, registered_s_before=20 * DAY, has_viewed_replay=True,
                country=("US", "United States")),
        _person("p-aud-7", "Ravi", "Cole", attended=False, registered_s_before=1 * DAY),
    ]


def session_payload(included: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    included = people() if included is None else included
    attended = [p for p in included if p["attributes"]["registrant_detail"]["attended"]]
    return {
        "data": {
            "id": SESSION_ID,
            "type": "sessions",
            "attributes": {
                "event_id": EVENT_ID,
                "status": "past",
                "timezone": "Europe/Paris",
                "name": "Synthetic webinar",
                "attendees_count": len(attended),
                "registrants_count": len(included),
                "duration": DURATION_S,
                "estimated_started_at": T0 - LATE_START_S,
                "started_at": T0,
                "ended_at": T0 + DURATION_S,
                "created_at": T0 - 30 * DAY,
                "updated_at": T0 + DURATION_S,
            },
        },
        "included": included,
    }


def event_payload() -> Dict[str, Any]:
    return {
        "data": {
            "id": EVENT_ID,
            "type": "events",
            "attributes": {
                "title": "Synthetic Product Webinar",
                "language": "en",
                "fields": [
                    {"id": "email", "type": "text", "order": 0, "required": True},
                    {"id": "first_name", "type": "text", "order": 1, "required": True},
                    {"id": "last_name", "type": "text", "order": 2, "required": True},
                    {"id": "company", "type": "text", "order": 3, "required": False},
                    {"id": "job_title", "type": "text", "order": 4, "required": False},
                    {"id": "team_size", "type": "unique_select", "order": 5, "required": False},
                ],
            },
        },
    }


# (offset from started_at in seconds, author id, flag, text). Flag is "team",
# "guest" or None (audience). The author id is the message's `author_id`
# attribute, a Livestorm id that is not a people id; `CHAT_PEOPLE` gives each
# author's people link, as a chat fetched with `include=people` carries it.
CHAT = (
    (-300, "u-aud-1", None, "Hello everyone"),
    (-120, "u-aud-2", None, "Excited for this one"),
    (-60, "u-team-1", "team", "Welcome! We start in a minute."),
    (-30, "u-aud-3", None, "Can you hear us?"),
    (45, "u-team-1", "team", "Slides: https://example.test/slides"),
    (62, "u-aud-2", None, "Hi from Lyon"),
    (66, "u-aud-4", None, "Hello from Berlin"),
    (70, "u-aud-1", None, "Thanks!"),
    (130, "u-aud-2", None, "Great intro"),
    (200, "u-aud-3", None, "What about pricing?"),
    (260, "u-guest-1", "guest", "Happy to cover pricing later"),
    (600, "u-aud-4", None, "This dashboard looks useful"),
    (615, "u-aud-1", None, "+1"),
    (900, "u-aud-2", None, "Is there a replay?"),
    (905, "u-team-2", "team", "Yes, the replay link comes by email"),
    (1200, "u-aud-1", None, "Love the export feature"),
    (1500, "u-aud-3", None, "Thank you all"),
    (DURATION_S, "u-aud-2", None, "Bye!"),
    (DURATION_S + 40, "u-aud-4", None, "See you next time"),
)


CHAT_PEOPLE = {
    "u-aud-1": "p-aud-1", "u-aud-2": "p-aud-2", "u-aud-3": "p-aud-3", "u-aud-4": "p-aud-4",
    "u-team-1": "p-host", "u-team-2": "p-cohost", "u-guest-1": "p-guest",
}


def chat_payload(*, people_links: bool = True) -> Dict[str, Any]:
    """The chat as the chat fetcher stores it: each message links to its
    author's people record. `people_links=False` gives a chat cached before
    StormIQ asked for the links."""
    data = []
    for index, (offset, author, flag, text) in enumerate(CHAT, start=1):
        markup = f"<p>{text}</p>"
        if "https://" in text:
            url = text[text.index("https://"):]
            markup = f'<p>{text[: text.index("https://")]}<a href="{url}">{url}</a></p>'
        data.append(
            {
                "id": f"msg-{index:03d}",
                "type": "chat_messages",
                "attributes": {
                    "text_content": text,
                    "html_content": markup,
                    "created_at": T0 + offset,
                    "author_id": author,
                    "from_team_member": flag == "team",
                    "from_guest_speaker": flag == "guest",
                    "session_id": SESSION_ID,
                    "event_id": EVENT_ID,
                },
            }
        )
        if people_links:
            data[-1]["relationships"] = {"people": {"data": {"type": "people", "id": CHAT_PEOPLE[author]}}}
    return {"data": data, "pages_fetched": 1, "requested_page_size": 50}


def questions_payload() -> Dict[str, Any]:
    """Four questions: one asked in the waiting room and answered in writing,
    one answered orally, one answered in writing, one left open."""
    items = (
        ("qst-001", -45, "p-aud-1", "Will the slides be shared?", 50, "Yes, see the chat.", False),
        ("qst-002", 300, "p-aud-2", "How does pricing work for larger teams with several hundred seats?", None, None, True),
        ("qst-003", 720, "p-aud-3", "Can we export to CSV?", 760, "Yes, from the Chat page.", False),
        ("qst-004", 1650, "p-aud-5", "Is there an API?", None, None, False),
    )
    data = []
    for question_id, asked, asker, question, answered, response, orally in items:
        data.append(
            {
                "id": question_id,
                "type": "questions",
                "attributes": {
                    "question": question,
                    "created_at": T0 + asked,
                    "updated_at": T0 + (answered if answered is not None else asked),
                    "responded_at": T0 + answered if answered is not None else None,
                    "response": response,
                    "response_orally": orally,
                    "question_author_id": asker,
                    "response_author_id": "p-host" if response else None,
                    "up_votes_count": 1 if question_id == "qst-002" else 0,
                    "session_id": SESSION_ID,
                    "event_id": EVENT_ID,
                },
            }
        )
    by_id = {person["id"]: person for person in people()}
    included = [copy.deepcopy(by_id[person_id]) for person_id in ("p-host", "p-aud-1", "p-aud-2", "p-aud-3", "p-aud-5")]
    return {"data": data, "included": included, "pages_fetched": 1, "requested_page_size": 50}


def _utterance(speaker: int, start: float, words: List[str], confidence: float = 0.92) -> Dict[str, Any]:
    timed = []
    t = start
    for word in words:
        timed.append({"word": f" {word}", "start": round(t, 2), "end": round(t + 0.35, 2), "confidence": confidence})
        t += 0.45
    text = " ".join(words)
    return {
        "start": timed[0]["start"], "end": timed[-1]["end"], "speaker": speaker, "channel": 0,
        "confidence": confidence, "language": "en", "text": text, "words": timed,
    }


# The scripted lines of the transcript: (speaker, start in recording seconds,
# words). The recording started about 20 s after `started_at`, so a line at
# recording second r is heard at session second r + 20: the host announces
# the slides link just before it is posted (45 s), calls the room to the chat
# before three greetings (62 to 70 s), and the guest reads the pricing
# question (asked at 300 s) about ten seconds after it was asked.
SCRIPT = (
    (0, 3.0, "welcome everyone thanks for joining us today".split()),
    (0, 12.0, "today we look at the new dashboard and the export".split()),
    (0, 20.0, "i just put the slides link in the chat".split()),
    (0, 30.0, "tell us in the chat where you are joining from".split()),
    (1, 60.0, "happy to be here let me share my screen".split()),
    (1, 288.0, "great question how does pricing work for larger teams with several hundred seats".split()),
    (1, 300.0, "pricing works per team and scales with seats".split()),
    (0, 760.0, "yes you can export everything to csv".split()),
    (0, 1700.0, "thank you all see you next time".split()),
)
FILLER = "we walk through each view step by step and show how it helps your team every week".split()
UNCLEAR_AT_S = 1204.0  # a stretch of words heard badly
PHANTOM_AT_S = 905.0  # a speaker heard for one second


def _utterances() -> List[Dict[str, Any]]:
    """The scripted lines, filler talk every 10 seconds between them (the host
    and the guest speaker in turns of 5 minutes), one stretch of
    low-confidence words and a phantom third speaker."""
    utterances = [_utterance(speaker, start, words) for speaker, start, words in SCRIPT]
    for start in range(24, 1690, 10):
        if any(abs(start - line[1]) < 10 for line in SCRIPT) or abs(start - PHANTOM_AT_S) < 10:
            continue
        speaker = 0 if (start // 300) % 2 == 0 else 1
        confidence = 0.45 if abs(start - UNCLEAR_AT_S) < 1 else 0.92
        utterances.append(_utterance(speaker, float(start), FILLER, confidence))
    utterances.append(_utterance(2, PHANTOM_AT_S, ["right", "exactly"], 0.7))
    return sorted(utterances, key=lambda u: u["start"])


def transcript_payload() -> Dict[str, Any]:
    """A Gladia transcript of the whole session, made and normalized as the
    worker stores it today (one chunk, the speaker cap's provenance), with
    the machine-dependent fields pinned."""
    from livestorm_app.gladia import transcriber

    utterances = _utterances()
    sentences = [
        {"sentence": u["text"], "start": u["start"], "end": u["end"], "speaker": u["speaker"], "channel": 0,
         "confidence": u["confidence"], "language": "en", "words": copy.deepcopy(u["words"])}
        for u in utterances
    ]
    gladia = {
        "id": "synthetic-gladia-job", "status": "done", "kind": "pre-recorded",
        "file": {"audio_duration": float(DURATION_S)},
        "request_params": {"model": "solaria-1", "diarization": True, "sentences": True},
        "result": {
            "metadata": {"audio_duration": float(DURATION_S), "number_of_distinct_channels": 1, "billing_time": float(DURATION_S)},
            "transcription": {
                "languages": ["en"],
                "full_transcript": " ".join(u["text"] for u in utterances),
                "utterances": utterances,
            },
            "sentences": {"success": True, "is_empty": False, "results": sentences},
            "named_entity_recognition": {"success": True, "is_empty": True, "results": []},
        },
    }
    request = {"diarization_config": {"min_speakers": 1, "max_speakers": 4}}
    chunk = {"gladia_result": gladia, "offset_seconds": 0.0, "gladia_request": request, "started_job": {"id": gladia["id"]}}
    provenance = transcriber._provenance([chunk], None)
    provenance["people_on_stage"] = 2
    normalized = transcriber._normalize_transcription(
        transcriber._merge_chunk_results([chunk]),
        source_video=Path("recording.mp4"),
        extracted_audio=None,
        requested_model=transcriber.DEFAULT_MODEL,
        actual_model=transcriber.DEFAULT_MODEL,
        session_id=SESSION_ID,
        provenance=provenance,
    )
    normalized["source_video"] = "recording.mp4"
    normalized["created_at"] = "2026-10-01T10:00:00+00:00"
    return normalized


def cached_row() -> Dict[str, Any]:
    """The session's `session_cache` row, as `db.fetch_cached_session` returns it."""
    return {
        "account_key_hash": "synthetic-hash",
        "session_id": SESSION_ID,
        "organization_id": ORGANIZATION_ID,
        "session_payload": session_payload(),
        "chat_payload": chat_payload(),
        "questions_payload": questions_payload(),
        "transcript_payload": transcript_payload(),
        "transcript_speaker_names": {"0": "Nora Vale", "1": "Rhea Stone"},
        "analysis_md": None,
        "analysis_bundle": {"English": "# Synthetic analysis\n\nThe audience asked about pricing and exports."},
        "deep_analysis_md": None,
        "deep_analysis_bundle": {},
        "content_repurpose_bundle": {
            "English": {"summary": "A summary.", "blog": "A blog post.", "email": "An email.", "social_media": "A post."},
        },
        "smart_recap_bundle": {"professional": "# Recap\n\nA synthetic recap."},
        "event_payload": event_payload(),
        "has_cover_image": False,
        "cover_image_mime": None,
        "cover_image_generated_at": None,
        "created_by_user_id": "synthetic-user",
        "created_by_email": "creator@example.test",
        "created_by_name": "Synthetic Creator",
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
    }


# ── A larger webinar, for the grouped measures ──────────────────────────────
#
# The webinar above is too small for the grouped measures' gates, so they
# read insufficient there. The crowd has the same people on stage and 120
# audience members built by fixed rules, so its record shows a row of each
# kind: channels, Other and direct rows with rates, select values, merged
# lead-time buckets, a room map and chat concentration.

CROWD_SESSION_ID = "synthetic-crowd"
CROWD_SIZE = 120
CROWD_TEAM_SIZES = ("1-10", "11-50", "51-200", "201-1000")
# Registered this long before the start, by index % 10: the 4-7 and 8-14
# buckets are small and merge.
CROWD_LEAD_S = (5 * 3600,) * 3 + (2 * DAY,) * 3 + (5 * DAY, 10 * DAY) + (20 * DAY,) * 2
# Chat authors (crowd index: messages), all of them attendees.
CROWD_CHAT = {0: 9, 1: 7, 2: 5, 4: 2, 5: 2, 6: 2, 8: 2, 9: 2, 10: 2, 12: 2, 13: 2, 14: 2}
# The crowd's own values its record must never carry, besides `PRIVATE_VALUES`.
CROWD_PRIVATE_VALUES = ("Crowd", "Member0", "p-crowd-", "u-crowd-", "msg-crowd-", "qst-crowd-", "Hi from the crowd")


def _crowd_member(index: int) -> Dict[str, Any]:
    """Crowd member `index`, by fixed rules. Channels: half from a
    newsletter UTM, a sixth from a partner's site, a sixth from five small
    social UTMs (they fold into Other) and a sixth with no source. One in
    four didn't come (half of them watched the replay). Lead times follow
    `CROWD_LEAD_S`; four people registered after the start."""
    channel = index % 6
    source: Dict[str, Any] = {}
    if channel in (0, 1, 2):
        source = {"utm_source": "newsletter", "utm_medium": "email"}
    elif channel == 3:
        source = {"referrer": "https://www.partner.example/post?ref=synthetic"}
    elif channel == 4:
        source = {"utm_source": f"social-{index // 6 % 5}"}
    attended = index % 4 != 3
    return _person(
        f"p-crowd-{index:03d}",
        "Crowd",
        f"Member{index:03d}",
        attended=attended,
        registered_s_before=-120 if index % 30 == 29 else CROWD_LEAD_S[index % 10],
        team_size="1000+" if index % 17 == 0 else CROWD_TEAM_SIZES[(index // 3) % 4],
        attendance_rate=25 + (index * 37) % 76,
        messages=CROWD_CHAT.get(index, 0),
        has_viewed_replay=index % 8 == 7 or (attended and index % 10 == 0),
        **source,
    )


def crowd_people() -> List[Dict[str, Any]]:
    on_stage = [person for person in people() if person["id"] in ("p-host", "p-cohost", "p-guest")]
    return on_stage + [_crowd_member(index) for index in range(CROWD_SIZE)]


def crowd_chat_payload() -> Dict[str, Any]:
    """`CROWD_CHAT`'s messages, spread over the session, and three from the host."""
    authors = [index for index, count in CROWD_CHAT.items() for _ in range(count)]
    data = []
    for number, author in enumerate(authors + [None] * 3, start=1):
        person_id = "p-host" if author is None else f"p-crowd-{author:03d}"
        data.append(
            {
                "id": f"msg-crowd-{number:03d}",
                "type": "chat_messages",
                "attributes": {
                    "text_content": "Hi from the crowd",
                    "created_at": T0 + 30 * number,
                    "author_id": f"u-crowd-{number:03d}",
                    "from_team_member": author is None,
                    "from_guest_speaker": False,
                    "session_id": CROWD_SESSION_ID,
                    "event_id": EVENT_ID,
                },
                "relationships": {"people": {"data": {"type": "people", "id": person_id}}},
            }
        )
    return {"data": data, "pages_fetched": 1, "requested_page_size": 50}


def crowd_questions_payload() -> Dict[str, Any]:
    """Three questions from attendees: answered in writing, orally, or not."""
    items = ((16, 400, 460, "Hi from the crowd", False), (20, 900, None, None, True), (24, 1500, None, None, False))
    data = [
        {
            "id": f"qst-crowd-{number}",
            "type": "questions",
            "attributes": {
                "question": "Hi from the crowd?",
                "created_at": T0 + asked,
                "responded_at": T0 + answered if answered is not None else None,
                "response": response,
                "response_orally": orally,
                "question_author_id": f"p-crowd-{asker:03d}",
                "response_author_id": "p-host" if response else None,
                "session_id": CROWD_SESSION_ID,
                "event_id": EVENT_ID,
            },
        }
        for number, (asker, asked, answered, response, orally) in enumerate(items, start=1)
    ]
    return {"data": data, "pages_fetched": 1, "requested_page_size": 50}


def crowd_row() -> Dict[str, Any]:
    """The crowd's `session_cache` row, with the fields the record reads."""
    payload = session_payload(crowd_people())
    payload["data"]["id"] = CROWD_SESSION_ID
    return {
        "session_id": CROWD_SESSION_ID,
        "organization_id": ORGANIZATION_ID,
        "session_payload": payload,
        "event_payload": event_payload(),
        "chat_payload": crowd_chat_payload(),
        "questions_payload": crowd_questions_payload(),
        "updated_at": UPDATED_AT,
    }


# ── The committed files ─────────────────────────────────────────────────────

DATETIME_COLUMNS = ("created_at", "updated_at", "cover_image_generated_at")


def row_to_json(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value.isoformat() if key in DATETIME_COLUMNS and isinstance(value, datetime) else value for key, value in row.items()}


def row_from_json(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: datetime.fromisoformat(value) if key in DATETIME_COLUMNS and isinstance(value, str) else value for key, value in data.items()}


def rounded(value: Any, places: int = 6) -> Any:
    """`value` with every float rounded, so a snapshot doesn't depend on the
    last bits of a platform's floating-point maths. Adding 0.0 turns a
    rounded -0.0 into 0.0, so a value near zero has one spelling in JSON."""
    if isinstance(value, float):
        return round(value, places) + 0.0
    if isinstance(value, dict):
        return {key: rounded(item, places) for key, item in value.items()}
    if isinstance(value, list):
        return [rounded(item, places) for item in value]
    return value


def dump(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


CONNECTION_ID = "synthetic-connection"


@contextmanager
def serving(row: Dict[str, Any], *, fresh_record_cache: bool = False) -> Iterator[Any]:
    """A test client of the real app, signed in to `row`'s organization,
    with `api_logic`'s scoped reads served from `row` (other sessions and
    other orgs see nothing) and an empty insights cache. Nothing reaches a
    database, Redis or a provider."""
    from fastapi.testclient import TestClient

    import app as app_module
    from livestorm_app import api_logic, auth, oauth_client

    def visible(session_id: str, organization_id: Any) -> Optional[Dict[str, Any]]:
        return row if session_id == row["session_id"] and organization_id == row["organization_id"] else None

    def read_fields(session_id, fields, *, organization_id):
        found = visible(session_id, organization_id)
        return {field: found.get(field) for field in fields} if found is not None else None

    def read_row(api_key, session_id, *, organization_id):
        return visible(session_id, organization_id)

    connection = {
        "connection_id": CONNECTION_ID, "organization_id": row["organization_id"], "email": "viewer@example.test",
        "user_id": "synthetic-viewer", "access_token": "synthetic-token", "token_type": "Bearer", "profile": {},
    }
    patches = [
        mock.patch.object(api_logic, "fetch_session_fields", read_fields),
        mock.patch.object(api_logic, "fetch_cached_session", read_row),
        mock.patch.object(api_logic, "_insights_cache", OrderedDict()),
        mock.patch.object(auth, "refresh_connection_if_needed", lambda cid: connection if cid == CONNECTION_ID else None),
        mock.patch.object(auth, "is_admin_email", lambda email: False),
    ]
    if fresh_record_cache:
        patches.append(mock.patch.object(api_logic, "_record_cache", OrderedDict()))
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        client = TestClient(app_module.app)
        client.cookies.set(oauth_client.LIVESTORM_OAUTH_COOKIE, CONNECTION_ID)
        yield client


def _get_json(row: Dict[str, Any], path: str, **options: Any) -> Dict[str, Any]:
    with serving(row, **options) as client:
        response = client.get(path)
    if response.status_code != 200:
        raise RuntimeError(f"{path} returned {response.status_code}")
    return response.json()


def record_response(row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What `GET /api/sessions/{id}/record` sends for `row` (default:
    `cached_row()`), through the real route and builder."""
    row = cached_row() if row is None else row
    return _get_json(row, f"/api/sessions/{row['session_id']}/record", fresh_record_cache=True)


def classic_workspace(row: Dict[str, Any]) -> Dict[str, Any]:
    """What `GET /api/sessions/{id}` sends for `row`, floats rounded."""
    return rounded(_get_json(row, f"/api/sessions/{row['session_id']}"))


def write_record_fixture() -> None:
    for path, row in ((RECORD_FIXTURE, cached_row()), (RECORD_CROWD_FIXTURE, crowd_row())):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump(record_response(row)), encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")


def write_classic_snapshot() -> None:
    if CLASSIC_SNAPSHOT.exists():
        frozen = row_from_json(json.loads(CLASSIC_SNAPSHOT.read_text(encoding="utf-8"))["row"])
    else:
        frozen = cached_row()
    CLASSIC_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    CLASSIC_SNAPSHOT.write_text(dump({"row": row_to_json(frozen), "workspace": classic_workspace(frozen)}), encoding="utf-8")
    print(f"wrote {CLASSIC_SNAPSHOT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    import conftest  # noqa: F401  (blanks .env settings before the app loads, as in the tests)

    if sys.argv[1:] == ["--classic"]:
        write_classic_snapshot()
    elif not sys.argv[1:]:
        write_record_fixture()
    else:
        sys.exit("usage: tests/synthetic_session.py [--classic]")
