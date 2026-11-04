from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as ApiPath
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from livestorm_app.api_logic import (
    SessionNotFoundError,
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
    get_content_repurposing_job_data,
    get_deep_analysis_job_data,
    get_overall_analysis_job_data,
    get_smart_recap_job_data,
    get_transcript_job_status_data,
    list_workspace_sessions_data,
    save_speaker_labels,
    start_content_repurposing,
    start_deep_analysis,
    start_overall_analysis,
    start_smart_recap,
)
from livestorm_app.auth import (
    LOCAL_DEV_ENV_VAR,
    Caller,
    env_admin_emails,
    get_caller,
    get_optional_caller,
    local_api_key,
    local_dev_enabled,
    require_admin,
    running_on_render,
)
from livestorm_app.config import get_runtime_secret, load_env_file
from livestorm_app.db import (
    admin_delete_session,
    admin_list_sessions,
    admin_list_users,
    demote_admin,
    ensure_database_schema,
    fetch_cover_image,
    get_all_settings,
    promote_admin,
    set_setting,
)
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
    seed_dev_connection,
    validate_handshake,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"
BRAND_ASSETS_DIR = BASE_DIR / "assets"

# Livestorm session ids are UUIDs. Accept any short URL-safe id so an
# upstream format change doesn't break lookups, but reject anything
# path-like or oversized before it reaches the DB or a filename.
SessionId = Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]

MAX_SPEAKER_LABELS = 100
MAX_SPEAKER_ID_LENGTH = 64
MAX_SPEAKER_LABEL_LENGTH = 120


class EventSessionsRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    event_id: str = Field(..., alias="eventId")


class WorkspaceEventsRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
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

    @field_validator("speaker_names")
    @classmethod
    def _limit_speaker_names(cls, value: Dict[str, str]) -> Dict[str, str]:
        if len(value) > MAX_SPEAKER_LABELS:
            raise ValueError(f"At most {MAX_SPEAKER_LABELS} speaker labels are allowed.")
        for speaker, label in value.items():
            if len(str(speaker)) > MAX_SPEAKER_ID_LENGTH or len(str(label)) > MAX_SPEAKER_LABEL_LENGTH:
                raise ValueError(
                    f"Speaker ids are limited to {MAX_SPEAKER_ID_LENGTH} characters "
                    f"and labels to {MAX_SPEAKER_LABEL_LENGTH}."
                )
        return value


class AnalysisRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    output_language: str = Field("English", alias="outputLanguage")


class SmartRecapRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    tone: str


def _raise_http_error(resource_label: str, exc: Exception, status_code: int = 400) -> None:
    """Convert a service exception into an HTTP error.

    Upstream debug details (URLs, raw response bodies) are logged on the
    server and never returned to the client.
    """
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, SessionNotFoundError):
        raise HTTPException(status_code=404, detail={"resource": resource_label, "message": str(exc)})
    error_payload = format_service_error(exc, resource_label)
    debug_details = error_payload.get("details")
    debug_details = debug_details if isinstance(debug_details, dict) else {}
    if isinstance(exc, RuntimeError):
        logger.info("%s request failed: %s", resource_label, error_payload.get("message"))
    else:
        logger.warning("%s request failed: %s | details=%s", resource_label, error_payload.get("message"), debug_details, exc_info=True)
    public_details: Dict[str, Any] = {"resource": resource_label}
    if debug_details.get("status_code"):
        public_details["status_code"] = debug_details["status_code"]
    raise HTTPException(
        status_code=status_code,
        detail={
            "resource": resource_label,
            "message": error_payload["message"],
            "details": public_details,
        },
    )


_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _pdf_response(result: Dict[str, Any]) -> Response:
    filename = _UNSAFE_FILENAME_CHARS.sub("-", str(result.get("filename") or "")).strip("-.") or "stormiq-export.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=result["content"], media_type="application/pdf", headers=headers)


def _cookie_secure(request: Request) -> bool:
    """Secure cookies everywhere except plain-HTTP local development.

    Render terminates TLS at its proxy, so the app sees plain HTTP; the
    RENDER env var marks production regardless of scheme.
    """
    if running_on_render():
        return True
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded_proto == "https" or request.url.scheme == "https"


load_env_file()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        ensure_database_schema()
    except Exception:
        logger.exception("Database schema initialization failed")
    if local_dev_enabled():
        logger.warning(
            "Local-dev mode is ON (%s): requests without an OAuth session act as a trusted local operator.",
            LOCAL_DEV_ENV_VAR,
        )
    yield


# Interactive API docs and the OpenAPI schema are a local convenience; in
# production they would publish the full route map to anonymous visitors.
_API_DOCS_ENABLED = not running_on_render()

app = FastAPI(
    title="Livestorm Post Event Analysis API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _API_DOCS_ENABLED else None,
    redoc_url="/redoc" if _API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _API_DOCS_ENABLED else None,
)

