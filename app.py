from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from livestorm_app.api_logic import (
    build_analysis_pdf,
    build_content_repurposing_pdf,
    build_smart_recap_pdf,
    fetch_available_events,
    fetch_all_session_data,
    fetch_session_base_data,
    fetch_session_transcript_data,
    fetch_event_sessions,
    format_service_error,
    get_cached_workspace,
    run_content_repurposing,
    run_deep_analysis,
    run_overall_analysis,
    run_smart_recap,
    save_speaker_labels,
)
from livestorm_app.config import ENV_PATH, get_runtime_secret, load_env_file
from livestorm_app.db import ensure_database_schema
from livestorm_app.oauth_client import (
    LIVESTORM_OAUTH_COOKIE,
    LIVESTORM_OAUTH_HANDSHAKE_COOKIE,
    build_authorization_url,
    delete_connection,
    exchange_authorization_code,
    fetch_me,
    get_connection_identity,
    get_frontend_app_url,
    oauth_enabled,
    persist_oauth_connection,
    refresh_connection_if_needed,
    validate_handshake,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"
BRAND_ASSETS_DIR = BASE_DIR / "assets"


class EventSessionsRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    event_id: str = Field(..., alias="eventId")


class WorkspaceEventsRequest(BaseModel):
    api_key: str = Field(..., alias="apiKey")
    page_number: int = Field(0, alias="pageNumber")
    page_size: int = Field(20, alias="pageSize")
    title: str = Field("", alias="title")
    scheduling_status: str = Field("", alias="schedulingStatus")


class FetchSessionRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    transcript_api_key: Optional[str] = Field("", alias="transcriptApiKey")
    force_refresh: bool = Field(False, alias="forceRefresh")


class SpeakerLabelsRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    speaker_names: Dict[str, str] = Field(default_factory=dict, alias="speakerNames")


class AnalysisRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    output_language: str = Field("English", alias="outputLanguage")


class SmartRecapRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    tone: str


def _raise_http_error(resource_label: str, exc: Exception, status_code: int = 400) -> None:
    error_payload = format_service_error(exc, resource_label)
    raise HTTPException(
        status_code=status_code,
        detail={
            "resource": resource_label,
            "message": error_payload["message"],
            "details": error_payload["details"],
        },
    )


def _resolve_livestorm_auth(raw_api_key: str, request: Request) -> str:
    direct_api_key = str(raw_api_key or "").strip()
    if direct_api_key:
        return direct_api_key
    if _allow_local_api_key_fallback(request):
        local_api_key = get_runtime_secret("LS_API_KEY", "")
        if str(local_api_key or "").strip():
            return str(local_api_key).strip()
    connection_id = str(request.cookies.get(LIVESTORM_OAUTH_COOKIE) or "").strip()
    if not connection_id:
        raise HTTPException(
            status_code=401,
            detail={"resource": "Livestorm authentication", "message": "Please connect with Livestorm first."},
        )
    connection = refresh_connection_if_needed(connection_id)
    if not isinstance(connection, dict):
        raise HTTPException(
            status_code=401,
            detail={"resource": "Livestorm authentication", "message": "Your Livestorm connection has expired. Please reconnect."},
        )
    access_token = str(connection.get("access_token") or "").strip()
    token_type = str(connection.get("token_type") or "Bearer").strip() or "Bearer"
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail={"resource": "Livestorm authentication", "message": "Your Livestorm connection is missing an access token. Please reconnect."},
        )
    return f"{token_type} {access_token}"


def _get_bootstrap_auth(request: Request) -> Dict[str, Any]:
    connection_id = str(request.cookies.get(LIVESTORM_OAUTH_COOKIE) or "").strip()
    connection = refresh_connection_if_needed(connection_id) if connection_id else None
    return {
        "oauthEnabled": oauth_enabled(),
        "connectedUser": get_connection_identity(connection),
        "allowLocalApiKeyFallback": _allow_local_api_key_fallback(request),
    }


def _allow_local_api_key_fallback(request: Request | None) -> bool:
    if request is None:
        return False
    hostname = str(request.url.hostname or "").strip().lower()
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    return bool(str(get_runtime_secret("LS_API_KEY", "") or "").strip())


load_env_file()
try:
    ensure_database_schema()
except Exception:
    logger.exception("Database schema initialization failed")

