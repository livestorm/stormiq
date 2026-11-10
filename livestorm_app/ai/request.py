"""A v1 job's request: what it says, and how each provider is sent it.

`build_request(kind, …)` gives one `LLMRequest` for a job kind; the job's
spec (`ai/jobs.py`) says which layout it uses. `llm_client.generate_text`
and `generate_json` take that request, and `anthropic_call` and
`openai_payload` write it for the provider. The classic outputs don't come
through here: `llm_client.call_llm` keeps sending their own messages.

The three layouts:

- `dossier` (the understanding passes). The session dossier comes first,
  then the job's instructions. The passes of one run share the dossier, so
  it is the prefix worth caching; anything job-specific in front of it
  would stop that. The dossier also holds what attendees wrote in the chat,
  so the instructions are sent on the operator's channel, a `role: "system"`
  message after it, where the model accepts one: text inside a user turn
  can be forged by whoever wrote the chat, a system message can't. On a
  model without that channel they follow the dossier in the same user turn.
  `system` is the frozen text every dossier job shares, byte for byte.
- `instructions` (the profile, the chat labels, the meaning check). The
  reverse: the instructions are the stable part, sent as the system prompt,
  and the call's own data (`payload`: one chat batch, say) is the user turn.
- `legacy`: a classic output's own `messages`, system entries joined into
  the system prompt, as `call_llm` sends them.

Caching. A request marks its stable prefix (`cache_control`) only when the
job's spec allows it (`JobSpec.cache`) and the run makes at least two calls
over that prefix (`shared_calls`): with a single call, a cache write is a
surcharge that nothing reads back. A prefix shorter than the model's
minimum (`ModelCaps.min_cache_tokens`) is not cached by the API, without an
error. `call_number` says which call of the run this is: from the second
on, `llm_client` warns when a call read nothing from the cache.

What is sent with every layout comes from the catalog row of the model
(`ai/models.py`), never from the shape of its id: an effort only where the
model takes one (`ai.jobs.effort_for`), adaptive thinking stated where the
model thinks by default (never `disabled`, never a token budget), the
server-side fallback for a declined request only where the model accepts
it, and never a `temperature`.

Pure: no database, network or model call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from livestorm_app.ai.dossier import Dossier, stable_dumps
from livestorm_app.ai.jobs import JobSpec, effort_for, job_spec
from livestorm_app.ai.models import ModelCaps

# The beta that accepts `fallbacks: "default"` (the list form takes another one).
SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_ROLES = ("system", "user", "assistant")


@dataclass(frozen=True)
class JsonFormat:
    """A strict JSON schema the answer must follow (`ai.schemas.strict_json_schema`)."""

    name: str  # the schema's registered name; OpenAI requires one
    schema: Dict[str, Any]


@dataclass(frozen=True)
class LLMRequest:
    """One call of a v1 job. Build it with `build_request`, which checks it
    against the job's layout."""

    spec: JobSpec
    system: str = ""  # frozen text shared by every job of the layout
    instructions: str = ""  # the job's (or pass's) own, from prompts/*.txt
    context: Union[Dossier, str, None] = None  # the dossier (`dossier` layout only)
    payload: Optional[str] = None  # the call's own data: a chat batch, a note
    messages: Tuple[Mapping[str, str], ...] = ()  # `legacy` layout only
    shared_calls: int = 1  # how many calls of the run share this prefix
    call_number: int = 1  # which of them this is, from 1

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def cached(self) -> bool:
        """Whether the request marks its stable prefix for the prompt cache."""
        return self.spec.cache and self.spec.layout != "legacy" and self.shared_calls >= 2

    @property
    def expects_cache_read(self) -> bool:
        """Whether an earlier call of the run should already have written the prefix."""
        return self.cached and self.call_number >= 2

    @property
    def context_text(self) -> str:
        return self.context.text if isinstance(self.context, Dossier) else (self.context or "")

    @property
    def dossier_hash(self) -> Optional[str]:
        return self.context.sha256 if isinstance(self.context, Dossier) else None

    def prompt_hash(self, json_format: Optional[JsonFormat] = None) -> Optional[str]:
        """A hash of the request's fixed parts: the layout, the system text,
        the instructions and the answer's schema. The same for every session
        and every call of a job until a prompt or a schema changes, so usage
        rows can be compared across a prompt change. None for the `legacy`
        layout, whose messages mix the prompt with the session's data."""
        if self.spec.layout == "legacy":
            return None
        fixed = {
            "layout": self.spec.layout,
            "system": self.system,
            "instructions": self.instructions,
            "schema": json_format.schema if json_format is not None else None,
        }
        return hashlib.sha256(stable_dumps(fixed).encode("utf-8")).hexdigest()


