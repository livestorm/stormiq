"""One-off backfill: populate session_cache.organization_id on legacy rows.

Pre-Phase-4 rows have NULL organization_id and are invisible to
`/api/workspace-sessions`. This script stamps them with an org_id so
they show up in the Single Analysis grid without users having to
re-fetch each session.

Why this exists at all:
    The org_id field was added in Phase 4 to scope cache reads. Any row
    upserted *after* that commit lands with the correct org_id. Rows
    from earlier deploys have no org_id and need a one-shot fix.

Usage (from any machine with DATABASE_URL set — Render shell, locally, etc.):

    # Dry-run, picking the most-recent OAuth connection's org_id
    python scripts/backfill_organization_id.py --auto --dry-run

    # Actually apply, after confirming the dry-run output looked right
    python scripts/backfill_organization_id.py --auto

    # Specify a particular org_id explicitly
    python scripts/backfill_organization_id.py --org-id "<livestorm_org_uuid>"

    # Look up via a known user's email
    python scripts/backfill_organization_id.py --from-connection alice@example.com

The script:
  - Never overwrites a non-NULL organization_id (only fills NULLs).
  - Asks for confirmation before committing changes (unless --yes).
  - Prints a clear summary before and after.

If your workspace has multiple Livestorm orgs cached in this database,
DO NOT use --auto. Use --org-id once per org and limit the update with
--account-key-hash <hash> to scope the backfill, or talk to me first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Tuple

# Make `livestorm_app` importable when running this file as a script.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from livestorm_app.config import load_env_file  # noqa: E402
from livestorm_app.db import database_enabled, get_db_connection  # noqa: E402
from livestorm_app.oauth_client import _extract_profile  # noqa: E402


def _fix_oauth_connections(dry_run: bool) -> Tuple[int, int]:
    """Re-extract `organization_id` from each oauth_connections.profile.raw.

    Up to Phase 4 the OAuth profile extractor read the wrong JSON path,
    so every connection landed with `organization_id = ''`. Now that the
    extractor is fixed, we can replay it against the cached raw /me
    payloads (which we always saved into `profile.raw`) to populate the
    column without forcing every user to re-OAuth.

    Returns (rows_with_blank_org_id, rows_updated).
    """
    candidates = 0
    updated = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT connection_id, email, organization_id, profile
                FROM oauth_connections
                WHERE organization_id IS NULL OR organization_id = ''
                """
            )
            rows = cur.fetchall()

        for row in rows:
            candidates += 1
            profile = row.get("profile") or {}
            raw = profile.get("raw") if isinstance(profile, dict) else None
            if not isinstance(raw, dict):
                continue
            extracted = _extract_profile(raw)
            new_org_id = str(extracted.get("organization_id") or "").strip()
            if not new_org_id:
                continue
            if dry_run:
                updated += 1
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE oauth_connections
                    SET organization_id = %s,
                        profile = profile || %s::jsonb,
                        updated_at = NOW()
                    WHERE connection_id = %s
                    """,
                    (
                        new_org_id,
                        # Persist the refreshed parsed bits so future reads
                        # see a self-consistent profile object.
                        '{"organization_id": "' + new_org_id.replace('"', '\\"') + '"}',
                        row["connection_id"],
                    ),
                )
                updated += 1
        if not dry_run:
            conn.commit()
    return candidates, updated


def _count_rows() -> Tuple[int, int]:
    """Return (total_rows, rows_with_null_org)."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM session_cache")
            total = int(cur.fetchone()["c"])
            cur.execute(
                "SELECT COUNT(*) AS c FROM session_cache WHERE organization_id IS NULL"
            )
            null_count = int(cur.fetchone()["c"])
    return total, null_count


def _resolve_auto_org_id() -> Tuple[str, str]:
    """Pick the most-recently-updated OAuth connection's org_id.

    Prefers the persisted `organization_id` column. Falls back to
    re-extracting from `profile.raw` for cases where the OAuth fix
    step hasn't been applied yet (e.g. when this script is run
    --dry-run before any commits hit the DB).
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # First pass: persisted column.
            cur.execute(
                """
                SELECT email, organization_id
                FROM oauth_connections
                WHERE organization_id IS NOT NULL
                  AND organization_id <> ''
                ORDER BY updated_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if row:
                return str(row["organization_id"]).strip(), f"oauth_connections.email={row['email']}"

            # Second pass: re-extract from profile.raw so --auto still
            # works in dry-run mode against an un-fixed database.
            cur.execute(
                """
                SELECT email, profile
                FROM oauth_connections
                WHERE profile IS NOT NULL
                ORDER BY updated_at DESC
                """
            )
            for candidate in cur.fetchall():
                profile = candidate.get("profile") or {}
                raw = profile.get("raw") if isinstance(profile, dict) else None
                if not isinstance(raw, dict):
                    continue
                extracted = _extract_profile(raw)
                org_id = str(extracted.get("organization_id") or "").strip()
                if org_id:
                    return org_id, f"profile.raw (oauth_connections.email={candidate['email']})"

    raise RuntimeError(
        "No OAuth connection has a usable organization_id. Pass --org-id explicitly."
    )


