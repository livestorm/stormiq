"""Structured outputs and refusals: the strict JSON schemas, what each provider
is sent for a JSON step, and what each stop does to the answer.

Pure tests on synthetic provider responses: Anthropic is a fake SDK client
(`tests/fake_anthropic.py`), OpenAI's POST (`llm_client._post_with_retry`) is
replaced, and the usage writer collects rows instead of saving them. The real-Postgres cases (a refusal saves nothing
but its usage row) are in test_data_integrity.py.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any, List, Literal, Optional, Tuple

import pytest
from pydantic import BaseModel, Field

from livestorm_app import api_logic, db, llm_client
from livestorm_app.ai import generation, jobs, models, schemas, usage
from livestorm_app.ai.dossier import make_dossier
from livestorm_app.ai.request import build_request
from livestorm_app.ai.usage import CallContext

from fake_anthropic import FakeStreamScript, scripted_providers

SECRET = "sk-test-SECRET"
# Content a model might answer with; it must never reach an error, a log or a usage row.
CONFIDENTIAL = "confidential-answer-text"


# ── Synthetic schemas ──────────────────────────────────────────────────────


class Evidence(BaseModel):
    """A pointer to one chat message."""

    eid: str = Field(description="A chat evidence id such as C0001", min_length=5, max_length=5)
    weight: float = Field(ge=0, le=1)


class Reading(BaseModel):
    kind: Literal["public", "internal"]
    summary: str = Field(min_length=1)
    evidence: List[Evidence] = Field(min_length=1, max_length=3)
    best: Evidence
    note: Optional[str]


@pytest.fixture
def registered(monkeypatch):
    """Register the synthetic schemas for one test, as `@json_schema` would at import."""
    monkeypatch.setattr(schemas, "SCHEMAS", dict(schemas.SCHEMAS))
    monkeypatch.setattr(schemas, "_STRICT", dict(schemas._STRICT))
    schemas.json_schema(Reading)
    return Reading


VALID = {
    "kind": "public",
    "summary": "Questions on pricing",
    "evidence": [{"eid": "C0001", "weight": 0.5}],
    "best": {"eid": "C0002", "weight": 1},
    "note": None,
}


# ── Strict schemas ─────────────────────────────────────────────────────────


def test_the_strict_schema_is_closed_inlined_and_unbounded(registered):
    strict = schemas.strict_json_schema(Reading)
    assert schemas.lint_strict_schema(strict) == []
    assert strict["type"] == "object" and strict["additionalProperties"] is False
    assert strict["required"] == ["kind", "summary", "evidence", "best", "note"]
    assert strict["properties"]["kind"] == {"type": "string", "enum": ["public", "internal"]}
    # Nested models are written out in place, with their own closed objects.
    best = strict["properties"]["best"]
    assert best["additionalProperties"] is False and best["required"] == ["eid", "weight"]
    assert best["properties"]["eid"] == {"description": "A chat evidence id such as C0001", "type": "string"}
    assert strict["properties"]["evidence"]["items"] == best
    # An optional field is required, and may be null.
    assert strict["properties"]["note"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    text = json.dumps(strict)
    for keyword in ("$ref", "$defs", "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "title"):
        assert keyword not in text, keyword
    # Callers get a copy.
    strict["properties"].clear()
    assert schemas.strict_json_schema(Reading)["properties"]


def test_pydantic_still_checks_the_bounds_left_out_of_the_schema(registered):
    assert Reading.model_validate_json(json.dumps(VALID)).best.eid == "C0002"
    for broken in (
        {**VALID, "evidence": []},  # minItems
        {**VALID, "summary": ""},  # minLength
        {**VALID, "best": {"eid": "C1", "weight": 0.5}},  # length
        {**VALID, "best": {"eid": "C0001", "weight": 2}},  # maximum
    ):
        with pytest.raises(ValueError):
            Reading.model_validate_json(json.dumps(broken))


def test_a_recursive_schema_fails_at_import():
    class Node(BaseModel):
        children: List["Node"]

    with pytest.raises(schemas.SchemaError, match="recursive"):
        schemas.json_schema(Node)
    assert "Node" not in schemas.SCHEMAS


def _model(**fields: Any) -> type:
    return type("Probe", (BaseModel,), {"__annotations__": {name: kind for name, (kind, _) in fields.items()},
                                         **{name: default for name, (_, default) in fields.items() if default is not ...}})


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"counts": (dict, ...)}, "free-form object"),
        ({"pair": (Tuple[int, str], ...)}, "'prefixItems' not supported"),
        ({"items": (list, ...)}, "needs a type"),
        ({"anything": (Any, ...)}, "needs a type"),
        ({"label": (str, "default")}, "every field must be required"),
        ({"label": (Optional[str], None)}, "every field must be required"),
    ],
)
def test_schemas_outside_the_strict_subset_are_refused(fields, message):
    with pytest.raises(schemas.SchemaError, match=message):
        schemas.build_strict_schema(_model(**fields))


def test_only_registered_uniquely_named_models_are_schemas(registered):
    class Unregistered(BaseModel):
        label: str

    with pytest.raises(schemas.SchemaError, match="not a registered schema"):
        schemas.strict_json_schema(Unregistered)
    duplicate = type("Reading", (BaseModel,), {"__annotations__": {"label": str}})
    with pytest.raises(schemas.SchemaError, match="Two schemas are named Reading"):
        schemas.json_schema(duplicate)
    schemas.json_schema(Reading)  # registering the same model again is harmless


def test_every_registered_schema_passes_the_lint(registered):
    # Nothing registers a real schema yet: 1d imports its schema modules here.
    assert schemas.SCHEMAS
    for name, model in schemas.SCHEMAS.items():
        assert schemas.lint_strict_schema(schemas.strict_json_schema(model)) == [], name


def test_a_schema_that_fails_the_lint_is_never_registered(monkeypatch):
    monkeypatch.setattr(schemas, "SCHEMAS", dict(schemas.SCHEMAS))
    monkeypatch.setattr(schemas, "_STRICT", dict(schemas._STRICT))
    monkeypatch.setattr(schemas, "lint_strict_schema", lambda schema, path="$": ["$: a problem"])

    class Linted(BaseModel):
        label: str

    with pytest.raises(schemas.SchemaError, match="Linted: \\$: a problem"):
        schemas.json_schema(Linted)
    assert "Linted" not in schemas.SCHEMAS and Linted not in schemas._STRICT


def test_the_lint_names_what_breaks_the_subset():
    problems = schemas.lint_strict_schema({
        "type": "object",
        "properties": {"a": {"type": "string", "minLength": 1}, "b": {"type": "object", "properties": {"c": {"type": "integer"}}}},
        "required": ["a"],
    })
    assert "$: additionalProperties is not false" in problems
    assert "$: not every field is required" in problems
    assert "$.a: keyword 'minLength' is not sent" in problems
    assert "$.b: additionalProperties is not false" in problems


# ── Provider fakes ─────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, body: dict, headers: Optional[dict] = None):
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body


def anthropic_body(text: str = "", stop: str = "end_turn", *, model: str = "claude-haiku-5-5", content=None, stop_details=None):
    return {
        "model": model,
        # The 5.5 models may start with an empty thinking block; only text blocks are the answer.
        "content": content if content is not None else [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
        "stop_reason": stop,
        "stop_details": stop_details,
        "usage": {"input_tokens": 1000, "output_tokens": 200},
    }


def openai_body(content: Optional[str] = "", finish: str = "stop", *, refusal: Optional[str] = None):
    return {
        "model": "gpt-4o-mini-2024-07-18",
        "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content, "refusal": refusal}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
    }


@pytest.fixture
def http(monkeypatch):
    """Replace the providers: each call takes the next scripted response
    (Anthropic through a fake SDK client, OpenAI's POST)."""
    script: list = []
    sent: list = []
    scripted_providers(monkeypatch, script, sent)
    monkeypatch.setattr(db, "get_setting", lambda key, default=None: default)
    return script, sent


@pytest.fixture
def rows(monkeypatch):
    saved: list = []
    monkeypatch.setattr(db, "insert_llm_usage", saved.append)
    return saved


@pytest.fixture
def anthropic(monkeypatch):
    monkeypatch.setattr(llm_client, "get_provider", lambda: "anthropic")


@pytest.fixture
def openai(monkeypatch):
    """OpenAI, with an OpenAI model for the `profile` job (v1 jobs have none yet)."""
    monkeypatch.setattr(llm_client, "get_provider", lambda: "openai")
    monkeypatch.setitem(jobs._SPECS, "profile", dataclasses.replace(jobs.job_spec("profile"), default_openai="gpt-4o-mini"))


PROFILE = CallContext("profile", "sess-1", "org-a", "profile:sess-1:_")
MESSAGES = [{"role": "system", "content": "Read the session."}, {"role": "user", "content": "Session data"}]


def _request(kind: str = "profile"):
    """A v1 call's request, laid out for its job: the same two texts as
    MESSAGES, with a dossier in front for a job that reads one."""
    if jobs.job_spec(kind).layout == "dossier":
        dossier = make_dossier("Session data", record_version="6")
        return build_request(kind, system="You read sessions.", context=dossier, instructions="Read the session.")
    return build_request(kind, instructions="Read the session.", payload="Session data")


def _json(**overrides) -> str:
    return json.dumps({**VALID, **overrides})


def _outcomes(rows: list) -> list:
    return [(row["purpose"], row["outcome"], row["stop_reason"], row["refusal_category"]) for row in rows]


# ── What a JSON step sends ─────────────────────────────────────────────────


def test_anthropic_gets_the_schema_in_output_config(http, rows, anthropic, registered):
    script, sent = http
    script.append(FakeResponse(anthropic_body(_json())))
    reading = llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
    assert isinstance(reading, Reading) and reading.evidence[0].eid == "C0001"
    payload = sent[0]["payload"]
    assert payload["model"] == "claude-haiku-5-5"
    assert payload["max_tokens"] == 6_000  # the job's budget: 2,000 answer + 4,000 low-effort thinking
    # The job's effort and the schema travel together; the instructions are the system prompt.
    assert payload["output_config"] == {
        "effort": "low", "format": {"type": "json_schema", "schema": schemas.strict_json_schema(Reading)},
    }
    assert payload["thinking"] == {"type": "adaptive"}
    assert "temperature" not in payload
    assert payload["system"] == [{"type": "text", "text": "Read the session."}]
    assert payload["messages"] == [{"role": "user", "content": "Session data"}]
    # Haiku 5.5 has no server-side fallback, so the call asks for none and needs no beta.
    assert "fallbacks" not in payload and sent[0]["betas"] == []
    assert _outcomes(rows) == [("main", "ok", "end_turn", None)]
    assert (rows[0]["job_kind"], rows[0]["job_id"], rows[0]["model_requested"]) == ("profile", "profile:sess-1:_", "claude-haiku-5-5")
    assert rows[0]["effort"] == "low" and len(rows[0]["prompt_hash"]) == 64 and rows[0]["dossier_hash"] is None


def test_openai_gets_a_strict_response_format(http, rows, openai, registered):
    script, sent = http
    script.append(FakeResponse(openai_body(_json())))
    assert llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE).kind == "public"
    payload = sent[0]["payload"]
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Reading", "strict": True, "schema": schemas.strict_json_schema(Reading)},
    }
    assert "temperature" not in payload and payload["max_tokens"] == 2_000
    assert payload["messages"] == MESSAGES
    assert _outcomes(rows) == [("main", "ok", "stop", None)]


