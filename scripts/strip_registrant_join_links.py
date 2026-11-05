"""Remove registrants' personal join links from cached Livestorm payloads.

StormIQ never stores a registrant's personal join link; it doesn't need it.
New fetches drop it before saving (`livestorm.payloads.strip_payload_join_links`).
This script cleans rows saved before that. It removes `password_key` and
`connection_link` from each person's `registrant_detail` in
`session_payload`, `chat_payload` and `questions_payload`. Every other key
is kept.

How it works:
    - Dry run by default: it reads and reports, and writes nothing.
      `--apply` writes.
    - Reads rows in batches (`--batch-size`, default 50) and rewrites them
      one at a time through `db.rewrite_base_payloads`. That leaves
      `updated_at` alone, so the session library keeps its order.
    - A row saved again since it was read is skipped: run the script again.
    - A database error stops the script.
    - Prints session ids and counts only, never payload values.
    - Safe to re-run: a clean row is left alone.

Usage (`.env` is not read: set DATABASE_URL explicitly):
    DATABASE_URL=postgresql://stormiq:stormiq@localhost:5433/stormiq \\
        python scripts/strip_registrant_join_links.py            # dry run
    DATABASE_URL=... python scripts/strip_registrant_join_links.py --apply

It refuses a database that isn't local unless `--allow-remote-database`
is also passed. Run it locally first.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from livestorm_app.db import (  # noqa: E402
    BASE_PAYLOAD_COLUMNS,
    get_database_url,
    rewrite_base_payloads,
    scan_base_payloads,
)
from livestorm_app.livestorm.payloads import (  # noqa: E402
    strip_payload_join_links,
    strip_registrant_join_link,
)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}


def _require_database(allow_remote: bool) -> None:
    if not os.environ.get("DATABASE_URL"):
        sys.exit("Set DATABASE_URL explicitly; .env is not read by this script.")
    host = urlparse(get_database_url()).hostname or ""
    if host not in LOCAL_HOSTS and not allow_remote:
        sys.exit("Refusing to run: DATABASE_URL is not a local database. Pass --allow-remote-database to run it anyway.")


def _records_with_join_links(payload: Any) -> int:
    """How many records in `data` and `included` hold a join link."""
    if not isinstance(payload, dict):
        return 0
    count = 0
    for section in ("data", "included"):
        value = payload.get(section)
        records = value if isinstance(value, list) else [value]
        count += sum(1 for record in records if strip_registrant_join_link(record) is not record)
    return count


def run(*, apply: bool, batch_size: int = 50, out: Callable[[str], None] = print) -> Dict[str, int]:
    """Scan every cached session and, with `apply`, rewrite the ones that hold join links."""
    totals = {"scanned": 0, "to_clean": 0, "cleaned": 0, "changed_since_read": 0}
    after = ""
    while True:
        batch = scan_base_payloads(after, batch_size)
        if not batch:
            break
        for row in batch:
            totals["scanned"] += 1
            session_id = row["session_id"]
            counts = {column: _records_with_join_links(row.get(column)) for column in BASE_PAYLOAD_COLUMNS}
            if not any(counts.values()):
                continue
            totals["to_clean"] += 1
            summary = " ".join(f"{column}={count}" for column, count in counts.items())
            if not apply:
                out(f"would clean {session_id}: {summary}")
                continue
            stripped = {
                column: strip_payload_join_links(row[column]) for column, count in counts.items() if count
            }
            if rewrite_base_payloads(session_id, expected_updated_at=row["updated_at"], **stripped):
                totals["cleaned"] += 1
                out(f"cleaned {session_id}: {summary}")
            else:
                totals["changed_since_read"] += 1
                out(f"skipped {session_id}: saved again since it was read; run the script again")
        after = batch[-1]["session_id"]
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the cleaned payloads (default: dry run)")
    parser.add_argument("--batch-size", type=int, default=50, help="rows read per query (default 50)")
    parser.add_argument(
        "--allow-remote-database",
        action="store_true",
        help="run against a DATABASE_URL that is not local",
    )
    args = parser.parse_args()
    _require_database(args.allow_remote_database)

    totals = run(apply=args.apply, batch_size=args.batch_size)
    mode = "Applied" if args.apply else "Dry run"
    print(
        f"{mode}: scanned {totals['scanned']} session(s); {totals['to_clean']} held join links; "
        f"cleaned {totals['cleaned']}; skipped {totals['changed_since_read']} saved again since read."
    )
    return 2 if totals["changed_since_read"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