def _text_or_empty(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"A request's {name} is text.")
    return value


def _legacy_messages(messages: Any) -> Tuple[Dict[str, str], ...]:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)) or not messages:
        raise ValueError("A request in the legacy layout takes a non-empty list of messages.")
    checked = []
    for message in messages:
        role = message.get("role") if isinstance(message, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if role not in _ROLES or not isinstance(content, str):
            raise ValueError("Each message needs a role (system, user or assistant) and text content.")
        checked.append({"role": role, "content": content})
    return tuple(checked)


def build_request(
    kind: str,
    *,
    system: str = "",
    instructions: str = "",
    context: Union[Dossier, str, None] = None,
    payload: Optional[str] = None,
    messages: Optional[Sequence[Mapping[str, str]]] = None,
    shared_calls: int = 1,
    call_number: int = 1,
) -> LLMRequest:
    """The request of one call of job `kind`, checked against the job's layout
    (`JobSpec.layout`). Raises ValueError for parts the layout doesn't take
    or lacks, before any paid call:

    - `dossier`: `context` (a `Dossier`, or text) and `instructions`;
      `system` and `payload` are optional;
    - `instructions`: `instructions` and `payload`; `system` is optional;
    - `legacy`: `messages` only.

    `shared_calls` is how many calls of this run send the same stable prefix
    (the dossier, or the instructions), and `call_number` which one this is.
    """
    spec = job_spec(kind)
    system = _text_or_empty(system, "system text")
    instructions = _text_or_empty(instructions, "instructions")
    payload = _text_or_empty(payload, "payload") or None
    if isinstance(shared_calls, bool) or isinstance(call_number, bool) or not isinstance(shared_calls, int) or not isinstance(call_number, int):
        raise ValueError("shared_calls and call_number are whole numbers.")
    if shared_calls < 1 or not 1 <= call_number <= shared_calls:
        raise ValueError("call_number runs from 1 to shared_calls.")

    if spec.layout == "legacy":
        if system or instructions or context is not None or payload:
            raise ValueError(f"{spec.label} uses the legacy layout: it takes messages only.")
        return LLMRequest(spec, messages=_legacy_messages(messages), shared_calls=shared_calls, call_number=call_number)
    if messages is not None:
        raise ValueError(f"{spec.label} uses the {spec.layout} layout: it takes no messages.")
    if not instructions.strip():
        raise ValueError(f"{spec.label} needs its instructions.")

    if spec.layout == "dossier":
        if not isinstance(context, (Dossier, str)) or not (context.text if isinstance(context, Dossier) else context).strip():
            raise ValueError(f"{spec.label} uses the dossier layout: it needs the dossier as its context.")
    else:
        if context is not None:
            raise ValueError(f"{spec.label} uses the instructions layout: its data goes in the payload, not a context.")
        if not payload or not payload.strip():
            raise ValueError(f"{spec.label} needs a payload: the data this call is about.")
    return LLMRequest(
        spec, system=system, instructions=instructions, context=context, payload=payload,
        shared_calls=shared_calls, call_number=call_number,
    )


# ── What each provider is sent ─────────────────────────────────────────────


@dataclass(frozen=True)
class AnthropicCall:
    """One Messages API call: the SDK's keyword arguments, the betas they
    need, and the effort they send (None when the model takes none)."""

    params: Dict[str, Any]
    betas: Tuple[str, ...] = ()
    effort: Optional[str] = None


def _text_block(text: str, cache_control: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    block: Dict[str, Any] = {"type": "text", "text": text}
    if cache_control is not None:
        block["cache_control"] = dict(cache_control)
    return block


def _cache_control(request: LLMRequest) -> Optional[Dict[str, str]]:
    if not request.cached:
        return None
    return {"type": "ephemeral", "ttl": "1h"} if request.spec.cache_ttl == "1h" else {"type": "ephemeral"}


def anthropic_call(
    request: LLMRequest, caps: ModelCaps, max_tokens: int, json_format: Optional[JsonFormat] = None
) -> AnthropicCall:
    """`request` as a Messages API call to `caps`' model.

    The stable prefix carries the request's one `cache_control` marker: the
    dossier block, or the instructions block. Thinking is stated as adaptive
    where the model thinks by default, and left out otherwise: it is never
    turned on for a model whose budget has no room for it, never `disabled`
    and never given a token budget. No `temperature` is ever sent.
    """
    spec = request.spec
    params: Dict[str, Any] = {"model": caps.id, "max_tokens": int(max_tokens)}
    marker = _cache_control(request)
    if spec.layout == "legacy":
        system_parts = [message["content"] for message in request.messages if message["role"] == "system"]
        if system_parts:
            params["system"] = "\n\n".join(system_parts)
        params["messages"] = [dict(message) for message in request.messages if message["role"] != "system"]
    elif spec.layout == "dossier":
        if request.system:
            params["system"] = [_text_block(request.system)]
        blocks: List[Dict[str, Any]] = [_text_block(request.context_text, marker)]
        if request.payload:
            blocks.append(_text_block(request.payload))
        if caps.mid_conversation_system:
            # The operator's channel, after the cached prefix. It must be the last message.
            params["messages"] = [{"role": "user", "content": blocks}, {"role": "system", "content": request.instructions}]
        else:
            params["messages"] = [{"role": "user", "content": blocks + [_text_block(request.instructions)]}]
    else:
        system_blocks = [_text_block(request.system)] if request.system else []
        params["system"] = system_blocks + [_text_block(request.instructions, marker)]
        params["messages"] = [{"role": "user", "content": request.payload}]

    if caps.thinks and caps.adaptive_thinking:
        params["thinking"] = {"type": "adaptive"}
    output_config: Dict[str, Any] = {}
    effort = effort_for(spec, caps)
    if effort is not None:
        output_config["effort"] = effort
    if json_format is not None:
        output_config["format"] = {"type": "json_schema", "schema": json_format.schema}
    if output_config:
        params["output_config"] = output_config
    betas: Tuple[str, ...] = ()
    if spec.fallbacks and caps.server_fallback:
        params["fallbacks"] = "default"
        betas = (SERVER_FALLBACK_BETA,)
    return AnthropicCall(params=params, betas=betas, effort=effort)


def openai_payload(
    request: LLMRequest, caps: ModelCaps, max_tokens: int, json_format: Optional[JsonFormat] = None
) -> Dict[str, Any]:
    """`request` as a Chat Completions body for `caps`' model. The same order
    as for Anthropic, so OpenAI's automatic prefix cache sees the same stable
    prefix: the dossier first with the instructions after it as a `system`
    message, or the instructions first. The token parameter comes from the
    model's row. No `temperature` and no effort are sent."""
    spec = request.spec
    if spec.layout == "legacy":
        messages: List[Dict[str, str]] = [dict(message) for message in request.messages]
    elif spec.layout == "dossier":
        messages = [{"role": "system", "content": request.system}] if request.system else []
        messages.append({"role": "user", "content": request.context_text})
        if request.payload:
            messages.append({"role": "user", "content": request.payload})
        if caps.mid_conversation_system:
            messages.append({"role": "system", "content": request.instructions})
        else:
            messages.append({"role": "user", "content": request.instructions})
    else:
        messages = [{"role": "system", "content": request.system}] if request.system else []
        messages.append({"role": "system", "content": request.instructions})
        messages.append({"role": "user", "content": request.payload or ""})
    payload: Dict[str, Any] = {"model": caps.id, "messages": messages, caps.token_param: int(max_tokens)}
    if json_format is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": json_format.name, "strict": True, "schema": json_format.schema},
        }
    return payload
