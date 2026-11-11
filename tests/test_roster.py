"""The v1 roster: one person's facts, roles, channels, lead time, refs and views.

Covers `livestorm.payloads.parse_person`, `record/roster.py` and
`record/inputs.py`. Pure functions on synthetic payloads: no database, Redis
or network. Every name, email and company below is made up.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from livestorm_app.analytics.session_overview import session_is_internal
from livestorm_app.livestorm.payloads import PERSON_KEYS, email_domain, is_answer_field, parse_person, referrer_domain
from livestorm_app.record import roster as roster_module
from livestorm_app.record.inputs import RecordInputs, count_people_on_stage, load_record_inputs
from livestorm_app.record.roster import (
    AUDIENCE,
    CHANNEL_RULE_VERSION,
    GUEST_SPEAKER,
    ROSTER_COLUMNS,
    TEAM,
    RosterRows,
    assign_person_refs,
    classify_role,
    derive_channel,
    display_people,
    is_organizer,
    lead_time_bucket,
    people_on_stage,
    person_ref,
    prompt_view,
    resolve_field_definitions,
    roster_from_rows,
    roster_from_session_payload,
)

T0 = int(datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).timestamp())
DAY = 86400
REPO_ROOT = Path(__file__).resolve().parent.parent

# Values that must never leave parse_person, the roster or prompt_view.
NEVER_KEPT = (
    "Ada.Quill@Example-Corp.test",
    "ada.quill@",
    "ada@quillworks.test",
    "fixture-password-key",
    "live?key=",
    "password_key",
    "connection_link",
    "Firefox",
    "Linux",
    "Lyon",
    "avatar",
    "1920",
    "trk=",
    "/feed",
    "00 00 00",
    "Example Street",
    "Exampleville",
    "example_handle",
    "quill-profile",
)

# Livestorm's built-in contact fields: never registration answers.
CONTACT_FIELDS = ("phone", "address", "city", "twitter", "linkedin", "facebook", "website")


def full_record(person_id="p-ada"):
    """A people record with every attribute Livestorm sends, including the
    ones StormIQ must drop (email, device, IP city, avatar, join link)."""
    return {
        "id": person_id,
        "type": "people",
        "attributes": {
            "created_at": T0 - 20 * DAY,
            "updated_at": T0 + 100,
            "timezone": "Europe/Paris",
            "first_name": "Ada",
            "last_name": "Quill",
            "email": "Ada.Quill@Example-Corp.test",
            "messages_count": 3,
            "questions_count": 1,
            "votes_count": 2,
            "up_votes_count": 4,
            "role": "participant",
            "avatar_link": "https://cdn.example.test/avatar/ada.png",
            "replay_view_detail": {"watched_at": T0 + 2 * DAY},
            "registrant_detail": {
                "event_id": "e1",
                "session_id": "s1",
                "created_at": T0 - 5 * DAY,
                "updated_at": T0,
                "fields": [
                    {"id": "email", "type": "text", "value": "Ada.Quill@Example-Corp.test", "required": True},
                    {"id": "first_name", "type": "text", "value": "Ada", "required": True},
                    {"id": "last_name", "type": "text", "value": "Quill", "required": True},
                    {"id": "company", "type": "text", "value": "Quillworks Ltd", "required": False},
                    {"id": "job_title", "type": "text", "value": "Head of Growth", "required": False},
                    {"id": "team_size", "type": "unique_select", "value": "11-50", "required": False},
                    {"id": "topics", "type": "multiple_select", "value": ["pricing", " onboarding ", ""], "required": False},
                    {"id": "why_join", "type": "text", "value": "  To learn  ", "required": False},
                    {"id": "website", "type": "url", "value": "https://quillworks.test", "required": False},
                    # Livestorm's built-in contact fields are type text.
                    {"id": "phone", "type": "text", "value": "+00 0 00 00 00 00", "required": False},
                    {"id": "address", "type": "text", "value": "1 Example Street", "required": False},
                    {"id": "city", "type": "text", "value": "Exampleville", "required": False},
                    {"id": "twitter", "type": "text", "value": "@example_handle", "required": False},
                    {"id": "linkedin", "type": "url", "value": "https://linkedin.example.test/in/quill-profile", "required": False},
                    {"id": "country", "type": "text", "value": "Exampleland", "required": False},
                    {"id": "work_email", "type": "email", "value": "ada@quillworks.test", "required": False},
                    {"id": "consent_marketing", "type": "consent", "value": "true", "required": False},
                    {"id": "photo", "type": "avatar", "value": "https://cdn.example.test/avatar/ada.png", "required": False},
                    {"id": "empty_answer", "type": "text", "value": None, "required": False},
                ],
                "referrer": "https://www.LinkedIn.com/feed/?trk=abc&email=ada%40quillworks.test",
                "utm_source": "Newsletter",
                "utm_medium": "Email",
                "utm_campaign": "oct-launch",
                "utm_term": "webinar",
                "utm_content": "cta-top",
                "browser_version": "120.0",
                "browser_name": "Firefox",
                "os_name": "Linux",
                "os_version": "6.1",
                "screen_height": "1080",
                "screen_width": "1920",
                "ip_city": "Lyon",
                "ip_country_code": "FR",
                "ip_country_name": "France",
                "attended": True,
                "attendance_rate": 87,
                "attendance_duration": 3120,
                "has_viewed_replay": False,
                "is_highlighted": True,
                "is_guest_speaker": False,
                "password_key": "fixture-password-key",
                "connection_link": "https://app.livestorm.test/p/e1/live?key=fixture-password-key",
                "registration_type": "detailed",
                "session_role": None,
            },
        },
    }


def person(person_id, *, role="participant", first="Pat", last=None, guest=False, attended=True, session_role=None, **detail):
    """A small people record. Extra keyword arguments go in registrant_detail."""
    registrant_detail = {"attended": attended, "is_guest_speaker": guest, "session_role": session_role, **detail}
    return {
        "id": person_id,
        "type": "people",
        "attributes": {
            "role": role,
            "first_name": first,
            "last_name": last or person_id.title(),
            "email": f"{person_id}@example.test",
            "registrant_detail": registrant_detail,
        },
    }


def session_payload(people, session_id="s1", **attributes):
    base = {"started_at": T0, "ended_at": T0 + 3600, "duration": 3600, "registrants_count": len(people)}
    base.update(attributes)
    return {
        "data": {"id": session_id, "type": "sessions", "attributes": base},
        "included": people,
    }


def public_session():
    """One organizer from the team, one guest speaker, three audience members
    (two attended)."""
    people = [
        person("host", role="team_member", session_role="organizer"),
        person("guest", guest=True),
        person("aud-1", first="Bram"),
        person("aud-2", first="Cleo"),
        person("aud-3", first="Dev", attended=False),
    ]
    return session_payload(people, attendees_count=4)


def internal_session():
    """An all-hands: everyone is a team member."""
    people = [person(f"tm-{index}", role="team_member", attended=index != 3) for index in range(5)]
    return session_payload(people, attendees_count=4)


# ── parse_person ────────────────────────────────────────────────────────────


def test_parse_person_reads_every_kept_field():
    assert parse_person(full_record()) == {
        "person_id": "p-ada",
        "livestorm_role": "participant",
        "is_guest_speaker": False,
        "session_role": None,
        "first_name": "Ada",
        "last_name": "Quill",
        "email_domain": "example-corp.test",
        "company": "Quillworks Ltd",
        "job_title": "Head of Growth",
        "answers": {"team_size": "11-50", "topics": ["pricing", "onboarding"], "why_join": "To learn", "country": "Exampleland"},
        "registered_at": T0 - 5 * DAY,
        "registration_type": "detailed",
        "referrer_domain": "linkedin.com",
        "utm_source": "Newsletter",
        "utm_medium": "Email",
        "utm_campaign": "oct-launch",
        "utm_term": "webinar",
        "utm_content": "cta-top",
        "timezone": "Europe/Paris",
        "country_code": "FR",
        "country_name": "France",
        "attended": True,
        "attendance_duration_s": 3120,
        "attendance_rate": 87.0,
        "has_viewed_replay": False,
        "replay_watched_at": T0 + 2 * DAY,
        "ls_messages_count": 3,
        "ls_questions_count": 1,
        "ls_votes_count": 2,
        "ls_up_votes_count": 4,
        "ls_updated_at": T0 + 100,
    }


def test_parse_person_returns_exactly_the_person_keys():
    assert tuple(parse_person(full_record())) == PERSON_KEYS
    assert tuple(parse_person({"id": "bare"})) == PERSON_KEYS


def test_parse_person_never_returns_the_email_address_join_link_or_device_data():
    parsed = parse_person(full_record())
    dumped = json.dumps(parsed)
    for value in NEVER_KEPT:
        assert value not in dumped, value
    assert "@" not in dumped
    assert not {"email", "password_key", "connection_link", "referrer", "browser_name", "ip_city", "avatar_link"} & set(parsed)


def test_parse_person_without_registrant_detail():
    record = full_record()
    del record["attributes"]["registrant_detail"]
    record["attributes"]["replay_view_detail"] = None
    parsed = parse_person(record)
    assert parsed["first_name"] == "Ada" and parsed["email_domain"] == "example-corp.test"
    assert parsed["is_guest_speaker"] is False
    assert parsed["answers"] == {}
    for key in (
        "session_role", "company", "job_title", "registered_at", "registration_type", "referrer_domain",
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "country_code", "country_name",
        "attended", "attendance_duration_s", "attendance_rate", "has_viewed_replay", "replay_watched_at",
    ):
        assert parsed[key] is None, key
    assert parsed["ls_messages_count"] == 3 and parsed["timezone"] == "Europe/Paris"


def test_parse_person_reads_names_and_the_email_domain_from_fields_when_attributes_lack_them():
    record = full_record()
    for key in ("first_name", "last_name", "email"):
        record["attributes"][key] = None
    parsed = parse_person(record)
    assert (parsed["first_name"], parsed["last_name"], parsed["email_domain"]) == ("Ada", "Quill", "example-corp.test")


@pytest.mark.parametrize(
    "raw, expected",
    [(True, True), (False, False), ("true", True), ("TRUE", True), (" false ", False), ("yes", None), (1, None), (None, None)],
)
def test_parse_person_reads_booleans_and_their_strings(raw, expected):
    record = full_record()
    record["attributes"]["registrant_detail"].update(attended=raw, has_viewed_replay=raw, is_guest_speaker=raw)
    parsed = parse_person(record)
    assert parsed["attended"] is expected
    assert parsed["has_viewed_replay"] is expected
    assert parsed["is_guest_speaker"] is (expected is True)


def test_parse_person_answers_keep_only_text_and_select_fields_that_are_not_identity():
    record = full_record()
    fields = record["attributes"]["registrant_detail"]["fields"]
    fields.append({"id": "team_size", "type": "unique_select", "value": "500+"})  # a repeat: the first copy wins
    fields.append({"id": "Email", "type": "text", "value": "other@example.test"})  # identity, whatever the case
    fields.append({"id": "no_type", "value": "kept?"})
    fields.append({"id": "blank_list", "type": "multiple_select", "value": ["", "  "]})
    fields.append("not a field")
    answers = parse_person(record)["answers"]
    assert answers == {"team_size": "11-50", "topics": ["pricing", "onboarding"], "why_join": "To learn", "country": "Exampleland"}


@pytest.mark.parametrize("slug", CONTACT_FIELDS + ("Phone", " city "))
@pytest.mark.parametrize("field_type", ["text", "url", "unique_select"])
def test_builtin_contact_fields_are_never_answers(slug, field_type):
    assert is_answer_field(slug, field_type) is False
    record = person("p1", fields=[{"id": slug, "type": field_type, "value": "fixture-contact"}])
    assert parse_person(record)["answers"] == {}


def test_country_is_the_one_builtin_text_field_kept():
    assert is_answer_field("country", "text") is True


@pytest.mark.parametrize("record", [None, "p1", [], {}, {"id": ""}, {"id": None}, {"id": "  "}])
def test_parse_person_skips_records_without_an_id(record):
    assert parse_person(record) is None


def test_parse_person_does_not_change_the_record():
    record = full_record()
    before = copy.deepcopy(record)
    parse_person(record)
    assert record == before


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ada@example.test", "example.test"),
        ("  Ada@Example.TEST ", "example.test"),
        ("ada@sub.example.test.", "sub.example.test"),
        ("no-at-sign", None),
        ("a@b@c.test", None),
        ("ada quill@example.test", None),
        ("ada@", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_email_domain_keeps_only_the_domain(raw, expected):
    assert email_domain(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://www.linkedin.com/feed?x=1", "linkedin.com"),
        ("HTTP://News.Example.TEST/path", "news.example.test"),
        ("linkedin.com/some/page", "linkedin.com"),
        ("www.example.test", "example.test"),
        ("https://app.livestorm.co/p/x/live?key=abc", "app.livestorm.co"),
        ("not a url", None),
        ("(direct)", None),
        ("http://[::1", None),
        ("", None),
        (None, None),
    ],
)
def test_referrer_domain_keeps_only_the_host(raw, expected):
    assert referrer_domain(raw) == expected


# ── Roles ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "livestorm_role, guest, expected",
    [
        ("team_member", False, (TEAM, "livestorm_role")),
        ("team_member", True, (TEAM, "livestorm_role")),  # team wins over guest speaker
        (" team_member ", None, (TEAM, "livestorm_role")),
        ("Team_Member", None, (AUDIENCE, "default")),  # exact, as the classic internal rule reads it
        ("participant", True, (GUEST_SPEAKER, "guest_speaker_flag")),
        ("participant", "true", (GUEST_SPEAKER, "guest_speaker_flag")),
        ("participant", "TRUE", (GUEST_SPEAKER, "guest_speaker_flag")),
        (None, True, (GUEST_SPEAKER, "guest_speaker_flag")),
        ("participant", "false", (AUDIENCE, "default")),
        ("participant", False, (AUDIENCE, "default")),
        ("participant", 1, (AUDIENCE, "default")),
        ("moderator", False, (AUDIENCE, "default")),  # an unknown role is audience
        (None, None, (AUDIENCE, "default")),
    ],
)
def test_classify_role(livestorm_role, guest, expected):
    assert classify_role({"livestorm_role": livestorm_role, "is_guest_speaker": guest}) == expected


@pytest.mark.parametrize(
    "session_role, expected",
    [("organizer", True), (" Organizer ", True), (None, False), ("", False), ("speaker", False)],
)
def test_is_organizer_reads_session_role(session_role, expected):
    assert is_organizer({"session_role": session_role}) is expected


def test_an_organizer_keeps_the_role_livestorm_gives():
    payload = session_payload(
        [
            person("host", role="team_member", session_role="organizer"),
            person("odd", role="participant", session_role="organizer"),
        ]
    )
    roster = roster_from_session_payload(payload)
    people = roster.people.set_index("person_id")
    assert people.loc["host", "role"] == TEAM and people.loc["odd", "role"] == AUDIENCE
    assert people["is_organizer"].tolist() == [True, True]
    assert people["session_role"].tolist() == ["organizer", "organizer"]  # kept as sent
    assert roster.meta.organizers == 2


# ── People on stage (the speaker cap of a new transcript) ──────────────────


def on_stage(payload):
    """The count the worker reads: a cached row with the session payload."""
    return count_people_on_stage({"session_payload": payload})


def test_people_on_stage_are_organizers_and_guest_speakers():
    assert on_stage(public_session()) == 2
    assert people_on_stage(roster_from_session_payload(public_session())) == 2


def test_the_synthetic_webinar_has_two_people_on_stage():
    from synthetic_session import session_payload as synthetic_session_payload

    assert on_stage(synthetic_session_payload()) == 2


def test_someone_on_stage_twice_counts_once_and_attendance_plays_no_part():
    payload = session_payload(
        [
            person("host", role="team_member", session_role="organizer", guest=True),
            person("absent-host", role="team_member", session_role=" Organizer ", attended=False),
            person("guest", guest="true", attended=None),
            person("cohost", role="team_member"),  # a team member, not marked on stage
            person("aud", first="Bram"),
            person("host", role="team_member", session_role="organizer"),  # a repeat
        ]
    )
    assert on_stage(payload) == 3
    assert people_on_stage(roster_from_session_payload(payload)) == 3


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        "not a payload",
        {"data": {"id": "s1", "attributes": {}}},
        session_payload([]),
        session_payload([{"id": "x", "type": "messages", "attributes": {}}]),
    ],
)
def test_without_people_the_count_is_unknown(payload):
    assert on_stage(payload) is None


@pytest.mark.parametrize("cached", [None, "not a row", [], {}])
def test_without_a_cached_row_the_count_is_unknown(cached):
    assert count_people_on_stage(cached) is None


def test_nobody_marked_on_stage_is_unknown_not_zero():
    """An internal meeting can mark nobody as an organizer or guest speaker.
    None keeps Gladia's default cap, where 0 would give the floor of 3 to a
    room where many people talk (docs/product.md §13, "Speaker cap")."""
    assert on_stage(internal_session()) is None
    assert people_on_stage(roster_from_session_payload(internal_session())) == 0


# ── Channels ────────────────────────────────────────────────────────────────


def channel_of(**fields):
    return tuple(derive_channel(fields))


def test_the_utm_source_wins_over_the_referrer_and_the_registration_type():
    assert channel_of(utm_source=" LinkedIn ", utm_medium="Social", referrer_domain="example.test", registration_type="API") == (
        "linkedin",
        "social",
        "utm",
    )


def test_a_blank_utm_source_falls_through_to_the_referrer():
    assert channel_of(utm_source="  ", utm_medium="email", referrer_domain="www.Example.TEST") == ("example.test", None, "referrer")


@pytest.mark.parametrize("host", ["livestorm.co", "app.livestorm.co", "eu.app.livestorm.co", "livestorm.io", "WWW.LIVESTORM.CO"])
def test_livestorm_referrers_are_ignored(host):
    assert channel_of(referrer_domain=host, registration_type="detailed") == ("type:detailed", None, "registration_type")
    assert channel_of(referrer_domain=host) == ("direct_or_unknown", None, "none")


def test_a_host_that_only_ends_like_livestorm_is_not_livestorm():
    assert channel_of(referrer_domain="notlivestorm.co") == ("notlivestorm.co", None, "referrer")


def test_the_registration_type_then_direct_or_unknown():
    assert channel_of(registration_type="API") == ("type:api", None, "registration_type")
    assert channel_of() == ("direct_or_unknown", None, "none")
    assert channel_of(utm_source=None, referrer_domain=None, registration_type=" ") == ("direct_or_unknown", None, "none")


def test_the_roster_carries_the_channel_and_its_rule_version():
    roster = roster_from_session_payload(session_payload([full_record()]))
    row = roster.people.iloc[0]
    assert (row["channel"], row["channel_detail"], row["channel_source"]) == ("newsletter", "email", "utm")
    assert row["referrer_domain"] == "linkedin.com"
    assert roster.meta.channel_rule_version == CHANNEL_RULE_VERSION == 1


# ── Lead time ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "seconds_before_start, expected",
    [
        (0, "same_day"),
        (DAY - 1, "same_day"),
        (DAY, "1-3"),
        (4 * DAY - 1, "1-3"),
        (4 * DAY, "4-7"),
        (8 * DAY - 1, "4-7"),
        (8 * DAY, "8-14"),
        (15 * DAY - 1, "8-14"),
        (15 * DAY, "15+"),
        (400 * DAY, "15+"),
        (-1, "after_start"),
        (-3 * DAY, "after_start"),
    ],
)
def test_lead_time_bucket_edges(seconds_before_start, expected):
    assert lead_time_bucket(T0 - seconds_before_start, T0) == expected


def test_lead_time_reads_iso_strings_and_datetimes():
    assert lead_time_bucket("2026-09-25T09:00:00Z", T0) == "4-7"
    assert lead_time_bucket(datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc), "2026-10-01T09:00:00+00:00") == "1-3"


@pytest.mark.parametrize("registered_at, started_at", [(None, T0), (T0, None), (0, T0), ("soon", T0)])
def test_lead_time_needs_both_times(registered_at, started_at):
    assert lead_time_bucket(registered_at, started_at) is None


def test_lead_time_never_uses_estimated_started_at():
    record = person("p1", created_at=T0 - 2 * DAY)
    payload = session_payload([record], started_at=None, estimated_started_at=T0)
    row = roster_from_session_payload(payload).people.iloc[0]
    assert row["lead_bucket"] is None and pd.isna(row["lead_time_days"])
    on_time = roster_from_session_payload(session_payload([record])).people.iloc[0]
    assert on_time["lead_bucket"] == "1-3" and on_time["lead_time_days"] == pytest.approx(2.0)


# ── Field definitions ───────────────────────────────────────────────────────

EVENT_FIELDS = [
    {"id": "email", "type": "text", "order": 0, "required": True},
    {"id": "first_name", "type": "text", "order": 1, "required": True},
    {"id": "company", "type": "text", "order": 2, "required": False},
    {"id": "team_size", "type": "unique_select", "order": 5, "required": True},
    {"id": "topics", "type": "multiple_select", "order": 4, "required": False},
    {"id": "website", "type": "url", "order": 3, "required": False},
    {"id": "topics", "type": "multiple_select", "order": 9, "required": False},  # a repeat
    "not a field",
]


def test_field_definitions_without_attributes_come_from_the_event():
    fields, source = resolve_field_definitions(EVENT_FIELDS)
    assert source == "event_fields"
    assert fields == (
        {"slug": "topics", "name": "Topics", "type": "multiple_select", "order": 4, "required": False, "options": []},
        {"slug": "team_size", "name": "Team size", "type": "unique_select", "order": 5, "required": True, "options": []},
    )


@pytest.mark.parametrize("fields", [[], None, EVENT_FIELDS[:3]])
def test_no_answer_field_gives_no_definitions(fields):
    assert resolve_field_definitions(fields) == ((), "none")


def test_builtin_contact_fields_get_no_definition():
    fields = [{"id": slug, "type": "text", "order": index} for index, slug in enumerate(CONTACT_FIELDS + ("country",))]
    attributes = [{"slug": slug, "name": slug.title(), "type": "text", "builtin": True} for slug in CONTACT_FIELDS + ("country",)]
    for people_attributes in (None, attributes):
        definitions, _ = resolve_field_definitions(fields, people_attributes)
        assert [field["slug"] for field in definitions] == ["country"]


def test_field_definitions_take_labels_and_options_from_people_attributes():
    attributes = [
        {
            "slug": "team_size",
            "name": "How big is your team?",
            "type": "unique_select",
            "options": [{"value": "small", "label": "1-10", "position": 1, "user_id": "u-1"}],
        }
    ]
    fields, source = resolve_field_definitions(EVENT_FIELDS, attributes)
    assert source == "people_attributes"
    by_slug = {field["slug"]: field for field in fields}
    assert by_slug["team_size"]["name"] == "How big is your team?"
    assert by_slug["team_size"]["options"] == [{"value": "small", "label": "1-10", "position": 1}]
    assert by_slug["topics"]["name"] == "Topics"
    assert resolve_field_definitions(EVENT_FIELDS, [{"slug": "other", "name": "Other"}])[1] == "event_fields"


def test_the_first_people_attribute_with_a_slug_gives_its_label():
    attributes = [{"slug": "team_size", "name": "First label"}, {"slug": "team_size", "name": "Later label"}]
    fields, _ = resolve_field_definitions(EVENT_FIELDS, attributes)
    assert {field["slug"]: field["name"] for field in fields}["team_size"] == "First label"


def test_the_include_roster_takes_its_fields_from_the_event_payload():
    event = {"data": {"id": "e1", "attributes": {"fields": EVENT_FIELDS}}}
    meta = roster_from_session_payload(session_payload([full_record()]), event_payload=event).meta
    assert meta.fields_source == "event_fields" and [field["slug"] for field in meta.fields] == ["topics", "team_size"]
    assert roster_from_session_payload(session_payload([full_record()])).meta.fields_source == "none"


# ── Person refs ─────────────────────────────────────────────────────────────

# Two synthetic ids whose 8-character refs collide in session "s-collide".
COLLIDING = ("person-14687", "person-73600")


def test_person_ref_is_p_and_8_hex_and_depends_on_the_session():
    ref = person_ref("s1", "p1")
    assert re.fullmatch(r"P-[0-9a-f]{8}", ref)
    assert ref == person_ref("s1", "p1")
    assert ref != person_ref("s2", "p1") and ref != person_ref("s1", "p2")


def test_refs_are_stable_across_parses_payload_order_and_new_people():
    people = public_session()["included"]
    first = roster_from_session_payload(session_payload(people))
    shuffled = list(people)
    random.Random(7).shuffle(shuffled)
    again = roster_from_session_payload(session_payload(shuffled + [person("late-registrant")]))
    refs = dict(zip(first.people["person_id"], first.people["person_ref"], strict=True))
    refs_again = dict(zip(again.people["person_id"], again.people["person_ref"], strict=True))
    assert all(refs_again[person_id] == ref for person_id, ref in refs.items())
    assert refs == {person_id: person_ref("s1", person_id) for person_id in refs}


def test_colliding_refs_are_lengthened_until_unique():
    low, high = COLLIDING
    assert person_ref("s-collide", low) == person_ref("s-collide", high)  # the fixture really collides
    refs = assign_person_refs("s-collide", [high, low, "someone-else"])
    assert refs[low] == person_ref("s-collide", low)  # the smallest id keeps the short ref
    assert refs[high].startswith(refs[low]) and len(refs[high]) == len(refs[low]) + 1
    assert len(set(refs.values())) == 3
    roster = roster_from_session_payload(session_payload([person(low), person(high)], session_id="s-collide"))
    assert roster.people["person_ref"].is_unique


# Made-up digests: all three share their first 8 hex characters, and b and c
# share the 9th too.
THREE_WAY = {
    "a": "abcdef01" + "0" * 56,
    "b": "abcdef01" + "23" + "0" * 54,
    "c": "abcdef01" + "24" + "0" * 54,
}


def test_a_three_way_collision_lengthens_until_every_ref_is_unique(monkeypatch):
    monkeypatch.setattr(roster_module, "_digest", lambda session_id, person_id: THREE_WAY[person_id])
    assert assign_person_refs("s", ["a", "b"]) == {"a": "P-abcdef01", "b": "P-abcdef012"}
    # c joins: b's ref lengthens, although c's id is larger.
    assert assign_person_refs("s", ["c", "b", "a"]) == {"a": "P-abcdef01", "b": "P-abcdef0123", "c": "P-abcdef0124"}
    # a leaves: b now has the smallest id and takes the short ref back.
    assert assign_person_refs("s", ["b", "c"]) == {"b": "P-abcdef01", "c": "P-abcdef012"}


# ── Views ───────────────────────────────────────────────────────────────────


def test_prompt_view_holds_refs_roles_and_job_titles_only():
    payload = public_session()
    payload["included"].append(full_record())
    roster = roster_from_session_payload(payload)
    view = prompt_view(roster)
    assert all(set(item) == {"ref", "role", "job_title"} for item in view)
    assert [item["ref"] for item in view] == sorted(item["ref"] for item in view)
    dumped = json.dumps(view)
    for value in NEVER_KEPT + ("Ada", "Quill", "Bram", "Cleo", "example-corp", "example.test", "p-ada", "aud-1", "host"):
        assert value not in dumped, value
    assert "@" not in dumped
    titles = {item["job_title"] for item in view}
    assert titles == {"Head of Growth", "role unknown"}
    assert {item["ref"]: item["role"] for item in view} == dict(zip(roster.people["person_ref"], roster.people["role"], strict=True))
    assert sorted(item["role"] for item in view) == [AUDIENCE] * 4 + [GUEST_SPEAKER, TEAM]


def test_display_people_shows_names_only_on_a_public_session():
    roster = roster_from_session_payload(public_session())
    shown = display_people(roster, profile="public")
    assert [row["name"] for row in shown] == ["Bram Aud-1", "Cleo Aud-2", "Dev Aud-3", "Pat Guest", "Pat Host"]
    assert set(shown[0]) == {"ref", "role", "name", "company", "job_title", "attended"}
    assert [row["attended"] for row in shown] == [True, True, False, True, True]
    assert [row["name"] for row in display_people(roster, profile="public", limit=2, offset=1)] == ["Cleo Aud-2", "Dev Aud-3"]


def test_display_people_keeps_unknown_attendance_unknown():
    payload = public_session()
    unknown = person("aud-4", first="Eve")
    del unknown["attributes"]["registrant_detail"]
    payload["included"].append(unknown)
    shown = {row["name"]: row["attended"] for row in display_people(roster_from_session_payload(payload), profile="public")}
    assert shown["Eve Aud-4"] is None
    assert shown["Dev Aud-3"] is False and shown["Bram Aud-1"] is True


@pytest.mark.parametrize("profile", [None, "", "Public", " public", "Internal", "unknown", "webinar"])
def test_display_people_fails_closed(profile):
    assert display_people(roster_from_session_payload(public_session()), profile=profile) == []


def test_display_people_names_an_internal_session_too():
    """The owner's decision (November 2026): internal sessions are named on
    screen, as public ones are."""
    roster = roster_from_session_payload(public_session())
    assert display_people(roster, profile="internal") == display_people(roster, profile="public") != []


# ── The roster ──────────────────────────────────────────────────────────────


def test_the_include_roster_counts_people_by_role_and_reads_partial():
    meta = roster_from_session_payload(public_session()).meta
    assert (meta.source, meta.complete, meta.coverage) == ("session_include", None, "partial")
    assert (meta.people_count, meta.record_count, meta.registrants_count, meta.attendees_count) == (5, None, 5, 4)
    assert meta.role_counts == {"team": 1, "guest_speaker": 1, "audience": 3}
    assert meta.attended_role_counts == {"team": 1, "guest_speaker": 1, "audience": 2}
    assert meta.organizers == 1 and meta.internal is False and meta.session_id == "s1"


def test_an_internal_session_is_flagged_and_nobody_is_relabelled():
    roster = roster_from_session_payload(internal_session())
    assert roster.meta.internal is True
    assert roster.meta.role_counts == {"team": 5, "guest_speaker": 0, "audience": 0}
    assert roster.meta.attended_role_counts == {"team": 4, "guest_speaker": 0, "audience": 0}
    assert set(roster.people["role"]) == {TEAM}


def test_unknown_attendance_is_not_counted_as_attended():
    payload = public_session()
    unknown = person("aud-4")
    del unknown["attributes"]["registrant_detail"]
    payload["included"].append(unknown)
    meta = roster_from_session_payload(payload).meta
    assert meta.role_counts == {"team": 1, "guest_speaker": 1, "audience": 4}
    assert meta.attended_role_counts == {"team": 1, "guest_speaker": 1, "audience": 2}


def team(person_id, **kwargs):
    return person(person_id, role="team_member", **kwargs)


INTERNAL_CASES = {
    "public": (public_session(), False),
    "all-hands": (internal_session(), True),
    "no people": (session_payload([]), False),
    "no payload": ({}, False),
    # The team is 3 of 8 people but all 3 attendees: counted over attendees.
    "team are the attendees": (
        session_payload([team(f"tm-{i}") for i in range(3)] + [person(f"aud-{i}", attended=False) for i in range(5)]),
        True,
    ),
    # Team members who are guest speakers don't count as team.
    "team guest speakers": (session_payload([team(f"tm-{i}", guest=True) for i in range(3)] + [person("aud-1")]), False),
    # Nobody attended: counted over registrants.
    "nobody attended": (
        session_payload([team(f"tm-{i}", attended=False) for i in range(3)] + [person(f"aud-{i}", attended=False) for i in range(2)]),
        True,
    ),
    "attendance unknown": (
        session_payload([team(f"tm-{i}", attended=None) for i in range(3)] + [person(f"aud-{i}", attended=None) for i in range(2)]),
        True,
    ),
    # The team is 3 of 5 registrants, but the one attendee is audience.
    "one audience attendee": (
        session_payload([team(f"tm-{i}", attended=False) for i in range(3)] + [person("aud-1"), person("aud-2", attended=False)]),
        False,
    ),
    # The role is matched exactly, as classic does.
    "odd casing": (session_payload([person(f"tm-{i}", role="Team_Member") for i in range(3)]), False),
}


@pytest.mark.parametrize("payload, expected", list(INTERNAL_CASES.values()), ids=list(INTERNAL_CASES))
def test_the_internal_flag_is_the_classic_rule(payload, expected):
    assert session_is_internal(payload) is expected
    roster = roster_from_session_payload(payload)
    assert roster.meta.internal is expected


def test_the_internal_flag_and_counts_by_role_can_differ():
    """Measures read `meta.internal`; they never recompute it from the counts."""
    team_are_attendees = roster_from_session_payload(INTERNAL_CASES["team are the attendees"][0]).meta
    assert team_are_attendees.internal is True and team_are_attendees.role_counts["team"] == 3
    assert team_are_attendees.people_count == 8
    team_guest_speakers = roster_from_session_payload(INTERNAL_CASES["team guest speakers"][0]).meta
    assert team_guest_speakers.internal is False
    assert team_guest_speakers.attended_role_counts == {"team": 3, "guest_speaker": 0, "audience": 1}


def test_a_repeated_person_counts_once_and_other_records_are_skipped():
    people = public_session()["included"]
    later_copy = copy.deepcopy(people[2])
    later_copy["attributes"]["first_name"] = "Later"
    later_copy["attributes"]["registrant_detail"]["fields"] = [{"id": "job_title", "type": "text", "value": "Later title"}]
    payload = session_payload(people + [later_copy, {"id": "x", "type": "events"}, {"type": "people"}, "junk"])
    roster = roster_from_session_payload(payload)
    assert roster.meta.people_count == 5 and roster.people["person_id"].is_unique
    kept = roster.people.set_index("person_id").loc["aud-1"]
    assert kept["display_name"] == "Bram Aud-1" and kept["job_title"] is None  # the first copy wins


@pytest.mark.parametrize("payload", [None, {}, {"data": None}, {"data": {"id": "s1"}, "included": "junk"}, session_payload([])])
def test_an_empty_or_unusable_payload_gives_an_empty_roster_with_every_column(payload):
    roster = roster_from_session_payload(payload)
    assert list(roster.people.columns) == list(ROSTER_COLUMNS)
    assert roster.people.empty and roster.meta.people_count == 0
    assert roster.meta.coverage == "partial" and roster.meta.internal is False
    assert roster.meta.role_counts == {"team": 0, "guest_speaker": 0, "audience": 0}


def test_the_roster_has_typed_columns_and_unknown_attendance_stays_unknown():
    record = full_record("p-unknown")
    del record["attributes"]["registrant_detail"]
    people = roster_from_session_payload(session_payload([full_record(), record])).people
    assert str(people["attended"].dtype) == "boolean" and pd.isna(people["attended"].iloc[1])
    assert str(people["is_guest_speaker"].dtype) == "bool" and str(people["ls_messages_count"].dtype) == "Int64"
    assert people["registered_at"].iloc[0] == T0 - 5 * DAY and people["lead_bucket"].iloc[0] == "4-7"
    assert people["answers"].iloc[1] == {}


def test_the_roster_never_holds_the_email_join_link_or_device_data():
    roster = roster_from_session_payload(session_payload([full_record()]))
    assert "email" not in roster.people.columns
    dumped = roster.people.drop(columns=["display_name"]).to_json() + json.dumps(dataclasses.asdict(roster.meta))
    for value in NEVER_KEPT:
        assert value not in dumped, value


def test_building_a_roster_does_not_change_the_payload():
    payload = session_payload([full_record()] + public_session()["included"])
    before = copy.deepcopy(payload)
    roster_from_session_payload(payload)
    assert payload == before


# ── The loader ──────────────────────────────────────────────────────────────


def cached_row(**overrides):
    row = {
        "session_id": "s1",
        "session_payload": public_session(),
        "event_payload": {"data": {"attributes": {"fields": EVENT_FIELDS}}},
        "chat_payload": {"data": []},
        "questions_payload": {"data": []},
        "transcript_payload": None,
    }
    row.update(overrides)
    return row


def test_the_loader_builds_the_roster_from_the_include_list_by_default():
    inputs = load_record_inputs(cached_row())
    assert isinstance(inputs, RecordInputs)
    assert inputs.session_id == "s1" and inputs.clock.t0_unix == T0
    assert inputs.roster.meta.source == "session_include" and inputs.roster.meta.people_count == 5
    assert inputs.roster.meta.fields_source == "event_fields"
    assert inputs.chat_payload == {"data": []} and inputs.transcript_payload is None


def test_the_loader_prefers_stored_rows():
    stored = RosterRows(
        people=[parse_person(full_record()), parse_person(person("aud-9"))],
        complete=True,
        record_count=2,
        fields_source="none",
        synced_at="2026-10-08T09:00:00Z",
    )
    roster = load_record_inputs(cached_row(), roster_rows=stored).roster
    assert (roster.meta.source, roster.meta.complete, roster.meta.coverage) == ("paginated", True, "complete")
    assert roster.meta.record_count == 2 and roster.meta.synced_at == "2026-10-08T09:00:00Z"
    assert sorted(roster.people["person_id"]) == ["aud-9", "p-ada"]
    assert roster.meta.registrants_count == 5  # still the session's own counter
    # A ref is the same whichever source the people come from.
    assert roster.people["person_ref"].tolist() == [person_ref("s1", pid) for pid in roster.people["person_id"]]
    from_include = load_record_inputs(cached_row()).roster.people
    public_rows = RosterRows(people=[parse_person(record) for record in public_session()["included"]])
    from_rows = load_record_inputs(cached_row(), roster_rows=public_rows).roster.people
    assert dict(zip(from_rows["person_id"], from_rows["person_ref"], strict=True)) == dict(
        zip(from_include["person_id"], from_include["person_ref"], strict=True)
    )


def test_people_on_stage_come_from_the_same_source_as_the_record():
    """Stored rows win over the include list for the speaker cap too, so the
    record and the cap never read different people."""
    stored = RosterRows(
        people=[
            parse_person(person("host", role="team_member", session_role="organizer")),
            parse_person(person("cohost", role="team_member", session_role="organizer")),
            parse_person(person("guest", guest=True)),
            parse_person(person("aud-9")),
        ],
        complete=True,
        record_count=4,
    )
    assert count_people_on_stage(cached_row()) == 2  # the include list
    assert count_people_on_stage(cached_row(), roster_rows=stored) == 3
    assert people_on_stage(load_record_inputs(cached_row(), roster_rows=stored).roster) == 3
    assert count_people_on_stage(cached_row(), roster_rows=RosterRows(people=[])) is None


def test_a_stored_roster_becomes_roster_rows():
    """`db.fetch_session_roster`'s header and rows, as the loader takes them."""
    people = [parse_person(full_record()), parse_person(person("aud-9"))]
    fields = [{"slug": "team_size", "name": "Team size", "type": "select"}]
    stored = {
        "roster": {
            "roster_version": 3, "source": "paginated", "complete": True, "record_count": 2, "people_count": 2,
            "fields": fields, "fields_source": "people_attributes", "reason": "initial",
            "synced_at": datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc), "refresh_due_at": None, "refreshed_at": None,
        },
        "people": people,
    }
    rows = roster_module.stored_roster_rows(stored)
    assert rows == RosterRows(
        people=people, source="paginated", complete=True, record_count=2, fields=fields,
        fields_source="people_attributes", synced_at="2026-10-08T09:00:00+00:00",
    )
    meta = load_record_inputs(cached_row(), roster_rows=rows).roster.meta
    assert (meta.source, meta.complete, meta.coverage, meta.people_count, meta.record_count) == ("paginated", True, "complete", 2, 2)
    assert meta.fields == tuple(fields) and meta.fields_source == "people_attributes"
    json.dumps(dataclasses.asdict(meta))  # the time is kept as text: the header stays plain JSON