def test_the_classic_outputs_send_what_they_sent_before(http, rows, anthropic, monkeypatch):
    script, sent = http
    script.append(FakeResponse(anthropic_body("# Recap", model="claude-sonnet-4-6")))
    llm_client.call_llm("key", "claude-sonnet-4-6", MESSAGES, temperature=0.2, max_tokens=900)
    assert set(sent[0]["payload"]) == {"model", "max_tokens", "messages", "temperature", "system"}
    monkeypatch.setattr(llm_client, "get_provider", lambda: "openai")
    script.append(FakeResponse(openai_body("# Recap")))
    llm_client.call_llm("key", "gpt-4o-mini", MESSAGES, temperature=0.2, max_tokens=900)
    assert list(sent[1]["payload"]) == ["model", "temperature", "messages", "max_tokens"]


# ── Checked before any paid call ───────────────────────────────────────────


def test_a_call_needs_the_context_of_its_own_job(http, rows, anthropic, registered):
    _, sent = http
    for ctx in (None, CallContext("chat_labels", "sess-1", "org-a")):
        with pytest.raises(ValueError, match="needs a CallContext for profile"):
            llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=ctx)
    with pytest.raises(schemas.SchemaError, match="not a registered schema"):
        llm_client.generate_json("profile", Evidence, _request(), api_key="key", ctx=PROFILE)
    assert sent == [] and rows == []


