"""Chat, question and answer authors resolved to roster people (`record/authors.py`).

Also covers the roles raised by what a person did (`roster.classify_role`,
`roster.escalate_roles`), the author columns of `evidence.audience_events`,
the loader's authors, the chat fetcher's `include=people` (on a patched
transport), and that classic reads a fetched chat as before. Pure functions
on synthetic payloads: no database, Redis or network. Every name and id below
is made up.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from livestorm_app import api_logic
from livestorm_app.ai import llm_inputs
from livestorm_app.analytics import audience
from livestorm_app.livestorm import client as livestorm_client
from livestorm_app.record import clock, evidence
from livestorm_app.record.authors import (
    EMPTY,
    RESOLVED,
    ROLE_FROM_MESSAGE_FLAGS,
    UNRESOLVED,
    AuthorIndex,
    resolve_authors,
)
from livestorm_app.record.inputs import load_record_inputs
from livestorm_app.record.roster import (
    AUDIENCE,
    GUEST_SPEAKER,
    PARTIAL,
    ROLE_BY_DEFAULT,
    ROLE_FROM_CHAT_FLAG,
    ROLE_FROM_GUEST_FLAG,
    ROLE_FROM_LIVESTORM_ROLE,
    ROLE_FROM_WRITTEN_ANSWER,
    TEAM,
    RoleSignals,
    classify_role,
    escalate_roles,
    person_ref,
    roster_from_session_payload,
)

T0 = int(datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).timestamp())
JOIN_LINK_KEYS = ("password_key", "connection_link")


def person(person_id, *, role="participant", guest=False, attended=True, first="Pat"):
    return {
        "id": person_id,
        "type": "people",
        "attributes": {
            "role": role,
            "first_name": first,
            "last_name": person_id.title(),
            "email": f"{person_id}@example.test",
            "registrant_detail": {"attended": attended, "is_guest_speaker": guest},
        },
    }


def session_payload(people, session_id="s1"):
    attributes = {"started_at": T0, "ended_at": T0 + 3600, "duration": 3600, "registrants_count": len(people)}
    return {"data": {"id": session_id, "type": "sessions", "attributes": attributes}, "included": people}


def webinar():
    """A host from the team, a guest speaker, three audience members (one
    did not attend) and a moderator registered as a participant."""
    return session_payload(
        [
            person("host", role="team_member"),
            person("guest", guest=True),
            person("aud-1", first="Bram"),
            person("aud-2", first="Cleo"),
            person("aud-3", attended=False),
            person("moderator"),
        ]
    )


def link(person_id):
    return {"people": {"data": {"type": "people", "id": person_id}}}


def chat(*messages):
    """Chat messages as the fetcher stores them.

    Each message is a people id (its people link), None (no link), or a
    (people id or None, extra attributes) pair. Each writer also gets an
    `author_id` that is not a people id, as Livestorm sends it.
    """
    chat_ids: dict = {}
    data = []
    for index, message in enumerate(messages):
        person_id, extra = message if isinstance(message, tuple) else (message, {})
        chat_id = chat_ids.setdefault(person_id, f"c-{len(chat_ids) + 1:04d}")
        attributes = {"author_id": chat_id, "text_content": "hello", "created_at": T0 + 60 * index}
        attributes.update(extra)
        record = {"id": f"m{index:03d}", "type": "chat_messages", "attributes": attributes}
        if person_id is not None:
            record["relationships"] = link(person_id)
        data.append(record)
    return {"data": data}


def cached_before_the_link(chat_payload):
    """The same chat as cached before StormIQ asked for `include=people`: no relationships."""
    old = copy.deepcopy(chat_payload)
    for record in old["data"]:
        record.pop("relationships", None)
    return old


def questions(*items):
    """Questions: (asker id, responder id or None) pairs; a responder means a written answer."""
    data = []
    for index, (asker, responder) in enumerate(items):
        attributes = {"question": "How?", "question_author_id": asker, "created_at": T0 + 30 * index}
        if responder is not None:
            attributes.update({"response": "Like this.", "response_author_id": responder, "responded_at": T0 + 30 * index + 10})
        data.append({"id": f"q{index:03d}", "type": "questions", "attributes": attributes})
    return {"data": data}


TEAM_MESSAGE = {"from_team_member": True}
GUEST_MESSAGE = {"from_guest_speaker": True}


def index_for(chat_payload=None, questions_payload=None, payload=None):
    roster = roster_from_session_payload(payload or webinar())
    return resolve_authors(chat_payload, questions_payload, roster), roster


def roles_of(roster):
    people = roster.people
    return dict(zip(people["person_id"], zip(people["role"], people["role_source"], strict=True), strict=True))


# ── Resolution ──────────────────────────────────────────────────────────────


def test_question_askers_and_responders_resolve_through_the_roster():
    index, roster = index_for(questions_payload=questions(("aud-1", "host"), ("aud-2", None), ("aud-1", "guest")))
    asker = index.author("question", "aud-1")
    assert (asker.items, asker.person_id, asker.person_ref) == (2, "aud-1", person_ref("s1", "aud-1"))
    assert (asker.role, asker.role_source) == (AUDIENCE, ROLE_BY_DEFAULT)
    assert index.author("answer", "host").role == TEAM
    question = index.coverage["question"]
    assert (question.items, question.resolved_items, question.authors, question.resolved_authors) == (3, 3, 2, 2)
    assert (question.state, question.resolved_share) == (RESOLVED, 1.0)
    assert index.coverage["answer"].state == RESOLVED and index.coverage["answer"].items == 2
    assert set(roster.people["person_id"]) >= {"aud-1", "aud-2", "host", "guest"}


def test_chat_authors_resolve_through_their_people_link():
    index, _ = index_for(chat("aud-1", "aud-1", ("host", TEAM_MESSAGE), "stranger"))
    resolved = index.author("chat", "aud-1")
    assert (resolved.person_id, resolved.person_ref, resolved.items) == ("aud-1", person_ref("s1", "aud-1"), 2)
    assert index.author("chat", "host").person_ref == person_ref("s1", "host")
    stranger = index.author("chat", "stranger")  # linked, but not in the roster
    assert (stranger.person_id, stranger.person_ref) == (None, None)
    coverage = index.coverage["chat"]
    assert (coverage.items, coverage.resolved_items, coverage.authors, coverage.resolved_authors) == (4, 3, 3, 2)
    assert (coverage.state, coverage.resolved_share) == (PARTIAL, 2 / 3)


def test_a_chat_author_id_is_never_read_as_a_people_id():
    """`author_id` holding a roster people id resolves nobody; only the link counts."""
    chat_payload = chat((None, {"author_id": "aud-1"}), ("aud-2", {"author_id": "aud-1"}))
    index, _ = index_for(chat_payload)
    assert index.author("chat", "aud-1") is None
    assert index.author("chat", "aud-2").person_id == "aud-2"
    assert index.coverage["chat"].without_author == 1
    events = evidence.audience_events(chat_payload, None, clock.clock_base(webinar()), index)
    assert events["author_id"].tolist() == [None, "aud-2"]
    assert events["author_ref"].tolist() == [None, person_ref("s1", "aud-2")]


def test_a_chat_cached_before_the_link_reads_unresolved():
    """Old cached chats have no relationships: honest coverage, no guess, no raised role."""
    fetched = chat(("aud-1", TEAM_MESSAGE), "aud-2", "aud-2", ("moderator", GUEST_MESSAGE))
    old = cached_before_the_link(fetched)
    old["data"][1]["attributes"]["author_id"] = "aud-2"  # even a people id in author_id
    index, roster = index_for(old)
    coverage = index.coverage["chat"]
    assert (coverage.items, coverage.without_author, coverage.resolved_items) == (4, 4, 0)
    assert (coverage.authors, coverage.resolved_authors, coverage.resolved_share) == (0, 0, None)
    assert coverage.state == UNRESOLVED
    assert index.authors["chat"] == {} and index.role_signals == {} and index.contributors() == frozenset()
    assert escalate_roles(roster, index.role_signals) is roster
    events = evidence.audience_events(old, None, clock.clock_base(webinar()), index)
    assert events["author_id"].isna().all() and events["author_ref"].isna().all()
    assert events["is_team"].tolist() == [True, False, False, True]  # the message flags still decide

    # Fetched again, the same chat resolves.
    assert resolve_authors(fetched, None, roster).coverage["chat"].state == RESOLVED


def test_an_unresolved_linked_author_takes_its_message_flags():
    index, _ = index_for(chat(("stranger-1", TEAM_MESSAGE), "stranger-2", ("stranger-1", GUEST_MESSAGE), ("stranger-3", {"from_guest_speaker": "true"})))
    roles = {author_id: (author.role, author.role_source) for author_id, author in index.authors["chat"].items()}
    assert roles == {
        "stranger-1": (TEAM, ROLE_FROM_MESSAGE_FLAGS),  # team wins over guest speaker
        "stranger-2": (AUDIENCE, ROLE_FROM_MESSAGE_FLAGS),
        "stranger-3": (GUEST_SPEAKER, ROLE_FROM_MESSAGE_FLAGS),
    }
    assert index.coverage["chat"].state == UNRESOLVED and index.role_signals == {}


@pytest.mark.parametrize(
    "relationships, expected",
    [
        (link("aud-1"), "aud-1"),
        (link(" aud-1 "), "aud-1"),
        ({"people": {"data": {"type": "users", "id": "aud-1"}}}, None),
        ({"people": {"data": {"id": "aud-1"}}}, None),
        ({"people": {"data": [{"type": "people", "id": "aud-1"}]}}, None),
        ({"people": {"data": None}}, None),
        ({"people": {"data": {"type": "people", "id": ""}}}, None),
        ({"people": {"data": {"type": "people", "id": True}}}, None),
        ({"author": {"data": {"type": "people", "id": "aud-1"}}}, None),
        ({}, None),
        ("junk", None),
    ],
    ids=["link", "spaces", "other-type", "no-type", "list", "null", "blank-id", "boolean-id", "other-name", "empty", "junk"],
)
def test_only_a_people_link_names_a_chat_author(relationships, expected):
    record = {"id": "m1", "type": "chat_messages", "attributes": {"author_id": "aud-1"}, "relationships": relationships}
    assert evidence.people_link(record) == expected


def test_an_author_never_resolves_by_name_email_ref_or_case():
    """Only an exact people id matches (surrounding spaces aside)."""
    ref = person_ref("s1", "aud-1")
    index, _ = index_for(chat("Bram", "aud-1@example.test", ref, "AUD-1", " aud-2 "))
    assert [author.person_id for author in index.authors["chat"].values()] == [None, None, None, None, "aud-2"]


def test_items_without_an_author_count_and_never_resolve():
    payload = chat("aud-1", None, "", "   ")
    payload["data"].append({"id": "m-x", "type": "chat_messages", "attributes": {"text_content": "no author key"}})
    index, _ = index_for(payload)
    coverage = index.coverage["chat"]
    assert (coverage.items, coverage.without_author, coverage.resolved_items, coverage.state) == (5, 4, 1, PARTIAL)
    assert (coverage.authors, coverage.resolved_authors) == (1, 1)


def test_items_count_as_the_evidence_table_counts_them():
    """A message repeated across pages counts once, and an answer needs response text."""
    payload = chat("aud-1", "stranger")
    payload["data"].append(copy.deepcopy(payload["data"][0]))
    questions_payload = questions(("aud-1", "host"))
    questions_payload["data"].append(
        {"id": "q-oral", "type": "questions", "attributes": {"question_author_id": "aud-2", "response": "  ", "response_author_id": "host"}}
    )
    index, _ = index_for(payload, questions_payload)
    events = evidence.audience_events(payload, questions_payload, clock.clock_base(webinar()), index)
    for kind in ("chat", "question", "answer"):
        assert index.coverage[kind].items == int((events["kind"] == kind).sum()), kind


@pytest.mark.parametrize("chat_payload, questions_payload", [(None, None), ({}, {}), ({"data": []}, {"data": []}), ("junk", 7)])
def test_empty_payloads_give_empty_coverage(chat_payload, questions_payload):
    index, _ = index_for(chat_payload, questions_payload)
    for kind in ("chat", "question", "answer"):
        coverage = index.coverage[kind]
        assert (coverage.items, coverage.authors, coverage.resolved_share, coverage.state) == (0, 0, None, EMPTY)
    assert index.authors == {"chat": {}, "question": {}, "answer": {}}


def test_an_empty_roster_resolves_nobody():
    index, _ = index_for(chat("aud-1"), questions(("aud-1", "host")), payload=session_payload([]))
    assert {kind: coverage.state for kind, coverage in index.coverage.items()} == {
        "chat": UNRESOLVED,
        "question": UNRESOLVED,
        "answer": UNRESOLVED,
    }
    assert index.author("question", "aud-1").role == AUDIENCE
    assert (index.author("answer", "host").role, index.author("answer", "host").role_source) == (TEAM, ROLE_BY_DEFAULT)
    assert index.role_signals == {}


def test_one_person_who_chats_and_asks_is_one_contributor():
    index, _ = index_for(chat("aud-1", "aud-1", "stranger"), questions(("aud-1", "host"), ("aud-2", None)))
    assert index.contributors() == frozenset({"aud-1", "aud-2"})  # responders and unresolved authors aren't counted


def test_resolving_does_not_change_the_payloads():
    chat_payload, questions_payload = chat(("aud-1", TEAM_MESSAGE)), questions(("aud-1", "host"))
    before = copy.deepcopy((chat_payload, questions_payload))
    index_for(chat_payload, questions_payload)
    assert (chat_payload, questions_payload) == before


def test_the_index_is_json_safe():
    index, _ = index_for(chat(("aud-1", TEAM_MESSAGE), "stranger"), questions(("aud-1", "moderator")))
    json.dumps(dataclasses.asdict(index))


# ── Roles raised by what a person did ───────────────────────────────────────


@pytest.mark.parametrize(
    "livestorm_role, guest, signals, expected",
    [
        ("participant", False, RoleSignals(team_message=True), (TEAM, ROLE_FROM_CHAT_FLAG)),
        ("participant", False, RoleSignals(guest_message=True), (GUEST_SPEAKER, ROLE_FROM_CHAT_FLAG)),
        ("participant", False, RoleSignals(wrote_answer=True), (TEAM, ROLE_FROM_WRITTEN_ANSWER)),
        ("participant", False, RoleSignals(guest_message=True, wrote_answer=True), (TEAM, ROLE_FROM_WRITTEN_ANSWER)),
        ("participant", False, RoleSignals(True, True, True), (TEAM, ROLE_FROM_CHAT_FLAG)),
        ("participant", True, RoleSignals(team_message=True), (TEAM, ROLE_FROM_CHAT_FLAG)),
        ("participant", True, RoleSignals(wrote_answer=True), (TEAM, ROLE_FROM_WRITTEN_ANSWER)),
        ("participant", True, RoleSignals(guest_message=True), (GUEST_SPEAKER, ROLE_FROM_GUEST_FLAG)),
        ("team_member", False, RoleSignals(guest_message=True), (TEAM, ROLE_FROM_LIVESTORM_ROLE)),
        ("team_member", False, RoleSignals(wrote_answer=True), (TEAM, ROLE_FROM_LIVESTORM_ROLE)),
        ("participant", False, RoleSignals(), (AUDIENCE, ROLE_BY_DEFAULT)),
    ],
)
def test_classify_role_with_signals(livestorm_role, guest, signals, expected):
    assert classify_role({"livestorm_role": livestorm_role, "is_guest_speaker": guest}, signals) == expected


def test_a_participant_moderator_is_raised_to_team_by_a_resolved_chat_flag():
    index, roster = index_for(chat(("moderator", TEAM_MESSAGE), "moderator", ("aud-2", GUEST_MESSAGE), "aud-1"))
    assert index.role_signals == {"aud-2": RoleSignals(guest_message=True), "moderator": RoleSignals(team_message=True)}
    assert (index.author("chat", "moderator").role, index.author("chat", "moderator").role_source) == (TEAM, ROLE_FROM_CHAT_FLAG)
    assert index.author("chat", "aud-1").role == AUDIENCE

    raised = escalate_roles(roster, index.role_signals)
    roles = roles_of(raised)
    assert roles["moderator"] == (TEAM, ROLE_FROM_CHAT_FLAG)
    assert roles["aud-2"] == (GUEST_SPEAKER, ROLE_FROM_CHAT_FLAG)
    assert roles["aud-1"] == (AUDIENCE, ROLE_BY_DEFAULT)
    assert roster.meta.role_counts == {"team": 1, "guest_speaker": 1, "audience": 4}
    assert raised.meta.role_counts == {"team": 2, "guest_speaker": 2, "audience": 2}
    assert raised.meta.attended_role_counts == {"team": 2, "guest_speaker": 2, "audience": 1}
    assert raised.meta.internal == roster.meta.internal  # Livestorm's own roles decide it
    assert roles_of(roster)["moderator"] == (AUDIENCE, ROLE_BY_DEFAULT)  # the roster passed in is unchanged


def test_writing_an_answer_raises_the_responder_to_team():
    questions_payload = questions(("aud-1", "moderator"), ("aud-2", "guest"), ("moderator", None), ("aud-1", "host"))
    index, roster = index_for(None, questions_payload)
    assert index.role_signals == {
        "guest": RoleSignals(wrote_answer=True),
        "host": RoleSignals(wrote_answer=True),
        "moderator": RoleSignals(wrote_answer=True),
    }
    responder = index.author("answer", "moderator")
    assert (responder.role, responder.role_source) == (TEAM, ROLE_FROM_WRITTEN_ANSWER)
    assert (index.author("answer", "guest").role, index.author("answer", "guest").role_source) == (TEAM, ROLE_FROM_WRITTEN_ANSWER)
    assert index.author("answer", "host").role_source == ROLE_FROM_LIVESTORM_ROLE
    # The same person asking is team too.
    assert (index.author("question", "moderator").role, index.author("question", "moderator").role_source) == (TEAM, ROLE_FROM_WRITTEN_ANSWER)

    raised = escalate_roles(roster, index.role_signals)
    roles = roles_of(raised)
    assert roles["moderator"] == (TEAM, ROLE_FROM_WRITTEN_ANSWER)
    assert roles["guest"] == (TEAM, ROLE_FROM_WRITTEN_ANSWER)
    assert roles["host"] == (TEAM, ROLE_FROM_LIVESTORM_ROLE)
    assert roles["aud-1"] == (AUDIENCE, ROLE_BY_DEFAULT)  # asking raises nobody
    assert raised.meta.role_counts == {"team": 3, "guest_speaker": 0, "audience": 3}

    events = evidence.audience_events(None, questions_payload, clock.clock_base(webinar()), index).set_index("eid")
    assert events.loc["Q003", "is_team"]  # the moderator's own question
    assert events.loc[["A001", "A002", "A003"], "is_team"].tolist() == [True, True, True]


def test_an_answer_from_an_unresolved_responder_raises_nobody():
    index, roster = index_for(None, questions(("aud-1", "stranger")))
    assert index.role_signals == {}
    assert (index.author("answer", "stranger").role, index.author("answer", "stranger").role_source) == (TEAM, ROLE_BY_DEFAULT)
    assert escalate_roles(roster, index.role_signals) is roster


def test_an_asker_raised_in_chat_is_team_in_the_question_index_too():
    index, _ = index_for(chat(("moderator", TEAM_MESSAGE)), questions(("moderator", None)))
    assert index.author("question", "moderator").role == TEAM


def test_unresolved_chat_flags_never_raise_a_roster_role():
    index, roster = index_for(chat(("stranger", TEAM_MESSAGE)))
    assert escalate_roles(roster, index.role_signals) is roster


def test_escalate_roles_with_nothing_to_raise_returns_the_same_roster():
    roster = roster_from_session_payload(webinar())
    assert escalate_roles(roster, {}) is roster
    signals = {"aud-1": RoleSignals(), "host": RoleSignals(team_message=True, wrote_answer=True), "nobody": RoleSignals(True, True, True)}
    assert escalate_roles(roster, signals) is roster


def test_raised_roles_never_change_the_internal_flag():
    """Two team members of four attendees is not internal; three would be.
    Raising the participant must not flip the flag."""
    people = [person("tm-0", role="team_member"), person("tm-1", role="team_member"), person("par"), person("aud")]
    index, roster = index_for(chat(("par", TEAM_MESSAGE)), questions(("aud", "aud")), payload=session_payload(people))
    assert roster.meta.internal is False
    raised = escalate_roles(roster, index.role_signals)
    assert raised.meta.attended_role_counts["team"] == 4
    assert raised.meta.internal is False


def test_an_internal_session_keeps_its_roles():
    """Three team members of four attendees is internal: the participant who
    wrote as team and answered a question stays audience, in the roster and
    in the author index. The same activity raises them on a public session,
    so this test can fail."""
    chat_payload = chat(("par", TEAM_MESSAGE), ("par", GUEST_MESSAGE))
    questions_payload = questions(("tm-0", "par"), ("par", None))

    public = [person("tm-0", role="team_member"), person("par"), person("aud-1"), person("aud-2")]
    public_index, public_roster = index_for(chat_payload, questions_payload, payload=session_payload(public))
    assert public_roster.meta.internal is False
    assert public_index.role_signals == {"par": RoleSignals(team_message=True, guest_message=True, wrote_answer=True)}
    assert public_index.author("question", "par").role == TEAM
    assert roles_of(escalate_roles(public_roster, public_index.role_signals))["par"] == (TEAM, ROLE_FROM_CHAT_FLAG)

    internal = [person(f"tm-{index}", role="team_member") for index in range(3)] + [person("par")]
    index, roster = index_for(chat_payload, questions_payload, payload=session_payload(internal))
    assert roster.meta.internal is True
    assert index.role_signals == {}
    for kind in ("chat", "question", "answer"):
        assert (index.author(kind, "par").role, index.author(kind, "par").role_source) == (AUDIENCE, ROLE_BY_DEFAULT), kind
    assert escalate_roles(roster, {"par": RoleSignals(True, True, True)}) is roster

    inputs = load_record_inputs(
        {"session_id": "s1", "session_payload": session_payload(internal), "chat_payload": chat_payload, "questions_payload": questions_payload}
    )
    assert roles_of(inputs.roster)["par"] == (AUDIENCE, ROLE_BY_DEFAULT)
    assert inputs.roster.meta.role_counts == roster.meta.role_counts


# ── Evidence ────────────────────────────────────────────────────────────────


def test_a_chat_message_keeps_its_own_flags_whether_or_not_its_author_resolved():
    chat_payload = chat(("host", {}), ("aud-1", TEAM_MESSAGE), ("stranger", GUEST_MESSAGE), None)
    index, _ = index_for(chat_payload)
    events = evidence.audience_events(chat_payload, None, clock.clock_base(webinar()), index)
    assert events["is_team"].tolist() == [False, True, True, False]
    assert events["author_id"].tolist() == ["host", "aud-1", "stranger", None]
    assert events["author_ref"].tolist() == [person_ref("s1", "host"), person_ref("s1", "aud-1"), None, None]


def test_questions_take_the_resolved_role_and_written_answers_are_team():
    questions_payload = questions(("host", "aud-1"), ("guest", None), ("aud-1", "host"), ("stranger", "stranger"))
    index, _ = index_for(None, questions_payload)
    events = evidence.audience_events(None, questions_payload, clock.clock_base(webinar()), index)
    by_eid = events.set_index("eid")
    # Questions: team or guest speaker when resolved so; not team otherwise.
    # aud-1 wrote an answer, so their own question is team.
    assert by_eid.loc[["Q001", "Q002", "Q003", "Q004"], "is_team"].tolist() == [True, True, True, False]
    q_refs = [person_ref("s1", "host"), person_ref("s1", "guest"), person_ref("s1", "aud-1"), None]
    assert by_eid.loc[["Q001", "Q002", "Q003", "Q004"], "author_ref"].tolist() == q_refs
    # Answers: always team, whatever the responder.
    assert by_eid.loc[["A001", "A002", "A003"], "is_team"].tolist() == [True, True, True]
    # Each answer carries its responder's ref and people id, not its asker's.
    a_refs = [person_ref("s1", "aud-1"), person_ref("s1", "host"), None]
    assert by_eid.loc[["A001", "A002", "A003"], "author_ref"].tolist() == a_refs
    assert by_eid.loc[["A001", "A002", "A003"], "author_id"].tolist() == ["aud-1", "host", "stranger"]
    assert by_eid.loc[["A001", "A002", "A003"], "reply_to"].tolist() == ["Q001", "Q003", "Q004"]


def test_evidence_ids_do_not_depend_on_the_authors():
    chat_payload, questions_payload = chat("aud-1", "stranger", "host"), questions(("aud-1", "host"))
    base = clock.clock_base(webinar())
    index, _ = index_for(chat_payload, questions_payload)
    without = evidence.audience_events(chat_payload, questions_payload, base)
    with_authors = evidence.audience_events(chat_payload, questions_payload, base, index)
    assert with_authors["eid"].tolist() == without["eid"].tolist()
    assert with_authors["livestorm_id"].tolist() == without["livestorm_id"].tolist()
    old = evidence.audience_events(cached_before_the_link(chat_payload), questions_payload, base, index)
    assert old["eid"].tolist() == without["eid"].tolist()


# ── The loader ──────────────────────────────────────────────────────────────


def test_the_loader_resolves_authors_and_raises_roles():
    cached = {
        "session_id": "s1",
        "session_payload": webinar(),
        "chat_payload": chat(("moderator", TEAM_MESSAGE), ("stranger", {})),
        "questions_payload": questions(("aud-1", "host"), ("aud-2", "aud-1")),
    }
    inputs = load_record_inputs(cached)
    assert isinstance(inputs.authors, AuthorIndex)
    assert {kind: coverage.state for kind, coverage in inputs.authors.coverage.items()} == {
        "chat": PARTIAL,
        "question": RESOLVED,
        "answer": RESOLVED,
    }
    roles = roles_of(inputs.roster)
    assert roles["moderator"] == (TEAM, ROLE_FROM_CHAT_FLAG)
    assert roles["aud-1"] == (TEAM, ROLE_FROM_WRITTEN_ANSWER)
    assert inputs.roster.meta.role_counts == {"team": 3, "guest_speaker": 1, "audience": 2}


def test_the_loader_reads_an_old_cached_chat_as_unresolved():
    cached = {"session_id": "s1", "session_payload": webinar(), "chat_payload": cached_before_the_link(chat(("moderator", TEAM_MESSAGE)))}
    inputs = load_record_inputs(cached)
    assert inputs.authors.coverage["chat"].state == UNRESOLVED
    assert roles_of(inputs.roster)["moderator"] == (AUDIENCE, ROLE_BY_DEFAULT)


def test_the_loader_without_chat_or_questions_has_empty_coverage():
    inputs = load_record_inputs({"session_id": "s1", "session_payload": webinar()})
    assert {coverage.state for coverage in inputs.authors.coverage.values()} == {EMPTY}
    assert inputs.roster.meta.role_counts == {"team": 1, "guest_speaker": 1, "audience": 4}


# ── The chat fetcher: include=people, then drop the people ─────────────────


def registrant(person_id):
    """A people record as `include=people` returns it, join link included."""
    record = person(person_id)
    record["attributes"]["registrant_detail"].update(
        {"password_key": f"pk-{person_id}", "connection_link": f"https://example.test/join/{person_id}?key=pk-{person_id}"}
    )
    return record


def chat_page(number, person_ids, next_page):
    """One page of `GET /sessions/{id}/chat_messages?include=people`."""
    data = [
        {
            "id": f"m{number}-{index}",
            "type": "chat_messages",
            "attributes": {
                "author_id": f"c-{person_id}-x",
                "text_content": f"message {number}-{index} https://example.test/doc",
                "html_content": "<p>hello</p>",
                "created_at": T0 + 60 * (10 * number + index),
                "updated_at": T0 + 60 * (10 * number + index),
                "from_team_member": person_id == "host",
                "from_guest_speaker": "true" if person_id == "guest" else False,
            },
            "relationships": link(person_id),
        }
        for index, person_id in enumerate(person_ids)
    ]
    included = [registrant(person_id) for person_id in dict.fromkeys(person_ids)]
    return {"data": data, "included": included, "meta": {"current_page": number, "next_page": next_page, "record_count": 5}}


CHAT_PAGES = {0: chat_page(0, ["aud-1", "host", "aud-1"], 1), 1: chat_page(1, ["guest", "aud-2"], None)}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return copy.deepcopy(self._payload)


@pytest.fixture
def chat_transport(monkeypatch):
    """Serve CHAT_PAGES from a patched transport and record each request's params."""
    calls = []

    def fake_get(url, *, headers, params=None):
        assert url.endswith("/sessions/s1/chat_messages")
        calls.append(dict(params or {}))
        return FakeResponse(CHAT_PAGES[params["page[number]"]])

    monkeypatch.setattr(livestorm_client, "livestorm_get", fake_get)
    return calls