def test_a_stored_roster_with_an_odd_header_reads_as_unknown():
    people = [parse_person(person("aud-9"))]
    rows = roster_module.stored_roster_rows(
        {"roster": {"source": "", "complete": "yes", "record_count": "many", "fields": None, "synced_at": 0}, "people": people + ["x", None]}
    )
    assert rows == RosterRows(people=people, source="paginated", complete=None, record_count=None, fields=[], fields_source="none", synced_at=None)
    assert roster_module.stored_roster_rows({}) == RosterRows(people=[], fields=[])
    # A backfilled roster keeps its own source, and an unknown completeness stays partial.
    backfilled = roster_module.stored_roster_rows({"roster": {"source": "session_include", "complete": None}, "people": people})
    meta = roster_from_rows(backfilled, session_id="s1").meta
    assert (meta.source, meta.complete, meta.coverage) == ("session_include", None, "partial")


def stored_roster(people, **header):
    return roster_from_rows(RosterRows(people=people, **header), session_id="s1").meta


def test_stored_rows_read_complete_only_when_the_counts_match():
    people = [parse_person(person(f"p{index}")) for index in range(3)]
    meta = stored_roster(people, complete=True, record_count=3)
    assert (meta.complete, meta.coverage, meta.people_count) == (True, "complete", 3)