def test_a_model_without_structured_outputs_is_never_called(http, rows, anthropic, registered, monkeypatch):
    real = jobs.resolve_model

    def no_structured_outputs(kind, provider=None):
        choice = real(kind, provider)
        return dataclasses.replace(choice, caps=dataclasses.replace(choice.caps, structured_outputs=False))

    monkeypatch.setattr(jobs, "resolve_model", no_structured_outputs)
    _, sent = http
    with pytest.raises(llm_client.LLMError, match="can't return structured output"):
        llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
    assert sent == []


def test_a_model_outside_the_catalog_is_never_called(http, rows, anthropic, registered, monkeypatch):
    monkeypatch.setattr(db, "get_setting", lambda key, default=None: "claude-unknown-9" if key == "llm_model.anthropic.profile" else default)
    _, sent = http
    with pytest.raises(llm_client.LLMError, match="not in the model catalog"):
        llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
    assert sent == []


def test_a_budget_that_must_stream_streams_from_anthropic(http, rows, anthropic):
    # The understanding job's 48,000 tokens are over the 16,000 a call may wait for unstreamed.
    script, sent = http
    script.append(FakeStreamScript(anthropic_body("Understood.", model="claude-sonnet-5-5")))
    ctx = CallContext("understanding", "sess-1", "org-a")
    assert llm_client.generate_text("understanding", _request("understanding"), api_key="key", ctx=ctx) == "Understood."
    assert (sent[0]["stream"], sent[0]["payload"]["max_tokens"]) == (True, 48_000)
    assert "temperature" not in sent[0]["payload"]
    assert [(row["streamed"], row["max_tokens"], row["outcome"]) for row in rows] == [(True, 48_000, "ok")]


