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
import time
from typing import Any, Dict, List

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
    """
    provider = get_provider()
    if provider == "anthropic":
        return _call_anthropic(api_key, model, messages, temperature, max_tokens)
    return _call_openai(api_key, model, messages, temperature, max_tokens)


# ── Anthropic ─────────────────────────────────────────────────────────────────

def _call_anthropic(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> str:
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
    for block in body.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block["text"]).strip()
    return ""


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _call_openai(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> str:
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
    if choices:
        content = choices[0].get("message", {}).get("content") or ""
        return str(content).strip()
    return ""


# ── Shared retry helper ────────────────────────────────────────────────────────

def _post_with_retry(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    provider: str,
    max_attempts: int = 3,
) -> requests.Response:
    resp: requests.Response | None = None
    for attempt in range(max_attempts):
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code == 429 and attempt < max_attempts - 1:
            try:
                wait = min(float(resp.headers.get("Retry-After") or 0), 30.0) or (10.0 * (attempt + 1))
            except (TypeError, ValueError):
                wait = 10.0 * (attempt + 1)
            logger.warning(
                "%s 429 on attempt %d/%d — waiting %.0fs",
                provider.capitalize(),
                attempt + 1,
                max_attempts,
                wait,
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    assert resp is not None
    resp.raise_for_status()
    return resp
