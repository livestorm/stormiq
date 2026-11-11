"""Health check and the frontend's bootstrap state."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request

from livestorm_app.auth import (
    Caller,
    get_optional_caller,
    local_api_key,
)
from livestorm_app.db import fetch_surprise_enabled
from livestorm_app.oauth_client import (
    get_connection_identity,
    oauth_enabled,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/health")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


def _surprise_enabled(caller: Optional[Caller]) -> bool:
    """Whether Follow-up shows Surprise me for the caller's organization.
    Fails closed: a failed read hides it (the job start checks again)."""
    if caller is None:
        return False
    try:
        return fetch_surprise_enabled(caller.org_id)
    except Exception:
        logger.exception("Org settings read failed")
        return False


@router.get("/api/bootstrap")
def bootstrap_defaults(request: Request, caller: Optional[Caller] = Depends(get_optional_caller)) -> Dict[str, Any]:
    # The raw LS_API_KEY is never sent to the browser. In local-dev mode the
    # server applies it itself (see livestorm_app.auth).
    return {
        "defaults": {"apiKey": ""},
        "org": {"surpriseEnabled": _surprise_enabled(caller)},
        "auth": {
            "oauthEnabled": oauth_enabled(),
            "connectedUser": get_connection_identity(caller.connection if caller else None),
            "allowLocalApiKeyFallback": bool(local_api_key(request)),
            "isAdmin": bool(caller and caller.is_admin),
        },
    }
