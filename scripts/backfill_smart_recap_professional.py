"""One-off backfill: generate a Professional Smart Recap for every cached
session that has a transcript but no Professional recap yet.

Why: as of this change, fresh transcriptions automatically enqueue a
Professional recap so each session is ready to feed the cover-image
generator downstream. Pre-change cached sessions don't have one — this
script fills them in.

Usage (run with OPENAI_API_KEY + DATABASE_URL in env):

    # Show what would be generated without calling OpenAI
    python scripts/backfill_smart_recap_professional.py --dry-run

    # Apply — runs each generation synchronously; ~10-20s per session
    python scripts/backfill_smart_recap_professional.py --yes

Idempotent: skips sessions whose `smart_recap_bundle.professional` is
already non-empty. Safe to re-run after partial failures — only the
still-missing ones are picked up.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from livestorm_app.api_logic import run_smart_recap  # noqa: E402
from livestorm_app.config import load_env_file  # noqa: E402
from livestorm_app.db import database_enabled, ensure_database_schema, get_db_connection  # noqa: E402


def _gather_session_ids_needing_recap() -> List[str]:
    """Return session_ids that have a transcript but no Professional recap."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id
                FROM session_cache
                WHERE transcript_payload IS NOT NULL
                  AND (
                    smart_recap_bundle IS NULL
                    OR COALESCE(smart_recap_bundle->>'professional', '') = ''
                  )
                ORDER BY updated_at DESC
                """
            )
            return [row["session_id"] for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="List sessions that would be generated, don't call OpenAI")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    parser.add_argument(
        "--throttle-ms",
        type=int,
        default=500,
        help="Sleep between OpenAI calls in milliseconds (default 500). Helps avoid rate limits.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N sessions (0 = no limit). Useful for testing.",
    )
    args = parser.parse_args()

    load_env_file()
    if not database_enabled():
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1
    ensure_database_schema()  # Idempotent — make sure smart_recap_bundle column exists

    openai_key = str(os.getenv("OPENAI_API_KEY", "") or "").strip()
    if not openai_key:
        print("ERROR: OPENAI_API_KEY is not set.", file=sys.stderr)
        return 1

    session_ids = _gather_session_ids_needing_recap()
    if args.limit > 0:
        session_ids = session_ids[: args.limit]

    if not session_ids:
        print("Nothing to backfill — every cached session with a transcript already has a Professional recap.")
        return 0

    print(f"Sessions needing Professional Smart Recap: {len(session_ids)}")
    if args.dry_run:
        for sid in session_ids:
            print(f"  would generate: {sid}")
        return 0

    if not args.yes:
        prompt = (
            f"\nGenerate Professional recap for {len(session_ids)} session(s)? "
            "Each call hits OpenAI; ~10-20s per session. [y/N] "
        )
        answer = input(prompt).strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1

    succeeded = 0
    failed = 0
    for index, session_id in enumerate(session_ids, start=1):
        prefix = f"[{index}/{len(session_ids)}] {session_id}"
        try:
            run_smart_recap(openai_key, session_id, "professional")
            succeeded += 1
            print(f"  ✓ {prefix}")
        except Exception as exc:
            failed += 1
            print(f"  ✗ {prefix} : {exc}")
        if args.throttle_ms > 0 and index < len(session_ids):
            time.sleep(args.throttle_ms / 1000)

    print(f"\nDone. Succeeded: {succeeded}, Failed: {failed}")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
