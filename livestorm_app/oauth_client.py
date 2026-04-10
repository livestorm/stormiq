from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

from livestorm_app.config import (
    LIVESTORM_OAUTH_AUTHORIZE_URL,
    LIVESTORM_OAUTH_DEFAULT_SCOPES,
    LIVESTORM_OAUTH_ME_URL,
    LIVESTORM_OAUTH_TOKEN_URL,
    get_runtime_secret,
)
from livestorm_app.db import (
    delete_oauth_connection,
    fetch_oauth_connection,
    upsert_oauth_connection,
    update_oauth_connection_tokens,
)


LIVESTORM_OAUTH_COOKIE = "livestorm_oauth_connection"
LIVESTORM_OAUTH_HANDSHAKE_COOKIE = "livestorm_oauth_handshake"


def oauth_enabled() -> bool:
    return bool(get_oauth_client_id() and get_oauth_client_secret() and get_oauth_redirect_uri())


def get_oauth_client_id() -> str:
    return str(get_runtime_secret("LIVESTORM_OAUTH_CLIENT_ID", "")).strip()


def get_oauth_client_secret() -> str:
    return str(get_runtime_secret("LIVESTORM_OAUTH_CLIENT_SECRET", "")).strip()


def get_oauth_redirect_uri() -> str:
    return str(get_runtime_secret("LIVESTORM_OAUTH_REDIRECT_URI", "")).strip()


def get_oauth_scopes() -> str:
    return str(get_runtime_secret("LIVESTORM_OAUTH_SCOPES", LIVESTORM_OAUTH_DEFAULT_SCOPES)).strip()


def get_frontend_app_url() -> str:
    return str(get_runtime_secret("FRONTEND_APP_URL", "")).strip()


def get_session_secret() -> str:
    return (
        str(get_runtime_secret("SESSION_SECRET", "")).strip()
        or get_oauth_client_secret()
        or "dev-insecure-session-secret"
    )


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def _sign_payload(data: Dict[str, Any]) -> str:
    payload_bytes = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload = _b64encode(payload_bytes)
    signature = hmac.new(get_session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload}.{_b64encode(signature)}"


