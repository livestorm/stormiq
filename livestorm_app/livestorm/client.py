"""Livestorm REST API client: the shared HTTP session (timeouts, capped retries), fetchers, and error formatting."""

from __future__ import annotations

import time
from http.cookiejar import DefaultCookiePolicy
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from livestorm_app.analytics.audience import (
    build_chat_df_from_payload,
    build_questions_df_from_payload,
)
from livestorm_app.config import (
    API_BASE,
    DEFAULT_PAGE_SIZE,
    LIVESTORM_MAX_PAGE_SIZE,
    MAX_PAGES,
    START_PAGE_NUMBER,
)
from livestorm_app.livestorm.payloads import (
    _extract_next_page,
    _states_last_page,
    extract_events,
    extract_messages,
    extract_questions,
    extract_record_count,
    extract_records,
    extract_sessions,
    parse_people_attributes,
    strip_payload_join_links,
    strip_registrant_join_link,
)


def build_headers(key: str) -> Dict[str, str]:
    return {
        "Authorization": key,
        "accept": "application/vnd.api+json",
    }


# ── Livestorm HTTP ────────────────────────────────────────────────────────────
#
# Every Livestorm call is a GET, so retrying is safe: rate limits (429),
# transient server errors and dropped connections are retried with
# exponential backoff (0s, 1s, 2s). A long `Retry-After` is capped so a web
# request never sleeps for minutes. After the last attempt the final
# response is returned and the caller's `raise_for_status()` reports it.

LIVESTORM_TIMEOUT = (10, 30)  # (connect, read) seconds
LIVESTORM_MAX_RETRY_AFTER_SECONDS = 10.0


class _CappedRetry(Retry):
    def get_retry_after(self, response):  # type: ignore[override]
        value = super().get_retry_after(response)
        return None if value is None else min(value, LIVESTORM_MAX_RETRY_AFTER_SECONDS)


def _build_livestorm_http() -> requests.Session:
    retry = _CappedRetry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    # Shared by every user's requests: never keep cookies between them.
    session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


_livestorm_http = _build_livestorm_http()


def livestorm_get(url: str, *, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None) -> requests.Response:
    """GET a Livestorm API URL with retries and timeouts (see above)."""
    return _livestorm_http.get(url, headers=headers, params=params, timeout=LIVESTORM_TIMEOUT)


def format_livestorm_http_error(exc: requests.HTTPError, resource_label: str) -> str:
    response = exc.response
    status_code = response.status_code if response is not None else None
    details = ""

    if response is not None:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                if isinstance(payload.get("error"), str):
                    details = payload["error"]
                elif isinstance(payload.get("message"), str):
                    details = payload["message"]
        except ValueError:
            details = ""

    if status_code == 400:
        return (
            f"{resource_label} request is invalid (HTTP 400). "
            "Please verify the provided ID and request parameters."
        )
    if status_code in (401, 403):
        return (
            f"{resource_label} request was unauthorized (HTTP {status_code}). "
            "Please check your Livestorm API key permissions."
        )
    if status_code == 404:
        return (
            "Resource not found (HTTP 404). "
            "Please verify the provided Session ID/Event ID exists in your Livestorm workspace."
        )
    if status_code == 429:
        return f"{resource_label} rate limited (HTTP 429). Please wait a few seconds and try again."
    if status_code is not None and status_code >= 500:
        return (
            f"Livestorm server error while fetching {resource_label.lower()} (HTTP {status_code}). "
            "Please retry shortly."
        )
    if details:
        return f"{resource_label} API request failed (HTTP {status_code}): {details}"
    if status_code is not None:
        return f"{resource_label} API request failed (HTTP {status_code})."
    return f"{resource_label} API request failed."


