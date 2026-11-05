"""Print the shape of the payloads cached in the LOCAL database.

For each payload column of `session_cache` (session, event, chat, questions,
transcript), across the chosen sessions, it prints:

- every key path, with how many payloads hold it, how often it occurs, and its
  JSON types. Floats are split into whole and fractional; a string under a key
  ending in `_at` is marked when it is an ISO date;
- the values of a few enum-like keys (`type`, `role`, `session_role`,
  `registration_type`, `status`, `kind`, `language`), and true/false counts;
- per-session clock checks: whether `duration` equals `ended_at - started_at`
  (and the gap in seconds), the late start in seconds, and how many chat
  messages, questions and answers fall before, during and after the session;
- referrer classes (Livestorm host, other host, empty), never the referrer;
- how many chat and question authors match a person in the session's people.

It never prints names, emails, message text, URLs or any other free-text
value. Keys that are not lowercase identifiers print as `<key>`.

Reads only. Refuses any non-local database (it checks the host libpq will
use, so a `host` or `hostaddr` query parameter can't point it elsewhere), and
does not read `.env`:

    DATABASE_URL=postgresql://stormiq:stormiq@localhost:5433/stormiq \\
        .venv/bin/python scripts/diagnose_payload_shapes.py [--session ID ...] [--limit N] [--include-bench]

By default it reads every cached session except the synthetic `bench-*` ones.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import psycopg
from psycopg.conninfo import conninfo_to_dict

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from livestorm_app.db import (  # noqa: E402
    ALL_ORGS,
    admin_list_sessions,
    fetch_cached_session,
    get_database_url,
)
from livestorm_app.livestorm.payloads import extract_messages, extract_questions  # noqa: E402
from livestorm_app.record.clock import clock_base, parse_time  # noqa: E402
from livestorm_app.record.evidence import audience_events  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}
LOOPBACK_ADDRESSES = {"127.0.0.1", "::1"}
PAYLOAD_COLUMNS = ("session_payload", "event_payload", "chat_payload", "questions_payload", "transcript_payload")
ENUM_KEYS = {"type", "role", "session_role", "registration_type", "status", "kind", "language", "languages"}

_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_ENUM = re.compile(r"^[A-Za-z][A-Za-z0-9_\-.]{0,39}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
LIVESTORM_HOST_SUFFIXES = ("livestorm.co", "livestorm.io")


def _split(value: Any) -> List[str]:
    return [part.strip() for part in str(value or "").split(",")]


def is_local_database(url: str) -> bool:
    """Whether libpq would connect to a local database for `url`.

    It reads the parameters libpq will use, not just the URL's host: a `host`
    query parameter overrides the host, and `hostaddr` (or `PGHOSTADDR`)
    overrides where it connects. Every host must be local, every `hostaddr` a
    loopback address, and no service file may supply either.
    """
    try:
        params = conninfo_to_dict(url)
    except psycopg.Error:
        return False
    if params.get("service") or os.environ.get("PGSERVICE"):
        return False
    hosts = _split(params.get("host"))
    addresses = [address for address in _split(params.get("hostaddr") or os.environ.get("PGHOSTADDR")) if address]
    return all(host in LOCAL_HOSTS for host in hosts) and all(address in LOOPBACK_ADDRESSES for address in addresses)


def _require_local_database() -> None:
    if not os.environ.get("DATABASE_URL"):
        sys.exit("Set DATABASE_URL explicitly (a local database); .env is not read by this script.")
    if not is_local_database(get_database_url()):
        sys.exit("Refusing to read: the database URL does not point at a local database.")


# ── Key paths ───────────────────────────────────────────────────────────────


def _key_label(key: Any) -> str:
    return key if isinstance(key, str) and _SAFE_KEY.match(key) else "<key>"


def _type_label(key: str, value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float-whole" if value.is_integer() else "float"
    if isinstance(value, str):
        if not value:
            return "empty-string"
        if key.endswith("_at"):
            if parse_time(value) is not None and _ISO_DATE.match(value.strip()):
                return "iso-string"
            if parse_time(value) is not None:
                return "numeric-string"
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


class ShapeReport:
    """Key paths, types, enum values and booleans, aggregated over payloads."""

    def __init__(self) -> None:
        self.payloads = 0
        self.occurrences: Counter = Counter()
        self.payloads_with: Counter = Counter()
        self.types: Dict[str, Counter] = defaultdict(Counter)
        self.enums: Dict[str, Counter] = defaultdict(Counter)
        self.booleans: Dict[str, Counter] = defaultdict(Counter)

    def add(self, payload: Any) -> None:
        self.payloads += 1
        seen: set = set()
        self._walk(payload, "", "", seen)
        for path in seen:
            self.payloads_with[path] += 1

    def _walk(self, value: Any, path: str, key: str, seen: set) -> None:
        label = path or "(root)"
        seen.add(label)
        self.occurrences[label] += 1
        self.types[label][_type_label(key, value)] += 1
        if isinstance(value, bool):
            self.booleans[label][str(value).lower()] += 1
        elif key in ENUM_KEYS and isinstance(value, str):
            self.enums[label][value if _SAFE_ENUM.match(value) else "<other>"] += 1
        if isinstance(value, dict):
            for child_key, child in value.items():
                name = _key_label(child_key)
                self._walk(child, f"{path}.{name}" if path else name, name, seen)
        elif isinstance(value, list):
            for item in value:
                self._walk(item, f"{path}[]", key, seen)

    def print(self, title: str) -> None:
        print(f"\n== {title}: {self.payloads} payloads ==")
        if not self.payloads:
            return
        width = max(len(path) for path in self.occurrences)
        print(f"  {'path':<{width}}  payloads  count  types")
        for path in sorted(self.occurrences):
            types = ", ".join(f"{name}={count}" for name, count in self.types[path].most_common())
            print(f"  {path:<{width}}  {self.payloads_with[path]:>8}  {self.occurrences[path]:>5}  {types}")
        if self.enums:
            print("  enum-like values:")
            for path in sorted(self.enums):
                values = ", ".join(f"{name}={count}" for name, count in self.enums[path].most_common())
                print(f"    {path}: {values}")
        if self.booleans:
            print("  booleans:")
            for path in sorted(self.booleans):
                values = ", ".join(f"{name}={count}" for name, count in sorted(self.booleans[path].items()))
                print(f"    {path}: {values}")


# ── Checks ──────────────────────────────────────────────────────────────────


def _people_ids(payload: Any) -> set:
    ids = set()
    if isinstance(payload, dict):
        for item in payload.get("included") or []:
            if isinstance(item, dict) and item.get("type") == "people" and item.get("id"):
                ids.add(str(item["id"]).strip())
    return ids


def referrer_class(value: Any) -> str:
    """Livestorm host, other host, not a URL or empty. Never the value itself."""
    if not isinstance(value, str) or not value.strip():
        return "empty"
    try:
        host = (urlparse(value.strip()).hostname or "").lower()
    except ValueError:  # a malformed URL, such as an unclosed IPv6 bracket
        return "not a URL"
    if not host:
        return "not a URL"
    if any(host == suffix or host.endswith("." + suffix) for suffix in LIVESTORM_HOST_SUFFIXES):
        return "Livestorm host"
    return "other host"


def _seconds(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.0f}"


def clock_row(session_id: str, row: Dict[str, Any]) -> str:
    base = clock_base(row.get("session_payload"))
    attributes = ((row.get("session_payload") or {}).get("data") or {}).get("attributes") or {}
    started, ended = parse_time(attributes.get("started_at")), parse_time(attributes.get("ended_at"))
    gap = None
    if started is not None and ended is not None and base.duration_attr_s is not None:
        gap = (ended - started) - base.duration_attr_s
    events = audience_events(row.get("chat_payload"), row.get("questions_payload"), base)
    phases = []
    for kind in ("chat", "question", "answer"):
        subset = events[events["kind"] == kind]
        counts = subset["phase"].fillna("none").value_counts()
        phases.append("/".join(str(int(counts.get(name, 0))) for name in ("before", "live", "after", "none")))
    return (
        f"  {session_id:<38} {'yes' if base.has_clock else 'no':<5} {base.end_source:<9} "
        f"{_seconds(gap):>7} {_seconds(base.late_start_s):>7}  {phases[0]:<17} {phases[1]:<13} {phases[2]}"
    )


def check_rows(rows: Dict[str, Dict[str, Any]]) -> None:
    print("\n== Clock checks ==")
    print("  gap_s = (ended_at - started_at) - duration; late_s = started_at - estimated_started_at")
    print("  event counts are before/live/after/no time")
    print(f"  {'session':<38} {'clock':<5} {'end from':<9} {'gap_s':>7} {'late_s':>7}  {'chat':<17} {'questions':<13} answers")
    for session_id, row in rows.items():
        print(clock_row(session_id, row))

    referrers: Counter = Counter()
    chat_authors: Counter = Counter()
    question_authors: Counter = Counter()
    for row in rows.values():
        session_payload = row.get("session_payload")
        people = _people_ids(session_payload)
        included = session_payload.get("included") if isinstance(session_payload, dict) else None
        for item in included if isinstance(included, list) else []:
            if isinstance(item, dict) and item.get("type") == "people":
                detail = (item.get("attributes") or {}).get("registrant_detail")
                referrers[referrer_class(detail.get("referrer") if isinstance(detail, dict) else None)] += 1
        for message in extract_messages(row.get("chat_payload")):
            author = str(((message or {}).get("attributes") or {}).get("author_id") or "").strip()
            chat_authors["no author_id" if not author else "in session people" if author in people else "not in session people"] += 1
        question_people = people | _people_ids(row.get("questions_payload"))
        for question in extract_questions(row.get("questions_payload")):
            author = str(((question or {}).get("attributes") or {}).get("question_author_id") or "").strip()
            question_authors[
                "no question_author_id" if not author else "in people" if author in question_people else "not in people"
            ] += 1

    print("\n== Referrers (session people) ==")
    _print_counter(referrers)
    print("\n== Chat authors vs the session's people ==")
    _print_counter(chat_authors)
    print("\n== Question authors vs the session's and the questions' people ==")
    _print_counter(question_authors)


def _print_counter(counter: Counter) -> None:
    if not counter:
        print("  (none)")
    for name, count in counter.most_common():
        print(f"  {name}: {count}")


# ── Main ────────────────────────────────────────────────────────────────────


def _session_ids(args: argparse.Namespace) -> List[str]:
    if args.session:
        return list(dict.fromkeys(args.session))
    listed = [row["session_id"] for row in admin_list_sessions(limit=args.limit)]
    return [sid for sid in listed if args.include_bench or not str(sid).startswith("bench-")]


def _load(session_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    rows = {}
    for session_id in session_ids:
        row = fetch_cached_session("", session_id, organization_id=ALL_ORGS)
        if row is None:
            print(f"  skipped a session that is not cached: {session_id}", file=sys.stderr)
            continue
        rows[session_id] = row
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", action="append", help="a session id to read (repeatable); default: all")
    parser.add_argument("--limit", type=int, default=500, help="most recent sessions to read (default 500)")
    parser.add_argument("--include-bench", action="store_true", help="also read the synthetic bench-* sessions")
    args = parser.parse_args()
    _require_local_database()

    rows = _load(_session_ids(args))
    print(f"Payload shapes for {len(rows)} cached sessions (local database)")
    for column in PAYLOAD_COLUMNS:
        report = ShapeReport()
        for row in rows.values():
            if row.get(column) is not None:
                report.add(row[column])
        report.print(column)
    if rows:
        check_rows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
