"""A synthetic public webinar, shaped like a `session_cache` row.

Every name, email, company, id and message below is made up. The row is
what `db.fetch_cached_session` returns for a 30-minute session that started
95 seconds late: a team host (organizer) and co-host, a guest speaker,
seven audience members with UTMs, a referrer and a registration type, chat
in the waiting room, live and after the end, questions answered in writing,
orally or not at all, a short transcript and a few AI outputs.

Two committed files come from it:

- `frontend/tests/fixtures/record-v1.json`: the v1 record route's response
  for this row, built by the real code. `tests/test_record_route.py` fails
  when it drifts. Regenerate it after a deliberate change to the record:

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
    "Welcome", "pricing", "slides", "replay link", "Will the", "Is there an API",
    "password_key", "connection_link", "synthetic-password-key",
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
        _person("p-aud-2", "Mila", "Ford", registered_s_before=2 * DAY, team_size="1-10", messages=2, questions=1,
                attendance_rate=80, referrer="https://www.linkedin.example/feed/?trk=synthetic", country=("DE", "Germany")),
        _person("p-aud-3", "Sam", "Reed", registered_s_before=5 * 3600, messages=2, questions=1, attendance_rate=75,
                registration_type="detailed"),
        _person("p-aud-4", "Lena", "Hart", registered_s_before=6 * DAY, team_size="51-200", messages=1, attendance_rate=60,
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
# "guest" or None (audience).
CHAT = (
    (-300, "u-aud-1", None, "Hello everyone"),
    (-120, "u-aud-2", None, "Excited for this one"),
    (-60, "u-team-1", "team", "Welcome! We start in a minute."),
    (-30, "u-aud-3", None, "Can you hear us?"),
    (45, "u-team-1", "team", "Slides: https://example.test/slides"),
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


def chat_payload() -> Dict[str, Any]:
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
    return {"data": data, "pages_fetched": 1, "requested_page_size": 50}


def questions_payload() -> Dict[str, Any]:
    """Four questions: one asked in the waiting room and answered in writing,
    one answered orally, one answered in writing, one left open."""
    items = (
        ("qst-001", -45, "p-aud-1", "Will the slides be shared?", 50, "Yes, see the chat.", False),
        ("qst-002", 300, "p-aud-2", "How does pricing work for teams?", None, None, True),
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


def _utterance(speaker: int, start: float, words: List[str]) -> Dict[str, Any]:
    timed = []
    t = start
    for word in words:
        timed.append({"word": f" {word}", "start": round(t, 2), "end": round(t + 0.35, 2), "confidence": 0.92})
        t += 0.45
    text = " ".join(words)
    return {
        "start": timed[0]["start"], "end": timed[-1]["end"], "speaker": speaker, "channel": 0,
        "confidence": 0.92, "language": "en", "text": text, "words": timed,
    }


def transcript_payload() -> Dict[str, Any]:
    """A short Gladia transcript, normalized as the worker stores it, with
    the machine-dependent fields pinned."""
    from livestorm_app.gladia import transcriber

    utterances = [
        _utterance(0, 3.0, "welcome everyone thanks for joining us today".split()),
        _utterance(0, 12.0, "today we look at the new dashboard and the export".split()),
        _utterance(1, 60.0, "happy to be here let me share my screen".split()),
        _utterance(1, 300.0, "pricing works per team and scales with seats".split()),
        _utterance(0, 760.0, "yes you can export everything to csv".split()),
        _utterance(0, 1700.0, "thank you all see you next time".split()),
    ]
    sentences = [
        {"sentence": u["text"], "start": u["start"], "end": u["end"], "speaker": u["speaker"], "channel": 0,
         "confidence": u["confidence"], "language": "en", "words": copy.deepcopy(u["words"])}
        for u in utterances
    ]
    gladia = {
        "id": "synthetic-gladia-job", "status": "done", "kind": "pre-recorded",
        "file": {"audio_duration": float(DURATION_S)},
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
    normalized = transcriber._normalize_transcription(
        gladia,
        source_video=Path("recording.mp4"),
        extracted_audio=None,
        requested_model=transcriber.DEFAULT_MODEL,
        actual_model=transcriber.DEFAULT_MODEL,
        session_id=SESSION_ID,
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
        "transcript_speaker_names": {"0": "Host", "1": "Guest"},
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
    RECORD_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    RECORD_FIXTURE.write_text(dump(record_response()), encoding="utf-8")
    print(f"wrote {RECORD_FIXTURE.relative_to(REPO_ROOT)}")


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