def _unsign_payload(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64encode(
        hmac.new(get_session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        return json.loads(_b64decode(payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None


def build_authorization_url(return_to: str = "/") -> Dict[str, str]:
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64encode(hashlib.sha256(verifier.encode("utf-8")).digest())
    handshake_token = _sign_payload(
        {
            "state": state,
            "verifier": verifier,
            "return_to": return_to or "/",
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        }
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": get_oauth_client_id(),
            "redirect_uri": get_oauth_redirect_uri(),
            "scope": get_oauth_scopes(),
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    return {
        "url": f"{LIVESTORM_OAUTH_AUTHORIZE_URL}?{query}",
        "handshake_token": handshake_token,
    }


def validate_handshake(handshake_token: str, state: str) -> Dict[str, Any]:
    payload = _unsign_payload(handshake_token)
    if not isinstance(payload, dict):
        raise RuntimeError("OAuth handshake cookie is invalid or expired.")
    if str(payload.get("state") or "").strip() != str(state or "").strip():
        raise RuntimeError("OAuth state mismatch. Please try connecting again.")
    verifier = str(payload.get("verifier") or "").strip()
    if not verifier:
        raise RuntimeError("OAuth PKCE verifier is missing.")
    return payload


def exchange_authorization_code(code: str, verifier: str) -> Dict[str, Any]:
    response = requests.post(
        LIVESTORM_OAUTH_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": get_oauth_client_id(),
            "client_secret": get_oauth_client_secret(),
            "code": code,
            "redirect_uri": get_oauth_redirect_uri(),
            "code_verifier": verifier,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    response = requests.post(
        LIVESTORM_OAUTH_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": get_oauth_client_id(),
            "client_secret": get_oauth_client_secret(),
            "refresh_token": refresh_token,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def fetch_me(access_token: str) -> Dict[str, Any]:
    response = requests.get(
        LIVESTORM_OAUTH_ME_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "accept": "application/vnd.api+json",
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def _extract_profile(me_payload: Dict[str, Any]) -> Dict[str, Any]:
    data = me_payload.get("data")
    data = data if isinstance(data, dict) else {}
    attributes = data.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    organization = attributes.get("organization")
    organization = organization if isinstance(organization, dict) else {}
    resource_type = str(data.get("type") or "").strip()
    first_name = str(attributes.get("first_name") or "").strip()
    last_name = str(attributes.get("last_name") or "").strip()
    full_name = f"{first_name} {last_name}".strip()
    organization_id = str(organization.get("id") or "").strip()
    organization_name = str(organization.get("name") or "").strip()

    if resource_type == "organizations":
        organization_id = str(data.get("id") or "").strip()
        organization_name = str(attributes.get("name") or organization_name).strip()
        if not full_name:
            full_name = organization_name

    return {
        "user_id": str(data.get("id") or "").strip(),
        "email": str(attributes.get("email") or "").strip(),
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "organization_id": organization_id,
        "organization_name": organization_name,
        "resource_type": resource_type,
        "raw": me_payload,
    }


def _build_expires_at(token_payload: Dict[str, Any]) -> Optional[str]:
    try:
        expires_in = int(token_payload.get("expires_in") or 0)
    except (TypeError, ValueError):
        return None
    if expires_in <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()


def persist_oauth_connection(token_payload: Dict[str, Any], me_payload: Dict[str, Any]) -> Dict[str, Any]:
    profile = _extract_profile(me_payload)
    connection_id = secrets.token_urlsafe(24)
    upsert_oauth_connection(
        connection_id=connection_id,
        provider="livestorm",
        user_id=profile["user_id"],
        email=profile["email"],
        organization_id=profile["organization_id"],
        access_token=str(token_payload.get("access_token") or "").strip(),
        refresh_token=str(token_payload.get("refresh_token") or "").strip(),
        token_type=str(token_payload.get("token_type") or "Bearer").strip() or "Bearer",
        scope=str(token_payload.get("scope") or "").strip(),
        expires_at=_build_expires_at(token_payload),
        profile=profile,
    )
    connection = fetch_oauth_connection(connection_id)
    if not isinstance(connection, dict):
        raise RuntimeError("OAuth connection could not be saved.")
    return connection


def delete_connection(connection_id: str) -> None:
    delete_oauth_connection(connection_id)


def _token_is_stale(connection: Dict[str, Any]) -> bool:
    raw = str(connection.get("expires_at") or "").strip()
    if not raw:
        return False
    try:
        expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    return expires_at <= (datetime.now(timezone.utc) + timedelta(minutes=5))


def refresh_connection_if_needed(connection_id: str) -> Optional[Dict[str, Any]]:
    connection = fetch_oauth_connection(connection_id)
    if not isinstance(connection, dict):
        return None
    if not _token_is_stale(connection):
        return connection
    refresh_token_value = str(connection.get("refresh_token") or "").strip()
    if not refresh_token_value:
        return connection
    token_payload = refresh_access_token(refresh_token_value)
    update_oauth_connection_tokens(
        connection_id=connection_id,
        access_token=str(token_payload.get("access_token") or "").strip(),
        refresh_token=str(token_payload.get("refresh_token") or refresh_token_value).strip(),
        token_type=str(token_payload.get("token_type") or "Bearer").strip() or "Bearer",
        scope=str(token_payload.get("scope") or connection.get("scope") or "").strip(),
        expires_at=_build_expires_at(token_payload),
    )
    return fetch_oauth_connection(connection_id)


def get_connection_identity(connection: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(connection, dict):
        return None
    profile = connection.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    return {
        "connected": True,
        "email": str(connection.get("email") or profile.get("email") or "").strip(),
        "fullName": str(profile.get("full_name") or "").strip(),
        "organizationName": str(profile.get("organization_name") or "").strip(),
        "resourceType": str(profile.get("resource_type") or "").strip(),
        "userId": str(connection.get("user_id") or profile.get("user_id") or "").strip(),
    }