# ── A request, laid out for its job ────────────────────────────────────────


def test_a_v1_call_takes_only_a_request_built_for_its_own_job(http, rows, anthropic, registered):
    _, sent = http
    with pytest.raises(TypeError, match="build_request"):
        llm_client.generate_json("profile", Reading, MESSAGES, api_key="key", ctx=PROFILE)
    with pytest.raises(ValueError, match="built for chat_labels"):
        llm_client.generate_json("profile", Reading, _request("chat_labels"), api_key="key", ctx=PROFILE)
    assert sent == [] and rows == []


def test_the_understanding_job_sends_the_dossier_first_and_asks_for_the_fallback(http, rows, anthropic):
    script, sent = http
    script.append(FakeStreamScript(anthropic_body("Understood.", model="claude-sonnet-5-5")))
    ctx = CallContext("understanding", "sess-1", "org-a", record_version="6")
    request = _request("understanding")
    assert llm_client.generate_text("understanding", request, api_key="key", ctx=ctx) == "Understood."
    payload = sent[0]["payload"]
    assert payload["system"] == [{"type": "text", "text": "You read sessions."}]
    assert payload["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Session data"}]},  # one call: nothing to cache
        {"role": "system", "content": "Read the session."},
    ]
    assert payload["output_config"] == {"effort": "medium"} and payload["thinking"] == {"type": "adaptive"}
    # Sonnet 5.5 accepts the server-side fallback: asked for through the beta namespace.
    assert payload["fallbacks"] == "default" and sent[0]["betas"] == ["server-side-fallback-2026-07-01"]
    row = rows[0]
    assert (row["effort"], row["dossier_hash"], row["record_version"]) == ("medium", request.dossier_hash, "6")
    assert row["prompt_hash"] == request.prompt_hash() and len(row["dossier_hash"]) == 64


def test_an_answer_from_the_fallback_model_is_used_and_noted(http, rows, anthropic, caplog):
    script, _ = http
    body = anthropic_body(model="claude-sonnet-5", content=[
        {"type": "fallback", "from": {"model": "claude-sonnet-5-5"}, "to": {"model": "claude-sonnet-5"}},
        {"type": "text", "text": "Understood anyway."},
    ])
    body["usage"]["iterations"] = [
        {"type": "message", "model": "claude-sonnet-5-5", "input_tokens": 1000, "output_tokens": 0},
        {"type": "fallback_message", "model": "claude-sonnet-5", "input_tokens": 1000, "output_tokens": 200},
    ]
    script.append(FakeStreamScript(body))
    ctx = CallContext("understanding", "sess-1", "org-a")
    with caplog.at_level(logging.WARNING):
        assert llm_client.generate_text("understanding", _request("understanding"), api_key="key", ctx=ctx) == "Understood anyway."
    assert "declined by claude-sonnet-5-5 and answered by its fallback claude-sonnet-5" in caplog.text
    row = rows[0]
    assert (row["model_requested"], row["model_served"], row["outcome"]) == ("claude-sonnet-5-5", "claude-sonnet-5", "ok")
    assert [attempt["type"] for attempt in row["usage_raw"]["iterations"]] == ["message", "fallback_message"]
    assert "Understood anyway" not in caplog.text and "Session data" not in caplog.text


@pytest.mark.parametrize(
    ("call_number", "cache_read", "warned"),
    [(1, 0, False), (2, 0, True), (2, 900, False), (3, 0, True)],
    ids=["first-call-writes", "second-call-misses", "second-call-reads", "third-call-misses"],
)
def test_a_later_call_that_reads_nothing_from_the_cache_is_noted(http, rows, anthropic, registered, caplog, call_number, cache_read, warned):
    """A cache miss is silent: the call succeeds and costs the whole prefix again."""
    script, sent = http
    body = anthropic_body(_json())
    body["usage"].update(cache_read_input_tokens=cache_read, cache_creation_input_tokens=0 if cache_read else 900)
    script.append(FakeResponse(body))
    request = build_request(
        "chat_labels", instructions="Label each message.", payload="C0001 hello", shared_calls=3, call_number=call_number,
    )
    with caplog.at_level(logging.WARNING):
        llm_client.generate_json("chat_labels", Reading, request, api_key="key", ctx=CallContext("chat_labels", "sess-1", "org-a"))
    assert sent[0]["payload"]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert ("read nothing from the prompt cache" in caplog.text) is warned
    if warned:
        assert f"call {call_number} of 3 for chat_labels" in caplog.text and "minimum prefix 512 tokens" in caplog.text
        assert "Label each message" not in caplog.text and "hello" not in caplog.text
    assert rows[0]["cache_read_tokens"] == cache_read


