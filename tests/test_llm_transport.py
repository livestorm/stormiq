"""The Anthropic SDK transport (`llm_client`): the client, the retry policy,
streaming and its deadline, the classic outputs' request bodies, and
`scripts/llm_smoke.py`.

No provider is called. Most tests use a fake SDK client
(`tests/fake_anthropic.py`); the ones that must see the bytes the SDK sends,
or the errors it raises, run the real SDK over an httpx2 mock transport.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import io
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from livestorm_app import config, db, llm_client
from livestorm_app.ai import jobs, models, usage
from livestorm_app.ai.dossier import make_dossier
from livestorm_app.ai.request import AnthropicCall, anthropic_call, build_request
from livestorm_app.ai.usage import CallContext

from fake_anthropic import (
    FakeAnthropic,
    FakeStreamScript,
    answer_body,
    connection_error,
    install,
    install_mock_transport,
    json_response,
    sse,
    status_error,
    stream_error_event,
    timeout_error,
)

SECRET = "sk-test-SECRET"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "anthropic-requests-before-sdk.json"
MESSAGES = [{"role": "system", "content": "Read the session."}, {"role": "user", "content": "Session data"}]
UNDERSTANDING = CallContext("understanding", "sess-1", "org-a", "understanding:sess-1:_")


@pytest.fixture
def sleeps(monkeypatch):
    waited: list = []
    monkeypatch.setattr(llm_client.time, "sleep", waited.append)
    return waited


@pytest.fixture
def fake(monkeypatch):
    client = FakeAnthropic()
    install(monkeypatch, client)
    return client


@pytest.fixture
def rows(monkeypatch):
    saved: list = []
    monkeypatch.setattr(db, "insert_llm_usage", saved.append)
    monkeypatch.setattr(db, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(llm_client, "get_provider", lambda: "anthropic")
    return saved


def _stream_call(deadline_s: float = 600, model: str = "claude-sonnet-5-5"):
    """A v1 job's streamed call (`llm_client._anthropic_call`) with a plain body."""
    call = AnthropicCall({"model": model, "max_tokens": 48_000, "messages": MESSAGES[1:], "system": "Read the session."})
    return llm_client._anthropic_call(SECRET, call, stream=True, deadline_s=deadline_s)


def _send(model: str = "claude-haiku-4-5-20251001", *, stream: bool = False, deadline_s: float = 600):
    """One Anthropic call: a classic output's, or a v1 job's stream when `stream`."""
    if stream:
        return _stream_call(deadline_s, model)
    return llm_client._anthropic_send(SECRET, model, MESSAGES, 0.2, 2_500)


def _request(kind: str):
    """A v1 call's request, laid out for its job."""
    if jobs.job_spec(kind).layout == "dossier":
        dossier = make_dossier("Session data", record_version="6")
        return build_request(kind, system="You read sessions.", context=dossier, instructions="Read the session.")
    return build_request(kind, instructions="Read the session.", payload="Session data")


# ── One client per process ─────────────────────────────────────────────────


class RecordingClient:
    built: list = []

    def __init__(self, **kwargs):
        RecordingClient.built.append(kwargs)
        self.api_key = kwargs["api_key"]

    def post(self, path, **kwargs):
        return FakeAnthropic([answer_body()]).post(path, **kwargs)


def test_the_client_is_built_once_with_the_key_passed_explicitly(monkeypatch):
    monkeypatch.setattr(llm_client, "_client", None)
    monkeypatch.setattr(anthropic, "Anthropic", RecordingClient)
    RecordingClient.built = []
    _send()
    _send()
    assert len(RecordingClient.built) == 1, "one client per process"
    built = RecordingClient.built[0]
    assert built["api_key"] == SECRET
    assert built["max_retries"] == 0, "the SDK's own retries are off"
    assert built["base_url"] == config.ANTHROPIC_BASE_URL == "https://api.anthropic.com"
    timeout = built["timeout"]
    assert (timeout.connect, timeout.read) == (10.0, 300.0)
    llm_client._anthropic_send("sk-test-OTHER", "claude-haiku-4-5-20251001", MESSAGES, 0.2, 2_500)
    assert [entry["api_key"] for entry in RecordingClient.built] == [SECRET, "sk-test-OTHER"], "a new key builds a new client"


def test_the_environment_never_picks_the_key_or_the_host(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-OTHER")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://elsewhere.example")
    client = llm_client._build_anthropic_client(SECRET)
    assert client.api_key == SECRET
    assert str(client.base_url).startswith("https://api.anthropic.com")
    assert client.max_retries == 0


def test_custom_headers_from_the_environment_stop_every_call(monkeypatch):
    # The SDK adds ANTHROPIC_CUSTOM_HEADERS to every request, over the key and
    # the API version it was given: one stray line could swap either.
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "anthropic-version: 2026-10-21\nx-api-key: sk-env-OTHER")
    monkeypatch.setattr(llm_client, "_client", None)
    with pytest.raises(llm_client.LLMError, match="ANTHROPIC_CUSTOM_HEADERS is set") as refused:
        _send()
    assert "sk-env-OTHER" not in str(refused.value) and "2026-10-21" not in str(refused.value)
    assert llm_client._client is None, "no client was built"
    with pytest.raises(llm_client.LLMError):
        llm_client._build_anthropic_client(SECRET)
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "  ")
    assert llm_client._build_anthropic_client(SECRET).api_key == SECRET, "a blank value adds no header"


