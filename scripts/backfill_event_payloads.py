"""One-off backfill: populate session_cache.event_payload for legacy rows.

After we added the event_payload column, new session fetches store the
parent event alongside the session. Older cached rows have no event
payload, so the Single Analysis card list falls back to "Untitled event".
This script fetches each missing event from Livestorm and stores it.

Usage (run with a valid Livestorm API key in env):

    # Dry-run — see what would be fetched, no DB writes
    python scripts/backfill_event_payloads.py --dry-run

    # Apply — fetches each missing event once (events shared across
    # sessions are fetched only once), then writes them to every
    # session_cache row referencing that event_id
    python scripts/backfill_event_payloads.py --yes

Auth resolution order:
    1. --api-key on the command line
    2. LS_API_KEY env var
    3. (Not supported) Bearer tokens from oauth_connections — those
       rotate, so re-using them via a script is unreliable. Set
       LS_API_KEY instead.

Safe to re-run: skips rows that already have a non-null event_payload.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from livestorm_app.config import load_env_file  # noqa: E402
from livestorm_app.db import (  # noqa: E402
    database_enabled,
    ensure_database_schema,
    get_db_connection,
    upsert_cached_session,
)
from livestorm_app.services import fetch_event_details  # noqa: E402


def _resolve_api_key(cli_value: Optional[str]) -> str:
    explicit = str(cli_value or "").strip()
    if explicit:
        return explicit
    fallback = str(os.getenv("LS_API_KEY", "") or "").strip()
    if fallback:
        return fallback
    raise RuntimeError(
        "No Livestorm API key found. Pass --api-key, or set LS_API_KEY in your environment."
    )


def _gather_rows_needing_event() -> list:
    """Return rows where session_payload is present but event_payload is NULL."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, organization_id, session_payload, account_key_hash
                FROM session_cache
                WHERE session_payload IS NOT NULL
                  AND event_payload IS NULL
                ORDER BY updated_at DESC
                """
            )
            return [dict(r) for r in cur.fetchall()]


def _event_id_from_payload(session_payload: Any) -> str:
    """data.attributes.event_id, or '' when missing."""
    if not isinstance(session_payload, dict):
        return ""
    data = session_payload.get("data") or {}
    attrs = data.get("attributes") or {}
    return str(attrs.get("event_id") or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-key", help="Livestorm API key (overrides LS_API_KEY env var)")
    parser.add_argument("--dry-run", action="store_true", help="List what would be fetched, no writes")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    parser.add_argument(
        "--throttle-ms",
        type=int,
        default=200,
        help="Sleep between Livestorm event fetches (default 200ms) to stay under rate limits",
    )
    args = parser.parse_args()

    load_env_file()
    if not database_enabled():
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1

    # Make sure the event_payload column exists. ensure_database_schema is
    # idempotent — safe to call even when the app is already running.
    ensure_database_schema()

    api_key = _resolve_api_key(args.api_key)
    rows = _gather_rows_needing_event()
    if not rows:
        print("Nothing to backfill — every cached session already has an event_payload.")
        return 0

    # Dedupe by event_id so we fetch each Livestorm event at most once.
    rows_by_event: Dict[str, list] = {}
    rows_without_event_id = 0
    for row in rows:
        event_id = _event_id_from_payload(row.get("session_payload"))
        if not event_id:
            rows_without_event_id += 1
            continue
        rows_by_event.setdefault(event_id, []).append(row)

    print(f"session_cache rows missing event_payload : {len(rows)}")
    print(f"  rows without resolvable event_id        : {rows_without_event_id}")
    print(f"  distinct events to fetch                : {len(rows_by_event)}")

    if args.dry_run:
        print("\nDRY RUN — would fetch the following event ids:")
        for event_id, matching_rows in rows_by_event.items():
            print(f"  {event_id}  → {len(matching_rows)} session(s)")
        return 0

    if not args.yes:
        prompt = (
            f"\nFetch {len(rows_by_event)} event(s) from Livestorm and stamp them "
            f"onto {len(rows) - rows_without_event_id} session_cache row(s)? [y/N] "
        )
        answer = input(prompt).strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1

    fetched: Set[str] = set()
    failed: Set[str] = set()
    rows_updated = 0
    for event_id, matching_rows in rows_by_event.items():
        try:
            event_payload = fetch_event_details(api_key, event_id)
        except Exception as exc:
            failed.add(event_id)
            print(f"  ✗ event_id={event_id} : {exc}")
            continue
        fetched.add(event_id)
        # Write this event_payload to every session_cache row that references
        # the event id. upsert_cached_session preserves existing fields and
        # only updates what we pass — safe to call repeatedly per session.
        for row in matching_rows:
            upsert_cached_session(
                row.get("account_key_hash") or "",
                row["session_id"],
                event_payload=event_payload,
            )
            rows_updated += 1
        if args.throttle_ms > 0:
            time.sleep(args.throttle_ms / 1000)

    print(
        f"\nDone. Fetched {len(fetched)} event(s), updated {rows_updated} session_cache row(s). "
        f"{len(failed)} fetch(es) failed."
    )
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