def test_a_job_without_a_shared_prefix_is_never_warned_about_its_cache(http, rows, anthropic, registered, caplog):
    script, _ = http
    script.append(FakeResponse(anthropic_body(_json())))
    with caplog.at_level(logging.WARNING):
        llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
    assert "prompt cache" not in caplog.text and "fallback" not in caplog.text


def test_a_budget_that_must_stream_is_refused_on_openai(http, rows, openai, monkeypatch):
    # StormIQ's OpenAI calls don't stream, so the call is refused before it is sent.
    monkeypatch.setitem(jobs._SPECS, "understanding", dataclasses.replace(jobs.job_spec("understanding"), default_openai="gpt-5.4-mini"))
    monkeypatch.setitem(models._BY_ID, "gpt-5.4-mini", dataclasses.replace(models.catalog_model("gpt-5.4-mini"), max_output=64_000))
    _, sent = http
    ctx = CallContext("understanding", "sess-1", "org-a")
    with pytest.raises(llm_client.LLMError, match="must stream"):
        llm_client.generate_text("understanding", _request("understanding"), api_key="key", ctx=ctx)
    assert sent == []


def test_only_a_budget_that_must_stream_streams(http, rows, anthropic, registered):
    script, sent = http
    script.append(FakeResponse(anthropic_body(_json())))
    llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
    script.append(FakeResponse(anthropic_body("# Recap", model="claude-sonnet-4-6")))
    llm_client.call_llm("key", "claude-sonnet-4-6", MESSAGES, temperature=0.2, max_tokens=16_000)
    assert [call["stream"] for call in sent] == [False, False]
    assert [row["streamed"] for row in rows] == [False, False]


# ── Refusals ───────────────────────────────────────────────────────────────


REFUSED = "The model declined this session. Nothing was saved."


@pytest.mark.parametrize(
    ("stop_details", "category"),
    [
        ({"type": "refusal", "category": "cyber", "explanation": "declined"}, "cyber"),
        (None, None),  # stop_details can be null: branch on stop_reason only
        ({"type": "refusal", "category": None}, None),
        ({"type": "refusal", "category": "free text with spaces"}, None),
    ],
)
def test_an_anthropic_refusal_raises_and_keeps_no_text(http, rows, anthropic, registered, stop_details, category):
    script, sent = http
    # A refusal mid-answer may come with partial text; none of it is kept.
    script.append(FakeResponse(anthropic_body('{"kind": "pub', "refusal", stop_details=stop_details)))
    with pytest.raises(llm_client.LLMRefused) as refused:
        llm_client.generate_json("profile", Reading, _request(), api_key=SECRET, ctx=PROFILE)
    # The user sees the same neutral message whatever the category; the
    # category stays on the exception and in the usage row.
    assert str(refused.value) == REFUSED and refused.value.category == category
    assert len(sent) == 1
    assert [(row["outcome"], row["stop_reason"]) for row in rows] == [("refused", "refusal")]
    assert rows[0]["refusal_category"] == category


def test_the_answer_comes_from_text_blocks_only(http):
    script, _ = http
    script.append(FakeResponse(anthropic_body(content=[
        {"type": "thinking", "thinking": "summarised reasoning", "signature": "sig"},
        {"type": "fallback", "from": {"model": "claude-sonnet-5-5"}, "to": {"model": "claude-opus-4-8"}},
        {"type": "text", "text": "Answer"},
    ])))
    assert llm_client._anthropic_send("key", "claude-haiku-5-5", [], None, 100).text == "Answer"


def test_the_adapters_drop_a_refusals_partial_text(http):
    script, _ = http
    script.append(FakeResponse(anthropic_body("Partial answer", "refusal")))
    assert llm_client._anthropic_send("key", "claude-haiku-5-5", [], None, 100).text == ""
    script.append(FakeResponse(openai_body("Partial answer", "content_filter")))
    assert llm_client._openai_send("key", "gpt-4o-mini", [], None, 100).text == ""