def test_the_sdk_never_logs_request_options(monkeypatch, caplog):
    # The SDK logs each request's options, the prompt included, at DEBUG.
    sdk_logger = logging.getLogger("anthropic")
    level = sdk_logger.level
    sdk_logger.setLevel(logging.NOTSET)
    try:
        install_mock_transport(monkeypatch, lambda request: json_response(answer_body()))
        with caplog.at_level(logging.DEBUG):
            llm_client._anthropic_send(SECRET, "claude-haiku-4-5-20251001", [{"role": "user", "content": "confidential prompt"}], 0.2, 100)
        assert sdk_logger.level == logging.INFO
        assert "confidential prompt" not in caplog.text and SECRET not in caplog.text
    finally:
        sdk_logger.setLevel(level)


# ── The pinned SDK ─────────────────────────────────────────────────────────


def test_the_pinned_sdk_sends_api_version_2023_06_01():
    # 2.x sends 2026-10-21, which refuses the classic outputs' temperature.
    assert anthropic.__version__.split(".")[0] == "1"
    assert config.ANTHROPIC_API_VERSION == "2023-06-01"


def test_the_pinned_sdks_error_classes():
    # A timeout is a connection error to the SDK, so the policy catches it first.
    assert issubclass(anthropic.APITimeoutError, anthropic.APIConnectionError)
    assert not issubclass(anthropic.APIConnectionError, anthropic.APIStatusError)
    # 529 is its own class, not an InternalServerError: the policy reads the status code.
    assert type(status_error(529)) is anthropic.OverloadedError
    assert not issubclass(anthropic.OverloadedError, anthropic.InternalServerError)
    assert type(status_error(503)) is anthropic.InternalServerError
    assert type(status_error(429)) is anthropic.RateLimitError
    for status in (400, 429, 500, 529):
        assert isinstance(status_error(status), anthropic.APIStatusError)


# ── The retry policy (today's `_post_with_retry`, on the SDK's errors) ─────


def test_rate_limits_overload_and_server_errors_are_retried(fake, sleeps):
    fake.script.extend([status_error(529, error_type="overloaded_error"), status_error(503), connection_error(), answer_body()])
    assert _send().text == "# Output"
    assert len(fake.requests) == 4 and len(sleeps) == 3


RETRY_AFTER_STATUSES = [(429, "rate_limit_error"), (529, "overloaded_error"), (503, "api_error")]


@pytest.mark.parametrize(("status", "error_type"), RETRY_AFTER_STATUSES, ids=["429", "529", "503"])
def test_retry_after_is_honoured_with_a_cap(fake, sleeps, status, error_type):
    fake.script.extend([
        status_error(status, error_type=error_type, retry_after="7"),
        status_error(status, error_type=error_type, retry_after="3600"),
        answer_body(),
    ])
    assert _send().text == "# Output"
    assert sleeps == [7.0, llm_client.LLM_BACKOFF_MAX_SECONDS]


def test_retry_after_is_honoured_when_a_stream_opens(fake, sleeps):
    fake.script.extend([status_error(529, error_type="overloaded_error", retry_after="7"), FakeStreamScript(answer_body("Second try"))])
    assert _send("claude-sonnet-5-5", stream=True, deadline_s=600).text == "Second try"
    assert sleeps == [7.0]


@pytest.mark.parametrize("status", [500, 502, 504])
def test_every_retried_status_is_retried(fake, sleeps, status):
    fake.script.extend([status_error(status), answer_body()])
    assert _send().text == "# Output"
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (timeout_error(), "no answer in time"),
        (status_error(400, error_type="invalid_request_error"), "HTTP 400 invalid_request_error"),
        (status_error(401, error_type="authentication_error"), "HTTP 401 authentication_error"),
        (status_error(413, error_type="request_too_large"), "HTTP 413 request_too_large"),
        (status_error(501), "HTTP 501 api_error"),
    ],
    ids=["timeout", "400", "401", "413", "501"],
)
def test_calls_that_would_fail_again_or_double_bill_are_not_repeated(fake, sleeps, failure, reason):
    fake.script.extend([failure, answer_body()])
    with pytest.raises(llm_client.LLMProviderError) as failed:
        _send()
    assert str(failed.value) == f"The Anthropic call failed ({reason}). Nothing was saved. Please try again."
    assert failed.value.__cause__ is failure
    assert sleeps == [] and len(fake.requests) == 1


def test_the_policy_gives_up_after_the_last_attempt(fake, sleeps):
    fake.script.extend([status_error(503)] * llm_client.LLM_MAX_ATTEMPTS)
    with pytest.raises(llm_client.LLMProviderError, match="HTTP 503"):
        _send()
    assert fake.script == [] and len(sleeps) == llm_client.LLM_MAX_ATTEMPTS - 1


