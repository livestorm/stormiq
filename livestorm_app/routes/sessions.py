"""One session: cover image, cached workspace, fetches, the transcript job and download, speaker labels."""

from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from livestorm_app.api_logic import (
    fetch_all_session_data,
    fetch_session_base_data,
    fetch_session_transcript_data,
    get_cached_workspace,
    get_transcript_download,
    get_transcript_job_status_data,
    save_speaker_labels,
)
from livestorm_app.auth import (
    Caller,
    get_caller,
)
from livestorm_app.db import (
    fetch_cover_image,
)
from livestorm_app.routes.common import SessionId, logger, raise_http_error
from livestorm_app.routes.models import FetchSessionRequest, SpeakerLabelsRequest

router = APIRouter()


@router.get("/api/sessions/{session_id}/cover.png")
def get_session_cover_image(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Response:
    """Serve the OpenAI-generated cover image for the workspace card grid.

    BYTEA columns stay out of `/api/workspace-sessions` so that endpoint
    is small (no 50× 2 MB payloads). The card looks up `hasCoverImage`
    there and renders `<img src=".../cover.png">` only when true.

    Org-scoped like every session route. Returns 404 with a small JSON body
    when the row has no cover yet or isn't visible to the caller (the
    frontend falls back to the placeholder gradient).
    """
    try:
        cover = fetch_cover_image(session_id, organization_id=caller.read_scope)
        if not isinstance(cover, dict) or not cover.get("bytes"):
            return JSONResponse({"message": "Cover image not available."}, status_code=404)
        # Browser-only cache (`private`): the image is org-scoped, so shared
        # caches must not store it. The bytes don't change until the worker
        # rewrites the row; after a regeneration the user can hard-refresh.
        headers = {
            "Cache-Control": "private, max-age=86400",
            "Content-Type": cover.get("mime", "image/png"),
        }
        return Response(content=cover["bytes"], media_type=headers["Content-Type"], headers=headers)
    except Exception:
        logger.exception("Cover image route failed for session_id=%s", session_id)
        return JSONResponse({"message": "Cover image lookup failed."}, status_code=500)


@router.get("/api/sessions/{session_id}")
def get_session_workspace(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    cached = get_cached_workspace(session_id, organization_id=caller.read_scope)
    if not isinstance(cached, dict):
        raise HTTPException(status_code=404, detail={"resource": "Session cache", "message": "No cached session found."})
    return cached


@router.get("/api/sessions/{session_id}/cached")
def get_cached_session_workspace(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Response:
    try:
        cached = get_cached_workspace(session_id, organization_id=caller.read_scope)
        if not isinstance(cached, dict):
            return Response(status_code=204)
        return JSONResponse(content=jsonable_encoder(cached))
    except Exception:
        logger.exception("Cached session route failed for session_id=%s", session_id)
        return Response(status_code=204)


@router.post("/api/sessions/{session_id}/fetch")
def fetch_session_workspace(session_id: SessionId, request: FetchSessionRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        api_key = caller.livestorm_auth(request.api_key)
        return fetch_all_session_data(
            api_key=api_key,
            session_id=session_id,
            force_refresh=request.force_refresh,
            job_connection_id=caller.job_connection_id(),
            read_scope=caller.read_scope,
            organization_id=caller.org_id,
            **caller.generator_kwargs(),
        )
    except Exception as exc:
        raise_http_error("Session data", exc)
        raise


@router.post("/api/sessions/{session_id}/fetch-base")
def fetch_session_base_workspace(session_id: SessionId, request: FetchSessionRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        api_key = caller.livestorm_auth(request.api_key)
        return fetch_session_base_data(
            api_key=api_key,
            session_id=session_id,
            force_refresh=request.force_refresh,
            read_scope=caller.read_scope,
            organization_id=caller.org_id,
            **caller.generator_kwargs(),
        )
    except Exception as exc:
        raise_http_error("Session overview", exc)
        raise


@router.post("/api/sessions/{session_id}/fetch-transcript")
def fetch_session_transcript_workspace(session_id: SessionId, request: FetchSessionRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        api_key = caller.livestorm_auth(request.api_key)
        return fetch_session_transcript_data(
            api_key=api_key,
            session_id=session_id,
            force_refresh=request.force_refresh,
            job_connection_id=caller.job_connection_id(),
            read_scope=caller.read_scope,
            organization_id=caller.org_id,
            **caller.generator_kwargs(),
        )
    except Exception as exc:
        raise_http_error("Transcript", exc)
        raise


@router.get("/api/sessions/{session_id}/transcript-job")
def get_transcript_job_status(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        return get_transcript_job_status_data("", session_id, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Transcript job", exc)
        raise


@router.get("/api/sessions/{session_id}/transcript.json")
def download_transcript_json(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Response:
    """The full raw transcript (the workspace only carries what the UI renders)."""
    try:
        payload = get_transcript_download(session_id, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Transcript", exc)
        raise
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="livestorm-transcript-{session_id}.json"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/api/sessions/{session_id}/speaker-labels")
def update_speaker_labels(session_id: SessionId, request: SpeakerLabelsRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        api_key = caller.livestorm_auth(request.api_key)
        return save_speaker_labels(api_key, session_id, request.speaker_names, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Speaker labels", exc)
        raise