@pytest.mark.parametrize("category", ["bio", None])
def test_a_refusal_keeps_its_message_through_the_job_queue(category):
    # The worker's arq pickles a failed job's exception; the poll reads it back
    # and returns str(exc) as its `error` (queue._job_status).
    from arq.jobs import deserialize_result, serialize_result

    def round_trip(exc):
        raw = serialize_result("run_smart_recap_job", (), {}, 1, 0, False, exc, 0, 0, "ref", "arq:queue", "job-1")
        return deserialize_result(raw).result

    refused = round_trip(llm_client.LLMRefused(category))
    assert type(refused) is llm_client.LLMRefused and refused.category == category
    assert str(refused) == REFUSED
    for error in (
        llm_client.LLMOutputTruncated("The AI answer was cut off."),
        llm_client.LLMInvalidOutput("The model's answer did not match its format. Nothing was saved."),
        usage.OrgBudgetExceeded("This organization reached its daily AI budget."),
    ):
        back = round_trip(error)
        assert type(back) is type(error) and str(back) == str(error)


@pytest.mark.parametrize(
    "body",
    [openai_body(None, "stop", refusal="I can't help with that."), openai_body("", "content_filter")],
    ids=["message.refusal", "content_filter"],
)
def test_an_openai_refusal_raises(http, rows, openai, registered, body):
    script, sent = http
    script.append(FakeResponse(body))
    with pytest.raises(llm_client.LLMRefused) as refused:
        llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
    assert str(refused.value) == REFUSED
    assert len(sent) == 1
    assert [row["outcome"] for row in rows] == ["refused"]
    assert "help with that" not in repr(rows), "the refusal's own wording is never kept"


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_a_classic_refusal_says_so_instead_of_empty(http, rows, monkeypatch, provider):
    monkeypatch.setattr(llm_client, "get_provider", lambda: provider)
    script, sent = http
    if provider == "anthropic":
        script.append(FakeResponse(anthropic_body("", "refusal", model="claude-sonnet-4-6", stop_details={"type": "refusal", "category": "bio"})))
    else:
        script.append(FakeResponse(openai_body(None, refusal="No.")))
    ctx = CallContext("smart_recap", "sess-1", "org-a", "smart_recap:sess-1:hype")
    with pytest.raises(llm_client.LLMRefused) as refused:
        llm_client.call_llm(SECRET, "model", MESSAGES, max_tokens=900, ctx=ctx)
    assert str(refused.value) == REFUSED
    assert len(sent) == 1, "a refusal is never retried"
    assert [(row["purpose"], row["outcome"]) for row in rows] == [("main", "refused")]


def test_a_refusal_on_the_truncation_retry_is_a_refusal(http, rows, anthropic):
    script, sent = http
    script.extend([
        FakeResponse(anthropic_body("# Part", "max_tokens", model="claude-sonnet-4-6")),
        FakeResponse(anthropic_body("# More", "refusal", model="claude-sonnet-4-6")),
    ])
    ctx = CallContext("smart_recap", "sess-1", "org-a")
    with pytest.raises(llm_client.LLMRefused):
        llm_client.call_llm("key", "claude-sonnet-4-6", MESSAGES, max_tokens=900, ctx=ctx)
    assert [(row["purpose"], row["outcome"]) for row in rows] == [("main", "truncated"), ("truncation_retry", "refused")]


@pytest.mark.parametrize(
    ("generator", "kwargs"),
    [
        (generation.generate_content_repurpose_bundle_with_openai, {"output_language": "English", "transcript_text": "Hello"}),
        (
            generation.translate_content_repurpose_bundle_with_openai,
            {"source_bundle": {"summary": "S", "blog": "B", "email": "E", "social_media": "M"},
             "source_language": "English", "target_language": "French"},
        ),
    ],
)
def test_the_content_bundle_loop_stops_on_a_refusal(http, rows, anthropic, generator, kwargs):
    script, sent = http
    script.extend([FakeResponse(anthropic_body("", "refusal", model="claude-haiku-4-5-20251001"))] * 3)
    ctx = CallContext("content_repurposing", "sess-1", "org-a")
    with pytest.raises(llm_client.LLMRefused):
        generator(api_key="key", model="claude-haiku-4-5-20251001", ctx=ctx, **kwargs)
    assert len(sent) == 1, "one paid call, not three"
    assert [(row["purpose"], row["outcome"]) for row in rows] == [("main", "refused")]


@pytest.mark.parametrize(
    ("run", "dimension"),
    [
        (api_logic.run_deep_analysis, "English"),
        (api_logic.run_smart_recap, "hype"),
        (api_logic.run_content_repurposing, "English"),
        (api_logic.run_content_repurposing, "French"),
    ],
)
def test_a_refused_classic_output_saves_nothing(http, rows, anthropic, monkeypatch, run, dimension):
    row = {
        "organization_id": "org-a",
        "session_payload": {"id": "s1"},
        "chat_payload": {"data": []},
        "questions_payload": {"data": []},
        "transcript_payload": {"transcript": {"segments": [{"start": 0, "end": 2, "text": "Hello there", "speaker": "A"}]}},
        "content_repurpose_bundle": {"English": {"summary": "S", "blog": "B", "email": "E", "social_media": "M"}},
    }
    merges: list = []
    monkeypatch.setattr(api_logic, "fetch_cached_session", lambda key, sid, *, organization_id: dict(row))
    monkeypatch.setattr(api_logic, "merge_cached_bundle_entry", lambda *args, **kwargs: merges.append(args))
    script, sent = http
    script.extend([FakeResponse(anthropic_body("", "refusal", model="claude-haiku-4-5-20251001"))] * 3)
    with pytest.raises(llm_client.LLMRefused, match="declined this session"):
        run(SECRET, "s1", dimension)
    assert merges == [] and len(sent) == 1
    assert [row["outcome"] for row in rows] == ["refused"]


