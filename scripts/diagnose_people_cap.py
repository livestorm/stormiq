"""Count what Livestorm's people endpoints return for a few sessions: integers and yes/no only.

It calls Livestorm with `LS_API_KEY` from the environment, for the sessions
cached in the LOCAL database or named with `--session`, and prints integers
and yes/no only, to answer:

- Is the session's `include=people` list capped? Compare its length with the
  paginated list's `meta.record_count` and the session's `registrants_count`.
- Does the paginated list (`GET /sessions/{id}/people`) count the team, and
  do the session's counters? Compare the `filter[role]` and
  `filter[attended]` counts with `registrants_count` and `attendees_count`.
- Does `GET /people_attributes` answer this token, and do registration field
  ids match attribute slugs? Are select answers stored as an option's value
  or its label?
- Does Livestorm send the `RateLimit-Monthly-*` headers?

For each session, numbered `#1`, `#2`… in the order chosen:

    #1 include people=… role_team_member=… role_participant=…
    #1 counters registrants_count=… attendees_count=…
    #1 paginated record_count=… unique_people=… pages=… duplicates=… complete=yes|no
    #1 filters role_team_member=… role_participant=… attended=… attended_team_member=…
    #1 fields ids=… matching_attribute_slug=… select_answers_value=… select_answers_label_only=… select_answers_neither=…

then once: the people attributes' HTTP status, whether they were refused and
how many there are; whether any `RateLimit-Monthly-*` header came back
(yes/no); and how many Livestorm calls the run made. A failed call prints its
HTTP status, or its error class when there was no response or the list never
reached its last page; a paginated list still unfinished after 10 minutes
prints `stopped_after_s=600`. It never prints a session id, a name, an email,
an answer, a URL, a header value or a response body, on stdout or stderr: it
silences urllib3's retry warnings, which name the URL. It writes nothing.

`LS_API_KEY` is an API key: what it shows about the people attributes and the
rate-limit headers may differ for a user's OAuth token.

Cost: per session, 1 call for the session, 1 per 50 registrants for the
paginated list and 4 small count calls; plus 1 call per 50 people attributes.
Livestorm documents a limit of 10,000 calls per 30 days.

Run it by hand (it doesn't read `.env`; set both variables explicitly):

    DATABASE_URL=postgresql://stormiq:stormiq@localhost:5433/stormiq LS_API_KEY=… \\
        .venv/bin/python scripts/diagnose_people_cap.py [--session ID ...] [--limit N] [--include-bench]

By default it checks the 50 most recently updated cached sessions (`--limit`),
leaving out the synthetic `bench-*` ones. `--session` picks sessions in the order
given. It refuses a `DATABASE_URL` that isn't local (the same check as
`diagnose_payload_shapes.py`).
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from livestorm_app.config import API_BASE  # noqa: E402
from livestorm_app.db import admin_list_sessions, get_database_url  # noqa: E402
from livestorm_app.livestorm import client  # noqa: E402
from livestorm_app.livestorm.payloads import extract_record_count  # noqa: E402

SELECT_TYPES = ("unique_select", "multiple_select")
MONTHLY_HEADER_PREFIX = "ratelimit-monthly-"
PEOPLE_DEADLINE_SECONDS = 600
COUNT_PROBES = (
    ("role_team_member", {"filter[role]": "team_member"}),
    ("role_participant", {"filter[role]": "participant"}),
    ("attended", {"filter[attended]": "true"}),
    ("attended_team_member", {"filter[attended]": "true", "filter[role]": "team_member"}),
)


def _shapes_script() -> Any:
    path = Path(__file__).resolve().parent / "diagnose_payload_shapes.py"
    spec = importlib.util.spec_from_file_location("diagnose_payload_shapes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_local_database_if_set() -> None:
    """Exit when `DATABASE_URL` is set to a database that isn't local."""
    if os.environ.get("DATABASE_URL") and not _shapes_script().is_local_database(get_database_url()):
        sys.exit("Refusing to run: the database URL does not point at a local database.")


def select_sessions(
    listed: Iterable[str], *, explicit: Optional[Sequence[str]], include_bench: bool
) -> List[str]:
    """The sessions to check: `explicit` in its order (repeats dropped), or
    the listed ones without the synthetic `bench-*` sessions."""
    if explicit:
        return list(dict.fromkeys(str(sid).strip() for sid in explicit if str(sid).strip()))
    return [sid for sid in listed if include_bench or not str(sid).startswith("bench-")]


