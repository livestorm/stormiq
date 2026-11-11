"""Before you share: the figures said on stage that the host should check
before an AI output goes out, as questions with one-click answers.

The understanding job flags figures (`understanding._figures`):

- `acronym`: a confusable acronym (NPS/NRR, ARR/MRR, CSAT/CSM) in words the
  transcript is unsure of. One check per figure: "Was it NPS or NRR?",
  answered with either acronym, or "neither" (leave the figure out).
- `two_values`: the same metric said with two values. One check per
  metric: "Churn: which value is right?", answered with one of the values,
  "both" (they measure different things) or "neither".

A check's `key` names it by what was said, not by the figure's id (which is
renumbered on every reading): `acronym:<utterance id>:<the span, plain>` or
`values:<the metric, plain>`. The host's choice is saved under it
(`db.save_figure_check`) and read back only while the understanding is
current, as every reading of it is. Choices are `options[].value`.

`figure_notes` says, per figure, what the Session analysis's brief writes
next to it: the host's answer, or "not checked" while unanswered.

Pure: no database, network or model call.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

ACRONYM = "acronym"
TWO_VALUES = "two_values"
NEITHER = "neither"
BOTH = "both"
MAX_KEY_CHARS = 200

_WHITESPACE = re.compile(r"\s+")


def _plain(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    value = "".join(ch if ch.isalnum() else " " for ch in value)
    return _WHITESPACE.sub(" ", value).strip()


def _figures(result: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [f for f in result.get("figures") or [] if isinstance(f, Mapping) and f.get("span")]


def acronym_key(figure: Mapping[str, Any]) -> str:
    return f"{ACRONYM}:{figure.get('utterance') or ''}:{_plain(figure.get('span'))}"[:MAX_KEY_CHARS]


def values_key(metric: Any) -> str:
    return f"values:{_plain(metric)}"[:MAX_KEY_CHARS]


def _moment(figure: Mapping[str, Any]) -> Dict[str, Any]:
    return {"utterance": figure.get("utterance"), "t": figure.get("t"), "quote": str(figure.get("span") or "")}


def figure_checks(result: Mapping[str, Any], saved: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The checks for a current understanding result, in the talk's order:
    `[{key, kind, question, moments: [{utterance, t, quote}], options:
    [{value, label}], choice}]`. `choice` is the saved value while it is
    still one of the options, else None."""
    checks: List[Tuple[int, Dict[str, Any]]] = []
    figures = _figures(result)
    order = {id(f): index for index, f in enumerate(figures)}
    for figure in figures:
        if ACRONYM not in (figure.get("flags") or []):
            continue
        pairs = [(said, alt) for said, alt in (figure.get("confusable") or {}).items() if said and alt]
        if not pairs:
            continue
        said, alt = pairs[0]
        checks.append(
            (
                order[id(figure)],
                {
                    "key": acronym_key(figure),
                    "kind": ACRONYM,
                    "question": f"Was it {said} or {alt}?",
                    "moments": [_moment(figure)],
                    "options": [
                        {"value": said, "label": said},
                        {"value": alt, "label": alt},
                        {"value": NEITHER, "label": "Neither: leave it out"},
                    ],
                },
            )
        )
    by_metric: Dict[str, List[Mapping[str, Any]]] = {}
    for figure in figures:
        if TWO_VALUES in (figure.get("flags") or []) and figure.get("metric"):
            by_metric.setdefault(_plain(figure["metric"]), []).append(figure)
    for group in by_metric.values():
        values: List[str] = []
        for figure in group:
            value = str(figure.get("value") or figure.get("span") or "").strip()
            if value and value not in values:
                values.append(value)
        if len(values) < 2:
            continue
        metric = str(group[0]["metric"])
        checks.append(
            (
                min(order[id(f)] for f in group),
                {
                    "key": values_key(metric),
                    "kind": TWO_VALUES,
                    "question": f"{metric[:1].upper()}{metric[1:]}: which value is right?",
                    "moments": [_moment(f) for f in group],
                    "options": [{"value": v, "label": v} for v in values]
                    + [
                        {"value": BOTH, "label": "Both: they measure different things"},
                        {"value": NEITHER, "label": "Neither: leave it out"},
                    ],
                },
            )
        )
    out = []
    for _, check in sorted(checks, key=lambda item: item[0]):
        choice = (saved.get(check["key"]) or {}).get("choice")
        check["choice"] = choice if choice in {o["value"] for o in check["options"]} else None
        out.append(check)
    return out


def find_check(checks: Sequence[Mapping[str, Any]], key: str) -> Optional[Mapping[str, Any]]:
    return next((check for check in checks if check["key"] == key), None)


def _answer_words(check: Mapping[str, Any]) -> str:
    choice = check.get("choice")
    if choice == NEITHER:
        return "checked by the host: leave it out"
    if choice == BOTH:
        return "checked by the host: both values are right, they measure different things"
    if check["kind"] == ACRONYM:
        return f"checked by the host: it was {choice}"
    return f"checked by the host: the right value is {choice}"


def figure_notes(result: Mapping[str, Any], saved: Mapping[str, Mapping[str, Any]]) -> Dict[Tuple[str, str], str]:
    """What the Session analysis's brief notes next to each flagged figure,
    by `(utterance id, span)`: the host's answer, or that it is not checked
    (then the figure must not be stated as a fact)."""
    notes: Dict[Tuple[str, str], List[str]] = {}
    for check in figure_checks(result, saved):
        words = _answer_words(check) if check["choice"] else "not checked by the host: do not state it as a fact"
        if check["kind"] == TWO_VALUES and not check["choice"]:
            words = "the same metric was said with two values, not checked by the host: do not state it as a fact"
        if check["kind"] == ACRONYM and not check["choice"]:
            words = "its acronym is unclear in the recording, not checked by the host: do not state it as a fact"
        for moment in check["moments"]:
            notes.setdefault((str(moment["utterance"]), moment["quote"]), []).append(words)
    return {key: "; ".join(words) for key, words in notes.items()}


def unchecked_count(checks: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for check in checks if not check.get("choice"))
