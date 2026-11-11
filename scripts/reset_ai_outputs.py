"""Remove every AI output, so users regenerate them with the current models
and formats (`db.reset_ai_outputs`).

What goes:
    - The AI bundles on each session: session analysis, deep analysis,
      repurposed content, Smart Recap (with Surprise me's take), FAQ,
      decisions, team digest, attendee and no-show follow-ups.
    - Their earlier versions (`output_versions`), the chat labels, the
      understanding (chapters, located answers, figures), the saved FAQ
      edits, Surprise me's spoken scripts, voice clips, timelines, shot
      lists, pictures and videos.
    - The desk's decisions on answers the AI located in the talk
      ("Confirm" / "Not this"); the host's own replies stay.
    - With `--include-covers`, the cover images too.

What stays: each session's fetched data (details, chat, questions,
people), its transcript (Gladia isn't paid again), the host's replies and
figure checks, sign-ins, admins, settings (models, voices, which
organizations have Surprise me) and `llm_usage`.

How it works:
    - Dry run by default: it counts what would go, and writes nothing.
      `--apply` removes it, in one transaction: all or nothing.
    - `updated_at` is left alone, so the session library keeps its order.
    - Prints counts only, never content.
    - Take a database backup first: this can't be undone otherwise.

Usage (`.env` is not read: set DATABASE_URL explicitly):
    DATABASE_URL=postgresql://stormiq:stormiq@localhost:5433/stormiq \\
        python scripts/reset_ai_outputs.py                       # dry run
    DATABASE_URL=... python scripts/reset_ai_outputs.py --apply

It refuses a database that isn't local unless `--allow-remote-database` is
also passed (on Render's shell, the production database is remote to it).
Run it locally first.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable, Dict
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from livestorm_app.db import count_ai_outputs, get_database_url, reset_ai_outputs  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}


def _require_database(allow_remote: bool) -> None:
    if not os.environ.get("DATABASE_URL"):
        sys.exit("Set DATABASE_URL explicitly; .env is not read by this script.")
    host = urlparse(get_database_url()).hostname or ""
    if host not in LOCAL_HOSTS and not allow_remote:
        sys.exit("Refusing to run: DATABASE_URL is not a local database. Pass --allow-remote-database to run it anyway.")


def run(*, apply: bool, include_covers: bool = False, out: Callable[[str], None] = print) -> Dict[str, int]:
    """Count what goes and, with `apply`, remove it. Returns the counts."""
    counts = count_ai_outputs(include_covers=include_covers)
    width = max(len(key) for key in counts)
    out("Would remove:" if not apply else "Removing:")
    for key, value in counts.items():
        out(f"  {key.ljust(width)}  {value}")
    if not apply:
        out("Dry run: nothing was changed. Pass --apply to remove these.")
        return counts
    done = reset_ai_outputs(include_covers=include_covers)
    out(f"Done: {sum(done.values())} rows changed in one transaction.")
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="remove the AI outputs (default: dry run)")
    parser.add_argument("--include-covers", action="store_true", help="remove the cover images too")
    parser.add_argument("--allow-remote-database", action="store_true", help="allow a database that isn't local")
    args = parser.parse_args()
    _require_database(args.allow_remote_database)
    run(apply=args.apply, include_covers=args.include_covers)


if __name__ == "__main__":
    main()