app = FastAPI(title="Livestorm Post Event Analysis API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if BRAND_ASSETS_DIR.exists():
    app.mount("/brand-assets", StaticFiles(directory=BRAND_ASSETS_DIR), name="brand-assets")


@app.get("/api/health")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/bootstrap")
def bootstrap_defaults(request: Request) -> Dict[str, Any]:
    auth = _get_bootstrap_auth(request)
    default_api_key = ""
    if ENV_PATH.exists() and (not auth.get("oauthEnabled") or auth.get("allowLocalApiKeyFallback")):
        default_api_key = get_runtime_secret("LS_API_KEY", "")
    return {
        "defaults": {
            "apiKey": default_api_key,
        },
        "auth": auth,
    }


@app.get("/api/auth/livestorm/start")
def start_livestorm_oauth(returnTo: str = Query("/")) -> Response:
    if not oauth_enabled():
        raise HTTPException(status_code=400, detail={"resource": "OAuth", "message": "Livestorm OAuth is not configured on the server yet."})
    auth_request = build_authorization_url(return_to=returnTo)
    response = RedirectResponse(auth_request["url"], status_code=302)
    response.set_cookie(
        LIVESTORM_OAUTH_HANDSHAKE_COOKIE,
        auth_request["handshake_token"],
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=600,
        path="/",
    )
    return response


@app.get("/api/auth/livestorm/callback")
def complete_livestorm_oauth(code: str = Query(""), state: str = Query(""), request: Request = None) -> Response:
    handshake_cookie = str((request.cookies.get(LIVESTORM_OAUTH_HANDSHAKE_COOKIE) if request else "") or "").strip()
    frontend_base = get_frontend_app_url()
    success_redirect = f"{frontend_base}/auth/callback?auth=success" if frontend_base else "/auth/callback?auth=success"
    failure_redirect = f"{frontend_base}/auth/callback?auth=error" if frontend_base else "/auth/callback?auth=error"
    if not handshake_cookie or not code.strip():
        response = RedirectResponse(failure_redirect, status_code=302)
        response.delete_cookie(LIVESTORM_OAUTH_HANDSHAKE_COOKIE, path="/")
        return response
    try:
        handshake = validate_handshake(handshake_cookie, state)
        token_payload = exchange_authorization_code(code.strip(), str(handshake.get("verifier") or "").strip())
        me_payload = fetch_me(str(token_payload.get("access_token") or "").strip())
        connection = persist_oauth_connection(token_payload, me_payload)
        response = RedirectResponse(success_redirect, status_code=302)
        response.set_cookie(
            LIVESTORM_OAUTH_COOKIE,
            str(connection.get("connection_id") or "").strip(),
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=60 * 60 * 24 * 30,
            path="/",
        )
        response.delete_cookie(LIVESTORM_OAUTH_HANDSHAKE_COOKIE, path="/")
        return response
    except Exception:
        logger.exception("Livestorm OAuth callback failed")
        response = RedirectResponse(failure_redirect, status_code=302)
        response.delete_cookie(LIVESTORM_OAUTH_HANDSHAKE_COOKIE, path="/")
        return response


@app.post("/api/auth/logout")
def logout_livestorm_oauth(request: Request) -> Response:
    connection_id = str(request.cookies.get(LIVESTORM_OAUTH_COOKIE) or "").strip()
    if connection_id:
        delete_connection(connection_id)
    response = JSONResponse({"ok": True})
    response.delete_cookie(LIVESTORM_OAUTH_COOKIE, path="/")
    return response


@app.post("/api/event-sessions")
def event_sessions(request: EventSessionsRequest, http_request: Request) -> Dict[str, Any]:
    try:
        api_key = _resolve_livestorm_auth(request.api_key, http_request)
        return fetch_event_sessions(api_key, request.event_id)
    except Exception as exc:
        _raise_http_error("Event sessions", exc)
        raise


@app.post("/api/workspace-events")
def workspace_events(request: WorkspaceEventsRequest, http_request: Request) -> Dict[str, Any]:
    try:
        api_key = _resolve_livestorm_auth(request.api_key, http_request)
        return fetch_available_events(
            api_key,
            page_number=request.page_number,
            page_size=request.page_size,
            title=request.title,
            scheduling_status=request.scheduling_status,
        )
    except Exception as exc:
        _raise_http_error("Workspace events", exc)
        raise


@app.get("/api/sessions/{session_id}")
def get_session_workspace(session_id: str) -> Dict[str, Any]:
    cached = get_cached_workspace(session_id)
    if not isinstance(cached, dict):
        raise HTTPException(status_code=404, detail={"resource": "Session cache", "message": "No cached session found."})
    return cached


@app.get("/api/sessions/{session_id}/cached")
def get_cached_session_workspace(session_id: str) -> Response:
    try:
        cached = get_cached_workspace(session_id)
        if not isinstance(cached, dict):
            return Response(status_code=204)
        return JSONResponse(content=jsonable_encoder(cached))
    except Exception:
        logger.exception("Cached session route failed for session_id=%s", session_id)
        return Response(status_code=204)


@app.post("/api/sessions/{session_id}/fetch")
def fetch_session_workspace(session_id: str, request: FetchSessionRequest, http_request: Request) -> Dict[str, Any]:
    try:
        api_key = _resolve_livestorm_auth(request.api_key, http_request)
        transcript_api_key = str(request.transcript_api_key or "").strip() or get_runtime_secret("GLADIA_KEY", "")
        return fetch_all_session_data(
            api_key=api_key,
            transcript_api_key=transcript_api_key,
            session_id=session_id,
            force_refresh=request.force_refresh,
        )
    except Exception as exc:
        _raise_http_error("Session data", exc)
        raise


@app.post("/api/sessions/{session_id}/fetch-base")
def fetch_session_base_workspace(session_id: str, request: FetchSessionRequest, http_request: Request) -> Dict[str, Any]:
    try:
        api_key = _resolve_livestorm_auth(request.api_key, http_request)
        return fetch_session_base_data(
            api_key=api_key,
            session_id=session_id,
            force_refresh=request.force_refresh,
        )
    except Exception as exc:
        _raise_http_error("Session overview", exc)
        raise


@app.post("/api/sessions/{session_id}/fetch-transcript")
def fetch_session_transcript_workspace(session_id: str, request: FetchSessionRequest, http_request: Request) -> Dict[str, Any]:
    try:
        api_key = _resolve_livestorm_auth(request.api_key, http_request)
        transcript_api_key = str(request.transcript_api_key or "").strip() or get_runtime_secret("GLADIA_KEY", "")
        return fetch_session_transcript_data(
            api_key=api_key,
            transcript_api_key=transcript_api_key,
            session_id=session_id,
            force_refresh=request.force_refresh,
        )
    except Exception as exc:
        _raise_http_error("Transcript", exc)
        raise


@app.post("/api/sessions/{session_id}/speaker-labels")
def update_speaker_labels(session_id: str, request: SpeakerLabelsRequest, http_request: Request) -> Dict[str, Any]:
    try:
        api_key = _resolve_livestorm_auth(request.api_key, http_request)
        return save_speaker_labels(api_key, session_id, request.speaker_names)
    except Exception as exc:
        _raise_http_error("Speaker labels", exc)
        raise


@app.post("/api/sessions/{session_id}/analysis")
def overall_analysis(session_id: str, request: AnalysisRequest) -> Dict[str, Any]:
    try:
        openai_api_key = get_runtime_secret("OPENAI_API_KEY", "")
        return run_overall_analysis(openai_api_key, session_id, request.output_language)
    except Exception as exc:
        _raise_http_error("Analysis", exc)
        raise


@app.post("/api/sessions/{session_id}/deep-analysis")
def deep_analysis(session_id: str, request: AnalysisRequest) -> Dict[str, Any]:
    try:
        openai_api_key = get_runtime_secret("OPENAI_API_KEY", "")
        return run_deep_analysis(openai_api_key, session_id, request.output_language)
    except Exception as exc:
        _raise_http_error("Deep analysis", exc)
        raise


@app.get("/api/sessions/{session_id}/analysis-pdf")
def analysis_pdf(session_id: str, kind: str = Query(...), language: str = Query("English")) -> Response:
    try:
        result = build_analysis_pdf(session_id, kind, language)
        filename = str(result["filename"])
        pdf_bytes = result["content"]
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
    except Exception as exc:
        _raise_http_error("Analysis PDF", exc)
        raise


@app.post("/api/sessions/{session_id}/smart-recap")
def smart_recap(session_id: str, request: SmartRecapRequest) -> Dict[str, Any]:
    try:
        openai_api_key = get_runtime_secret("OPENAI_API_KEY", "")
        return run_smart_recap(openai_api_key, session_id, request.tone)
    except Exception as exc:
        _raise_http_error("Smart Recap", exc)
        raise


@app.get("/api/sessions/{session_id}/smart-recap-pdf")
def smart_recap_pdf(session_id: str, tone: str = Query(...)) -> Response:
    try:
        result = build_smart_recap_pdf(session_id, tone)
        filename = str(result["filename"])
        pdf_bytes = result["content"]
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
    except Exception as exc:
        _raise_http_error("Smart Recap PDF", exc)
        raise


@app.post("/api/sessions/{session_id}/content-repurposing")
def content_repurposing(session_id: str, request: AnalysisRequest) -> Dict[str, Any]:
    try:
        openai_api_key = get_runtime_secret("OPENAI_API_KEY", "")
        return run_content_repurposing(openai_api_key, session_id, request.output_language)
    except Exception as exc:
        _raise_http_error("Content Repurposing", exc)
        raise


@app.get("/api/sessions/{session_id}/content-repurposing-pdf")
def content_repurposing_pdf(session_id: str, language: str = Query("English"), item: str = Query(...)) -> Response:
    try:
        result = build_content_repurposing_pdf(session_id, language, item)
        filename = str(result["filename"])
        pdf_bytes = result["content"]
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
    except Exception as exc:
        _raise_http_error("Content Repurposing PDF", exc)
        raise


if FRONTEND_DIST_DIR.exists():
    assets_dir = FRONTEND_DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str) -> Any:
        requested_path = FRONTEND_DIST_DIR / full_path
        if full_path and requested_path.exists() and requested_path.is_file():
            return FileResponse(requested_path)
        index_path = FRONTEND_DIST_DIR / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return JSONResponse({"message": "Frontend build not found."}, status_code=404)
