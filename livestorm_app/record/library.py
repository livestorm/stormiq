"""One session as a row of the Sessions library (proposal §3.1): its shape
(the sparkline), the audience's numbers, the questions still open, and the
lead finding, from what the session's Report already computes. Nothing here
is a new measure: each number is a measure of `record.measures` as the
Report shows it, the open questions the question desk's count, the lead
finding the first one `record.findings` ranks.

- **The sparkline.** The audience's chat per minute (the Pulse's
  `audienceChat` lane), summed into at most `SPARK_POINTS` buckets, so a
  two-hour session draws as many points as a short one.
- **Numbers.** Attendees (`m.audience_attendees`), wrote in chat or Q&A
  (`m.wrote_rate`) and questions (`m.questions_total`), each only when the
  Report shows its value (`SHOWN_STATES`), with `estimated` when it marks
  it so; else None.
- **Still open.** Open, and covered in the talk but not confirmed, as
  Follow-up counts them.
- **The lead finding.** `found[0]` as the findings route sends it (the page
  words it with the Report's templates), only while the session was read as
  it is now.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from livestorm_app.record.digest_brief import SHOWN_STATES

LIBRARY_VERSION = 1
SPARK_POINTS = 48


def sparkline(minutes: Sequence[Mapping[str, Any]], points: int = SPARK_POINTS) -> List[int]:
    """The audience's messages per minute, summed into at most `points`
    buckets of whole minutes, in order."""
    counts = [int(m.get("audienceChat") or 0) for m in minutes if isinstance(m, Mapping)]
    if len(counts) <= points:
        return counts
    size = math.ceil(len(counts) / points)
    return [sum(counts[start : start + size]) for start in range(0, len(counts), size)]


def _shown(measures: Mapping[str, Any], measure_id: str) -> Optional[Dict[str, Any]]:
    measure = measures.get(measure_id)
    if not isinstance(measure, Mapping) or measure.get("state") not in SHOWN_STATES:
        return None
    value = measure.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return {"value": value, "estimated": measure.get("state") != "ok"}


def _count(value: Any) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def library_row(
    record: Mapping[str, Any],
    findings: Mapping[str, Any],
    desk_summary: Mapping[str, Any],
) -> Dict[str, Any]:
    """The library's row for one session: `{version, sparkline, peak,
    attendees, wrote, questions, stillOpen, lead, analysed}`. `record` is the
    record block (`record.assemble.assemble_record(...)["record"]`),
    `findings` the findings route's body (with its `state`), `desk_summary`
    the question desk's `summary`."""
    minutes = [m for m in (record.get("pulse") or {}).get("minutes") or [] if isinstance(m, Mapping)]
    measures = record.get("measures") or {}
    busiest = max(minutes, key=lambda m: int(m.get("audienceChat") or 0), default=None)
    peak = (
        {"minute": int(busiest.get("minute") or 0), "messages": int(busiest.get("audienceChat") or 0)}
        if busiest is not None and int(busiest.get("audienceChat") or 0) > 0
        else None
    )
    analysed = findings.get("state") == "ready"
    found = findings.get("found") or []
    return {
        "version": LIBRARY_VERSION,
        "sparkline": sparkline(minutes),
        "peak": peak,
        "attendees": _shown(measures, "m.audience_attendees"),
        "wrote": _shown(measures, "m.wrote_rate"),
        "questions": _shown(measures, "m.questions_total"),
        "stillOpen": _count(desk_summary.get("open")) + _count(desk_summary.get("covered")),
        "lead": found[0] if analysed and found else None,
        "analysed": analysed,
    }
