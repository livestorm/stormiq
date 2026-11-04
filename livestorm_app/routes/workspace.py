"""Organization-level lists: cached session cards, Livestorm events, an event's sessions."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from livestorm_app.api_logic import (
    fetch_available_events,
    fetch_event_sessions,
    list_workspace_sessions_data,
)
from livestorm_app.auth import (
    Caller,
    get_caller,
    get_optional_caller,
)
from livestorm_app.routes.common import raise_http_error
from livestorm_app.routes.models import EventSessionsRequest, WorkspaceEventsRequest

router = APIRouter()


@router.get("/api/workspace-sessions")
def workspace_sessions(caller: Optional[Caller] = Depends(get_optional_caller)) -> Dict[str, Any]:
    """List every cached session the calling user's org can see.

    Powers the Single Analysis card grid. Always lists the caller's *own*
    org, admins included. Returns `{ sessions: [] }` when:
      - the caller has no OAuth connection (anonymous, or local dev without auto-login)
      - the org has no cached sessions yet
    The frontend treats empty list as the empty-state for the page.
    """
    org_id = caller.org_id if caller else ""
    if not org_id:
        return {"sessions": []}
    return list_workspace_sessions_data(org_id)


@router.post("/api/event-sessions")
def event_sessions(request: EventSessionsRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        api_key = caller.livestorm_auth(request.api_key)
        return fetch_event_sessions(api_key, request.event_id)
    except Exception as exc:
        raise_http_error("Event sessions", exc)
        raise


@router.post("/api/workspace-events")
def workspace_events(request: WorkspaceEventsRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        api_key = caller.livestorm_auth(request.api_key)
        return fetch_available_events(
            api_key,
            page_number=request.page_number,
            page_size=request.page_size,
            title=request.title,
            scheduling_status=request.scheduling_status,
        )
    except Exception as exc:
        raise_http_error("Workspace events", exc)
        raise
