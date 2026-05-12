"""One-off backfill: generate AI cover images for every cached session
that has a Professional Smart Recap but no cover yet.

Going forward, the worker auto-enqueues `run_cover_image_job` after
each Professional recap, so new sessions always get a cover. This
script catches up the 50-ish sessions that landed before the cover
job was wired in.

Usage (requires OPENAI_API_KEY + DATABASE_URL in env):

    # See what would be generated, without spending money
    python scripts/backfill_cover_images.py --dry-run

    # Apply. Cost ≈ $0.17 per cover at gpt-image-1 high quality
    # (so ~$8 for 50 sessions). Each call takes 15-45s, so allow
    # ~25 minutes of script runtime for 50 sessions.
    python scripts/backfill_cover_images.py --yes

The script is idempotent — it only touches rows where the Professional
recap exists AND cover_image_bytes is NULL. Re-run after partial
failures to fill the rest.
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

from livestorm_app.api_logic import run_cover_image_generation  # noqa: E402
from livestorm_app.config import load_env_file  # noqa: E402
from livestorm_app.db import database_enabled, ensure_database_schema, get_db_connection  # noqa: E402


def _gather_session_ids_needing_cover() -> List[str]:
    """Return session_ids that have a Professional recap but no cover image."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id
                FROM session_cache
                WHERE cover_image_bytes IS NULL
                  AND smart_recap_bundle IS NOT NULL
                  AND COALESCE(smart_recap_bundle->>'professional', '') <> ''
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
        help="Only process the first N sessions (0 = no limit). Useful for spot-testing.",
    )
    args = parser.parse_args()

    load_env_file()
    if not database_enabled():
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1
    ensure_database_schema()  # Idempotent — make sure cover_image_* columns exist

    openai_key = str(os.getenv("OPENAI_API_KEY", "") or "").strip()
    if not openai_key:
        print("ERROR: OPENAI_API_KEY is not set.", file=sys.stderr)
        return 1

    session_ids = _gather_session_ids_needing_cover()
    if args.limit > 0:
        session_ids = session_ids[: args.limit]

    if not session_ids:
        print("Nothing to backfill — every session with a Professional recap already has a cover image.")
        return 0

    print(f"Sessions needing a cover image: {len(session_ids)}")
    if args.dry_run:
        for sid in session_ids:
            print(f"  would generate: {sid}")
        return 0

    if not args.yes:
        prompt = (
            f"\nGenerate cover images for {len(session_ids)} session(s)? "
            "Each call hits OpenAI Images (high quality) and takes 15-45s. "
            f"Estimated cost ≈ ${len(session_ids) * 0.17:.2f}. [y/N] "
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
            result = run_cover_image_generation(openai_key, session_id)
            succeeded += 1
            print(f"  ✓ {prefix} ({result.get('status')}, {result.get('byteSize', 0)} bytes)")
        except Exception as exc:
            failed += 1
            print(f"  ✗ {prefix} : {exc}")
        if args.throttle_ms > 0 and index < len(session_ids):
            time.sleep(args.throttle_ms / 1000)

    print(f"\nDone. Succeeded: {succeeded}, Failed: {failed}")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
