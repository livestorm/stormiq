"""The Sessions library's row (`record/library.py`) and its route,
`GET /api/sessions/{id}/record/library`, on the synthetic webinar."""

from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.record.library import SPARK_POINTS, library_row, sparkline


def _get(row, path=None, *, analysed=True):
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}))
        stack.enter_context(
            mock.patch.object(api_logic, "fetch_session_roster", mock.Mock(side_effect=AssertionError("no stored roster")))
        )
        stack.enter_context(synthetic.with_understanding(row))
        if not analysed:
            stack.enter_context(mock.patch.object(api_logic, "fetch_understanding", lambda s, o: None))
        client = stack.enter_context(synthetic.serving(row, fresh_record_cache=True))
        return client.get(path or f"/api/sessions/{row['session_id']}/record/library")


# ── The row ──


def test_a_long_session_draws_as_many_points_as_a_short_one():
    assert sparkline([{"audienceChat": n} for n in (1, 0, 3)]) == [1, 0, 3]
    long = sparkline([{"audienceChat": 1} for _ in range(120)])
    assert len(long) <= SPARK_POINTS and sum(long) == 120


def test_numbers_only_as_the_report_shows_them_and_the_lead_only_when_analysed():
    record = {
        "pulse": {"minutes": [{"minute": 0, "audienceChat": 2}, {"minute": 1, "audienceChat": 9}, {"minute": 2, "audienceChat": 0}]},
        "measures": {
            "m.audience_attendees": {"state": "partial", "value": 79},
            "m.wrote_rate": {"state": "ok", "value": 0.7342},
            "m.questions_total": {"state": "unavailable", "value": None},
        },
    }
    lead = {"id": "moments", "values": {}}
    row = library_row(record, {"state": "ready", "found": [lead]}, {"open": 31, "covered": 7})
    assert row["sparkline"] == [2, 9, 0] and row["peak"] == {"minute": 1, "messages": 9}
    assert row["attendees"] == {"value": 79, "estimated": True}
    assert row["wrote"] == {"value": 0.7342, "estimated": False}
    assert row["questions"] is None, "a hidden value stays hidden"
    assert row["stillOpen"] == 38, "open and covered but not confirmed, as Follow-up counts them"
    assert row["lead"] == lead and row["analysed"] is True
    stale = library_row(record, {"state": "stale", "found": [lead]}, {})
    assert (stale["lead"], stale["analysed"], stale["stillOpen"]) == (None, False, 0)
    quiet = library_row({"pulse": {"minutes": [{"minute": 0, "audienceChat": 0}]}}, {}, {})
    assert quiet["peak"] is None and quiet["attendees"] is None


# ── The route ──


def test_the_route_serves_the_row_with_the_callers_scope():
    row = synthetic.cached_row()
    response = _get(row)
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == [
        "sessionId", "updatedAt", "version", "sparkline", "peak", "attendees", "wrote", "questions", "stillOpen", "lead", "analysed",
    ]
    assert body["analysed"] is True and sum(body["sparkline"]) > 0
    for word in ("Nora", "Theo", "@example", "P-0"):
        assert word not in response.text, "no name, email or ref in a library row"
    missing = _get(row, "/api/sessions/no-such-session/record/library")
    assert missing.status_code == 404 and missing.json()["detail"]["resource"] == "Session library"


def test_a_session_not_analysed_has_no_lead_finding():
    body = _get(synthetic.cached_row(), analysed=False).json()
    assert body["analysed"] is False and body["lead"] is None


def test_a_bug_in_the_row_returns_500_without_its_detail():
    from livestorm_app.record import library as module

    with mock.patch.object(module, "library_row", mock.Mock(side_effect=RuntimeError("secret detail"))):
        response = _get(synthetic.cached_row())
    assert response.status_code == 500 and "secret detail" not in response.text
