"""Diagnose why a user only sees their own analyses on Single Analysis.

The /api/workspace-sessions endpoint scopes by `organization_id` — same
across the whole Livestorm workspace, no per-user filter. If teammates'
sessions don't show up, it's a data mismatch:

  - teammates' session_cache rows have a different (or NULL) org_id, or
  - your oauth_connections row carries a different org_id from theirs.

This script prints both sides side-by-side so the mismatch is obvious.

Usage (Render shell, or anywhere DATABASE_URL is reachable):

    python scripts/diagnose_workspace_visibility.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from livestorm_app.config import load_env_file  # noqa: E402
from livestorm_app.db import database_enabled, get_db_connection  # noqa: E402


def main() -> int:
    load_env_file()
    if not database_enabled():
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT email,
                       organization_id,
                       updated_at
                FROM oauth_connections
                ORDER BY updated_at DESC
                """
            )
            connections = cur.fetchall()

            cur.execute(
                """
                SELECT organization_id,
                       created_by_email,
                       COUNT(*) AS rows
                FROM session_cache
                GROUP BY organization_id, created_by_email
                ORDER BY organization_id NULLS FIRST, rows DESC
                """
            )
            cache_groups = cur.fetchall()

    print("=" * 72)
    print("OAuth connections (one row per teammate that logged in):")
    print("=" * 72)
    print(f"  {'email':<40} {'organization_id':<40} updated_at")
    for row in connections:
        org = row.get("organization_id") or "<empty>"
        print(f"  {(row.get('email') or '<unknown>'):<40} {org:<40} {row.get('updated_at')}")

    print()
    print("=" * 72)
    print("session_cache grouped by organization_id + generator:")
    print("=" * 72)
    print(f"  {'organization_id':<40} {'created_by_email':<40} rows")
    for row in cache_groups:
        org = row.get("organization_id") or "<NULL>"
        creator = row.get("created_by_email") or "<unset>"
        print(f"  {org:<40} {creator:<40} {row['rows']}")

    print()
    print("=" * 72)
    print("Summary")
    print("=" * 72)
    org_ids_in_connections = {
        (row.get("organization_id") or "").strip()
        for row in connections
        if (row.get("organization_id") or "").strip()
    }
    org_ids_in_cache = Counter()
    for row in cache_groups:
        key = (row.get("organization_id") or "").strip() or "<NULL>"
        org_ids_in_cache[key] += int(row["rows"])

    print(f"  distinct org_ids in oauth_connections : {len(org_ids_in_connections)}")
    for oid in sorted(org_ids_in_connections):
        print(f"    - {oid}")
    print(f"  distinct org_ids in session_cache     : {len(org_ids_in_cache)}")
    for oid, n in org_ids_in_cache.most_common():
        marker = "" if oid in org_ids_in_connections else "   ← NOT in any oauth_connections row"
        print(f"    - {oid:<40} {n} rows{marker}")

    if "<NULL>" in org_ids_in_cache:
        print(
            "\n  ⚠ Some session_cache rows have NULL organization_id. They are "
            "invisible to /api/workspace-sessions. Run:\n"
            "    python scripts/backfill_organization_id.py --auto --dry-run\n"
            "  to preview a backfill."
        )
    if len(org_ids_in_cache) > 1 and "<NULL>" not in org_ids_in_cache:
        print(
            "\n  ⚠ session_cache has multiple distinct organization_ids. Either "
            "you have multiple Livestorm orgs cached here, or some rows were "
            "stamped before the OAuth fix shipped. Compare against the "
            "oauth_connections list above to spot the wrong one."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