@pytest.mark.parametrize(
    "people, header",
    [
        # The source says the list is incomplete.
        ([parse_person(person("p1"))], {"complete": False, "record_count": 1}),
        # It says complete, but Livestorm counts more people.
        ([parse_person(person("p1"))], {"complete": True, "record_count": 5}),
        # It says complete, with no count to check.
        ([parse_person(person("p1"))], {"complete": True, "record_count": None}),
        # Rows without an id or repeated are skipped, so 2 rows give 1 person.
        ([parse_person(person("p1")), parse_person(person("p1"))], {"complete": True, "record_count": 2}),
        ([parse_person(person("p1")), {"person_id": None}], {"complete": True, "record_count": 2}),
    ],
)
def test_stored_rows_read_partial_when_not_reconciled(people, header):
    meta = stored_roster(people, **header)
    assert meta.complete is False and meta.coverage == "partial"


def test_stored_rows_without_a_claim_read_partial_and_unknown():
    meta = stored_roster([parse_person(person("p1"))], record_count=1)
    assert meta.complete is None and meta.coverage == "partial"


def test_the_loader_takes_the_session_id_from_the_payload_when_the_row_has_none():
    inputs = load_record_inputs(cached_row(session_id=None))
    assert inputs.session_id == "s1"
    assert inputs.roster.people["person_ref"].tolist() == [person_ref("s1", pid) for pid in inputs.roster.people["person_id"]]