def format_generic_http_error(exc: requests.HTTPError, resource_label: str) -> str:
    response = exc.response
    status_code = response.status_code if response is not None else None
    details = ""

    if response is not None:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                for key in ("error", "message", "detail"):
                    if isinstance(payload.get(key), str):
                        details = payload[key]
                        break
        except ValueError:
            details = ""

    if status_code in (401, 403):
        openai_resources = {
            "Analysis",
            "Deep analysis",
            "Analysis PDF",
            "Smart Recap",
            "Smart Recap PDF",
            "Content Repurposing",
            "Content Repurposing PDF",
        }
        credential_label = "OpenAI API key" if resource_label in openai_resources else "Gladia API key"
        return (
            f"{resource_label} request was unauthorized (HTTP {status_code}). "
            f"Please check the configured {credential_label}."
        )
    if status_code == 404:
        return (
            f"{resource_label} not found (HTTP 404). "
            "Please verify the selected session has an available recording/transcript."
        )
    if status_code == 429:
        return f"{resource_label} rate limited (HTTP 429). Please retry in a moment."
    if status_code is not None and status_code >= 500:
        return f"{resource_label} server error (HTTP {status_code}). Please retry shortly."
    if details and status_code is not None:
        return f"{resource_label} request failed (HTTP {status_code}): {details}"
    if status_code is not None:
        return f"{resource_label} request failed (HTTP {status_code})."
    return f"{resource_label} request failed."


def build_http_error_debug_details(exc: requests.HTTPError, resource_label: str) -> Dict[str, Any]:
    response = exc.response
    details: Dict[str, Any] = {
        "resource": resource_label,
        "exception_type": type(exc).__name__,
    }
    if response is None:
        return details

    details["status_code"] = response.status_code
    details["url"] = response.url
    request = response.request
    if request is not None:
        details["method"] = request.method

    try:
        payload = response.json()
        if isinstance(payload, (dict, list)):
            details["response_json"] = payload
    except ValueError:
        body_text = response.text.strip()
        if body_text:
            details["response_text"] = body_text[:1200]
    return details


def build_request_exception_debug_details(exc: requests.RequestException, resource_label: str) -> Dict[str, Any]:
    details: Dict[str, Any] = {
        "resource": resource_label,
        "exception_type": type(exc).__name__,
        "message": str(exc),
    }
    response = getattr(exc, "response", None)
    if response is not None:
        details["status_code"] = response.status_code
        details["url"] = response.url
    request = getattr(exc, "request", None)
    if request is not None:
        details["method"] = request.method
        details["url"] = details.get("url") or request.url
    return details


def _page_size(requested: int) -> int:
    """Never ask Livestorm for more than its maximum page size.

    A larger value would come back as a short page, and the pagers' "short
    page means last page" fallback would then stop too early.
    """
    return min(int(requested), LIVESTORM_MAX_PAGE_SIZE)


