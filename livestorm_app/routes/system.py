"""Health check and the frontend's bootstrap state."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request

from livestorm_app.auth import (
    Caller,
    get_optional_caller,
    local_api_key,
)
from livestorm_app.oauth_client import (
    get_connection_identity,
    oauth_enabled,
)

router = APIRouter()


@router.get("/api/health")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@router.get("/api/bootstrap")
def bootstrap_defaults(request: Request, caller: Optional[Caller] = Depends(get_optional_caller)) -> Dict[str, Any]:
    # The raw LS_API_KEY is never sent to the browser. In local-dev mode the
    # server applies it itself (see livestorm_app.auth).
    return {
        "defaults": {"apiKey": ""},
        "auth": {
            "oauthEnabled": oauth_enabled(),
            "connectedUser": get_connection_identity(caller.connection if caller else None),
            "allowLocalApiKeyFallback": bool(local_api_key(request)),
            "isAdmin": bool(caller and caller.is_admin),
        },
    }