def test_the_loader_without_a_session_payload_gives_no_clock_and_no_people():
    inputs = load_record_inputs(cached_row(session_payload=None, session_id="s9"))
    assert inputs.clock.has_clock is False and inputs.roster.meta.people_count == 0
    with pytest.raises(TypeError):
        load_record_inputs(None)


def test_the_roster_meta_is_json_safe():
    meta = load_record_inputs(cached_row()).roster.meta
    json.dumps(dataclasses.asdict(meta))


# ── Classic stays apart ─────────────────────────────────────────────────────


RECORD_PACKAGE = ("livestorm_app", "record")

# The ways in from outside `record/`, each importing the package inside its
# own body, so it loads only when that code runs, never at startup or on a
# classic read: the v1 record route's loader, and the transcription job's
# options, which count the people on stage and fall back to the default
# speaker cap if anything in `record/` fails.
RECORD_ENTRY_POINTS = {
    ("livestorm_app", "api_logic"): {
        "get_session_record", "get_session_chat_messages", "get_session_transcript_view", "run_session_analysis",
        # The question desk: its inputs, rows, the question a write names, and the CSV.
        "_question_desk_inputs", "get_session_question_desk", "save_session_question_reply", "get_session_open_questions_csv",
        # The desk's located answers (`record.understanding.located_answers`) and the host's decision on one.
        "_located_answers", "decide_session_located_answer",
        # The Transcript tab's inputs, and the chapters (`record.understanding.chapter_list`, `show_notes`).
        "_transcript_inputs", "_chapter_list", "get_session_chapters",
        # The Report's findings (`record.findings`), with the desk they read, and the Sessions library's row (`record.library`).
        "_findings_and_desk", "get_session_library_row",
        # The Report's "Who spoke up" (`record.voices`).
        "get_session_voices",
        # The Pulse's key moments, told (`record.moments`).
        "get_session_moments",
        # The Follow-up's Session analysis: its brief (`record.session_brief`) from the record, the understanding and the desk.
        "_write_session_analysis",
        # The Follow-up's FAQ: its brief (`record.faq_brief`) from the question desk.
        "_write_faq",
        # Public formats (FAQ, Smart Recap, content): the audience's names to redact (`record.redaction`).
        "_public_redactor",
        # Surprise Me!: what StormIQ measured (`record.surprise_brief`), and the take checked against it.
        "_surprise_facts", "_check_surprise",
        # The Follow-up's decisions and commitments: the understanding's dossier (`record.understanding.build_dossier`), checked by `record.decisions`.
        "_write_decisions",
        # The Follow-up's team digest: its brief (`record.digest_brief`) from the record's measures, moments, chapters, desk and listed decisions.
        "_write_team_digest",
        # The v1.1 public formats: the attendee follow-up's brief (`record.format_briefs`) from the
        # placed chapters, the moments of content and the desk's answered questions, and its check.
        "_placed_chapters", "_attendee_followup_brief", "_write_public_format", "run_public_format",
        # Before you share: the figure checks (`record.figure_checks`).
        "_figure_check_list", "get_session_figure_checks", "save_session_figure_check",
        # The analysis's second step: the dossier, its checks and the result's version.
        "_read_understanding",
    },
    ("livestorm_app", "worker"): {"_transcription_options"},
    # The people sync's worker side: field definitions, `P-` refs and the session's end.
    ("livestorm_app", "people_sync"): {"run_people_sync"},
    # Checks a set of chat labels before it is written (`record.chat_labels.check_labels`).
    ("livestorm_app", "db"): {"replace_chat_labels", "replace_understanding"},
    # Labels the chat in the worker: the chat's hash and the labels' checks.
    ("livestorm_app", "ai", "chat_labelling"): {"label_chat"},
}


