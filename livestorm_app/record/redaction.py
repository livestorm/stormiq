"""What attendees wrote or said, redacted before it reaches a writer of a
public format (the FAQ, Smart Recap, the summary, blog post, follow-up
email and social posts), and the output checked after.

The owner's decision (November 2026): people's names, emails, phone numbers
and links go; companies and products stay (a product question loses its
point without them). Redaction by construction: the writer never receives
them, so no prompt has to be obeyed for them to stay out.

- **Whose names** (`public_redactor`). The audience's, from the session's
  people (`record.roster`): on a public session every person whose role is
  audience, on an internal one everyone. Speakers keep theirs: a word of a
  speaker's name saved on the Transcript tab is never hidden.
- **How** (`Redactor.redact`). An email, a link and a phone number (8
  digits or more) become `[email]`, `[link]` and `[phone]`. A full name
  becomes `[name]` whatever its case; a single word of a name (3 letters or
  more) only when it is written with a capital, and not at the start of a
  sentence when it is also a common word ("Will it work?"). A name written
  all in lower case ("hi im marie") is left to the writer's prompt, which
  drops names: the check below still catches a full name.
- **The check** (`Redactor.leaks`). What a writer returned may hold no
  email, phone number, audience member's full name or placeholder; each
  kind found is named, never the text itself.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from livestorm_app.record.roster import AUDIENCE, Roster

NAME = "[name]"
EMAIL = "[email]"
PHONE = "[phone]"
LINK = "[link]"
PLACEHOLDERS = (NAME, EMAIL, PHONE, LINK)
MIN_NAME_WORD = 3
MIN_PHONE_DIGITS = 8

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LINK = re.compile(r"(?:\bhttps?://|\bwww\.)[^\s<>\"']*[^\s<>\"'.,;:!?)\]]")
# Digits in groups split by a space, dot, dash or parentheses, an optional
# leading "+": "+33 6 12 34 56 78", "(555) 123-4567". Counted after the match.
_PHONE = re.compile(r"(?<![\w+])\+?\(?\d[\d\s().-]{6,}\d(?![\w])")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]\s+|\n\s*)$")
_REPEATS = re.compile(r"\[name\](?:\s+\[name\])+")

# Name words that are also common words at the start of a sentence, in
# English and French. Only consulted for a capitalised word that begins one.
COMMON_WORDS = frozenset(
    """
    will may mark grant hope bill frank jack ray june april august rose faith joy pat sunny dean chase
    earl gene long young king white brown green black wood hill little hall ward price bell cook park
    rich best strong lane field page stone day love bird fox reed wells banks cross small gray grey short
    moon bond ball snow major noble sharp story case
    pierre claire marine constance aurore olive prudence petit grand blanc noir roux fort fortune
    """.split()
)


def _words(name: str) -> List[str]:
    return [word for word in re.split(r"[\s\-]+", name.strip()) if len(word) >= MIN_NAME_WORD]


# Not a phone number: a date ("2026-11-11", "11.11.2026") or a grouped
# number ("10.000.000", "12 500 000").
_NOT_PHONE = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{4}|\d{1,3}(?:[., ]\d{3})+")


def _phone(match: re.Match) -> bool:
    found = match.group(0).strip()
    if _NOT_PHONE.fullmatch(found):
        return False
    return sum(char.isdigit() for char in found) >= MIN_PHONE_DIGITS


@dataclass(frozen=True)
class Redactor:
    """The names to hide: `full_names` as written ("Marie Dupont") and their
    `words` ("Marie", "Dupont"), each set without the speakers' words."""

    full_names: Tuple[str, ...] = ()
    words: Tuple[str, ...] = ()

    def redact(self, text: Any) -> str:
        """`text` with emails, links, phone numbers and the hidden names
        replaced by their placeholder."""
        out = str(text or "")
        if not out:
            return out
        out = _EMAIL.sub(EMAIL, out)
        out = _LINK.sub(LINK, out)
        out = _PHONE.sub(lambda m: PHONE if _phone(m) else m.group(0), out)
        full, words = self._patterns()
        if full is not None:
            out = full.sub(NAME, out)
        if words is not None:
            out = words.sub(self._word, out)
        return _REPEATS.sub(NAME, out)

    def _patterns(self) -> Tuple[Optional[re.Pattern], Optional[re.Pattern]]:
        """One pattern for the full names and one for the words, the longest
        first: a session's thousands of names cost one pass each."""
        full = (
            re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in self.full_names) + r")(?!\w)", re.IGNORECASE)
            if self.full_names
            else None
        )
        variants = [v for word in self.words for v in (re.escape(word), re.escape(word.upper()))]
        words = re.compile(r"(?<!\w)(?:" + "|".join(variants) + r")(?!\w)") if variants else None
        return full, words

    @staticmethod
    def _word(match: re.Match) -> str:
        word = match.group(0)
        start = _SENTENCE_START.search(match.string[: match.start()]) is not None
        if start and word.casefold() in COMMON_WORDS:
            return word
        return NAME

    def leaks(self, text: Any) -> List[str]:
        """The kinds of personal detail `text` holds, in a fixed order:
        "an email", "a phone number", "an attendee's name", "a placeholder".
        Empty when it holds none."""
        body = str(text or "")
        found = []
        if _EMAIL.search(body):
            found.append("an email")
        if any(_phone(match) for match in _PHONE.finditer(body)):
            found.append("a phone number")
        full, _ = self._patterns()
        if full is not None and full.search(body):
            found.append("an attendee's name")
        if any(placeholder in body for placeholder in PLACEHOLDERS):
            found.append("a placeholder")
        return found


def redactor_for(hidden_names: Iterable[Any], kept_names: Iterable[Any] = ()) -> Redactor:
    """A `Redactor` for these names, keeping every word of `kept_names`."""
    kept = {word.casefold() for name in kept_names if isinstance(name, str) for word in _words(name)}
    full: List[str] = []
    words: List[str] = []
    for name in hidden_names:
        if not isinstance(name, str) or not name.strip():
            continue
        name_words = [word for word in _words(name) if word.casefold() not in kept]
        joined = " ".join(name.split())
        if len(name_words) >= 2 and joined not in full:
            full.append(joined)
        for word in name_words:
            capital = word[:1].upper() + word[1:]
            if capital not in words:
                words.append(capital)
    # The longest first, so "Marie-Claire" goes before "Marie".
    return Redactor(
        full_names=tuple(sorted(full, key=lambda n: (-len(n), n))),
        words=tuple(sorted(words, key=lambda w: (-len(w), w))),
    )


def public_redactor(roster: Optional[Roster], kept_names: Sequence[Any] = ()) -> Redactor:
    """The session's `Redactor`: the audience's names (everyone's on an
    internal session), without the words of `kept_names`, the speakers'
    names saved on the Transcript tab. Without people, contacts only."""
    if roster is None:
        return redactor_for((), kept_names)
    people = roster.people
    everyone = bool(roster.meta.internal)
    hidden = [
        name
        for name, role in zip(people["display_name"], people["role"], strict=True)
        if isinstance(name, str) and (everyone or role == AUDIENCE)
    ]
    return redactor_for(hidden, kept_names)


def saved_speaker_names(cached: Mapping[str, Any]) -> List[str]:
    """The speakers' names the host saved on the Transcript tab."""
    names = cached.get("transcript_speaker_names")
    return [str(name) for name in names.values() if name] if isinstance(names, Mapping) else []