def _resolve_org_id_from_email(email: str) -> Tuple[str, str]:
    target = str(email or "").strip()
    if not target:
        raise RuntimeError("--from-connection requires a non-empty email")
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT email, organization_id
                FROM oauth_connections
                WHERE LOWER(email) = LOWER(%s)
                  AND organization_id IS NOT NULL
                  AND organization_id <> ''
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (target,),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError(
            f"No OAuth connection found for {target!r} with a non-empty organization_id."
        )
    return str(row["organization_id"]).strip(), f"oauth_connections.email={target}"


def _apply_backfill(org_id: str, account_key_hash_filter: Optional[str]) -> int:
    """Update NULL rows in session_cache. Returns the affected row count."""
    sql = "UPDATE session_cache SET organization_id = %s WHERE organization_id IS NULL"
    params: list = [org_id]
    if account_key_hash_filter:
        sql += " AND account_key_hash = %s"
        params.append(account_key_hash_filter)
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            affected = cur.rowcount or 0
        conn.commit()
    return int(affected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--org-id", help="Livestorm organization UUID to write")
    source.add_argument("--from-connection", metavar="EMAIL", help="Resolve org_id from oauth_connections.email")
    source.add_argument("--auto", action="store_true", help="Use the most-recent OAuth connection's org_id (after the oauth_connections fix step)")
    parser.add_argument(
        "--account-key-hash",
        help="Optional: only backfill rows where account_key_hash matches (scopes to one fetcher's history)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show the change without committing")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    parser.add_argument(
        "--skip-oauth-fix",
        action="store_true",
        help="Don't try to re-extract organization_id on existing oauth_connections rows",
    )
    args = parser.parse_args()

    load_env_file()
    if not database_enabled():
        print("ERROR: DATABASE_URL (or POSTGRES_URL / RENDER_POSTGRES_URL) is not set.", file=sys.stderr)
        return 1

    # Step 1 — fix oauth_connections.organization_id from the cached
    # raw /me payloads. Phase 4 shipped with a broken JSON:API
    # extractor; this replays the (now-correct) extractor against the
    # raw payloads we kept in profile.raw so users don't need to
    # re-OAuth.
    if not args.skip_oauth_fix:
        candidates, fixed = _fix_oauth_connections(dry_run=args.dry_run)
        print("oauth_connections fix:")
        print(f"  rows with blank organization_id : {candidates}")
        print(f"  rows {('would be ' if args.dry_run else '')}updated  : {fixed}")
        print()

    # Step 2 — resolve the target org_id for the session_cache backfill.
    if not (args.org_id or args.from_connection or args.auto):
        print("ERROR: pass one of --org-id, --from-connection EMAIL, or --auto for the session_cache step.", file=sys.stderr)
        return 1

    if args.org_id:
        org_id, source_label = args.org_id.strip(), "--org-id argument"
    elif args.from_connection:
        org_id, source_label = _resolve_org_id_from_email(args.from_connection)
    else:
        org_id, source_label = _resolve_auto_org_id()

    if not org_id:
        print("ERROR: resolved org_id is empty.", file=sys.stderr)
        return 1

    total, null_count = _count_rows()
    print("session_cache backfill:")
    print(f"  rows total                   : {total}")
    print(f"  with organization_id present : {total - null_count}")
    print(f"  with NULL organization_id    : {null_count}")
    print(f"  target organization_id       : {org_id}")
    print(f"  target source                : {source_label}")
    if args.account_key_hash:
        print(f"  account_key_hash filter      : {args.account_key_hash}")

    if null_count == 0:
        print("\nNothing to backfill — all rows already have an organization_id.")
        return 0

    if args.dry_run:
        print(f"\nDRY RUN — would update {null_count} session_cache row(s). Re-run without --dry-run to apply.")
        return 0

    if not args.yes:
        prompt = f"\nUpdate {null_count} session_cache row(s) to organization_id={org_id!r}? [y/N] "
        answer = input(prompt).strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1

    affected = _apply_backfill(org_id, args.account_key_hash)
    print(f"\nDone. Updated {affected} session_cache row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