def test_the_policy_gives_up_after_the_last_connection_error(fake, sleeps):
    fake.script.extend([connection_error() for _ in range(llm_client.LLM_MAX_ATTEMPTS)])
    with pytest.raises(llm_client.LLMProviderError) as failed:
        _send()
    assert str(failed.value) == "The Anthropic call failed (connection error). Nothing was saved. Please try again."
    assert fake.script == [] and len(fake.requests) == llm_client.LLM_MAX_ATTEMPTS
    assert len(sleeps) == llm_client.LLM_MAX_ATTEMPTS - 1


# ── Streaming ──────────────────────────────────────────────────────────────


def test_a_stream_returns_its_final_message(fake, sleeps):
    fake.script.append(FakeStreamScript(answer_body("Streamed answer", model="claude-sonnet-5-5"), request_id="req_s1"))
    result = _send("claude-sonnet-5-5", stream=True, deadline_s=600)
    assert (result.text, result.stop, result.request_id, result.streamed) == ("Streamed answer", "complete", "req_s1", True)
    assert fake.requests[0]["_stream"] and fake.streams[0].closed


@pytest.mark.parametrize(
    "failure",
    [status_error(529, error_type="overloaded_error"), status_error(429), connection_error()],
    ids=["529-at-open", "429-at-open", "connection-at-open"],
)
def test_a_stream_is_retried_when_it_fails_before_its_first_event(fake, sleeps, failure):
    fake.script.extend([failure, FakeStreamScript(answer_body("Second try"))])
    assert _send("claude-sonnet-5-5", stream=True, deadline_s=600).text == "Second try"
    assert len(fake.requests) == 2 and len(sleeps) == 1


def test_an_overload_event_before_any_other_is_retried(fake, sleeps):
    fake.script.extend([FakeStreamScript(error=stream_error_event("overloaded_error"), fail_after=0), FakeStreamScript(answer_body("Second try"))])
    assert _send("claude-sonnet-5-5", stream=True, deadline_s=600).text == "Second try"
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (stream_error_event("overloaded_error"), "error while answering: overloaded_error"),
        (httpx2.RemoteProtocolError("peer closed the connection"), "the stream broke: RemoteProtocolError"),
        (httpx2.ReadTimeout("no chunk in 300 s"), "the stream broke: ReadTimeout"),
    ],
    ids=["error-event", "connection-lost", "read-timeout"],
)
def test_an_error_mid_stream_is_never_retried(fake, sleeps, failure, reason):
    fake.script.extend([FakeStreamScript(answer_body("Partial answer"), error=failure, fail_after=3), FakeStreamScript(answer_body("Second try"))])
    with pytest.raises(llm_client.LLMProviderError) as failed:
        _send("claude-sonnet-5-5", stream=True, deadline_s=600)
    assert str(failed.value) == f"The Anthropic call failed ({reason}). Nothing was saved. Please try again."
    assert len(fake.requests) == 1 and sleeps == [], "output may be billed from the first event on"
    assert fake.streams[0].closed and fake.streams[0].events_read == 3
    assert "Partial answer" not in str(failed.value)


def _job_clock(monkeypatch, fake, seconds_per_event: float) -> dict:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: clock["now"])

    def tick(event_type):
        clock["now"] += seconds_per_event

    fake.on_event = tick
    return clock


def test_the_deadline_stops_a_stream_and_nothing_is_saved(fake, sleeps, rows, monkeypatch):
    monkeypatch.setitem(jobs._SPECS, "understanding", dataclasses.replace(jobs.job_spec("understanding"), call_deadline_s=300))
    _job_clock(monkeypatch, fake, seconds_per_event=100)
    fake.script.extend([FakeStreamScript(answer_body("Never saved", model="claude-sonnet-5-5")), FakeStreamScript(answer_body("Second try"))])
    with pytest.raises(llm_client.LLMDeadlineExceeded, match="longer than 300 s") as stopped:
        llm_client.generate_text("understanding", _request("understanding"), api_key=SECRET, ctx=UNDERSTANDING)
    assert isinstance(stopped.value, llm_client.LLMProviderError)
    stream = fake.streams[0]
    assert stream.closed and stream.events_read == 4, "stopped at the first event past 300 s"
    assert len(fake.requests) == 1 and sleeps == [], "never retried"
    assert rows == [], "no answer, so no usage row"
    assert "Never saved" not in str(stopped.value)


def test_a_stream_within_its_deadline_is_answered(fake, rows, monkeypatch):
    _job_clock(monkeypatch, fake, seconds_per_event=10)
    fake.script.append(FakeStreamScript(answer_body("Understood.", model="claude-sonnet-5-5")))
    assert llm_client.generate_text("understanding", _request("understanding"), api_key=SECRET, ctx=UNDERSTANDING) == "Understood."
    assert jobs.job_spec("understanding").call_deadline_s == jobs.DEFAULT_CALL_DEADLINE_S == 600
    assert fake.body(0)["max_tokens"] == 48_000 and fake.body(0)["stream"] is True
    assert [(row["streamed"], row["outcome"]) for row in rows] == [(True, "ok")]


