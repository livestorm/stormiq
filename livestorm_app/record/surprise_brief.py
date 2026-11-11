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
  number said aloud counts too: the transcript often spells it ("fifteen",
  "vingt-cinq"), the take writes "15" (`spoken_numbers`, English and
  French). A small count (0 to 10, no "%") is free: "for the 3rd time" is a
  joke's rhythm, not a statistic. A percentage must match one given.
  Anything else raises `SurpriseRejected`, which names the kind of figure,
  never the text.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence, Set

from livestorm_app.record.digest_brief import chapter_lines, figures, moment_lines, vital_lines
from livestorm_app.record.session_brief import clock

SURPRISE_BRIEF_VERSION = 1
FREE_COUNT = 10


# Number words, English and French, by value. Scales multiply what comes
# before them ("two hundred", "trois mille"); "quatre-vingt" is 80.
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
}
_SCALES = {"hundred": 100, "cent": 100, "cents": 100, "thousand": 1000, "mille": 1000, "million": 1_000_000, "millions": 1_000_000}
_JOINERS = {"and", "et"}
# A word, or a mark that ends a number (anything but a letter, a space or a
# hyphen: "soixante-dix, trois mille" is two numbers).
_WORD = re.compile(r"[a-zà-ÿ]+|[^\sa-zà-ÿ-]")


def spoken_numbers(text: str) -> Set[str]:
    """The numbers spelled out in `text` ("twenty-five", "two hundred",
    "quatre-vingt-dix"), as digits ("25", "200", "90"). Each run of number
    words is one number; any other word ends it."""
    found: Set[str] = set()
    total = current = 0
    previous: Optional[int] = None
    active = False

    def close() -> None:
        nonlocal total, current, active, previous
        if active:
            found.add(str(total + current))
        total = current = 0
        active = False
        previous = None

    last = ""
    for word in _WORD.findall(str(text or "").lower()):
        if word == "cent" and last == "pour":  # "pour cent" is "percent", not 100
            close()
            last = word
            continue
        last = word
        if word in _UNITS:
            value = _UNITS[word]
            if value == 20 and previous == 4:  # quatre-vingt
                current += 80 - 4
            else:
                current += value
            previous, active = value, True
        elif word in _SCALES and (active or word in ("hundred", "thousand", "cent", "mille")):
            scale = _SCALES[word]
            if scale == 100:
                current = max(current, 1) * 100
            else:
                total += max(current, 1) * scale
                current = 0
            previous, active = scale, True
        elif word in _JOINERS and active:
            continue
        else:
            close()
    close()
    return found


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
    plain = {value for value, _ in allowed_numbers} | spoken_numbers(source)
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