def has_monthly_header(headers: Any) -> bool:
    """Whether a response carries any `RateLimit-Monthly-*` header."""
    keys = getattr(headers, "keys", None)
    return callable(keys) and any(str(name).lower().startswith(MONTHLY_HEADER_PREFIX) for name in keys())


def _yes(flag: Any) -> str:
    return "yes" if flag else "no"


def _number(value: Any) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else "-"


def _status(exc: Exception) -> str:
    """A failed call as its HTTP status, or its error class: never its message,
    which can hold the URL."""
    response = getattr(exc, "response", None)
    return f"HTTP {response.status_code}" if response is not None else type(exc).__name__


def silence_retry_warnings() -> None:
    """Keep urllib3's retry warnings off stderr: they name the request's URL,
    which holds the session id."""
    logging.getLogger("urllib3").setLevel(logging.ERROR)


def include_counts(session_payload: Mapping[str, Any]) -> Dict[str, Any]:
    """The `include=people` list's length and its people by role, and the session's counters."""
    data = session_payload.get("data")
    attributes = data.get("attributes") if isinstance(data, Mapping) else None
    attributes = attributes if isinstance(attributes, Mapping) else {}
    included = session_payload.get("included")
    included = included if isinstance(included, list) else []
    people = [item for item in included if isinstance(item, Mapping) and item.get("type") == "people"]

    def role(item: Mapping[str, Any]) -> Any:
        person = item.get("attributes")
        return person.get("role") if isinstance(person, Mapping) else None

    return {
        "people": len(people),
        "role_team_member": sum(1 for item in people if role(item) == "team_member"),
        "role_participant": sum(1 for item in people if role(item) == "participant"),
        "registrants_count": attributes.get("registrants_count"),
        "attendees_count": attributes.get("attendees_count"),
    }


