"""Request identity and access scope for every web route.

One place decides who the caller is, which org's cached sessions they can
read, and which Livestorm credentials their upstream calls use. Routes
depend on `get_caller` (or `require_admin`) and never resolve org ids or
admin status by hand.

Caller kinds:
    user       OAuth-connected Livestorm user. Reads only their own org.
    admin      OAuth user listed in ADMIN_EMAILS or `admin_users`. Reads every org.
    local_dev  No OAuth connection, but local-dev mode is on (see below).
               Reads every org and calls Livestorm with LS_API_KEY.

Local-dev mode is enabled only when STORMIQ_LOCAL_DEV is truthy AND the
process is not running on Render (Render sets RENDER=true on every
service). The env flag is what grants access. A request additionally
needs a loopback Host header (localhost / 127.0.0.1 / ::1) to use it:
that does not authenticate anyone, it stops a DNS-rebinding web page from
reaching a developer's local server under its own hostname.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request

from livestorm_app.config import get_runtime_secret
from livestorm_app.db import ALL_ORGS, OrgScope, is_admin_email
from livestorm_app.oauth_client import (
    DEV_CONNECTION_ID,
    LIVESTORM_OAUTH_COOKIE,
    refresh_connection_if_needed,
)


logger = logging.getLogger(__name__)

LOCAL_DEV_ENV_VAR = "STORMIQ_LOCAL_DEV"
_TRUTHY = {"1", "true", "yes", "on"}
_local_dev_on_render_warned = False


def running_on_render() -> bool:
    return bool(str(os.environ.get("RENDER") or "").strip())


def local_dev_enabled() -> bool:
    """True when the operator explicitly enabled local-dev mode off Render."""
    global _local_dev_on_render_warned
    flag = str(get_runtime_secret(LOCAL_DEV_ENV_VAR, "") or "").strip().lower() in _TRUTHY
    if not flag:
        return False
    if running_on_render():
        if not _local_dev_on_render_warned:
            logger.error("%s is set on Render; ignoring it. Local-dev mode is never enabled in production.", LOCAL_DEV_ENV_VAR)
            _local_dev_on_render_warned = True
        return False
    return True


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def local_dev_request(request: Request) -> bool:
    """Local-dev mode applies to this request (flag on, not Render, loopback Host)."""
    if not local_dev_enabled():
        return False
    return str(request.url.hostname or "").strip().lower() in LOOPBACK_HOSTS


def _ls_api_key() -> str:
    return str(get_runtime_secret("LS_API_KEY", "") or "").strip()


def local_api_key(request: Request) -> str:
    """LS_API_KEY, but only for a local-dev request."""
    return _ls_api_key() if local_dev_request(request) else ""


def env_admin_emails() -> set[str]:
    raw = str(get_runtime_secret("ADMIN_EMAILS", "") or "")
    return {email.strip().lower() for email in raw.split(",") if email.strip()}


def _is_admin_connection(connection: Dict[str, Any]) -> bool:
    email = str(connection.get("email") or "").strip().lower()
    if not email:
        return False
    return email in env_admin_emails() or is_admin_email(email)


@dataclass(frozen=True)
class Caller:
    kind: str  # "user" | "admin" | "local_dev"
    org_id: str
    connection: Optional[Dict[str, Any]] = None
    local_dev: bool = False  # request qualified for local-dev mode (see module docstring)

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"

    @property
    def read_scope(self) -> OrgScope:
        """Org scope for reading cached sessions.

        Admins and local dev read every org. A user reads only their own org;
        a user whose connection carries no org id gets "" and sees nothing.
        """
        if self.kind in ("admin", "local_dev"):
            return ALL_ORGS
        return self.org_id

    def livestorm_auth(self, inline_api_key: str = "") -> str:
        """Authorization value for upstream Livestorm API calls."""
        inline = str(inline_api_key or "").strip()
        if inline:
            if not self.local_dev:
                raise HTTPException(
                    status_code=400,
                    detail={"resource": "Livestorm authentication", "message": "Inline API keys are only accepted in local development."},
                )
            return inline
        local_key = _ls_api_key() if self.local_dev else ""
        if local_key:
            return local_key
        connection = self.connection or {}
        access_token = str(connection.get("access_token") or "").strip()
        if not access_token:
            raise HTTPException(
                status_code=401,
                detail={"resource": "Livestorm authentication", "message": "Your Livestorm connection is missing an access token. Please reconnect."},
            )
        token_type = str(connection.get("token_type") or "Bearer").strip() or "Bearer"
        return f"{token_type} {access_token}"

    def generator_kwargs(self) -> Dict[str, str]:
        """`created_by_*` attribution for cache rows; empty without OAuth."""
        connection = self.connection
        if not isinstance(connection, dict):
            return {}
        profile = connection.get("profile")
        profile = profile if isinstance(profile, dict) else {}
        user_id = str(connection.get("user_id") or profile.get("user_id") or "").strip()
        email = str(connection.get("email") or profile.get("email") or "").strip()
        name = str(profile.get("full_name") or "").strip() or email
        out: Dict[str, str] = {}
        if user_id:
            out["created_by_user_id"] = user_id
        if email:
            out["created_by_email"] = email
        if name:
            out["created_by_name"] = name
        return out


def _connection_cookie(request: Request) -> str:
    return str(request.cookies.get(LIVESTORM_OAUTH_COOKIE) or "").strip()


def resolve_connection(request: Request) -> Optional[Dict[str, Any]]:
    """Load (and refresh if needed) the caller's OAuth connection, or None."""
    connection_id = _connection_cookie(request)
    if not connection_id:
        return None
    if connection_id == DEV_CONNECTION_ID and not local_dev_request(request):
        # The dev auto-login row uses a fixed, guessable id. Outside local
        # dev it must never authenticate anyone.
        return None
    try:
        connection = refresh_connection_if_needed(connection_id)
    except Exception:
        logger.warning("OAuth connection refresh failed; treating the caller as signed out.", exc_info=True)
        return None
    return connection if isinstance(connection, dict) else None


def resolve_caller(request: Request) -> Optional[Caller]:
    """Resolve the caller once per request (cached on request.state)."""
    if getattr(request.state, "caller_resolved", False):
        return request.state.caller
    connection = resolve_connection(request)
    local_dev = local_dev_request(request)
    caller: Optional[Caller] = None
    if connection is not None:
        caller = Caller(
            kind="admin" if _is_admin_connection(connection) else "user",
            org_id=str(connection.get("organization_id") or "").strip(),
            connection=connection,
            local_dev=local_dev,
        )
    elif local_dev:
        caller = Caller(kind="local_dev", org_id="", local_dev=True)
    request.state.caller = caller
    request.state.caller_resolved = True
    return caller


def get_caller(request: Request) -> Caller:
    """FastAPI dependency: the authenticated caller, or 401."""
    caller = resolve_caller(request)
    if caller is not None:
        return caller
    if _connection_cookie(request):
        message = "Your Livestorm connection has expired. Please reconnect."
    else:
        message = "Please connect with Livestorm first."
    raise HTTPException(status_code=401, detail={"resource": "Livestorm authentication", "message": message})


def get_optional_caller(request: Request) -> Optional[Caller]:
    """FastAPI dependency for routes that also serve anonymous visitors."""
    return resolve_caller(request)


def require_admin(caller: Caller = Depends(get_caller)) -> Caller:
    """FastAPI dependency: an admin caller, or 403."""
    if not caller.is_admin:
        raise HTTPException(status_code=403, detail={"resource": "Admin", "message": "Admin access required."})
    return caller
