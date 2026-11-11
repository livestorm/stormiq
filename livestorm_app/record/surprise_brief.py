"""Surprise Me! (the Smart Recap's `surprise` tone) grounded in the record
(proposal §10): what StormIQ measured, given next to the transcript, and the
take checked against both before it is saved.

- **The facts** (`build_surprise_brief`): the session's title and length;
  the vitals the Report shows (`digest_brief.vital_lines`, the estimated
  ones marked); the key moments with what was being said
  (`digest_brief.moment_lines`, never who); the chapters placed on the
  session clock. Attendees' details are redacted after, with the
  transcript (`record.redaction`).
- **The check** (`check_surprise`): every clock time and every number in
  the take must be in the facts or the transcript, written the same way. A
  small count (0 to 10, no "%") is free: "for the 3rd time" is a joke's
  rhythm, not a statistic. A percentage must match one given. Anything else
  raises `SurpriseRejected`, which names the kind of figure, never the text.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from livestorm_app.record.digest_brief import chapter_lines, figures, moment_lines, vital_lines
from livestorm_app.record.session_brief import clock

SURPRISE_BRIEF_VERSION = 1
FREE_COUNT = 10


class SurpriseRejected(ValueError):
    """The take failed the check; the message says which kind of figure."""


def build_surprise_brief(
    *,
    title: str,
    length_s: Optional[float],
    measures: Mapping[str, Any],
    moments: Mapping[str, Any],
    chapters: Sequence[Mapping[str, Any]],
) -> str:
    """The facts a surprise take may use, as plain text: title, length,
    vitals, key moments and chapters, each part "none" when empty."""
    parts = [
        "# What StormIQ measured in this session",
        f"version: {SURPRISE_BRIEF_VERSION}",
        f"title: {' '.join(str(title or 'Untitled session').split())}",
        f"length: {clock(length_s) if length_s else 'unknown'}",
        "times: minutes:seconds from the start of the session",
        "",
        "## Vitals (quote them as written, never compute others)",
        "\n".join(vital_lines(measures, None)) or "- none measured",
        "",
        "## When the chat was busiest",
        "\n".join(moment_lines(moments)) or "- none found",
        "",
        "## Chapters",
        "\n".join(chapter_lines(chapters)) or "- none placed",
    ]
    return "\n".join(parts)


def check_surprise(text: str, allowed: str) -> str:
    """The take when every time and number in it is in `allowed` (the facts
    and the transcript, as sent); else `SurpriseRejected`."""
    take = str(text or "").strip()
    if not take:
        raise SurpriseRejected("is empty")
    source = "\n".join(line for line in str(allowed or "").splitlines() if not line.startswith("version:"))
    allowed_times, allowed_numbers = figures(source)
    plain = {value for value, _ in allowed_numbers}
    times, numbers = figures(take)
    if times - allowed_times:
        raise SurpriseRejected("gives a time that isn't in the session")
    for value, percent in numbers:
        if percent:
            if (value, True) not in allowed_numbers:
                raise SurpriseRejected("gives a percentage that isn't in the session")
            continue
        small = value.isdigit() and int(value) <= FREE_COUNT
        if not small and value not in plain:
            raise SurpriseRejected("gives a number that isn't in the session")
    return take