def _names_record(node, package):
    """Whether one AST node imports `livestorm_app.record`, absolutely or
    relatively, or names it in a string (as `importlib` would)."""
    if isinstance(node, ast.Import):
        targets = [tuple(alias.name.split(".")) for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
        base = package[: len(package) - node.level + 1] if node.level else []
        module = tuple(base + (node.module.split(".") if node.module else []))
        targets = [module] + [module + (alias.name,) for alias in node.names]
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        targets = [tuple(node.value.split("."))] if node.value.startswith("livestorm_app.") else []
    else:
        return False
    return any(target[: len(RECORD_PACKAGE)] == RECORD_PACKAGE for target in targets)


def record_import_sites(source, module_parts):
    """Where Python source imports `livestorm_app.record`: one entry per
    import, the name of the top-level function whose body holds it, or None
    anywhere else (module level, a class, a decorator or a default value).
    `module_parts` is the module's dotted name, split."""
    package = list(module_parts[:-1])
    sites = []
    for statement in ast.parse(source).body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inside = [node for part in statement.body for node in ast.walk(part)]
            outside = [
                node
                for part in [*statement.decorator_list, statement.args, *([statement.returns] if statement.returns else [])]
                for node in ast.walk(part)
            ]
            sites += [statement.name for node in inside if _names_record(node, package)]
            sites += [None for node in outside if _names_record(node, package)]
        else:
            sites += [None for node in ast.walk(statement) if _names_record(node, package)]
    return sites


def imports_record(source, module_parts):
    """Whether Python source imports `livestorm_app.record` anywhere."""
    return bool(record_import_sites(source, module_parts))


@pytest.mark.parametrize(
    "source, module, expected",
    [
        ("from livestorm_app.record.roster import x", "livestorm_app.api_logic", True),
        ("import livestorm_app.record.clock", "livestorm_app.api_logic", True),
        ("from livestorm_app import record", "livestorm_app.api_logic", True),
        ("from . import record", "livestorm_app.__init__", True),
        ("from . import record", "livestorm_app.api_logic", True),
        ("from .. import record", "livestorm_app.analytics.audience", True),
        ("from ..record.roster import x", "livestorm_app.analytics.audience", True),
        ("from .record import inputs", "livestorm_app.db", True),
        ("importlib.import_module('livestorm_app.record.roster')", "livestorm_app.db", True),
        ("from . import records_helper", "livestorm_app.api_logic", False),
        ("from .analytics import audience", "livestorm_app.api_logic", False),
        ("from . import record", "livestorm_app.analytics.audience", False),  # livestorm_app.analytics.record
        ("record = 1", "livestorm_app.api_logic", False),
    ],
)
def test_the_import_guard_sees_every_form_of_import(source, module, expected):
    assert imports_record(source, tuple(module.split("."))) is expected


@pytest.mark.parametrize(
    "source, expected",
    [
        ("from livestorm_app.record import assemble", [None]),
        ("def get_session_record():\n    from livestorm_app.record import assemble", ["get_session_record"]),
        ("def get_session_record():\n    def inner():\n        import livestorm_app.record.clock", ["get_session_record"]),
        ("@decorate('livestorm_app.record.x')\ndef get_session_record():\n    pass", [None]),
        ("def get_session_record(x='livestorm_app.record.x'):\n    pass", [None]),
        ("class Reader:\n    def get_session_record(self):\n        from livestorm_app.record import assemble", [None]),
        ("def other():\n    from .record import inputs", ["other"]),
    ],
)
def test_the_import_guard_knows_where_an_import_runs(source, expected):
    assert record_import_sites(source, ("livestorm_app", "api_logic")) == expected


def _classic_sources():
    return [REPO_ROOT / "app.py"] + [
        path for path in (REPO_ROOT / "livestorm_app").rglob("*.py") if "record" not in path.relative_to(REPO_ROOT).parts
    ]


def test_classic_code_does_not_import_the_record_package():
    """A bug in `record/` must not be able to break a classic read. Outside
    `record/`, only the functions in RECORD_ENTRY_POINTS import it (the record
    route's loader and two worker-side functions), inside their body."""
    offenders = []
    for path in _classic_sources():
        parts = path.relative_to(REPO_ROOT).with_suffix("").parts
        allowed = RECORD_ENTRY_POINTS.get(parts, set())
        for site in record_import_sites(path.read_text(encoding="utf-8"), parts):
            if site not in allowed:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {site or 'outside a function'}")
    assert offenders == []
    assert roster_module.__name__ == "livestorm_app.record.roster"


def test_the_record_entry_point_still_imports_the_package():
    """Keeps the allowance above from outliving the code it allows."""
    for parts, functions in RECORD_ENTRY_POINTS.items():
        source = (REPO_ROOT / Path(*parts)).with_suffix(".py").read_text(encoding="utf-8")
        assert set(record_import_sites(source, parts)) == functions


def test_starting_the_app_and_reading_a_classic_workspace_never_loads_the_record():
    """In a fresh process: the app starts and serves a classic workspace
    without `livestorm_app.record` ever being imported."""
    script = (
        "import sys\n"
        "sys.path[:0] = [sys.argv[1], sys.argv[2]]\n"
        "import conftest\n"
        "import synthetic_session as synthetic\n"
        "assert synthetic.classic_workspace(synthetic.cached_row())['sessionId'] == synthetic.SESSION_ID\n"
        "loaded = sorted(name for name in sys.modules if name.startswith('livestorm_app.record'))\n"
        "assert loaded == [], loaded\n"
        "assert synthetic.record_response()['record']['version'] == sys.modules['livestorm_app.record.assemble'].RECORD_VERSION\n"
        "assert 'livestorm_app.record.measures' in sys.modules\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(REPO_ROOT), str(REPO_ROOT / "tests")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
