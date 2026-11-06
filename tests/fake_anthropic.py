"""Fakes of the Anthropic SDK client for tests: no network, scripted answers.

Two kinds, both installed in place of `llm_client._anthropic_client`:

- `FakeAnthropic`, a fake client object. `post` (a non-streamed call: the
  body `llm_client._post_message` wrote) and `messages.stream` record what
  they were sent and take the next step of a script: an answer body (a dict,
  or `FakeMessage`), a `FakeStreamScript` for a stream, or an exception to
  raise (`status_error`, `timeout_error`, `connection_error` build the ones
  the pinned SDK raises).
- `mock_transport_client`, the real SDK client over an httpx2 mock transport,
  for tests that must see the bytes the SDK sends or the errors it raises.

Bodies are synthetic.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import anthropic
import httpx2

from livestorm_app import llm_client

API_URL = "https://api.anthropic.com/v1/messages"


def answer_body(text: str = "# Output", stop: Optional[str] = "end_turn", *, model: str = "claude-haiku-4-5-20251001", **extra) -> Dict[str, Any]:
    body = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "stop_details": None,
        "usage": {"input_tokens": 1200, "output_tokens": 300},
    }
    body.update(extra)
    return body


class FakeMessage:
    """What `messages.create` returns: `to_dict()` and `_request_id`, as the SDK's Message."""

    def __init__(self, body: Any, request_id: Optional[str] = None):
        self._body = body
        self._request_id = request_id

    def to_dict(self, **kwargs) -> Any:
        return copy.deepcopy(self._body)


class FakeStreamScript:
    """One stream: `events` event types, then the final message `body`. With
    `error`, the stream raises it after `fail_after` events instead of ending."""

    def __init__(
        self,
        body: Optional[Dict[str, Any]] = None,
        *,
        events: tuple = ("message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"),
        error: Optional[BaseException] = None,
        fail_after: int = 0,
        request_id: Optional[str] = "req_stream",
    ):
        self.body = body if body is not None else answer_body()
        self.events = events
        self.error = error
        self.fail_after = fail_after
        self.request_id = request_id


class FakeMessageStream:
    def __init__(self, script: FakeStreamScript, on_event: Optional[Callable[[str], None]]):
        self._script = script
        self._on_event = on_event
        self.request_id = script.request_id
        self.closed = False
        self.events_read = 0

    def __iter__(self):
        for index, event_type in enumerate(self._script.events):
            if self._script.error is not None and index == self._script.fail_after:
                raise self._script.error
            self.events_read += 1
            if self._on_event is not None:
                self._on_event(event_type)
            yield SimpleNamespace(type=event_type)
        if self._script.error is not None:
            raise self._script.error

    def get_final_message(self) -> FakeMessage:
        return FakeMessage(self._script.body, self._script.request_id)

    def close(self) -> None:
        self.closed = True


class _FakeStreamManager:
    def __init__(self, fake: "FakeAnthropic"):
        self._fake = fake
        self.stream: Optional[FakeMessageStream] = None

    def __enter__(self) -> FakeMessageStream:
        step = self._fake._next()
        if isinstance(step, BaseException):
            raise step  # failed when the stream opened, before any event
        if not isinstance(step, FakeStreamScript):
            step = FakeStreamScript(step.to_dict() if isinstance(step, FakeMessage) else step)
        self.stream = FakeMessageStream(step, self._fake.on_event)
        self._fake.streams.append(self.stream)
        return self.stream

    def __exit__(self, *exc_info) -> None:
        if self.stream is not None:
            self.stream.close()


class _FakeMessages:
    def __init__(self, fake: "FakeAnthropic"):
        self._fake = fake

    def stream(self, **params):
        self._fake._record({**params, "_stream": True})
        return _FakeStreamManager(self._fake)


class FakeAnthropic:
    """A fake SDK client. `requests` holds each call's fields (with `_stream`):
    a non-streamed call's parsed body, a stream's keyword arguments; `body(i)`
    is the JSON the SDK would send for call i, and `contents[i]` the bytes a
    non-streamed call sent (None for a stream). Steps come from `script`, or
    from `next_step` when given; `on_request(i)` runs as call i is made,
    `on_event(type)` as a stream yields each event."""

    def __init__(
        self,
        script: Optional[List[Any]] = None,
        *,
        api_key: str = "sk-test-key",
        next_step: Optional[Callable[[], Any]] = None,
        on_request: Optional[Callable[[int], None]] = None,
    ):
        self.api_key = api_key
        self.script: List[Any] = list(script or [])
        self.requests: List[Dict[str, Any]] = []
        self.contents: List[Optional[bytes]] = []
        self.streams: List[FakeMessageStream] = []
        self.on_event: Optional[Callable[[str], None]] = None
        self.on_request = on_request
        self._next_step = next_step
        self.messages = _FakeMessages(self)

    def _record(self, params: Dict[str, Any], content: Optional[bytes] = None) -> None:
        self.requests.append(params)
        self.contents.append(content)
        if self.on_request is not None:
            self.on_request(len(self.requests) - 1)

    def post(self, path: str, *, cast_to: Any, content: bytes, options: Optional[Dict[str, Any]] = None):
        """A non-streamed Messages call, as `llm_client._post_message` makes it."""
        assert (path, cast_to) == ("/v1/messages", anthropic.types.Message)
        self._record({**json.loads(content), "_stream": False}, content)
        step = self._next()
        if isinstance(step, BaseException):
            raise step
        return step if isinstance(step, FakeMessage) else FakeMessage(step)

    def _next(self) -> Any:
        if self._next_step is not None:
            return self._next_step()
        if not self.script:
            raise AssertionError("FakeAnthropic: no scripted answer left")
        return self.script.pop(0)

    def body(self, index: int = -1) -> Dict[str, Any]:
        params = dict(self.requests[index])
        stream = params.pop("_stream")
        params.pop("extra_headers", None)
        extra = params.pop("extra_body", None) or {}
        body = {**params, **extra}
        if stream:
            body["stream"] = True
        return body