def test_the_chat_fetcher_asks_for_people_and_drops_them(chat_transport):
    payload = livestorm_client.fetch_chat_messages("Bearer t", "s1")
    assert [call["page[number]"] for call in chat_transport] == [0, 1]
    assert {call["include"] for call in chat_transport} == {"people"}
    assert {call["page[size]"] for call in chat_transport} == {50}
    assert "included" not in payload
    assert (payload["pages_fetched"], payload["requested_page_size"]) == (2, 50)
    assert [message["relationships"] for message in payload["data"]] == [link(p) for p in ("aud-1", "host", "aud-1", "guest", "aud-2")]
    assert [message["attributes"] for message in payload["data"]] == [
        message["attributes"] for number in (0, 1) for message in CHAT_PAGES[number]["data"]
    ]
    assert not any(key in json.dumps(payload) for key in JOIN_LINK_KEYS)


def test_the_chat_fetcher_still_strips_join_links(monkeypatch):
    """Defensive: a join link anywhere in the chat payload never comes back."""
    page = chat_page(0, ["aud-1"], None)
    page["data"][0]["attributes"]["registrant_detail"] = {"attended": True, "password_key": "pk", "connection_link": "https://example.test/j"}
    monkeypatch.setattr(livestorm_client, "livestorm_get", lambda url, *, headers, params=None: FakeResponse(page))
    payload = livestorm_client.fetch_chat_messages("Bearer t", "s1")
    assert payload["data"][0]["attributes"]["registrant_detail"] == {"attended": True}
    assert payload["data"][0]["relationships"] == link("aud-1")
    assert "included" not in payload