def field_checks(people: Iterable[Any], attributes: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """Counts that say how registration answers line up with the people attributes.

    `ids`: distinct registration field ids among the people;
    `matching_attribute_slug`: how many of them are an attribute's slug.
    For select fields, each answer (each item of a multiple select) counts as
    matching an option's value, else only a label, else neither.
    """
    options: Dict[str, Dict[str, set]] = {}
    for attribute in attributes:
        slug = str(attribute.get("slug") or "")
        values = {str(option.get("value")) for option in attribute.get("options") or () if option.get("value") is not None}
        labels = {str(option.get("label")) for option in attribute.get("options") or () if option.get("label") is not None}
        options[slug] = {"values": values, "labels": labels}

    ids: set = set()
    counts = {"value": 0, "label_only": 0, "neither": 0}
    for person in people:
        detail = (person.get("attributes") or {}).get("registrant_detail") if isinstance(person, Mapping) else None
        fields = detail.get("fields") if isinstance(detail, Mapping) else None
        for field in fields if isinstance(fields, list) else ():
            if not isinstance(field, Mapping) or field.get("id") in (None, ""):
                continue
            slug = str(field.get("id"))
            ids.add(slug)
            if str(field.get("type") or "").lower() not in SELECT_TYPES:
                continue
            answers = field.get("value")
            for answer in answers if isinstance(answers, list) else [answers]:
                if answer in (None, ""):
                    continue
                known = options.get(slug, {"values": set(), "labels": set()})
                key = "value" if str(answer) in known["values"] else "label_only" if str(answer) in known["labels"] else "neither"
                counts[key] += 1
    return {
        "ids": len(ids),
        "matching_attribute_slug": sum(1 for slug in ids if slug in options),
        "select_answers_value": counts["value"],
        "select_answers_label_only": counts["label_only"],
        "select_answers_neither": counts["neither"],
    }


def count_probe(key: str, session_id: str, filters: Mapping[str, str]) -> str:
    """`meta.record_count` of the people list under `filters`, from one 1-item page."""
    try:
        response = client.livestorm_get(
            f"{API_BASE}/sessions/{session_id}/people",
            headers=client.build_headers(key),
            params={"page[number]": 0, "page[size]": 1, **filters},
        )
        response.raise_for_status()
        return _number(extract_record_count(response.json()))
    except requests.RequestException as exc:
        return _status(exc)


class CallLog:
    """Counts Livestorm calls and notes whether any carried a monthly rate-limit header."""

    def __init__(self, get: Callable[..., Any]) -> None:
        self._get = get
        self.calls = 0
        self.monthly_header = False

    def __call__(self, url: str, *, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None) -> Any:
        self.calls += 1
        response = self._get(url, headers=headers, params=params)
        self.monthly_header = self.monthly_header or has_monthly_header(getattr(response, "headers", None))
        return response


def check_session(key: str, number: int, session_id: str, attributes: Sequence[Mapping[str, Any]], out: Callable[[str], None]) -> None:
    label = f"#{number}"
    try:
        details = client.fetch_session_details(key, session_id)
    except requests.RequestException as exc:
        out(f"{label} session {_status(exc)}")
        return
    counts = include_counts(details)
    out(f"{label} include people={counts['people']} role_team_member={counts['role_team_member']} role_participant={counts['role_participant']}")
    out(f"{label} counters registrants_count={_number(counts['registrants_count'])} attendees_count={_number(counts['attendees_count'])}")

    people: List[Any] = []
    try:
        listed = client.fetch_session_people(key, session_id, deadline=time.monotonic() + PEOPLE_DEADLINE_SECONDS)
        people = listed["people"]
        out(
            f"{label} paginated record_count={_number(listed['record_count'])} unique_people={len(people)} "
            f"pages={listed['pages_fetched']} duplicates={listed['duplicates']} complete={_yes(listed['complete'])}"
        )
    except (requests.RequestException, client.LivestormPaginationError) as exc:
        out(f"{label} paginated {_status(exc)}")
    except client.LivestormDeadlineExceeded:
        out(f"{label} paginated stopped_after_s={PEOPLE_DEADLINE_SECONDS}")

    probes = " ".join(f"{name}={count_probe(key, session_id, filters)}" for name, filters in COUNT_PROBES)
    out(f"{label} filters {probes}")
    checks = field_checks(people, attributes)
    out(f"{label} fields " + " ".join(f"{name}={value}" for name, value in checks.items()))


def run(key: str, session_ids: Sequence[str], out: Callable[[str], None] = print) -> int:
    """Check each session and print the summary. Returns the number of Livestorm calls made."""
    calls = CallLog(client.livestorm_get)
    original = client.livestorm_get
    client.livestorm_get = calls
    try:
        out(f"Livestorm people counts for {len(session_ids)} session(s). Integers and yes/no only.")
        try:
            listed = client.fetch_people_attributes(key)
            attributes = listed["attributes"]
            attributes_line = (
                f"people_attributes http_status={_number(listed['http_status'])} "
                f"forbidden={_yes(listed['status'] == 'forbidden')} count={len(attributes)}"
            )
        except (requests.RequestException, client.LivestormPaginationError) as exc:
            attributes = []
            attributes_line = f"people_attributes {_status(exc)}"
        for number, session_id in enumerate(session_ids, start=1):
            check_session(key, number, session_id, attributes, out)
        out(attributes_line)
        out(f"ratelimit_monthly_headers={_yes(calls.monthly_header)}")
        out(f"calls={calls.calls}")
    finally:
        client.livestorm_get = original
    return calls.calls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", action="append", help="a session id to check (repeatable); default: the cached ones")
    parser.add_argument("--limit", type=int, default=50, help="most recent sessions to check (default 50)")
    parser.add_argument("--include-bench", action="store_true", help="also check the synthetic bench-* sessions")
    args = parser.parse_args()

    silence_retry_warnings()
    require_local_database_if_set()
    key = os.environ.get("LS_API_KEY", "").strip()
    if not key:
        sys.exit("Set LS_API_KEY in the environment; .env is not read by this script.")
    if args.session:
        session_ids = select_sessions((), explicit=args.session, include_bench=True)
    else:
        if not os.environ.get("DATABASE_URL"):
            sys.exit("Set DATABASE_URL explicitly (a local database) or pass --session; .env is not read.")
        listed = [row["session_id"] for row in admin_list_sessions(limit=args.limit)]
        session_ids = select_sessions(listed, explicit=None, include_bench=args.include_bench)
    if not session_ids:
        sys.exit("No session to check.")
    run(key, session_ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
