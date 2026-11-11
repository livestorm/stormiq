"""Adding a session by its ID (`api_logic.fetch_session_base_data`): a
session Livestorm says hasn't happened (upcoming, live, never started,
cancelled, a draft) is refused before its chat is fetched or anything is
saved; a finished one goes on."""

from __future__ import annotations

import pytest

from livestorm_app import api_logic


def _fetch(monkeypatch, status):
    calls = {"chat": 0, "saved": 0}
    attributes = {"status": status} if status is not None else {}
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_logic, "fetch_session_details", lambda key, sid: {"data": {"id": sid, "attributes": attributes}})

    def chat(key, sid):
        calls["chat"] += 1
        return {"chat_payload": {"data": []}, "questions_payload": {"data": []}}

    def save(*args, **kwargs):
        calls["saved"] += 1

    monkeypatch.setattr(api_logic, "fetch_chat_and_questions_bundle", chat)
    monkeypatch.setattr(api_logic, "_fetch_event_payload_safe", lambda key, event_id: None)
    monkeypatch.setattr(api_logic, "upsert_cached_session", save)
    # Stop right after the save: the rest of the fetch is covered elsewhere.
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda *args, **kwargs: None)
    try:
        api_logic.fetch_session_base_data("Bearer t", "sess-1", job_connection_id="c", read_scope="org-a", organization_id="org-a")
    except RuntimeError as exc:
        return calls, exc
    return calls, None


@pytest.mark.parametrize(
    ("status", "words"),
    [
        ("upcoming", "hasn't taken place yet"),
        ("live", "is still live"),
        ("past_not_started", "never started"),
        ("canceled", "was cancelled"),
        ("draft", "still a draft"),
        ("UPCOMING", "hasn't taken place yet"),
    ],
)
def test_a_session_that_hasnt_happened_is_refused_before_anything_is_fetched_or_saved(monkeypatch, status, words):
    calls, error = _fetch(monkeypatch, status)
    assert isinstance(error, api_logic.SessionNotFinishedError)
    assert words in str(error) and "Add it once it has ended" in str(error)
    assert calls == {"chat": 0, "saved": 0}


@pytest.mark.parametrize("status", ["past", "on_demand", None])
def test_a_finished_session_goes_on(monkeypatch, status):
    calls, error = _fetch(monkeypatch, status)
    assert not isinstance(error, api_logic.SessionNotFinishedError)
    assert calls == {"chat": 1, "saved": 1}