def test_a_fetched_chat_resolves_against_the_session_people(chat_transport):
    payload = livestorm_client.fetch_chat_messages("Bearer t", "s1")
    index, _ = index_for(payload)
    assert index.coverage["chat"].state == RESOLVED
    assert index.role_signals == {"guest": RoleSignals(guest_message=True), "host": RoleSignals(team_message=True)}


# ── Classic reads a fetched chat as before ─────────────────────────────────


def classic_workspace(chat_payload):
    transcript = {
        "transcript": {
            "duration_seconds": 600.0,
            "segments": [
                {"start": float(start), "end": float(start) + 8.0, "speaker": "A", "text": "We are discussing the results today"}
                for start in range(0, 600, 10)
            ],
        }
    }
    return api_logic._serialize_cached_session(
        "s1",
        {
            "session_payload": webinar(),
            "chat_payload": chat_payload,
            "questions_payload": questions(("aud-1", "host"), ("aud-2", None)),
            "transcript_payload": transcript,
        },
    )


def test_classic_reads_a_fetched_chat_as_before(chat_transport):
    """Classic reads text, times, `author_id` and the team flags only: the
    people link changes no table, stat or AI input."""
    fetched = livestorm_client.fetch_chat_messages("Bearer t", "s1")
    before = cached_before_the_link(fetched)
    assert fetched != before

    new_df, old_df = audience.build_chat_df_from_payload(fetched), audience.build_chat_df_from_payload(before)
    pd.testing.assert_frame_equal(new_df, old_df)
    assert not any("people" in column or "rel" in column for column in new_df.columns)

    new, old = classic_workspace(fetched), classic_workspace(before)
    assert new["tables"]["chat"] == old["tables"]["chat"]
    assert len(new["tables"]["chat"]) == 5
    assert new["stats"] == old["stats"]
    assert new == old

    assert llm_inputs.build_compact_chat_payload_for_llm(new_df) == llm_inputs.build_compact_chat_payload_for_llm(old_df)
    assert llm_inputs.build_deep_analysis_chat_payload_for_llm(fetched) == llm_inputs.build_deep_analysis_chat_payload_for_llm(before)


# ── The benchmark seed's chat has the fetched shape ────────────────────────


def _seed_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "seed_benchmark_sessions.py"
    spec = importlib.util.spec_from_file_location("seed_benchmark_sessions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_benchmark_seed_chat_resolves_through_its_people_links():
    seed = _seed_script()
    rng = random.Random(7)
    people = seed.build_people(rng, 12)
    session = seed.build_session_payload("bench-test", "evt", 1800, people, T0)
    chat_payload = seed.build_chat(rng, 40, people, T0, 1800)

    assert "included" not in chat_payload
    people_ids = {p["id"] for p in people}
    assert all(m["relationships"]["people"]["data"]["id"] in people_ids for m in chat_payload["data"])
    assert not any(m["attributes"]["author_id"] in people_ids for m in chat_payload["data"])

    index = resolve_authors(chat_payload, None, roster_from_session_payload(session))
    assert index.coverage["chat"].state == RESOLVED
    assert index.coverage["chat"].resolved_items == 40
