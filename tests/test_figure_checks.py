"""Before you share: the figure checks (`record/figure_checks.py`), their
routes (`GET`/`PUT /api/sessions/{id}/record/figures`) and what the Session
analysis's brief says about a flagged figure, on the synthetic webinar."""

from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import synthetic_session as synthetic

from livestorm_app import api_logic
from livestorm_app.record.figure_checks import BOTH, NEITHER, figure_checks, figure_notes, unchecked_count
from livestorm_app.record.session_brief import understanding_lines


def _result():
    return synthetic.understanding(synthetic.cached_row())["result"]


# ── The checks ──


def test_each_flag_becomes_a_question_in_the_talks_order():
    checks = figure_checks(_result(), {})
    assert [(c["kind"], c["question"]) for c in checks] == [
        ("two_values", "Seats per team: which value is right?"),
        ("acronym", "Was it NRR or NPS?"),
    ]
    values, acronym = checks
    assert [o["value"] for o in values["options"]] == ["300 seats", "500 seats", BOTH, NEITHER]
    assert [m["utterance"] for m in values["moments"]] == ["U0028", "U0074"], "every moment the metric was said"
    assert [o["value"] for o in acronym["options"]] == ["NRR", "NPS", NEITHER]
    assert values["key"] == "values:seats per team" and acronym["key"] == "acronym:U0029:pricing works per team"
    assert unchecked_count(checks) == 2


def test_a_saved_answer_counts_only_while_it_is_one_of_the_options():
    saved = {
        "acronym:U0029:pricing works per team": {"choice": "NPS"},
        "values:seats per team": {"choice": "700 seats"},
    }
    checks = {c["kind"]: c for c in figure_checks(_result(), saved)}
    assert checks["acronym"]["choice"] == "NPS"
    assert checks["two_values"]["choice"] is None, "an answer that is no longer offered reopens the check"
    assert unchecked_count(checks.values()) == 1


def test_nothing_flagged_nothing_to_check():
    result = {**_result(), "figures": [{**f, "flags": []} for f in _result()["figures"]]}
    assert figure_checks(result, {}) == []
    assert figure_checks({}, {}) == []


# ── What the Session analysis is told ──


def test_the_brief_carries_the_hosts_answer_or_says_the_figure_is_unchecked():
    result = _result()
    times = {"U0028": 306.9, "U0029": 318.9, "U0074": 778.9}
    unchecked = understanding_lines(result, times, figure_notes(result, {}))["figures"]
    assert any("not checked by the host: do not state it as a fact" in line for line in unchecked)
    answered = figure_notes(result, {"acronym:U0029:pricing works per team": {"choice": "NPS"}, "values:seats per team": {"choice": BOTH}})
    lines = understanding_lines(result, times, answered)["figures"]
    assert '- 5:18 "pricing works per team" [checked by the host: it was NPS]' in lines
    assert all("both values are right" in line for line in lines if "seats" in line or "export" in line)


# ── The routes ──


def _client(saved):
    row = synthetic.cached_row()
    labels, stored = synthetic.chat_labels(row), synthetic.understanding(row)

    def fetch(session_id, organization_id):
        return dict(saved) if (session_id, organization_id) == (row["session_id"], row["organization_id"]) else {}

    def save(session_id, *, organization_id, check_key, choice, updated_by):
        if (session_id, organization_id) != (row["session_id"], row["organization_id"]):
            return False
        if choice is None:
            saved.pop(check_key, None)
        else:
            saved[check_key] = {"choice": choice, "by": updated_by}
        return True

    stack = ExitStack()
    for patch in (
        mock.patch.object(api_logic, "fetch_roster_status", lambda session_id, organization_id: {}),
        mock.patch.object(api_logic, "fetch_chat_labels", lambda session_id, organization_id: labels),
        mock.patch.object(api_logic, "fetch_understanding", lambda session_id, organization_id: stored),
        mock.patch.object(api_logic, "fetch_figure_checks", fetch),
        mock.patch.object(api_logic, "save_figure_check", save),
    ):
        stack.enter_context(patch)
    client = stack.enter_context(synthetic.serving(row))
    return stack, client, f"/api/sessions/{row['session_id']}/record/figures"


def test_the_host_answers_a_check_and_can_reopen_it():
    saved: dict = {}
    stack, client, path = _client(saved)
    with stack:
        body = client.get(path).json()
        assert (body["state"], body["unchecked"]) == ("ready", 2)
        key = body["checks"][1]["key"]
        answered = client.put(path, json={"key": key, "choice": "NPS"})
        assert answered.status_code == 200, answered.text
        assert answered.json()["unchecked"] == 1 and answered.json()["checks"][1]["choice"] == "NPS"
        assert saved == {key: {"choice": "NPS", "by": "synthetic-viewer"}}
        reopened = client.put(path, json={"key": key, "choice": None})
        assert reopened.json()["unchecked"] == 2 and saved == {}


def test_an_unknown_check_or_answer_is_refused_and_another_session_looks_missing():
    saved: dict = {}
    stack, client, path = _client(saved)
    with stack:
        key = client.get(path).json()["checks"][1]["key"]
        assert client.put(path, json={"key": key, "choice": "ARR"}).status_code == 409
        assert client.put(path, json={"key": "acronym:U9999:nothing", "choice": "NPS"}).status_code == 409
        assert client.put(path, json={"key": "", "choice": "NPS"}).status_code == 422
        assert client.get("/api/sessions/someone-elses/record/figures").status_code == 404
    assert saved == {}


def test_the_frontend_fixture_is_the_route_response():
    """`frontend/tests/fixtures/figures-v1.json` is what the route sends for
    the synthetic webinar. After a deliberate change, regenerate it:
    `.venv/bin/python tests/synthetic_session.py`."""
    committed = synthetic.FIGURES_FIXTURE.read_text(encoding="utf-8")
    assert synthetic.dump(synthetic.figures_response()) == committed
