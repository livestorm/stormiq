"""Fetching a session's people from Livestorm, on fake HTTP.

The paginated people list (`client.fetch_session_people`), the workspace's
people attributes (`client.fetch_people_attributes`), their payload helpers,
and the pure parts of `scripts/diagnose_people_cap.py`. Synthetic people
only (`tests/synthetic_session.py`); nothing reaches Livestorm.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from livestorm_app.livestorm import client
from livestorm_app.livestorm import payloads
import synthetic_session as synthetic

JOIN_LINK_KEYS = ("password_key", "connection_link")
FAR_DEADLINE = float("inf")


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = CaseInsensitiveDict(headers or {})

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return copy.deepcopy(self._payload)


def _person(person_id: str, **attributes) -> dict:
    return {"id": person_id, "type": "people", "attributes": {"role": "participant", **attributes}}


def _serve_pages(monkeypatch, pages: dict, *, headers: dict | None = None) -> list:
    """Serve `pages[number] = (records, meta)` and record each request.

    A `meta` of None serves the page without pagination metadata. `headers`
    maps a page number to its response headers.
    """
    calls: list = []

    def fake_get(url, *, headers: dict, params=None):
        calls.append({"url": url, **dict(params or {})})
        number = (params or {}).get("page[number]")
        records, meta = pages[number]
        payload = {"data": records} if meta is None else {"data": records, "meta": meta}
        return FakeResponse(payload, headers=(headers_by_page or {}).get(number))

    headers_by_page = headers
    monkeypatch.setattr(client, "livestorm_get", fake_get)
    return calls


def _meta(next_page, record_count) -> dict:
    return {"next_page": next_page, "record_count": record_count}


# ── The paginated people list ───────────────────────────────────────────────


def test_the_people_pager_asks_for_50_and_follows_next_page_until_null(monkeypatch):
    people = [_person(f"p{index}") for index in range(107)]
    calls = _serve_pages(
        monkeypatch,
        {0: (people[:50], _meta(1, 107)), 1: (people[50:100], _meta(2, 107)), 2: (people[100:], _meta(None, 107))},
    )
    result = client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE)

    assert [call["page[number]"] for call in calls] == [0, 1, 2]
    assert {call["page[size]"] for call in calls} == {50}
    assert {call["url"] for call in calls} == {"https://api.livestorm.co/v1/sessions/s1/people"}
    assert [person["id"] for person in result["people"]] == [f"p{index}" for index in range(107)]
    assert result["record_count"] == 107
    assert result["complete"] is True
    assert result["pages_fetched"] == 3
    assert result["duplicates"] == 0


def test_the_people_pager_stops_on_a_null_next_page_even_after_a_full_page(monkeypatch):
    calls = _serve_pages(monkeypatch, {0: ([_person(f"p{index}") for index in range(50)], _meta(None, 50))})
    result = client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE)
    assert [call["page[number]"] for call in calls] == [0]
    assert result["complete"] is True


def test_the_people_pager_never_asks_for_more_than_50(monkeypatch):
    calls = _serve_pages(monkeypatch, {0: ([], _meta(None, 0))})
    client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE, page_size=100)
    assert calls[0]["page[size]"] == 50


def test_the_people_pager_keeps_one_copy_of_a_person_listed_twice(monkeypatch):
    # The list can shift while people register: p3 comes back on page 1.
    _serve_pages(
        monkeypatch,
        {
            0: ([_person("p1"), _person("p2"), _person("p3", timezone="Europe/Paris")], _meta(1, 4)),
            1: ([_person("p3", timezone="Europe/Berlin"), _person("p4"), {"type": "people", "attributes": {}}], _meta(None, 4)),
        },
    )
    result = client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE, page_size=3)
    assert [person["id"] for person in result["people"]] == ["p1", "p2", "p3", "p4"]
    assert result["people"][2]["attributes"]["timezone"] == "Europe/Berlin", "the last listed copy is kept"
    assert result["duplicates"] == 1
    assert result["complete"] is True, "a record without an id is dropped, not counted"


@pytest.mark.parametrize(
    ("record_count", "complete"),
    [(5, False), (3, False), (4, True), (None, None)],
    ids=["fewer-people-than-the-count", "more-people-than-the-count", "matching", "no-count"],
)
def test_completeness_is_the_unique_people_against_record_count(monkeypatch, record_count, complete):
    meta = {"next_page": None} if record_count is None else _meta(None, record_count)
    _serve_pages(monkeypatch, {0: ([_person(f"p{index}") for index in range(4)], meta)})
    result = client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE)
    assert result["record_count"] == record_count
    assert result["complete"] is complete


def test_the_people_pager_reads_the_count_from_the_last_page_that_sent_one(monkeypatch):
    _serve_pages(monkeypatch, {0: ([_person("p1")], _meta(1, 3)), 1: ([_person("p2")], _meta(None, 2))})
    result = client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE, page_size=1)
    assert result["record_count"] == 2
    assert result["complete"] is True


def test_the_people_pager_strips_every_join_link(monkeypatch):
    people = synthetic.people()
    assert any(key in json.dumps(people) for key in JOIN_LINK_KEYS), "the fixture must hold join links"
    _serve_pages(monkeypatch, {0: (people[:5], _meta(1, 10)), 1: (people[5:], _meta(None, 10))})

    result = client.fetch_session_people("Bearer t", synthetic.SESSION_ID, deadline=FAR_DEADLINE, page_size=5)

    assert len(result["people"]) == 10
    assert not any(key in json.dumps(result) for key in JOIN_LINK_KEYS)
    host = result["people"][0]["attributes"]["registrant_detail"]
    assert host["fields"][0]["id"] == "email" and host["attended"] is True, "every other key is kept"


def test_the_people_pager_raises_past_its_deadline_and_returns_nothing(monkeypatch):
    calls = _serve_pages(monkeypatch, {0: ([_person("p1")], _meta(1, 2)), 1: ([_person("p2")], _meta(None, 2))})
    with pytest.raises(client.LivestormDeadlineExceeded):
        client.fetch_session_people("Bearer t", "s1", deadline=0.0)
    assert calls == [], "a deadline already passed makes no call"

    clock = iter([100.0, 200.0])
    monkeypatch.setattr(client, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(client.LivestormDeadlineExceeded):
        client.fetch_session_people("Bearer t", "s1", deadline=150.0, page_size=1)
    assert [call["page[number]"] for call in calls] == [0], "the deadline is checked before each page"


def test_a_failed_page_raises_and_returns_nothing(monkeypatch):
    def fake_get(url, *, headers, params=None):
        if params["page[number]"] == 1:
            return FakeResponse({"errors": []}, status_code=503)
        return FakeResponse({"data": [_person("p1")], "meta": _meta(1, 2)})

    monkeypatch.setattr(client, "livestorm_get", fake_get)
    with pytest.raises(requests.HTTPError):
        client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE, page_size=1)


def test_a_next_page_already_read_raises_and_returns_nothing(monkeypatch):
    calls = _serve_pages(monkeypatch, {0: ([_person("p1")], _meta(1, 3)), 1: ([_person("p2")], _meta(0, 3))})
    with pytest.raises(client.LivestormPaginationError):
        client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE, page_size=1)
    assert [call["page[number]"] for call in calls] == [0, 1], "page 0 is never asked for twice"

    _serve_pages(monkeypatch, {0: ([_person("p1")], _meta(0, None))})
    with pytest.raises(client.LivestormPaginationError):
        client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE, page_size=1)


def test_a_list_longer_than_max_pages_raises_and_returns_nothing(monkeypatch):
    monkeypatch.setattr(client, "MAX_PAGES", 2)
    pages = {number: ([_person(f"p{number}")], _meta(number + 1, 3)) for number in range(3)}
    calls = _serve_pages(monkeypatch, pages)
    with pytest.raises(client.LivestormPaginationError):
        client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE, page_size=1)
    assert [call["page[number]"] for call in calls] == [0, 1]

    # Without pagination metadata, a full last page also means "more to come".
    calls = _serve_pages(monkeypatch, {number: ([_person(f"p{number}")], None) for number in range(3)})
    with pytest.raises(client.LivestormPaginationError):
        client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE, page_size=1)
    assert [call["page[number]"] for call in calls] == [0, 1]


def test_a_list_that_ends_on_its_max_page_is_whole(monkeypatch):
    monkeypatch.setattr(client, "MAX_PAGES", 2)
    _serve_pages(monkeypatch, {0: ([_person("p1")], _meta(1, 2)), 1: ([_person("p2")], _meta(None, 2))})
    result = client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE, page_size=1)
    assert (len(result["people"]), result["complete"]) == (2, True)


def test_the_people_pager_reads_the_monthly_rate_limit_header_as_a_number(monkeypatch):
    _serve_pages(
        monkeypatch,
        {0: ([_person("p1")], _meta(1, 3)), 1: ([_person("p2")], _meta(2, 3)), 2: ([_person("p3")], _meta(None, 3))},
        headers={0: {"RateLimit-Monthly-Remaining": "9876"}, 1: {"ratelimit-monthly-remaining": " 9875 "}, 2: {}},
    )
    result = client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE, page_size=1)
    assert result["monthly_remaining"] == 9875, "the last page that sent it, read as a number"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"RateLimit-Monthly-Remaining": "42"}, 42),
        ({"RATELIMIT-MONTHLY-REMAINING": "0"}, 0),
        ({"RateLimit-Monthly-Remaining": "lots"}, None),
        ({"RateLimit-Monthly-Remaining": "-1"}, None),
        ({"RateLimit-Monthly-Limit": "10000"}, None),
        ({}, None),
    ],
)
def test_monthly_remaining_is_a_whole_number_or_none(headers, expected):
    assert client.monthly_remaining(CaseInsensitiveDict(headers)) == expected
    assert client.monthly_remaining(None) is None


@pytest.mark.parametrize(
    "fetch",
    [
        lambda: client.fetch_session_people("Bearer t", "s1", deadline=FAR_DEADLINE),
        lambda: client.fetch_people_attributes("Bearer t"),
    ],
    ids=["people", "people-attributes"],
)
def test_every_people_call_has_a_timeout(monkeypatch, fetch):
    timeouts: list = []

    def fake_http_get(url, *, headers, params=None, timeout=None):
        timeouts.append(timeout)
        return FakeResponse({"data": [], "meta": _meta(None, 0)})

    monkeypatch.setattr(client._livestorm_http, "get", fake_http_get)
    fetch()
    assert timeouts == [client.LIVESTORM_TIMEOUT]


# ── The workspace's people attributes ───────────────────────────────────────


def _attribute(slug, *, name=None, kind="text", builtin=False, options=None) -> dict:
    return {
        "id": f"attr-{slug}",
        "type": "people_attributes",
        "attributes": {
            "slug": slug, "type": kind, "builtin": builtin, "name": name or slug.title(),
            "placeholder": "Type here", "description": "A synthetic field", "user_id": "u-synthetic-creator",
            "created_at": 1700000000, "updated_at": 1700000000, "options": options or [],
        },
    }


TEAM_SIZE_OPTIONS = [
    {"value": "1-10", "label": "1 to 10", "position": 0},
    {"value": "11-50", "label": "11 to 50", "position": 1},
]


def test_people_attributes_are_read_from_every_page_without_user_ids(monkeypatch):
    calls = _serve_pages(
        monkeypatch,
        {
            0: ([_attribute("email", builtin=True, kind="email"), _attribute("team_size", name="Team size", kind="unique_select", options=TEAM_SIZE_OPTIONS)], _meta(1, 3)),
            1: ([_attribute("goal", name="Your goal")], _meta(None, 3)),
        },
    )
    result = client.fetch_people_attributes("Bearer t", page_size=2)

    assert {call["url"] for call in calls} == {"https://api.livestorm.co/v1/people_attributes"}
    assert result["status"] == "ok" and result["http_status"] == 200
    assert [attribute["slug"] for attribute in result["attributes"]] == ["email", "team_size", "goal"]
    assert result["attributes"][1] == {
        "slug": "team_size", "name": "Team size", "type": "unique_select", "builtin": False, "options": TEAM_SIZE_OPTIONS,
    }
    assert "u-synthetic-creator" not in json.dumps(result)
    assert "A synthetic field" not in json.dumps(result)


@pytest.mark.parametrize("status_code", [401, 403])
def test_people_attributes_refused_are_forbidden_not_a_failure(monkeypatch, status_code):
    monkeypatch.setattr(client, "livestorm_get", lambda url, *, headers, params=None: FakeResponse({}, status_code=status_code))
    assert client.fetch_people_attributes("Bearer t") == {"attributes": [], "status": "forbidden", "http_status": status_code}


def test_people_attributes_raise_past_their_deadline(monkeypatch):
    calls = _serve_pages(monkeypatch, {0: ([_attribute("goal")], _meta(None, 1))})
    with pytest.raises(client.LivestormDeadlineExceeded):
        client.fetch_people_attributes("Bearer t", deadline=0.0)
    assert calls == [], "a deadline already passed makes no call"


def test_people_attributes_that_never_reach_their_last_page_raise(monkeypatch):
    _serve_pages(monkeypatch, {0: ([_attribute("goal")], _meta(1, 2)), 1: ([_attribute("plan")], _meta(0, 2))})
    with pytest.raises(client.LivestormPaginationError):
        client.fetch_people_attributes("Bearer t", page_size=1)


@pytest.mark.parametrize("status_code", [400, 404, 500])
def test_other_people_attributes_errors_raise(monkeypatch, status_code):
    monkeypatch.setattr(client, "livestorm_get", lambda url, *, headers, params=None: FakeResponse({}, status_code=status_code))
    with pytest.raises(requests.HTTPError):
        client.fetch_people_attributes("Bearer t")


def test_parse_people_attributes_keeps_one_copy_per_slug_and_the_parsed_keys_only():
    records = [
        _attribute("goal", name="Your goal"),
        _attribute("goal", name="A repeated slug"),
        {"type": "people_attributes", "attributes": {"name": "No slug"}},
        "not a record",
        {"attributes": {"slug": "plan", "type": "UNIQUE_SELECT", "builtin": "true", "options": [{"label": "Pro"}, {"position": 2}, "x"]}},
    ]
    parsed = payloads.parse_people_attributes({"data": records})
    assert parsed == [
        {"slug": "goal", "name": "Your goal", "type": "text", "builtin": False, "options": []},
        {"slug": "plan", "name": None, "type": "unique_select", "builtin": True, "options": [{"value": None, "label": "Pro", "position": None}]},
    ]
    assert payloads.parse_people_attributes(records) == parsed
    assert payloads.parse_people_attributes(None) == []
    assert all(set(attribute) == set(payloads.PEOPLE_ATTRIBUTE_KEYS) for attribute in parsed)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"meta": {"record_count": 25}}, 25),
        ({"meta": {"record_count": "25"}}, 25),
        ({"meta": {"record_count": 0}}, 0),
        ({"meta": {"record_count": -1}}, None),
        ({"meta": {"record_count": True}}, None),
        ({"meta": {"record_count": 2.5}}, None),
        ({"meta": {"next_page": None}}, None),
        ({"meta": {"pagination": {"record_count": 7}}}, 7),
        ({}, None),
        ([], None),
    ],
)
def test_extract_record_count(payload, expected):
    assert payloads.extract_record_count(payload) == expected


# ── scripts/diagnose_people_cap.py ──────────────────────────────────────────


def _people_cap_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "diagnose_people_cap.py"
    spec = importlib.util.spec_from_file_location("diagnose_people_cap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_people_cap_script_picks_sessions_without_the_bench_ones():
    script = _people_cap_script()
    listed = ["s-2", "bench-10min", "s-1"]
    assert script.select_sessions(listed, explicit=None, include_bench=False) == ["s-2", "s-1"]
    assert script.select_sessions(listed, explicit=None, include_bench=True) == listed
    assert script.select_sessions(listed, explicit=["s-1", " s-9 ", "s-1", ""], include_bench=False) == ["s-1", "s-9"]


def test_the_people_cap_script_spots_the_monthly_headers_only():
    script = _people_cap_script()
    assert script.has_monthly_header(CaseInsensitiveDict({"ratelimit-monthly-remaining": "5"})) is True
    assert script.has_monthly_header(CaseInsensitiveDict({"RateLimit-Interval-Limit": "5"})) is False
    assert script.has_monthly_header(None) is False


def test_the_people_cap_script_counts_how_answers_match_the_attributes():
    script = _people_cap_script()
    people = synthetic.people()
    # Answers saved as an option's label, and one matching nothing.
    people[4]["attributes"]["registrant_detail"]["fields"].append({"id": "goals", "type": "multiple_select", "value": ["Learn", "Other"]})
    attributes = payloads.parse_people_attributes([
        _attribute("email"), _attribute("team_size", kind="unique_select", options=[
            {"value": "1-10", "label": "Tiny", "position": 0},
            {"value": "11-50", "label": "Small", "position": 1},
        ]),
        _attribute("goals", kind="multiple_select", options=[{"value": "learn", "label": "Learn", "position": 0}]),
    ])
    assert script.field_checks(people, attributes) == {
        "ids": 7,  # email, first_name, last_name, company, job_title, team_size, goals
        "matching_attribute_slug": 3,
        "select_answers_value": 2,  # 11-50 and 1-10
        "select_answers_label_only": 1,  # Learn
        "select_answers_neither": 2,  # 51-200 and Other
    }
    assert script.field_checks(people, [])["select_answers_neither"] == 5


def _fake_livestorm_for_the_script(
    monkeypatch, *, attributes_status: int = 403, people_list: str = "ok", attributes_list: str = "ok"
) -> list:
    """A session of 10 synthetic people behind every endpoint the script calls.

    `people_list` and `attributes_list` break the paginated lists: "503"
    fails them, "loop" points their `next_page` back at page 0. The
    one-item count probes always answer.
    """
    people = synthetic.people()
    session_payload = synthetic.session_payload()
    session_id = synthetic.SESSION_ID
    calls: list = []

    def count(filters: dict) -> int:
        def keep(person):
            attributes = person["attributes"]
            role = filters.get("filter[role]")
            attended = filters.get("filter[attended]")
            return (role is None or attributes["role"] == role) and (
                attended is None or attributes["registrant_detail"]["attended"] is (attended == "true")
            )

        return sum(1 for person in people if keep(person))

    def fake_get(url, *, headers, params=None):
        calls.append(url)
        params = dict(params or {})
        path = url.split("/v1", 1)[1]
        monthly = {"RateLimit-Monthly-Remaining": "9000", "RateLimit-Monthly-Limit": "10000"}
        if path == f"/sessions/{session_id}":
            return FakeResponse(session_payload, headers=monthly)
        if path == f"/sessions/{session_id}/people":
            filters = {key: value for key, value in params.items() if key.startswith("filter[")}
            if params["page[size]"] == 1:
                return FakeResponse({"data": people[:1], "meta": _meta(None, count(filters))}, headers=monthly)
            if people_list == "503":
                return FakeResponse({"errors": [{"detail": url}]}, status_code=503)
            next_page = 0 if people_list == "loop" else None
            return FakeResponse({"data": people, "meta": _meta(next_page, len(people))}, headers=monthly)
        if path == "/people_attributes":
            if attributes_status != 200:
                return FakeResponse({"errors": [{"detail": url}]}, status_code=attributes_status)
            next_page = 0 if attributes_list == "loop" else None
            records = [_attribute("team_size", kind="unique_select", options=TEAM_SIZE_OPTIONS)]
            return FakeResponse({"data": records, "meta": _meta(next_page, 1)})
        return FakeResponse({}, status_code=404)

    monkeypatch.setattr(client, "livestorm_get", fake_get)
    return calls


# What a line of the script may hold besides `#n`, integers and `name=value`
# pairs whose value is an integer, yes, no or "-".
SCRIPT_WORDS = {
    "include", "counters", "paginated", "filters", "fields", "session", "HTTP", "people_attributes",
    "LivestormPaginationError",
}


def _assert_integers_and_yes_or_no_only(lines: list, session_ids=(synthetic.SESSION_ID,)) -> None:
    output = "\n".join(lines)
    for session_id in session_ids:
        assert session_id not in output
    for private in (*synthetic.PRIVATE_VALUES, "/v1/", "://", "@"):
        assert private not in output, private
    for line in lines[1:]:
        for token in line.split():
            name, equals, value = token.partition("=")
            if equals:
                assert re.fullmatch(r"[a-z_]+", name) and re.fullmatch(r"\d+|yes|no|-", value), token
            else:
                assert re.fullmatch(r"#\d+|\d+", token) or token in SCRIPT_WORDS, token


def test_the_people_cap_script_prints_integers_and_yes_or_no_only(monkeypatch):
    script = _people_cap_script()
    calls = _fake_livestorm_for_the_script(monkeypatch)
    fake_get = client.livestorm_get
    lines: list = []

    made = script.run("Bearer t", [synthetic.SESSION_ID, "missing-session"], out=lines.append)

    assert lines == [
        "Livestorm people counts for 2 session(s). Integers and yes/no only.",
        "#1 include people=10 role_team_member=2 role_participant=8",
        "#1 counters registrants_count=10 attendees_count=8",
        "#1 paginated record_count=10 unique_people=10 pages=1 duplicates=0 complete=yes",
        "#1 filters role_team_member=2 role_participant=8 attended=8 attended_team_member=2",
        "#1 fields ids=6 matching_attribute_slug=0 select_answers_value=0 select_answers_label_only=0 select_answers_neither=3",
        "#2 session HTTP 404",
        "people_attributes http_status=403 forbidden=yes count=0",
        "ratelimit_monthly_headers=yes",
        f"calls={len(calls)}",
    ]
    assert made == len(calls) == 1 + 1 + 1 + 4 + 1
    assert client.livestorm_get is fake_get, "the call counter is removed after the run"
    _assert_integers_and_yes_or_no_only(lines, (synthetic.SESSION_ID, "missing-session"))


@pytest.mark.parametrize(
    ("breakage", "paginated", "attributes"),
    [
        ({"people_list": "503", "attributes_status": 500}, "#1 paginated HTTP 503", "people_attributes HTTP 500"),
        (
            {"people_list": "loop", "attributes_status": 200, "attributes_list": "loop"},
            "#1 paginated LivestormPaginationError",
            "people_attributes LivestormPaginationError",
        ),
        ({"deadline": True}, "#1 paginated stopped_after_s=600", "people_attributes http_status=403 forbidden=yes count=0"),
    ],
    ids=["failed-calls", "lists-that-loop", "deadline"],
)
def test_the_people_cap_script_prints_integers_and_yes_or_no_only_when_calls_fail(monkeypatch, breakage, paginated, attributes):
    script = _people_cap_script()
    breakage = dict(breakage)
    if breakage.pop("deadline", False):
        # The script's deadline is 600 s from now; the pager's clock has passed it.
        monkeypatch.setattr(client, "time", SimpleNamespace(monotonic=lambda: float("inf")))
    _fake_livestorm_for_the_script(monkeypatch, **breakage)
    lines: list = []

    script.run("Bearer t", [synthetic.SESSION_ID], out=lines.append)

    assert paginated in lines and attributes in lines
    assert "#1 fields ids=0 matching_attribute_slug=0 select_answers_value=0 select_answers_label_only=0 select_answers_neither=0" in lines
    assert "#1 filters role_team_member=2 role_participant=8 attended=8 attended_team_member=2" in lines
    _assert_integers_and_yes_or_no_only(lines)


def test_the_people_cap_script_keeps_urllib3_retry_warnings_off_stderr(monkeypatch):
    script = _people_cap_script()
    urllib3_logger = logging.getLogger("urllib3")
    saved = urllib3_logger.level
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("LS_API_KEY", "Bearer t")
    monkeypatch.setattr("sys.argv", ["diagnose_people_cap.py", "--session", "s1"])
    monkeypatch.setattr(script, "run", lambda key, session_ids: 0)
    try:
        urllib3_logger.setLevel(logging.NOTSET)
        assert script.main() == 0
        # urllib3 logs "Retrying (...) after connection broken by ...: <url>" at WARNING.
        assert not logging.getLogger("urllib3.connectionpool").isEnabledFor(logging.WARNING)
    finally:
        urllib3_logger.setLevel(saved)


def test_the_people_cap_script_reads_attribute_labels_when_allowed(monkeypatch):
    script = _people_cap_script()
    _fake_livestorm_for_the_script(monkeypatch, attributes_status=200)
    lines: list = []
    script.run("Bearer t", [synthetic.SESSION_ID], out=lines.append)
    assert "people_attributes http_status=200 forbidden=no count=1" in lines
    assert (
        "#1 fields ids=6 matching_attribute_slug=1 select_answers_value=2 select_answers_label_only=0 select_answers_neither=1"
        in lines
    )


def test_the_people_cap_script_refuses_a_remote_database_without_naming_it(monkeypatch):
    script = _people_cap_script()
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example.test:5432/app")
    with pytest.raises(SystemExit) as refused:
        script.require_local_database_if_set()
    assert "db.example.test" not in str(refused.value.code)

    monkeypatch.setenv("DATABASE_URL", "postgresql://stormiq:stormiq@localhost:5433/stormiq")
    script.require_local_database_if_set()
    monkeypatch.setenv("DATABASE_URL", "")
    script.require_local_database_if_set()


def test_the_people_cap_script_needs_a_key_from_the_environment(monkeypatch):
    script = _people_cap_script()
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("LS_API_KEY", "")
    monkeypatch.setattr("sys.argv", ["diagnose_people_cap.py", "--session", "s1"])
    with pytest.raises(SystemExit) as refused:
        script.main()
    assert "LS_API_KEY" in str(refused.value.code)
