"""Who wrote each chat message, question and written answer, as roster people.

`resolve_authors(chat_payload, questions_payload, roster)` gives an
`AuthorIndex`: for each author of the chat, the questions and the written
answers, the person it resolves to (if any) and a role, plus how much of each
source resolved (`AuthorCoverage`).

An author resolves only when its people id is the id of a person in the
roster. Nothing else counts: an author is never matched by name, by message
counts or by order. An author that doesn't resolve stays unresolved, and its
items count as unresolved.

Each item's people id, read by `evidence.chat_items` and
`evidence.question_items` (so items and ids mean the same here as in
`evidence.audience_events`):
- a chat message: its people link, `relationships.people.data.id`
  (`evidence.people_link`), sent when the chat is fetched with
  `include=people`. The message's `author_id` attribute is another id and is
  never read as a people id. A chat cached before StormIQ asked for the link
  has none, so it reads unresolved; nothing fetches a cached chat again;
- a question: `question_author_id`; a written answer: `response_author_id`.

Roles:
- a resolved author has the person's role, raised by what they did
  (`roster.RoleSignals`, `roster.classify_role`): a chat message of theirs
  flagged `from_team_member` raises it to team, one flagged
  `from_guest_speaker` to guest speaker, and writing an answer to team (an
  answer is a team action). `roster.escalate_roles` raises the roster the
  same way with `AuthorIndex.role_signals`. On an internal session nothing
  is raised: roles stay Livestorm's;
- an unresolved chat author has the role its messages' flags give: team when
  one is `from_team_member`, then guest speaker when one is
  `from_guest_speaker`, then audience;
- an unresolved asker is audience and an unresolved responder team.
  `evidence.audience_events` counts every written answer as team, whatever
  its responder's role.

Pure functions: no database, network or web framework.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, NamedTuple, Optional, Tuple

from livestorm_app.record.evidence import ANSWER, CHAT, QUESTION, chat_items, question_items
from livestorm_app.record.roster import (
    AUDIENCE,
    GUEST_SPEAKER,
    NO_ROLE_SIGNALS,
    PARTIAL,
    ROLE_BY_DEFAULT,
    TEAM,
    RoleSignals,
    Roster,
    classify_role,
)

KINDS = (CHAT, QUESTION, ANSWER)

# Coverage of one source.
RESOLVED = "resolved"  # every item's author resolved
UNRESOLVED = "unresolved"  # no item's author resolved
EMPTY = "empty"  # no item
# PARTIAL ("partial", from roster): some did.

# Where an unresolved chat author's role comes from.
ROLE_FROM_MESSAGE_FLAGS = "message_flags"


class Author(NamedTuple):
    """One author of one source, and the person it resolves to."""

    author_id: str  # the people id the items carry
    items: int  # messages, questions or written answers with this author
    person_id: Optional[str]  # None when unresolved
    person_ref: Optional[str]  # the person's `P-` ref; None when unresolved
    role: str  # team | guest_speaker | audience
    role_source: str  # the person's (roster.classify_role), message_flags or default


@dataclass(frozen=True)
class AuthorCoverage:
    """How much of one source resolved to people.

    - `items`: messages, questions or written answers, counted as
      `evidence.audience_events` counts them (a repeated Livestorm id once);
    - `without_author`: items with no people id, such as the messages of a
      chat cached before the people link was fetched; they never resolve;
    - `resolved_items`: items whose author resolved;
    - `authors` and `resolved_authors`: distinct people ids, and how many
      resolved;
    - `resolved_share`: `resolved_authors / authors`; None with no author;
    - `state`: `empty` with no item, `resolved` when every item resolved,
      `unresolved` when none did, `partial` otherwise.
    """

    items: int
    without_author: int
    resolved_items: int
    authors: int
    resolved_authors: int
    resolved_share: Optional[float]
    state: str


@dataclass(frozen=True)
class AuthorIndex:
    """Authors by source (`chat`, `question`, `answer`) and people id.

    `role_signals` maps the id of each resolved person whose role can rise
    to their `RoleSignals`: what `roster.escalate_roles` raises roles with.
    It is empty on an internal session, whose roles never change.
    """

    authors: Dict[str, Dict[str, Author]]
    coverage: Dict[str, AuthorCoverage]
    role_signals: Dict[str, RoleSignals]

    def author(self, kind: str, author_id: Optional[str]) -> Optional[Author]:
        """The author of one item, or None when it has no people id."""
        if author_id is None:
            return None
        return self.authors.get(kind, {}).get(author_id)

    def contributors(self) -> FrozenSet[str]:
        """Person ids of the resolved chat writers and askers. One person who
        chats and asks is one contributor."""
        return frozenset(
            author.person_id
            for kind in (CHAT, QUESTION)
            for author in self.authors.get(kind, {}).values()
            if author.person_id is not None
        )


class _Item(NamedTuple):
    author_id: Optional[str]
    team_flag: bool = False
    guest_flag: bool = False


def _source_items(chat_payload: Any, questions_payload: Any) -> Dict[str, List[_Item]]:
    """Each source's items with their people id, from `evidence`'s readers."""
    questions = question_items(questions_payload)
    return {
        CHAT: [_Item(item.author_id, item.team_flag, item.guest_flag) for item in chat_items(chat_payload)],
        QUESTION: [_Item(item.asker_id) for item in questions],
        ANSWER: [_Item(item.responder_id) for item in questions if item.response],
    }


def _coverage(items: List[_Item], authors: Dict[str, Author]) -> AuthorCoverage:
    without_author = sum(1 for item in items if item.author_id is None)
    resolved_items = sum(1 for item in items if item.author_id is not None and authors[item.author_id].person_id is not None)
    resolved_authors = sum(1 for author in authors.values() if author.person_id is not None)
    if not items:
        state = EMPTY
    elif resolved_items == len(items):
        state = RESOLVED
    elif resolved_items == 0:
        state = UNRESOLVED
    else:
        state = PARTIAL
    return AuthorCoverage(
        items=len(items),
        without_author=without_author,
        resolved_items=resolved_items,
        authors=len(authors),
        resolved_authors=resolved_authors,
        resolved_share=resolved_authors / len(authors) if authors else None,
        state=state,
    )


def _role_signals(items: Dict[str, List[_Item]], known: Dict[str, Any], internal: bool) -> Dict[str, RoleSignals]:
    """What each resolved person did that can raise their role. Nothing on an
    internal session."""
    if internal:
        return {}
    team = {item.author_id for item in items[CHAT] if item.team_flag and item.author_id in known}
    guest = {item.author_id for item in items[CHAT] if item.guest_flag and item.author_id in known}
    answered = {item.author_id for item in items[ANSWER] if item.author_id in known}
    return {
        person_id: RoleSignals(
            team_message=person_id in team, guest_message=person_id in guest, wrote_answer=person_id in answered
        )
        for person_id in sorted(team | guest | answered)
    }


def _unresolved_role(kind: str, team_flag: bool, guest_flag: bool) -> Tuple[str, str]:
    if kind == CHAT:
        if team_flag:
            return TEAM, ROLE_FROM_MESSAGE_FLAGS
        if guest_flag:
            return GUEST_SPEAKER, ROLE_FROM_MESSAGE_FLAGS
        return AUDIENCE, ROLE_FROM_MESSAGE_FLAGS
    return (TEAM if kind == ANSWER else AUDIENCE), ROLE_BY_DEFAULT


def resolve_authors(chat_payload: Any, questions_payload: Any, roster: Roster) -> AuthorIndex:
    """Resolve every chat, question and written-answer author to a roster
    person, by people id only (see the module docstring)."""
    people = roster.people
    known: Dict[str, Tuple[str, Any, Any]] = {
        person_id: (ref, livestorm_role, guest)
        for person_id, ref, livestorm_role, guest in zip(
            people["person_id"], people["person_ref"], people["livestorm_role"], people["is_guest_speaker"], strict=True
        )
    }
    items = _source_items(chat_payload, questions_payload)
    role_signals = _role_signals(items, known, roster.meta.internal)

    # Each chat author's message flags, for the role of one that doesn't resolve.
    flags_by_author: Dict[str, Tuple[bool, bool]] = {}
    for item in items[CHAT]:
        if item.author_id is not None:
            team, guest = flags_by_author.get(item.author_id, (False, False))
            flags_by_author[item.author_id] = (team or item.team_flag, guest or item.guest_flag)

    authors: Dict[str, Dict[str, Author]] = {}
    for kind in KINDS:
        counts = Counter(item.author_id for item in items[kind] if item.author_id is not None)
        by_id: Dict[str, Author] = {}
        for author_id, count in counts.items():
            person = known.get(author_id)
            if person is None:
                role, source = _unresolved_role(kind, *flags_by_author.get(author_id, (False, False)))
                by_id[author_id] = Author(author_id, count, None, None, role, source)
                continue
            ref, livestorm_role, guest = person
            role, source = classify_role(
                {"livestorm_role": livestorm_role, "is_guest_speaker": guest},
                role_signals.get(author_id, NO_ROLE_SIGNALS),
            )
            by_id[author_id] = Author(author_id, count, author_id, ref, role, source)
        authors[kind] = by_id

    return AuthorIndex(
        authors=authors,
        coverage={kind: _coverage(items[kind], authors[kind]) for kind in KINDS},
        role_signals=role_signals,
    )