def fetch_chat_messages(key: str, session: str, page_size: int = DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
    """Every chat message of a session, each with its author's people link.

    Every page asks for `include=people`, so each message carries
    `relationships.people.data` (`{type: "people", id}`): its author's
    people id, the key that joins it to the session's people
    (`record/authors.py`). Its `author_id` attribute is another id. The
    `included` people are dropped before returning, so the chat payload
    keeps only each message's people id. In the one real session checked,
    the session payload (`include=people` too) held every chat author; one
    missing from it reads unresolved. Join links are stripped first all the
    same.
    """
    url = f"{API_BASE}/sessions/{session}/chat_messages"
    headers = build_headers(key)
    page_size = _page_size(page_size)

    page_number = START_PAGE_NUMBER
    pages_fetched = 0
    seen_pages = set()
    all_messages: List[Dict[str, Any]] = []
    final_payload: Dict[str, Any] = {}

    while pages_fetched < MAX_PAGES:
        if page_number in seen_pages:
            break
        seen_pages.add(page_number)

        resp = livestorm_get(
            url,
            headers=headers,
            params={"page[number]": page_number, "page[size]": page_size, "include": "people"},
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            payload = {"data": extract_messages(payload)}

        final_payload = payload
        messages = extract_messages(payload)
        all_messages.extend(messages)
        pages_fetched += 1

        next_page = _extract_next_page(payload)
        if next_page is not None:
            if next_page in seen_pages:
                break
            page_number = next_page
            continue
        if _states_last_page(payload) or len(messages) < page_size:
            break
        page_number += 1

    final_payload["data"] = all_messages
    final_payload["pages_fetched"] = pages_fetched
    final_payload["requested_page_size"] = page_size
    stripped = strip_payload_join_links(final_payload)
    # The people come with the session payload: never stored twice.
    return {name: value for name, value in stripped.items() if name != "included"}


def fetch_session_questions(key: str, session: str, page_size: int = DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
    url = f"{API_BASE}/sessions/{session}/questions"
    headers = build_headers(key)
    page_size = _page_size(page_size)

    page_number = START_PAGE_NUMBER
    pages_fetched = 0
    seen_pages = set()
    all_questions: List[Dict[str, Any]] = []
    all_included: Dict[str, Dict[str, Any]] = {}
    final_payload: Dict[str, Any] = {}

    while pages_fetched < MAX_PAGES:
        if page_number in seen_pages:
            break
        seen_pages.add(page_number)

        resp = livestorm_get(
            url,
            headers=headers,
            params={"page[number]": page_number, "page[size]": page_size, "include": "asker,responder"},
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            payload = {"data": extract_questions(payload)}

        final_payload = payload
        page_questions = extract_questions(payload)
        all_questions.extend(page_questions)
        pages_fetched += 1

        included = payload.get("included")
        if isinstance(included, list):
            for item in included:
                if not isinstance(item, dict):
                    continue
                included_id = str(item.get("id") or "")
                included_type = str(item.get("type") or "")
                if included_id and included_type:
                    all_included[f"{included_type}:{included_id}"] = item

        next_page = _extract_next_page(payload)
        if next_page is not None:
            if next_page in seen_pages:
                break
            page_number = next_page
            continue
        if _states_last_page(payload) or len(page_questions) < page_size:
            break
        page_number += 1

    final_payload["data"] = all_questions
    final_payload["included"] = list(all_included.values())
    final_payload["pages_fetched"] = pages_fetched
    final_payload["requested_page_size"] = page_size
    return strip_payload_join_links(final_payload)


def fetch_session_details(key: str, session: str) -> Dict[str, Any]:
    resp = livestorm_get(
        f"{API_BASE}/sessions/{session}",
        headers=build_headers(key),
        params={"include": "people"},
    )
    resp.raise_for_status()
    payload = resp.json()
    return strip_payload_join_links(payload if isinstance(payload, dict) else {"data": payload})


def fetch_event_details(key: str, event_id: str) -> Dict[str, Any]:
    """GET /events/{event_id}. Returns the raw JSON:API payload.

    Used to populate `session_cache.event_payload` so the Single Analysis
    list view can show the event title (sessions themselves rarely have
    a `name` set — they're identified by their parent event).
    """
    resp = livestorm_get(
        f"{API_BASE}/events/{event_id}",
        headers=build_headers(key),
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, dict) else {"data": payload}


def fetch_event_past_sessions(key: str, event: str, page_size: int = DEFAULT_PAGE_SIZE) -> Dict[str, Any]:
    url = f"{API_BASE}/events/{event}/sessions"
    headers = build_headers(key)
    page_size = _page_size(page_size)

    page_number = START_PAGE_NUMBER
    pages_fetched = 0
    seen_pages = set()
    all_sessions: List[Dict[str, Any]] = []
    final_payload: Dict[str, Any] = {}

    while pages_fetched < MAX_PAGES:
        if page_number in seen_pages:
            break
        seen_pages.add(page_number)

        resp = livestorm_get(
            url,
            headers=headers,
            params={"page[number]": page_number, "page[size]": page_size, "filter[status]": "past"},
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            payload = {"data": extract_sessions(payload)}

        final_payload = payload
        page_sessions = extract_sessions(payload)
        all_sessions.extend(page_sessions)
        pages_fetched += 1

        next_page = _extract_next_page(payload)
        if next_page is not None:
            if next_page in seen_pages:
                break
            page_number = next_page
            continue
        if _states_last_page(payload) or len(page_sessions) < page_size:
            break
        page_number += 1

    final_payload["data"] = all_sessions
    final_payload["pages_fetched"] = pages_fetched
    final_payload["requested_page_size"] = page_size
    return final_payload


def fetch_workspace_events_page(
    key: str,
    page_number: int = START_PAGE_NUMBER,
    page_size: int = 20,
    title: str = "",
    scheduling_status: str = "",
) -> Dict[str, Any]:
    url = f"{API_BASE}/events"
    headers = build_headers(key)
    page_size = _page_size(page_size)
    params: Dict[str, Any] = {"page[number]": page_number, "page[size]": page_size}
    normalized_title = str(title or "").strip()
    normalized_status = str(scheduling_status or "").strip()
    if normalized_title:
        params["filter[title]"] = normalized_title
    if normalized_status:
        params["filter[scheduling_status]"] = normalized_status
    resp = livestorm_get(
        url,
        headers=headers,
        params=params,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        payload = {"data": extract_events(payload)}

    payload["pages_fetched"] = 1
    payload["requested_page_size"] = page_size
    payload["requested_page_number"] = page_number
    payload["requested_title"] = normalized_title
    payload["requested_scheduling_status"] = normalized_status
    payload["next_page"] = _extract_next_page(payload)
    return payload


# ── People ─────────────────────────────────────────────────────────────────
#
# A session's people come from the paginated list, `GET /sessions/{id}/people`,
# whose `meta.record_count` proves the list is complete. A large session takes
# many calls (100 for 5,000 registrants, at 50 per page), so these fetchers
# are for the worker only, never a web request. Every call goes through
# `livestorm_get` (its timeouts and retries); nothing else is retried.

RATE_LIMIT_MONTHLY_REMAINING = "RateLimit-Monthly-Remaining"


class LivestormDeadlineExceeded(TimeoutError):
    """A paginated fetch ran past its deadline. Nothing it read is returned."""


class LivestormPaginationError(RuntimeError):
    """A paginated fetch couldn't reach the list's last page: Livestorm
    pointed back at a page it had already sent, or the list ran past
    `MAX_PAGES`. Nothing it read is returned."""


def monthly_remaining(headers: Any) -> Optional[int]:
    """The `RateLimit-Monthly-Remaining` header as a number: the API calls
    left in the current 30-day period. None when it is missing or not a
    whole number."""
    getter = getattr(headers, "get", None)
    raw = getter(RATE_LIMIT_MONTHLY_REMAINING) if callable(getter) else None
    text = str(raw).strip() if raw is not None else ""
    return int(text) if text.isascii() and text.isdigit() else None


def _pages(
    key: str,
    url: str,
    *,
    page_size: int,
    deadline: Optional[float],
) -> Iterator[Tuple[Dict[str, Any], Any]]:
    """Yield `(payload, response)` for each page of a paginated GET.

    Starts at page 0 and follows `meta.next_page` until it is null; only a
    payload without pagination metadata stops at its first short page. Before
    each request it checks `deadline` (a `time.monotonic()` value) and raises
    `LivestormDeadlineExceeded` once it has passed. An HTTP error raises. It
    never stops early without raising: a next page it already read, or one
    past `MAX_PAGES`, raises `LivestormPaginationError`.
    """
    headers = build_headers(key)
    page_number = START_PAGE_NUMBER
    seen_pages: set = set()
    while True:
        if page_number in seen_pages:
            raise LivestormPaginationError("Livestorm pointed back at a page it had already sent; nothing was kept.")
        if len(seen_pages) >= MAX_PAGES:
            raise LivestormPaginationError(f"The list runs past {MAX_PAGES} pages; nothing was kept.")
        if deadline is not None and time.monotonic() >= deadline:
            raise LivestormDeadlineExceeded("Livestorm took too long to list every page; nothing was kept.")
        seen_pages.add(page_number)
        response = livestorm_get(url, headers=headers, params={"page[number]": page_number, "page[size]": page_size})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            payload = {"data": extract_records(payload)}
        yield payload, response

        next_page = _extract_next_page(payload)
        if next_page is not None:
            page_number = next_page
            continue
        if _states_last_page(payload) or len(extract_records(payload)) < page_size:
            break
        page_number += 1


def fetch_session_people(
    key: str,
    session_id: str,
    *,
    deadline: float,
    page_size: int = LIVESTORM_MAX_PAGE_SIZE,
) -> Dict[str, Any]:
    """Every person of a session, from the paginated people list.

    Asks for 50 per page and follows `meta.next_page`. Each record is
    returned without its registrant join link (`strip_registrant_join_link`).
    A person listed twice (the list can shift while people register) is kept
    once, as last listed. A record without an id is dropped. Returns:

    - `people`: the person records, in first-listed order;
    - `record_count`: Livestorm's `meta.record_count` on the last page that
      sent one, or None;
    - `complete`: True when the unique people equal `record_count`, False
      when they don't, None when Livestorm sent no count;
    - `pages_fetched`, `duplicates` (records seen again), and
      `monthly_remaining`: the `RateLimit-Monthly-Remaining` header of the
      last page that sent one, or None.

    Raises on any failed page (`requests.HTTPError`), past `deadline`, a
    `time.monotonic()` value (`LivestormDeadlineExceeded`), and when the list
    doesn't reach its last page (`LivestormPaginationError`: a next page
    already read, or more than `MAX_PAGES`), so a caller never gets part of
    a list. Worker only: a large session takes many calls.
    """
    url = f"{API_BASE}/sessions/{session_id}/people"
    people: Dict[str, Dict[str, Any]] = {}
    record_count: Optional[int] = None
    remaining: Optional[int] = None
    pages_fetched = 0
    duplicates = 0
    for payload, response in _pages(key, url, page_size=_page_size(page_size), deadline=deadline):
        pages_fetched += 1
        count = extract_record_count(payload)
        record_count = count if count is not None else record_count
        left = monthly_remaining(getattr(response, "headers", None))
        remaining = left if left is not None else remaining
        for record in extract_records(payload):
            person_id = str(record.get("id") or "").strip() if isinstance(record, dict) else ""
            if not person_id:
                continue
            if person_id in people:
                duplicates += 1
            people[person_id] = strip_registrant_join_link(record)
    return {
        "people": list(people.values()),
        "record_count": record_count,
        "complete": None if record_count is None else len(people) == record_count,
        "pages_fetched": pages_fetched,
        "duplicates": duplicates,
        "monthly_remaining": remaining,
    }


def fetch_people_attributes(
    key: str,
    *,
    deadline: Optional[float] = None,
    page_size: int = LIVESTORM_MAX_PAGE_SIZE,
) -> Dict[str, Any]:
    """The workspace's people attributes (`GET /people_attributes`, paginated).

    Returns `{attributes, status, http_status}`. `attributes` is
    `payloads.parse_people_attributes` of every page. A 401 or 403 (the token
    may not reach this endpoint) is not a failure: `status` is "forbidden",
    with no attributes, and the caller labels fields from the event instead.
    Any other HTTP error raises, and so do passing `deadline`
    (`LivestormDeadlineExceeded`) and a list that doesn't reach its last
    page (`LivestormPaginationError`).
    """
    url = f"{API_BASE}/people_attributes"
    records: List[Any] = []
    http_status = None
    try:
        for payload, response in _pages(key, url, page_size=_page_size(page_size), deadline=deadline):
            records.extend(extract_records(payload))
            http_status = getattr(response, "status_code", None)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in (401, 403):
            return {"attributes": [], "status": "forbidden", "http_status": status_code}
        raise
    return {"attributes": parse_people_attributes(records), "status": "ok", "http_status": http_status}


def fetch_chat_and_questions_bundle(key: str, session_id: str) -> Dict[str, Any]:
    payload = fetch_chat_messages(key, session_id)
    chat_df = build_chat_df_from_payload(payload)

    raw_questions_payload = fetch_session_questions(key, session_id)
    questions_df = build_questions_df_from_payload(raw_questions_payload)

    return {
        "chat_payload": payload,
        "chat_df": chat_df,
        "questions_payload": raw_questions_payload,
        "questions_df": questions_df,
    }