# ── Truncation ─────────────────────────────────────────────────────────────


def test_a_v1_job_fails_at_once_when_cut_off(http, rows, anthropic, registered):
    script, sent = http
    script.append(FakeResponse(anthropic_body('{"kind": "public", "summ', "max_tokens")))
    with pytest.raises(llm_client.LLMOutputTruncated, match="6000 tokens"):
        llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
    assert len(sent) == 1
    assert _outcomes(rows) == [("main", "truncated", "max_tokens", None)]


def test_call_llm_follows_the_jobs_truncation_policy(http, rows, anthropic):
    script, sent = http
    script.extend([FakeResponse(anthropic_body("part", "max_tokens"))] * 2)
    with pytest.raises(llm_client.LLMOutputTruncated):
        llm_client.call_llm("key", "claude-haiku-5-5", MESSAGES, max_tokens=8_000, ctx=CallContext("chat_labels"))
    assert len(sent) == 1, "a v1 job is not retried"


def test_a_job_with_a_truncation_retry_doubles_its_budget_once(http, rows, anthropic, registered, monkeypatch):
    monkeypatch.setitem(jobs._SPECS, "profile", dataclasses.replace(jobs.job_spec("profile"), truncation_retry=True))
    script, sent = http
    script.extend([FakeResponse(anthropic_body("{", "max_tokens")), FakeResponse(anthropic_body(_json()))])
    assert llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE).summary
    assert [call["payload"]["max_tokens"] for call in sent] == [6_000, 12_000]
    assert [(row["purpose"], row["outcome"]) for row in rows] == [("main", "truncated"), ("truncation_retry", "ok")]


def test_the_retry_budget_is_capped(monkeypatch):
    assert llm_client.retry_budget(2_500, "claude-haiku-4-5-20251001") == 5_000
    assert llm_client.retry_budget(12_000, "claude-haiku-4-5-20251001") == llm_client.TRUNCATION_RETRY_MAX_TOKENS
    assert llm_client.retry_budget(12_000, "gpt-4o-mini") == 16_000  # under its 16,384 limit
    assert llm_client.retry_budget(16_000, "some-unknown-model") == 16_000  # no larger: no retry
    # No catalog row is below 16,000 today; a smaller model limit caps the retry.
    small = dataclasses.replace(models.catalog_model("gpt-4o-mini"), max_output=10_000)
    monkeypatch.setitem(models._BY_ID, "gpt-4o-mini", small)
    assert llm_client.retry_budget(8_000, "gpt-4o-mini") == 10_000


def test_a_call_of_an_unknown_kind_retries_a_cut_off_answer_as_before(http, rows, anthropic):
    script, sent = http
    script.extend([FakeResponse(anthropic_body("part", "max_tokens")), FakeResponse(anthropic_body("# Whole"))])
    text = llm_client.call_llm("key", "claude-haiku-4-5-20251001", MESSAGES, max_tokens=2_500, ctx=CallContext("not_a_kind"))
    assert text == "# Whole"
    assert [call["payload"]["max_tokens"] for call in sent] == [2_500, 5_000]


# ── The daily budget on the v1 path ────────────────────────────────────────


@pytest.fixture
def automatic_profile(monkeypatch):
    """Pretend `profile` runs without a user asking, so the org budget holds it."""
    monkeypatch.setattr(jobs, "automatic_kinds", lambda: frozenset({"profile"}))


@pytest.mark.parametrize("entry", ["generate_json", "generate_text"])
def test_a_v1_call_over_the_daily_budget_is_never_sent(http, rows, anthropic, registered, automatic_profile, monkeypatch, entry):
    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, "1")
    monkeypatch.setattr(db, "llm_spend_since", lambda org, since: 2_000_000)
    _, sent = http
    with pytest.raises(usage.OrgBudgetExceeded, match="daily AI budget"):
        if entry == "generate_json":
            llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
        else:
            llm_client.generate_text("profile", _request(), api_key="key", ctx=PROFILE)
    assert sent == [] and rows == []


