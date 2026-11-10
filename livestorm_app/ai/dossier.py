"""The session dossier's type, and what keeps its bytes the same.

A dossier is one session's record written out as text, once, for the v1
jobs that read it (`ai/request.py`, the `dossier` layout). Several calls
share it as their cached prefix, and Anthropic's prompt cache is a prefix
match on bytes: one byte that differs between two builds of the same record
and every later call pays for the whole dossier again, silently. So a
dossier is built only from the helpers here:

- `stable_dumps`: JSON with sorted keys, no spaces and no ASCII escapes;
- `fixed_number`: a number with a fixed count of decimals, never an
  exponent, a float's repr or a negative zero;
- `normalize_text`: one newline style, NFC, no trailing spaces, no blank
  lines at either end;
- `make_dossier`: the normalized text with its hash.

The rules these helpers can't enforce are the builder's to keep: no
wall-clock time, request id or randomness in the text; items in id order;
prompt files read once and stripped (`ai/prompts.py`).

A dossier names people only by their `P-` ref (`record.roster.prompt_view`),
never by name, email or company (docs/security.md). This module ships the
type and the helpers; the builder that writes a record as a dossier comes
with the job that reads it.

Pure: no database, network or model call.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Union


@dataclass(frozen=True)
class Dossier:
    """One session's record as text. Build it with `make_dossier`, so `text`
    is normalized and `sha256` is its hash."""

    text: str
    record_version: str  # the record the text was written from
    sha256: str  # of `text` as UTF-8; stored on every usage row of a call that read it


def stable_dumps(value: Any) -> str:
    """`value` as JSON whose bytes depend only on its content: keys sorted,
    no spaces, characters written as themselves. NaN and infinities are
    refused (ValueError): they aren't JSON, and `repr` would write them."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fixed_number(value: Union[int, float], places: int = 2) -> str:
    """A number with exactly `places` decimals, for example `0.93`: never an
    exponent or a float's full repr, and `-0.00` reads `0.00`. Raises
    ValueError for a value that isn't a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("fixed_number takes a finite number.")
    if places < 0:
        raise ValueError("fixed_number takes a non-negative count of decimals.")
    text = f"{value:.{places}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def normalize_text(text: str) -> str:
    """`text` with one newline style (`\\n`), composed characters (NFC), no
    spaces or tabs at the end of a line and no blank lines at either end."""
    if not isinstance(text, str):
        raise TypeError("normalize_text takes a string.")
    unified = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip(" \t") for line in unified.split("\n")).strip("\n")


def make_dossier(parts: Union[str, Iterable[str]], *, record_version: str) -> Dossier:
    """The dossier of `parts` (one text, or its sections in order, joined by a
    blank line): normalized (`normalize_text`), with the SHA-256 of its UTF-8
    bytes. An empty dossier is refused: a call must never read one."""
    sections = [parts] if isinstance(parts, str) else list(parts)
    text = normalize_text("\n\n".join(normalize_text(section) for section in sections))
    if not text:
        raise ValueError("A dossier needs text.")
    version = str(record_version or "").strip()
    if not version:
        raise ValueError("A dossier needs the version of the record it was written from.")
    return Dossier(text=text, record_version=version, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
