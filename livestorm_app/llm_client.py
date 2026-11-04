"""Provider-agnostic LLM client for text generation.

Active provider is selected via the LLM_PROVIDER environment variable:
  "anthropic" (default) — Anthropic Messages API, reads CLAUDE_API_KEY
  "openai"              — OpenAI Chat Completions API, reads OPENAI_API_KEY

Cover image generation always uses the OpenAI Images API regardless of
LLM_PROVIDER — that logic lives in services.py and is untouched here.

Switching providers: set LLM_PROVIDER and the matching API-key env var,
then redeploy. No code changes required.
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Any, Dict, List, Tuple

import requests


logger = logging.getLogger(__name__)


def get_provider() -> str:
    """Return the active LLM provider.

    DB setting 'llm_provider' takes precedence; falls back to the
    LLM_PROVIDER env var, then to "anthropic".
    """
    try:
        from livestorm_app.db import get_setting
        db_val = get_setting("llm_provider")
        if db_val and db_val.strip().lower() in {"anthropic", "openai"}:
            return db_val.strip().lower()
    except Exception:
        pass
    return (os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()


def get_llm_key() -> str:
    """Return the API key for the active LLM provider.

    Raises RuntimeError if the required env var is missing so callers
    get a clear error message at job start rather than at HTTP call time.
    """
    provider = get_provider()
    if provider == "anthropic":
        key = (os.getenv("CLAUDE_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("CLAUDE_API_KEY not configured on worker process.")
        return key
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not configured on worker process.")
    return key


def get_llm_model(role: str) -> str:
    """Return the model name for the active provider and generation role.

    Roles:
      "default"     — overall analysis, deep analysis, content repurposing,
                      translation (maps to the cheaper/faster model)
      "smart_recap" — Smart Recap only (intentionally uses a stronger model)

    DB settings take precedence over config constants:
      llm_default_model      — model for "default" and "deep" roles
      llm_smart_recap_model  — model for "smart_recap" role
    """
    from livestorm_app.config import (
        DEFAULT_CLAUDE_MODEL,
        DEFAULT_OPENAI_MODEL,
        SMART_RECAP_CLAUDE_MODEL,
        SMART_RECAP_OPENAI_MODEL,
    )
    from livestorm_app.db import get_setting

    provider = get_provider()
    is_recap = role == "smart_recap"
    setting_key = "llm_smart_recap_model" if is_recap else "llm_default_model"

    try:
        db_model = get_setting(setting_key)
        if db_model and db_model.strip():
            return db_model.strip()
    except Exception:
        pass

    # Config-constant fallback (env-var era defaults)
    if provider == "anthropic":
        return SMART_RECAP_CLAUDE_MODEL if is_recap else DEFAULT_CLAUDE_MODEL
    return SMART_RECAP_OPENAI_MODEL if is_recap else DEFAULT_OPENAI_MODEL


class LLMOutputTruncated(RuntimeError):
    """The model stopped at its token limit, so the output is incomplete."""


# Ceiling for the automatic retry after truncation. Stays within the output
# limits of every configured model (gpt-4o-mini caps at ~16k tokens).
TRUNCATION_RETRY_MAX_TOKENS = 16000


def call_llm(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 2500,
) -> str:
    """Call the active LLM provider and return the generated text.

    `messages` may contain role="system" entries — the Anthropic adapter
    extracts them into the top-level `system` parameter automatically.

    If the model stops at `max_tokens`, the call is retried once with a
    larger budget; if the answer is still cut off, LLMOutputTruncated is
    raised so a partial result is never saved as if it were complete.
    """
    call = _call_anthropic if get_provider() == "anthropic" else _call_openai
    text, truncated = call(api_key, model, messages, temperature, max_tokens)
    if not truncated:
        return text
    larger_budget = min(max_tokens * 2, TRUNCATION_RETRY_MAX_TOKENS)
    if larger_budget > max_tokens:
        logger.warning("LLM output hit max_tokens=%d; retrying once with %d", max_tokens, larger_budget)
        text, truncated = call(api_key, model, messages, temperature, larger_budget)
        if not truncated:
            return text
    raise LLMOutputTruncated(
        f"The AI answer was cut off at its length limit ({max(larger_budget, max_tokens)} tokens), "
        "so it was not saved. Please try again."
    )


# ── Anthropic ─────────────────────────────────────────────────────────────────

def _call_anthropic(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> Tuple[str, bool]:
    """Return (text, truncated). Truncated means stop_reason == "max_tokens"."""
    from livestorm_app.config import ANTHROPIC_API_VERSION, ANTHROPIC_MESSAGES_URL

    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    user_messages = [m for m in messages if m["role"] != "system"]

    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": user_messages,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }

    resp = _post_with_retry(ANTHROPIC_MESSAGES_URL, headers, payload, provider="anthropic")
    body = resp.json()
    truncated = body.get("stop_reason") == "max_tokens"
    text = "".join(
        str(block.get("text") or "")
        for block in body.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    return text.strip(), truncated


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _call_openai(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> Tuple[str, bool]:
    """Return (text, truncated). Truncated means finish_reason == "length"."""
    from livestorm_app.config import OPENAI_CHAT_COMPLETIONS_URL

    token_param = "max_completion_tokens" if str(model or "").startswith("gpt-5") else "max_tokens"
    payload: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        token_param: max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    resp = _post_with_retry(OPENAI_CHAT_COMPLETIONS_URL, headers, payload, provider="openai")
    body = resp.json()
    choices = body.get("choices") or []
    if not choices:
        return "", False
    content = choices[0].get("message", {}).get("content") or ""
    return str(content).strip(), choices[0].get("finish_reason") == "length"


# ── Shared retry helper ────────────────────────────────────────────────────────

# Rate limits, overload (Anthropic 529) and transient server errors are worth
# retrying: the provider did not produce (or bill) an answer.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504, 529})
LLM_MAX_ATTEMPTS = 4
LLM_BACKOFF_BASE_SECONDS = 2.0
LLM_BACKOFF_MAX_SECONDS = 30.0
# (connect, read). Non-streaming calls send nothing until the whole answer is
# generated, so the read timeout must cover a 16k-token completion.
LLM_REQUEST_TIMEOUT = (10, 300)


def _retry_delay(attempt: int, retry_after: str | None = None) -> float:
    """Seconds to wait before retry `attempt` (0-based).

    Honours a numeric `Retry-After` header (capped), otherwise exponential
    backoff with jitter so parallel jobs don't retry in lockstep.
    """
    try:
        hinted = float(retry_after) if retry_after else 0.0
    except (TypeError, ValueError):
        hinted = 0.0
    if hinted > 0:
        return min(hinted, LLM_BACKOFF_MAX_SECONDS)
    backoff = LLM_BACKOFF_BASE_SECONDS * (2 ** attempt)
    return min(backoff + random.uniform(0, backoff / 2), LLM_BACKOFF_MAX_SECONDS)


def _post_with_retry(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    provider: str,
    max_attempts: int = LLM_MAX_ATTEMPTS,
) -> requests.Response:
    """POST to the provider, retrying transient failures.

    Retried: RETRYABLE_STATUS_CODES and connection errors. These almost
    always mean no answer was produced, but not always: a gateway 502/504
    or a connection reset after the request was sent can hide an answer
    that was generated and billed, and the retry then pays once more. That
    rare double charge is accepted over failing the whole job. Not retried:
    a read timeout, where the model is most likely still generating (and
    billing) the answer; and any other 4xx, which would fail again.
    """
    for attempt in range(max_attempts):
        last_attempt = attempt == max_attempts - 1
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=LLM_REQUEST_TIMEOUT)
        except requests.ConnectionError as exc:
            if last_attempt:
                raise
            wait = _retry_delay(attempt)
            logger.warning(
                "%s connection error on attempt %d/%d (%s) — retrying in %.1fs",
                provider.capitalize(), attempt + 1, max_attempts, type(exc).__name__, wait,
            )
            time.sleep(wait)
            continue
        if resp.status_code in RETRYABLE_STATUS_CODES and not last_attempt:
            wait = _retry_delay(attempt, resp.headers.get("Retry-After"))
            logger.warning(
                "%s HTTP %d on attempt %d/%d — retrying in %.1fs",
                provider.capitalize(), resp.status_code, attempt + 1, max_attempts, wait,
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    raise AssertionError("unreachable: the last attempt returns or raises")
