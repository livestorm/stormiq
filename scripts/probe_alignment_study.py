"""Run the alignment probe on the sessions cached in the LOCAL database.

For each session it prints one row: the probe's status, the offset applied
and its ±, the best range the anchors agree on, the anchors per kind
(agreeing / found), the duration gap (session − recording) and the reasons.
Then an ear-check worksheet: for every anchor, its kind, our own ids
(`C0005`, `Q002`, `U0003`), the recording time to listen at (`mm:ss`), the
chat side's session time, the offsets it allows and whether it agrees.
Play the replay at each recording time and check that the speech matches:
that is how the provisional thresholds in `livestorm_app/record/alignment.py`
get tuned.

It never prints names, emails, message or transcript text, speaker labels
or URLs. Session ids are printed so you can find the replay; `--summary`
prints only the counts per status and per anchor kind.

Reads only. Refuses any non-local database (the same check as
`diagnose_payload_shapes.py`), and does not read `.env`:

    DATABASE_URL=postgresql://stormiq:stormiq@localhost:5433/stormiq \\
        .venv/bin/python scripts/probe_alignment_study.py [--session ID ...] [--limit N] [--include-bench] [--summary]

By default it reads every cached session except the synthetic `bench-*` ones.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnose_payload_shapes import is_local_database  # noqa: E402

from livestorm_app.db import ALL_ORGS, admin_list_sessions, fetch_cached_session, get_database_url  # noqa: E402
from livestorm_app.record.alignment import ANCHOR_KINDS, STATUSES, Alignment  # noqa: E402
from livestorm_app.record.clock import ClockBase, session_label  # noqa: E402
from livestorm_app.record.inputs import load_record_inputs  # noqa: E402

__all__ = ["is_local_database", "report", "main"]


def _require_local_database() -> None:
    if not os.environ.get("DATABASE_URL"):
        sys.exit("Set DATABASE_URL explicitly (a local database); .env is not read by this script.")
    if not is_local_database(get_database_url()):
        sys.exit("Refusing to read: the database URL does not point at a local database.")


def _seconds(value: Optional[float], *, sign: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value:+.1f} s" if sign else f"{value:.1f} s"


def _mmss(value: Optional[float]) -> str:
    """Recording time as `mm:ss` (minutes keep counting past 59)."""
    if value is None:
        return "--:--"
    whole = int(max(value, 0.0))
    return f"{whole // 60:02d}:{whole % 60:02d}"


def _kinds(result: Alignment) -> str:
    found = Counter(anchor.kind for anchor in result.anchors)
    agree = Counter(anchor.kind for anchor in result.anchors if anchor.agrees)
    parts = [f"{kind} {agree[kind]}/{found[kind]}" for kind in ANCHOR_KINDS if found[kind]]
    return ", ".join(parts) or "none"


def session_row(session_id: str, result: Alignment) -> str:
    estimate = result.estimate
    best = f"{estimate.lo:+.1f}..{estimate.hi:+.1f} s" if estimate is not None else "-"
    offset = _seconds(result.offset_s, sign=True)
    plusminus = f"±{result.plusminus_s:.1f} s" if result.plusminus_s is not None else "± -"
    gap = result.durations.get("gapS")
    reasons = ", ".join(result.reasons) or "-"
    return (
        f"{session_id}  {result.status}  offset {offset} {plusminus}  best {best}  "
        f"anchors {_kinds(result)}  gap {_seconds(gap)}  reasons {reasons}"
    )


def worksheet(session_id: str, result: Alignment, base: ClockBase) -> List[str]:
    lines = [f"{session_id} ({result.status}, offset {_seconds(result.offset_s, sign=True)})"]
    for number, anchor in enumerate(result.anchors, start=1):
        speech = ",".join(anchor.u) or "-"
        chat_side = f"{anchor.e} at {session_label(anchor.t, base) or '-'} (session)" if anchor.e else "file creation time"
        burst = f", burst of {anchor.burst}" if anchor.burst else ""
        verdict = "agrees" if anchor.agrees else "disagrees"
        lines.append(
            f"  {number}. {anchor.kind:<10} {speech:<17} listen at {_mmss(anchor.r)} (recording)  "
            f"{chat_side}{burst}  allows {anchor.lo:+.1f}..{anchor.hi:+.1f} s  {verdict}"
        )
    return lines


def report(rows: Mapping[str, Mapping[str, Any]], *, summary: bool = False) -> None:
    """Print the study for `rows` (session id → cached row)."""
    results: Dict[str, Alignment] = {}
    bases: Dict[str, ClockBase] = {}
    for session_id, row in rows.items():
        inputs = load_record_inputs({**row, "session_id": session_id})
        results[session_id] = inputs.alignment
        bases[session_id] = inputs.clock

    statuses = Counter(result.status for result in results.values())
    found = Counter(anchor.kind for result in results.values() for anchor in result.anchors)
    with_transcript = sum(1 for row in rows.values() if row.get("transcript_payload") is not None)
    print(f"Alignment probe on {len(rows)} cached sessions, {with_transcript} with a transcript (local database)")
    print("Statuses: " + ", ".join(f"{status}: {statuses[status]}" for status in STATUSES))
    print("Anchors found: " + ", ".join(f"{kind}: {found[kind]}" for kind in ANCHOR_KINDS))
    if summary:
        return

    print("\n== Sessions ==")
    for session_id, result in results.items():
        print(session_row(session_id, result))
    print("\n== Ear-check worksheet (listen at each recording time) ==")
    for session_id, result in results.items():
        if result.anchors:
            print("\n".join(worksheet(session_id, result, bases[session_id])))


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
            print("  skipped a session that is not cached", file=sys.stderr)
            continue
        rows[session_id] = row
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", action="append", help="a session id to read (repeatable); default: all")
    parser.add_argument("--limit", type=int, default=500, help="most recent sessions to read (default 500)")
    parser.add_argument("--include-bench", action="store_true", help="also read the synthetic bench-* sessions")
    parser.add_argument("--summary", action="store_true", help="print the counts only, no session ids")
    args = parser.parse_args()
    _require_local_database()
    report(_load(_session_ids(args)), summary=args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