def test_a_streamed_refusal_keeps_no_text(fake, rows):
    refusal = answer_body("Partial text", "refusal", model="claude-sonnet-5-5", stop_details={"type": "refusal", "category": "cyber"})
    fake.script.append(FakeStreamScript(refusal))
    with pytest.raises(llm_client.LLMRefused) as refused:
        llm_client.generate_text("understanding", _request("understanding"), api_key=SECRET, ctx=UNDERSTANDING)
    assert refused.value.category == "cyber" and "Partial" not in str(refused.value)
    assert [(row["outcome"], row["streamed"]) for row in rows] == [("refused", True)]


def test_only_v1_budgets_over_16k_stream(fake, rows):
    fake.script.extend([answer_body("Holds."), answer_body("# Recap")])
    llm_client.generate_text("verify_meaning", _request("verify_meaning"), api_key=SECRET, ctx=CallContext("verify_meaning", "sess-1", "org-a"))
    llm_client.call_llm(SECRET, "claude-haiku-4-5-20251001", MESSAGES, temperature=0.2, max_tokens=16_000)
    assert [request["_stream"] for request in fake.requests] == [False, False]


# ── The real SDK over a mock transport ─────────────────────────────────────


def _fixture_cases():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["name"])
def test_the_classic_requests_send_the_bytes_they_sent_before(monkeypatch, case):
    # The fixture holds the bodies the requests transport sent before the SDK
    # (captured from requests' prepared request). `_post_message` writes the
    # body as requests did, so the SDK sends the same bytes.
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sent: list = []

    def handler(request):
        sent.append(request)
        return json_response(answer_body(), headers={"request-id": "req_1"})

    keys = install_mock_transport(monkeypatch, handler)
    call = case["call"]
    result = llm_client._anthropic_send(SECRET, call["model"], call["messages"], call["temperature"], call["max_tokens"])
    assert result.request_id == "req_1" and keys == [SECRET]
    request = sent[0]
    assert (request.method, str(request.url)) == ("POST", fixture["url"])
    assert request.headers["anthropic-version"] == case["headers"]["anthropic-version"] == config.ANTHROPIC_API_VERSION
    assert request.headers["content-type"] == case["headers"]["content-type"]
    assert request.headers["x-api-key"] == SECRET
    assert request.content == case["body"].encode("utf-8")


def test_the_classic_fixture_covers_temperature_both_ways():
    bodies = [json.loads(case["body"]) for case in _fixture_cases()]
    assert {body.get("temperature") for body in bodies} == {0.2, 0.1, None}
    assert any("system" not in body for body in bodies)
    # Classic outputs only: a schema, an effort or a fallback belongs to the v1 jobs' requests.
    assert not any({"output_config", "thinking", "fallbacks"} & set(body) for body in bodies)


def _stream_events(stop_reason: str = "end_turn", stop_details=None):
    return [
        {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 1, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 0},
        }},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Under"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "stood."}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None, "stop_details": stop_details}, "usage": {"output_tokens": 9000}},
        {"type": "message_stop"},
    ]


def _stream_response(events, request_id: str = "req_stream") -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "text/event-stream", "request-id": request_id}, content=sse(events))


def test_a_real_stream_becomes_one_result(monkeypatch):
    sent: list = []

    def handler(request):
        sent.append(request)
        return _stream_response(_stream_events())

    install_mock_transport(monkeypatch, handler)
    result = _stream_call(600)
    assert (result.text, result.stop, result.request_id, result.streamed) == ("Understood.", "complete", "req_stream", True)
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.cache_read_tokens) == (1200, 9000, 300)
    assert [json.loads(request.content) for request in sent] == [
        {"max_tokens": 48_000, "messages": MESSAGES[1:], "model": "claude-sonnet-5-5", "system": "Read the session.", "stream": True},
    ]
    assert sent[0].headers["connection"] == "close", "a stream has a connection of its own, for its deadline to shut"


def test_a_real_streamed_refusal_keeps_its_category(monkeypatch):
    events = _stream_events("refusal", {"type": "refusal", "category": "cyber", "explanation": "declined"})
    install_mock_transport(monkeypatch, lambda request: _stream_response(events))
    result = _stream_call(600)
    assert (result.stop, result.refusal_category, result.text) == ("refusal", "cyber", "")


# A v1 job's request (`ai/request.py`), sent by the pinned SDK's own methods.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _v1_call(model: str, kind: str = "understanding", **parts):
    spec = jobs.job_spec(kind)
    caps = models.catalog_model(model)
    request = _request(kind) if not parts else build_request(kind, **parts)
    return anthropic_call(request, caps, min(jobs.budget(spec, caps)[0], 6_000))