# No CORS middleware: the SPA is served by this app (and proxied by Vite in
# dev), so every browser request is same-origin.

if BRAND_ASSETS_DIR.exists():
    app.mount("/brand-assets", StaticFiles(directory=BRAND_ASSETS_DIR), name="brand-assets")


@app.get("/api/health")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/bootstrap")
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


class AdminPromoteRequest(BaseModel):
    email: str
    user_id: str = ""


@app.get("/api/admin/check")
def admin_check(caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    return {"isAdmin": True, "email": (caller.connection or {}).get("email", "")}


@app.get("/api/admin/users")
def admin_get_users(_caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    return {"users": admin_list_users()}


@app.post("/api/admin/users/promote")
def admin_promote_user(body: AdminPromoteRequest, caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    actor_email = str((caller.connection or {}).get("email") or "").strip()
    promote_admin(body.email.strip(), body.user_id.strip(), actor_email)
    return {"ok": True}


@app.post("/api/admin/users/demote")
def admin_demote_user(body: AdminPromoteRequest, caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    actor_email = str((caller.connection or {}).get("email") or "").strip().lower()
    target_email = str(body.email or "").strip().lower()
    if target_email in env_admin_emails():
        raise HTTPException(status_code=400, detail={"resource": "Admin", "message": "Cannot demote a bootstrap admin set via ADMIN_EMAILS env var."})
    if target_email == actor_email:
        raise HTTPException(status_code=400, detail={"resource": "Admin", "message": "You cannot demote yourself."})
    demote_admin(target_email)
    return {"ok": True}


@app.get("/api/admin/sessions")
def admin_get_sessions(_caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    return {"sessions": admin_list_sessions()}


@app.delete("/api/admin/sessions/{session_id}")
def admin_remove_session(session_id: SessionId, _caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    admin_delete_session(session_id)
    return {"ok": True, "sessionId": session_id}


class AdminSettingRequest(BaseModel):
    key: str
    value: str


@app.get("/api/admin/settings")
def admin_get_settings(_caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    return {"settings": get_all_settings()}


@app.post("/api/admin/settings")
def admin_update_setting(body: AdminSettingRequest, _caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    allowed_keys = {
        "llm_provider",
        "llm_default_model",
        "llm_smart_recap_model",
        "image_model",
    }
    key = str(body.key or "").strip()
    if key not in allowed_keys:
        raise HTTPException(status_code=400, detail={"resource": "Setting", "message": f"Unknown setting key: {key}"})
    value = str(body.value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail={"resource": "Setting", "message": "Value cannot be empty."})
    set_setting(key, value)
    return {"ok": True, "key": key, "value": value}


@app.get("/api/auth/livestorm/start")
def start_livestorm_oauth(request: Request, returnTo: str = Query("/")) -> Response:
    if not oauth_enabled():
        raise HTTPException(status_code=400, detail={"resource": "OAuth", "message": "Livestorm OAuth is not configured on the server yet."})
    auth_request = build_authorization_url(return_to=returnTo)
    response = RedirectResponse(auth_request["url"], status_code=302)
    response.set_cookie(
        LIVESTORM_OAUTH_HANDSHAKE_COOKIE,
        auth_request["handshake_token"],
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        max_age=600,
        path="/",
    )
    return response


@app.get("/api/auth/livestorm/callback")
def complete_livestorm_oauth(request: Request, code: str = Query(""), state: str = Query("")) -> Response:
    handshake_cookie = str(request.cookies.get(LIVESTORM_OAUTH_HANDSHAKE_COOKIE) or "").strip()
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
            secure=_cookie_secure(request),
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


@app.get("/api/dev/auto-login")
def dev_auto_login(request: Request) -> Response:
    """Local-dev only: seed a real OAuth identity from LS_API_KEY.

    Calls /me with the local API key, stores the user/org identity in
    oauth_connections under a stable connection_id, and sets the session
    cookie — so org filtering, 'Generated by', and the session card list
    all behave as they would after a real OAuth login. Requires
    STORMIQ_LOCAL_DEV and LS_API_KEY; never available on Render.
    """
    api_key = local_api_key(request)
    if not api_key:
        raise HTTPException(
            status_code=403,
            detail={"resource": "Dev auto-login", "message": f"Requires {LOCAL_DEV_ENV_VAR}=1, LS_API_KEY, and a localhost URL (never on Render)."},
        )
    try:
        connection_id = seed_dev_connection(api_key)
    except Exception as exc:
        logger.warning("Dev auto-login failed to fetch identity from Livestorm /me", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail={"resource": "Dev auto-login", "message": "Failed to fetch identity from Livestorm /me."},
        ) from exc
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(key=LIVESTORM_OAUTH_COOKIE, value=connection_id, httponly=True, samesite="lax", secure=_cookie_secure(request), path="/")
    return response


@app.get("/api/workspace-sessions")
def workspace_sessions(caller: Optional[Caller] = Depends(get_optional_caller)) -> Dict[str, Any]:
    """Phase 4: list every cached session the calling user's org can see.

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


@app.post("/api/event-sessions")
def event_sessions(request: EventSessionsRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        api_key = caller.livestorm_auth(request.api_key)
        return fetch_event_sessions(api_key, request.event_id)
    except Exception as exc:
        _raise_http_error("Event sessions", exc)
        raise


@app.post("/api/workspace-events")
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
        _raise_http_error("Workspace events", exc)
        raise


@app.get("/api/sessions/{session_id}/cover.png")
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


@app.get("/api/sessions/{session_id}")
def get_session_workspace(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    cached = get_cached_workspace(session_id, organization_id=caller.read_scope)
    if not isinstance(cached, dict):
        raise HTTPException(status_code=404, detail={"resource": "Session cache", "message": "No cached session found."})
    return cached


@app.get("/api/sessions/{session_id}/cached")
def get_cached_session_workspace(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Response:
    try:
        cached = get_cached_workspace(session_id, organization_id=caller.read_scope)
        if not isinstance(cached, dict):
            return Response(status_code=204)
        return JSONResponse(content=jsonable_encoder(cached))
    except Exception:
        logger.exception("Cached session route failed for session_id=%s", session_id)
        return Response(status_code=204)


@app.post("/api/sessions/{session_id}/fetch")
def fetch_session_workspace(session_id: SessionId, request: FetchSessionRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        api_key = caller.livestorm_auth(request.api_key)
        transcript_api_key = str(request.transcript_api_key or "").strip() or get_runtime_secret("GLADIA_KEY", "")
        return fetch_all_session_data(
            api_key=api_key,
            transcript_api_key=transcript_api_key,
            session_id=session_id,
            force_refresh=request.force_refresh,
            read_scope=caller.read_scope,
            organization_id=caller.org_id,
            **caller.generator_kwargs(),
        )
    except Exception as exc:
        _raise_http_error("Session data", exc)
        raise


@app.post("/api/sessions/{session_id}/fetch-base")
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
        _raise_http_error("Session overview", exc)
        raise


@app.post("/api/sessions/{session_id}/fetch-transcript")
def fetch_session_transcript_workspace(session_id: SessionId, request: FetchSessionRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        api_key = caller.livestorm_auth(request.api_key)
        transcript_api_key = str(request.transcript_api_key or "").strip() or get_runtime_secret("GLADIA_KEY", "")
        return fetch_session_transcript_data(
            api_key=api_key,
            transcript_api_key=transcript_api_key,
            session_id=session_id,
            force_refresh=request.force_refresh,
            read_scope=caller.read_scope,
            organization_id=caller.org_id,
            **caller.generator_kwargs(),
        )
    except Exception as exc:
        _raise_http_error("Transcript", exc)
        raise


@app.get("/api/sessions/{session_id}/transcript-job")
def get_transcript_job_status(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        return get_transcript_job_status_data("", session_id, organization_id=caller.read_scope)
    except Exception as exc:
        _raise_http_error("Transcript job", exc)
        raise


@app.post("/api/sessions/{session_id}/speaker-labels")
def update_speaker_labels(session_id: SessionId, request: SpeakerLabelsRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    try:
        api_key = caller.livestorm_auth(request.api_key)
        return save_speaker_labels(api_key, session_id, request.speaker_names, organization_id=caller.read_scope)
    except Exception as exc:
        _raise_http_error("Speaker labels", exc)
        raise


@app.post("/api/sessions/{session_id}/analysis")
def overall_analysis(session_id: SessionId, request: AnalysisRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start an Overall Analysis job.

    Phase 2 contract change: this no longer returns the analysis body
    inline. Returns either the full serialised workspace (if already
    cached for the requested language) or { jobId, jobKind, jobStatus,
    language } so the frontend can poll `/analysis/job`.
    """
    try:
        return start_overall_analysis(session_id, request.output_language, organization_id=caller.read_scope)
    except Exception as exc:
        _raise_http_error("Analysis", exc)
        raise


@app.get("/api/sessions/{session_id}/analysis/job")
def overall_analysis_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), language: str = Query("English")) -> Dict[str, Any]:
    try:
        return get_overall_analysis_job_data(session_id, jobId, language, organization_id=caller.read_scope)
    except Exception as exc:
        _raise_http_error("Analysis", exc)
        raise


@app.post("/api/sessions/{session_id}/deep-analysis")
def deep_analysis(session_id: SessionId, request: AnalysisRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start a Deep Analysis job. See `overall_analysis` for the contract."""
    try:
        return start_deep_analysis(session_id, request.output_language, organization_id=caller.read_scope)
    except Exception as exc:
        _raise_http_error("Deep analysis", exc)
        raise


@app.get("/api/sessions/{session_id}/deep-analysis/job")
def deep_analysis_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), language: str = Query("English")) -> Dict[str, Any]:
    try:
        return get_deep_analysis_job_data(session_id, jobId, language, organization_id=caller.read_scope)
    except Exception as exc:
        _raise_http_error("Deep analysis", exc)
        raise


@app.get("/api/sessions/{session_id}/analysis-pdf")
def analysis_pdf(session_id: SessionId, caller: Caller = Depends(get_caller), kind: str = Query(...), language: str = Query("English")) -> Response:
    try:
        return _pdf_response(build_analysis_pdf(session_id, kind, language, organization_id=caller.read_scope))
    except Exception as exc:
        _raise_http_error("Analysis PDF", exc)
        raise


@app.post("/api/sessions/{session_id}/smart-recap")
def smart_recap(session_id: SessionId, request: SmartRecapRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start a Smart Recap job. Same contract as overall_analysis, keyed by tone."""
    try:
        return start_smart_recap(session_id, request.tone, organization_id=caller.read_scope)
    except Exception as exc:
        _raise_http_error("Smart Recap", exc)
        raise


@app.get("/api/sessions/{session_id}/smart-recap/job")
def smart_recap_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), tone: str = Query("professional")) -> Dict[str, Any]:
    try:
        return get_smart_recap_job_data(session_id, jobId, tone, organization_id=caller.read_scope)
    except Exception as exc:
        _raise_http_error("Smart Recap", exc)
        raise


@app.get("/api/sessions/{session_id}/smart-recap-pdf")
def smart_recap_pdf(session_id: SessionId, caller: Caller = Depends(get_caller), tone: str = Query(...)) -> Response:
    try:
        return _pdf_response(build_smart_recap_pdf(session_id, tone, organization_id=caller.read_scope))
    except Exception as exc:
        _raise_http_error("Smart Recap PDF", exc)
        raise


@app.post("/api/sessions/{session_id}/content-repurposing")
def content_repurposing(session_id: SessionId, request: AnalysisRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start a Content Repurposing job. Same contract as overall_analysis."""
    try:
        return start_content_repurposing(session_id, request.output_language, organization_id=caller.read_scope)
    except Exception as exc:
        _raise_http_error("Content Repurposing", exc)
        raise


@app.get("/api/sessions/{session_id}/content-repurposing/job")
def content_repurposing_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), language: str = Query("English")) -> Dict[str, Any]:
    try:
        return get_content_repurposing_job_data(session_id, jobId, language, organization_id=caller.read_scope)
    except Exception as exc:
        _raise_http_error("Content Repurposing", exc)
        raise


@app.get("/api/sessions/{session_id}/content-repurposing-pdf")
def content_repurposing_pdf(session_id: SessionId, caller: Caller = Depends(get_caller), language: str = Query("English"), item: str = Query(...)) -> Response:
    try:
        return _pdf_response(build_content_repurposing_pdf(session_id, language, item, organization_id=caller.read_scope))
    except Exception as exc:
        _raise_http_error("Content Repurposing PDF", exc)
        raise


# ── Frontend (SPA) ─────────────────────────────────────────────────────────
#
# Vite ships hashed asset filenames (index-abc123.js) but the HTML that
# references them is unhashed and SHOULD NEVER be cached. Without these
# headers, a Render deploy that ships a fresh hash leaves users with
# browsers/CDNs holding the OLD index.html pointing at the OLD asset — the
# new asset 404s because the old file no longer exists in /assets. Forcing
# no-cache on the HTML entry-point makes every navigation re-fetch the
# index, while hashed asset files are served by the StaticFiles mount.
_INDEX_HTML_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

if (FRONTEND_DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST_DIR / "assets"), name="assets")


def _resolve_frontend_file(full_path: str, dist_dir: Path = FRONTEND_DIST_DIR) -> Optional[Path]:
    """Return the file inside the frontend build that `full_path` names, or None.

    Resolves `..` segments and symlinks before checking containment, so a
    request like `/%2e%2e/%2e%2e/app.py` cannot escape the build directory.
    """
    if not full_path:
        return None
    try:
        root = dist_dir.resolve()
        candidate = (root / full_path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


@app.get("/{full_path:path}")
def serve_frontend(full_path: str) -> Any:
    if full_path == "api" or full_path.startswith("api/"):
        return JSONResponse({"message": "Not found."}, status_code=404)
    requested_path = _resolve_frontend_file(full_path)
    if requested_path is not None:
        return FileResponse(requested_path)
    index_path = FRONTEND_DIST_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path, headers=_INDEX_HTML_HEADERS)
    return JSONResponse({"message": "Frontend build not found."}, status_code=404)