def test_a_v1_truncation_retry_is_checked_against_the_budget_again(http, rows, anthropic, registered, automatic_profile, monkeypatch):
    # The main call is under budget; its own cost takes the org over, so the retry is never sent.
    monkeypatch.setitem(jobs._SPECS, "profile", dataclasses.replace(jobs.job_spec("profile"), truncation_retry=True))
    monkeypatch.setattr(db, "llm_spend_since", lambda org, since: sum(row["cost_usd_micros"] or 0 for row in rows))
    monkeypatch.setenv(usage.ORG_DAILY_LLM_BUDGET_ENV, "0.0001")  # 100 micros; one call costs 200
    script, sent = http
    script.extend([FakeResponse(anthropic_body("{", "max_tokens")), FakeResponse(anthropic_body(_json()))])
    with pytest.raises(usage.OrgBudgetExceeded, match="daily AI budget"):
        llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
    assert len(sent) == 1
    assert [(row["purpose"], row["outcome"], row["cost_usd_micros"]) for row in rows] == [("main", "truncated", 200)]


# ── Answers that can't be used ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "answer",
    [
        _json(kind="hybrid"),  # outside the enum
        _json(evidence=[]),  # a bound the schema leaves out but Pydantic checks
        json.dumps({key: value for key, value in VALID.items() if key != "best"}),  # missing field
        f"Here is the reading: {CONFIDENTIAL}",  # not JSON
        "",
    ],
    ids=["enum", "bound", "missing", "not-json", "empty"],
)
def test_an_answer_that_fails_its_schema_raises_and_names_no_content(http, rows, anthropic, registered, caplog, answer):
    script, _ = http
    script.append(FakeResponse(anthropic_body(answer)))
    with caplog.at_level(logging.INFO):
        with pytest.raises(llm_client.LLMInvalidOutput, match="did not match its format") as invalid:
            llm_client.generate_json("profile", Reading, _request(), api_key=SECRET, ctx=PROFILE)
    assert str(invalid.value).endswith("Nothing was saved.")
    assert _outcomes(rows) == [("main", "invalid_output", "end_turn", None)]
    for leaked in (CONFIDENTIAL, "hybrid", "Here is", SECRET):
        assert leaked not in str(invalid.value) and leaked not in caplog.text and leaked not in repr(rows)


@pytest.mark.parametrize("stop", ["pause_turn", "tool_use", None])
def test_an_unexpected_stop_is_not_an_answer(http, rows, anthropic, registered, stop):
    script, sent = http
    script.append(FakeResponse(anthropic_body(_json(), stop)))
    with pytest.raises(llm_client.LLMInvalidOutput, match="stopped unexpectedly"):
        llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
    assert len(sent) == 1 and [row["outcome"] for row in rows] == ["other_stop"]


def test_generate_text_returns_text_and_refuses_an_empty_answer(http, rows, anthropic):
    script, sent = http
    ctx = CallContext("verify_meaning", "sess-1", "org-a")
    script.append(FakeResponse(anthropic_body("  Holds.  ")))
    assert llm_client.generate_text("verify_meaning", _request("verify_meaning"), api_key="key", ctx=ctx) == "Holds."
    # No schema for a text answer: the output config holds the job's effort alone.
    assert sent[0]["payload"]["output_config"] == {"effort": "low"} and "temperature" not in sent[0]["payload"]
    script.append(FakeResponse(anthropic_body("")))
    with pytest.raises(llm_client.LLMInvalidOutput, match="was empty"):
        llm_client.generate_text("verify_meaning", _request("verify_meaning"), api_key="key", ctx=ctx)
    assert [row["outcome"] for row in rows] == ["ok", "invalid_output"]


def test_the_error_classes():
    for error in (llm_client.LLMRefused, llm_client.LLMInvalidOutput, llm_client.LLMOutputTruncated):
        assert issubclass(error, llm_client.LLMError) and issubclass(error, RuntimeError)
    assert llm_client.LLMRefused("bio").category == "bio"
    assert llm_client.LLMRefused("not a tag!").category is None
    assert "bio" not in str(llm_client.LLMRefused("bio")), "the category is never in the message"


def test_only_the_classic_outputs_retry_a_cut_off_answer():
    for spec in jobs.JOB_SPECS:
        assert spec.truncation_retry is (spec.group == "legacy"), spec.kind


def test_a_paid_answer_that_cant_be_read_is_still_recorded(http, rows, anthropic, registered, monkeypatch):
    def broken(text, schema):
        raise RecursionError("bug")

    monkeypatch.setattr(llm_client, "_read_answer", broken)
    script, _ = http
    script.append(FakeResponse(anthropic_body(_json())))
    with pytest.raises(llm_client.LLMInvalidOutput, match=r"could not be read \(RecursionError\)"):
        llm_client.generate_json("profile", Reading, _request(), api_key="key", ctx=PROFILE)
    assert [row["outcome"] for row in rows] == ["invalid_output"]