def test_a_v1_call_on_haiku_goes_through_messages_create_without_a_beta(monkeypatch):
    sent: list = []

    def handler(request):
        sent.append(request)
        return json_response(answer_body("Labelled.", model="claude-haiku-5-5"), headers={"request-id": "req_v1"})

    install_mock_transport(monkeypatch, handler)
    call = _v1_call("claude-haiku-5-5", "chat_labels", instructions="Label each message.", payload="C0001 hello", shared_calls=2)
    result = llm_client._anthropic_call(SECRET, call)
    assert (result.text, result.stop, result.request_id, result.streamed) == ("Labelled.", "complete", "req_v1", False)
    request = sent[0]
    assert (request.method, request.url.path, request.url.query) == ("POST", "/v1/messages", b"")
    assert "anthropic-beta" not in request.headers
    assert request.headers["anthropic-version"] == config.ANTHROPIC_API_VERSION
    assert json.loads(request.content) == {
        "model": "claude-haiku-5-5",
        "max_tokens": 6_000,
        "system": [{"type": "text", "text": "Label each message.", "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "C0001 hello"}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low"},
    }


def test_a_v1_call_on_sonnet_asks_for_the_fallback_and_reads_a_fallback_answer(monkeypatch):
    """The dossier layout through the real SDK: the instructions travel as a
    `role: "system"` message, the fallback as a beta, and an answer the
    fallback model wrote is read from its text blocks."""
    sent: list = []
    rescued = answer_body(model="claude-sonnet-5", content=[
        {"type": "fallback", "from": {"model": "claude-sonnet-5-5"}, "to": {"model": "claude-sonnet-5"}},
        {"type": "text", "text": "Rescued."},
    ])
    rescued["usage"] = {
        "input_tokens": 1200, "output_tokens": 300,
        "iterations": [
            {"type": "message", "model": "claude-sonnet-5-5", "input_tokens": 1200, "output_tokens": 0},
            {"type": "fallback_message", "model": "claude-sonnet-5", "input_tokens": 1200, "output_tokens": 300},
        ],
    }

    def handler(request):
        sent.append(request)
        return json_response(rescued, headers={"request-id": "req_fb"})

    install_mock_transport(monkeypatch, handler)
    call = _v1_call("claude-sonnet-5-5")
    assert call.betas == (FALLBACK_BETA,)
    result = llm_client._anthropic_call(SECRET, call)
    assert (result.text, result.stop, result.model_served, result.request_id) == ("Rescued.", "complete", "claude-sonnet-5", "req_fb")
    assert usage.answered_by_fallback(result.usage)
    request = sent[0]
    assert request.url.path == "/v1/messages"
    assert FALLBACK_BETA in request.headers["anthropic-beta"].split(",")
    body = json.loads(request.content)
    assert body["fallbacks"] == "default" and "betas" not in body
    assert [message["role"] for message in body["messages"]] == ["user", "system"]
    assert body["messages"][1] == {"role": "system", "content": "Read the session."}
    assert body["system"] == [{"type": "text", "text": "You read sessions."}]
    assert body["thinking"] == {"type": "adaptive"} and body["output_config"] == {"effort": "medium"}
    assert "temperature" not in body


def test_a_v1_stream_with_the_fallback_goes_through_the_beta_stream(monkeypatch):
    sent: list = []

    def handler(request):
        sent.append(request)
        return _stream_response(_stream_events())

    install_mock_transport(monkeypatch, handler)
    result = llm_client._anthropic_call(SECRET, _v1_call("claude-sonnet-5-5"), stream=True, deadline_s=600)
    assert (result.text, result.stop, result.request_id, result.streamed) == ("Understood.", "complete", "req_stream", True)
    request = sent[0]
    body = json.loads(request.content)
    assert body["stream"] is True and body["fallbacks"] == "default"
    assert FALLBACK_BETA in request.headers["anthropic-beta"].split(",")
    assert request.headers["connection"] == "close", "a stream has a connection of its own, for its deadline to shut"


def test_a_real_error_event_mid_stream_is_not_retried(monkeypatch, sleeps):
    events = _stream_events()[:5] + [{"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}]
    seen: list = []

    def handler(request):
        seen.append(request)
        return _stream_response(events)

    install_mock_transport(monkeypatch, handler)
    with pytest.raises(llm_client.LLMProviderError, match="error while answering: overloaded_error"):
        _stream_call(600)
    assert len(seen) == 1 and sleeps == []


# A model that thinks for minutes may send no text: the SDK drops the server's
# `ping` events before `_stream_message` sees them, and each ping restarts the
# read timeout. The deadline must stop such a stream, and a silent one, on time.

PING = b'event: ping\ndata: {"type": "ping"}\n\n'


class ThinkingStream(httpx2.SyncByteStream):
    """A 200 stream that opens a thinking block, then sends only pings (or
    nothing) for up to `seconds`. Once closed, its read fails, as a read on a
    shut socket does."""

    def __init__(self, *, pings: bool, seconds: float = 15.0, every: float = 0.01):
        self.pings, self.seconds, self.every = pings, seconds, every
        self.closed = threading.Event()
        self.pings_sent = 0

    def __iter__(self):
        yield sse(_stream_events()[:2])
        for _ in range(int(self.seconds / self.every)):
            if self.closed.wait(self.every):
                raise httpx2.ReadError("read on a shut connection")
            if self.pings:
                self.pings_sent += 1
                yield PING

    def close(self):
        self.closed.set()


@pytest.mark.parametrize("pings", [True, False], ids=["pings", "silence"])
def test_the_deadline_stops_a_stream_that_sends_no_event(monkeypatch, sleeps, pings):
    body = ThinkingStream(pings=pings)
    seen: list = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream", "request-id": "req_think"}, stream=body)

    install_mock_transport(monkeypatch, handler)
    started = time.monotonic()
    with pytest.raises(llm_client.LLMDeadlineExceeded, match="longer than 1 s"):
        _stream_call(1)
    assert time.monotonic() - started < 10, "stopped by the deadline, not by the end of the stream"
    assert body.closed.is_set() and len(seen) == 1 and sleeps == [], "closed, never retried"
    assert (body.pings_sent > 10) is pings


@pytest.fixture
def thinking_server():
    """A local HTTP server answering a stream that opens a thinking block,
    then sends pings (or nothing) every 20 ms for up to 15 s. No provider."""
    stop = threading.Event()
    seen: list = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _chunk(self, data: bytes):
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        def do_POST(self):
            seen.append(self.headers.get("connection"))
            self.rfile.read(int(self.headers["content-length"]))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            try:
                self._chunk(sse(_stream_events()[:2]))
                for _ in range(750):
                    if stop.wait(0.02):
                        break
                    if self.server.pings:
                        self._chunk(PING)
            except OSError:
                pass  # the client shut the connection

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads, server.block_on_close, server.pings = True, False, True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server, seen
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("pings", [True, False], ids=["pings", "silence"])
def test_the_deadline_shuts_a_real_connection(monkeypatch, sleeps, thinking_server, pings):
    # Over a real socket, closing the response alone leaves the read waiting
    # for its 300 s timeout; the deadline's timer shuts the socket.
    server, seen = thinking_server
    server.pings = pings
    monkeypatch.setattr(config, "ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    client = llm_client._build_anthropic_client(SECRET, http_client=httpx2.Client(trust_env=False))
    monkeypatch.setattr(llm_client, "_anthropic_client", lambda api_key: client)
    started = time.monotonic()
    with pytest.raises(llm_client.LLMDeadlineExceeded, match="longer than 1 s"):
        _stream_call(1)
    assert time.monotonic() - started < 10, "not the read timeout, not the end of the server's stream"
    assert seen == ["close"] and sleeps == []


def test_the_deadline_timer_does_nothing_once_the_stream_ends():
    closed: list = []
    stream = SimpleNamespace(close=lambda: closed.append(True))
    deadline = llm_client._StreamDeadline(stream, 0.05)
    assert deadline.finish() is False
    threading.Event().wait(0.2)
    assert closed == [] and deadline.finish() is False
    fired = llm_client._StreamDeadline(stream, 0)
    for _ in range(100):
        if closed:
            break
        threading.Event().wait(0.01)
    assert closed == [True] and fired.finish() is True


def test_the_sdk_itself_retries_nothing(monkeypatch, sleeps):
    seen: list = []

    def handler(request):
        seen.append(request)
        return json_response({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}, status=529)

    install_mock_transport(monkeypatch, handler)
    with pytest.raises(llm_client.LLMProviderError, match="HTTP 529 overloaded_error"):
        _send()
    assert len(seen) == llm_client.LLM_MAX_ATTEMPTS, "only StormIQ's attempts: the SDK's max_retries is 0"
    assert [request.headers["x-stainless-retry-count"] for request in seen] == ["0"] * llm_client.LLM_MAX_ATTEMPTS


def test_a_real_retry_after_header_is_read(monkeypatch, sleeps):
    answers = [json_response({"type": "error", "error": {"type": "rate_limit_error", "message": "Slow down"}}, status=429, headers={"retry-after": "7"}), json_response(answer_body())]
    install_mock_transport(monkeypatch, lambda request: answers.pop(0))
    assert _send().text == "# Output"
    assert sleeps == [7.0]


@pytest.mark.parametrize(
    ("failure", "requests_made"),
    [(httpx2.ConnectTimeout, 1), (httpx2.ReadTimeout, 1), (httpx2.ConnectError, 2)],
    ids=["connect-timeout", "read-timeout", "connect-error"],
)
def test_a_timeout_is_never_retried_and_a_connection_error_is(monkeypatch, sleeps, failure, requests_made):
    # The SDK raises every timeout as APITimeoutError, a connect timeout too:
    # it is no longer retried (requests counted it as a connection error).
    seen: list = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            raise failure("synthetic", request=request)
        return json_response(answer_body())

    install_mock_transport(monkeypatch, handler)
    if requests_made == 1:
        with pytest.raises(llm_client.LLMProviderError, match="no answer in time") as failed:
            _send()
        assert isinstance(failed.value.__cause__, anthropic.APITimeoutError)
    else:
        assert _send().text == "# Output"
    assert len(seen) == requests_made


def test_the_key_never_reaches_an_error_or_a_log(monkeypatch, caplog, sleeps):
    def handler(request):
        return json_response({"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}, status=401)

    install_mock_transport(monkeypatch, handler)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(llm_client.LLMProviderError) as failed:
            _send()
    assert str(failed.value) == "The Anthropic call failed (HTTP 401 authentication_error). Nothing was saved. Please try again."
    assert SECRET not in str(failed.value) and SECRET not in caplog.text
    assert "api.anthropic.com" not in str(failed.value), "no URL in what users see"


# ── Errors through the job queue ───────────────────────────────────────────


def test_a_provider_error_keeps_its_message_through_the_job_queue():
    # arq pickles a failed job's exception and the poll reads it back (queue._job_status).
    # The SDK's own errors can't be rebuilt, which is why llm_client raises its own.
    from arq.jobs import DeserializationError, deserialize_result, serialize_result

    def round_trip(exc):
        raw = serialize_result("run_overall_analysis_job", (), {}, 1, 0, False, exc, 0, 0, "ref", "arq:queue", "job-1")
        return deserialize_result(raw).result

    for error in (
        llm_client._provider_error(status_error(529, error_type="overloaded_error")),
        llm_client.LLMDeadlineExceeded("The model's answer took longer than 600 s, so it was stopped. Nothing was saved."),
    ):
        back = round_trip(error)
        assert type(back) is type(error) and str(back) == str(error)
    with pytest.raises(DeserializationError):
        round_trip(status_error(529))


def test_the_error_classes():
    assert issubclass(llm_client.LLMProviderError, llm_client.LLMError)
    assert issubclass(llm_client.LLMDeadlineExceeded, llm_client.LLMProviderError)


# ── scripts/llm_smoke.py ────────────────────────────────────────────────────


def _smoke_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "llm_smoke.py"
    spec = importlib.util.spec_from_file_location("llm_smoke", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["llm_smoke"] = module  # dataclasses read their module
    spec.loader.exec_module(module)
    return module


CONFIDENTIAL = "confidential-answer-text"


class SmokeMessages:
    def __init__(self, calls: list, fail_names: dict):
        self._calls = calls
        self._fail = fail_names

    def _answer(self, params):
        self._calls.append(params)
        failure = self._fail.get(len(self._calls) - 1)
        if failure is not None:
            raise failure
        cached = "cache_control" in json.dumps(params.get("messages"))
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=CONFIDENTIAL)],
            usage=SimpleNamespace(cache_read_input_tokens=2911 if cached and len(self._calls) > 1 else 0),
        )

    def create(self, **params):
        return self._answer(params)

    def stream(self, **params):
        message = self._answer(params)

        class Stream:
            def __enter__(self):
                return SimpleNamespace(get_final_message=lambda: message)

            def __exit__(self, *exc_info):
                return False

        return Stream()


class SmokeClient:
    def __init__(self, fail_names=None):
        self.calls: list = []
        self.messages = SmokeMessages(self.calls, fail_names or {})
        self.beta = SimpleNamespace(messages=self.messages)


class SmokeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def test_the_smoke_script_refuses_to_run_without_the_flag():
    script = _smoke_script()

    def must_not_call(*args, **kwargs):
        raise AssertionError("no paid call without the flag")

    out = io.StringIO()
    code = script.main([], environ={"CLAUDE_API_KEY": SECRET}, anthropic_factory=must_not_call, openai_post=must_not_call, out=out)
    assert code == 2 and "--i-understand-this-costs" in out.getvalue()


def test_the_smoke_script_uses_the_apps_client():
    script = _smoke_script()
    assert inspect.signature(script.main).parameters["anthropic_factory"].default is llm_client._build_anthropic_client


def test_the_smoke_plan_follows_the_catalog():
    script = _smoke_script()
    probes = script.plan(legacy=True, only=[], dossier_model="claude-haiku-5-5", anthropic_on=True, openai_on=True)
    by_model: dict = {}
    for probe in probes:
        by_model.setdefault((probe.provider, probe.model), []).append(probe)
    offered = [caps.id for caps in models.catalog_models("anthropic", "offered")]
    for model_id in offered:
        caps = models.catalog_model(model_id)
        names = [probe.name for probe in by_model[("anthropic", model_id)]]
        assert names[:2] == ["plain", "effort=low"] and "thinking=adaptive" in names and "format=json_schema" in names
        assert ("fallbacks=default" in names) is caps.server_fallback
        for probe in by_model[("anthropic", model_id)]:
            assert "temperature" not in probe.params and "temperature" not in probe.params.get("extra_body", {})
            if probe.name.startswith("job="):
                spec = jobs.job_spec(probe.name[4:])
                assert (probe.params["max_tokens"], probe.stream) == jobs.budget(spec, caps)
                # The job's own request, as the app writes it: nothing hand-built.
                expected = anthropic_call(script._job_request(spec), caps, probe.params["max_tokens"])
                assert {key: value for key, value in probe.params.items() if key != "betas"} == expected.params
                assert (probe.params.get("betas", []), probe.beta) == (list(expected.betas), caps.server_fallback)
    assert not any(probe.name == "fallbacks=default" for probe in by_model[("anthropic", "claude-haiku-5-5")])
    understanding = [probe for probe in by_model[("anthropic", "claude-sonnet-5-5")] if probe.name == "job=understanding"]
    assert understanding[0].stream and understanding[0].params["max_tokens"] == 48_000
    assert understanding[0].params["messages"][-1] == {"role": "system", "content": script.PROMPT}, "the dossier layout"
    dossier = [probe for probe in probes if probe.cache]
    assert [probe.name for probe in dossier] == [
        "dossier-write", "dossier-repeat", "dossier-with-schema", "dossier-schema-repeat",
    ]
    assert dossier[0].params == dossier[1].params, "the repeat is the same request, so it reads the cache"
    assert dossier[2].params == dossier[3].params, "the schema repeat reads the entry the schema call wrote"
    assert "output_config" in dossier[2].params and dossier[2].params != dossier[0].params
    assert [probe.expect_cache_read for probe in dossier] == [False, True, False, True]
    text = dossier[0].params["messages"][0]["content"][0]["text"]
    assert len(text) // 4 > 2 * models.catalog_model("claude-haiku-5-5").min_cache_tokens
    legacy = {probe.model: probe for probe in probes if probe.name.startswith("temperature=") and probe.provider == "anthropic"}
    assert set(legacy) == {caps.id for caps in models.catalog_models("anthropic", "legacy") if caps.temperature_ok}
    assert all(probe.params["extra_body"] == {"temperature": 0.2} for probe in legacy.values())
    openai_rows = models.catalog_models("openai", "legacy")
    assert len([probe for probe in probes if probe.provider == "openai"]) == 5 * len(openai_rows)


def test_the_smoke_script_prints_acceptance_stops_and_cache_reads_only(monkeypatch):
    script = _smoke_script()

    def no_database(*args, **kwargs):
        raise AssertionError("the smoke script touches no database")

    monkeypatch.setattr(db, "get_db_connection", no_database)
    monkeypatch.setattr(db, "get_setting", no_database)
    probes = script.plan(legacy=False, only=[], dossier_model="claude-haiku-5-5", anthropic_on=True, openai_on=True)
    anthropic_probes = [probe for probe in probes if probe.provider == "anthropic"]
    fallback_index = next(index for index, probe in enumerate(anthropic_probes) if probe.name == "fallbacks=default")
    client = SmokeClient({fallback_index: status_error(400, error_type="invalid_request_error")})
    keys: list = []

    def factory(key):
        keys.append(key)
        return client

    def openai_post(url, *, headers, json, timeout):
        assert timeout == script.OPENAI_TIMEOUT and headers["Authorization"] == "Bearer sk-test-OPENAI"
        if "temperature" in json:
            return SmokeResponse(400, {"error": {"type": "invalid_request_error", "param": "temperature", "message": CONFIDENTIAL}})
        return SmokeResponse(200, {"choices": [{"finish_reason": "stop", "message": {"content": CONFIDENTIAL}}]})

    out = io.StringIO()
    code = script.main(
        ["--i-understand-this-costs"],
        environ={"CLAUDE_API_KEY": SECRET, "OPENAI_API_KEY": "sk-test-OPENAI"},
        anthropic_factory=factory, openai_post=openai_post, out=out,
    )
    lines = out.getvalue().splitlines()
    assert code == 1, "a call was not accepted"
    assert keys == [SECRET]
    assert len(lines) == len(probes) + 1
    assert lines[-1] == f"calls={len(probes)} not_accepted={1 + len(models.catalog_models('openai', 'legacy'))} cache_misses=0"
    assert "anthropic claude-haiku-5-5 effort=low accepted stop=end_turn" in lines
    assert "anthropic claude-haiku-5-5 dossier-repeat accepted stop=end_turn cache_read=2911" in lines
    assert any(line.endswith("fallbacks=default rejected HTTP 400 invalid_request_error") for line in lines)
    assert "openai gpt-4o-mini temperature=0.2 rejected HTTP 400 invalid_request_error param=temperature" in lines
    for leaked in (CONFIDENTIAL, SECRET, "sk-test-OPENAI", "synthetic session", "Reply with"):
        assert leaked not in out.getvalue()


def test_the_smoke_script_skips_a_provider_without_a_key():
    script = _smoke_script()

    def must_not_call(*args, **kwargs):
        raise AssertionError("no key, no call")

    out = io.StringIO()
    code = script.main(["--i-understand-this-costs"], environ={}, anthropic_factory=must_not_call, openai_post=must_not_call, out=out)
    assert code == 0
    assert out.getvalue().splitlines() == [
        "anthropic skipped: CLAUDE_API_KEY is not set",
        "openai skipped: OPENAI_API_KEY is not set",
        "calls=0 not_accepted=0 cache_misses=0",
    ]


def test_the_smoke_script_counts_a_repeat_that_reads_no_cache():
    script = _smoke_script()
    client = SmokeClient()
    client.messages._answer = lambda params, answer=client.messages._answer: _no_cache(answer(params))
    out = io.StringIO()
    code = script.main(
        ["--i-understand-this-costs", "--model", "claude-haiku-5-5"],
        environ={"CLAUDE_API_KEY": SECRET}, anthropic_factory=lambda key: client, openai_post=None, out=out,
    )
    lines = out.getvalue().splitlines()
    assert code == 1
    assert "anthropic claude-haiku-5-5 dossier-repeat accepted stop=end_turn cache_read=0 (expected > 0)" in lines
    assert "anthropic claude-haiku-5-5 dossier-schema-repeat accepted stop=end_turn cache_read=0 (expected > 0)" in lines
    assert "anthropic claude-haiku-5-5 dossier-write accepted stop=end_turn cache_read=0" in lines, "only the repeats must read the cache"
    assert "anthropic claude-haiku-5-5 dossier-with-schema accepted stop=end_turn cache_read=0" in lines
    assert lines[-1].endswith("not_accepted=0 cache_misses=2")


def _no_cache(message):
    message.usage.cache_read_input_tokens = 0
    return message