def install(monkeypatch, fake: Any) -> List[str]:
    """Make `llm_client` use `fake` as its SDK client. Returns the keys it was asked for."""
    keys: List[str] = []

    def client(api_key: str):
        keys.append(api_key)
        return fake

    monkeypatch.setattr(llm_client, "_anthropic_client", client)
    return keys


# ── The pinned SDK's own errors ────────────────────────────────────────────


def _request() -> httpx2.Request:
    return httpx2.Request("POST", API_URL)


def status_error(status: int, *, error_type: Optional[str] = None, retry_after: Optional[str] = None) -> anthropic.APIStatusError:
    """The error the SDK raises for an HTTP `status` (its own class for the status)."""
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    body = {"type": "error", "error": {"type": error_type or "api_error", "message": "synthetic"}}
    response = httpx2.Response(status, headers=headers, json=body, request=_request())
    client = anthropic.Anthropic(api_key="sk-test-key", max_retries=0)
    return client._make_status_error(f"Error code: {status}", body=body, response=response)


def stream_error_event(error_type: str = "overloaded_error") -> anthropic.APIStatusError:
    """What the SDK raises for an `error` event inside a 200 stream."""
    body = {"type": "error", "error": {"type": error_type, "message": "synthetic"}}
    response = httpx2.Response(200, headers={"content-type": "text/event-stream"}, request=_request())
    client = anthropic.Anthropic(api_key="sk-test-key", max_retries=0)
    return client._make_status_error(json.dumps(body), body=body, response=response)


def timeout_error() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(request=_request())


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=_request())


# ── The real SDK over a mock transport ─────────────────────────────────────


def sse(events: List[Dict[str, Any]]) -> bytes:
    """A server-sent event stream of Messages API events."""
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()


def mock_transport_client(handler: Callable[[httpx2.Request], httpx2.Response], api_key: str = "sk-test-key") -> anthropic.Anthropic:
    """The real SDK client, built as `llm_client` builds it, answering from `handler`."""
    return llm_client._build_anthropic_client(api_key, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def install_mock_transport(monkeypatch, handler: Callable[[httpx2.Request], httpx2.Response]) -> List[str]:
    """Route `llm_client`'s Anthropic calls to the real SDK over `handler`. Returns the keys used."""
    keys: List[str] = []
    clients: Dict[str, anthropic.Anthropic] = {}

    def client(api_key: str):
        keys.append(api_key)
        if api_key not in clients:
            clients[api_key] = mock_transport_client(handler, api_key)
        return clients[api_key]

    monkeypatch.setattr(llm_client, "_anthropic_client", client)
    return keys


def json_response(body: Dict[str, Any], *, status: int = 200, headers: Optional[Dict[str, str]] = None) -> httpx2.Response:
    return httpx2.Response(status, headers=headers or {}, json=body)


def scripted_providers(monkeypatch, script: list, sent: list) -> FakeAnthropic:
    """Both providers answering from one script of `FakeResponse`-like objects
    (`.json()` and `.headers`), as the tests' `http` fixtures did through
    `_post_with_retry` before the SDK. OpenAI still goes through
    `_post_with_retry`; Anthropic through a `FakeAnthropic`, which turns a
    response into the SDK's message (its body and `request-id`). Each call
    appends `{url, headers, payload, provider, stream}` to `sent`; an
    Anthropic call's payload is the JSON the SDK would send."""

    def next_step():
        step = script.pop(0)
        if isinstance(step, (BaseException, FakeMessage, FakeStreamScript)):
            return step
        return FakeMessage(step.json(), (getattr(step, "headers", None) or {}).get("request-id"))

    fake = FakeAnthropic(next_step=next_step)
    fake.on_request = lambda index: sent.append({
        "url": API_URL,
        "headers": {},
        "payload": fake.body(index),
        "provider": "anthropic",
        "stream": fake.requests[index]["_stream"],
    })
    install(monkeypatch, fake)

    def fake_post(url, headers, payload, provider):
        sent.append({"url": url, "headers": headers, "payload": payload, "provider": provider, "stream": False})
        return script.pop(0)

    monkeypatch.setattr(llm_client, "_post_with_retry", fake_post)
    return fake
