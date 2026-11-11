"""AI outputs: start a job, poll it, and download the PDF, for each of the four flows; Surprise me's show."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from livestorm_app.ai.surprise_voice import CLIP_KEY_PATTERN

from livestorm_app.api_logic import (
    SessionNotFoundError,
    SurpriseOffError,
    SurpriseVoiceNoTakeError,
    UnknownOutputKindError,
    get_surprise_show,
    get_surprise_voice_clip,
    start_surprise_show,
    get_session_output_versions,
    build_analysis_pdf,
    build_content_repurposing_pdf,
    build_smart_recap_pdf,
    get_content_repurposing_job_data,
    get_deep_analysis_job_data,
    get_decisions_job_data,
    get_session_decisions,
    get_faq_job_data,
    get_overall_analysis_job_data,
    get_session_analysis_job,
    get_smart_recap_job_data,
    get_team_digest_job_data,
    get_attendee_followup_job_data,
    start_content_repurposing,
    start_session_analysis,
    start_deep_analysis,
    start_decisions,
    start_faq,
    start_overall_analysis,
    start_smart_recap,
    start_team_digest,
    start_attendee_followup,
)
from livestorm_app.auth import (
    Caller,
    get_caller,
)
from livestorm_app.routes.common import SessionId, pdf_response, raise_http_error
from livestorm_app.routes.models import AnalysisRequest, SmartRecapRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/sessions/{session_id}/session-analysis")
def session_analysis(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start the v1 session analysis (the chat labels, for now), or join the
    run in flight. Returns {jobId, jobStatus, enqueued}; poll `/session-analysis/job`."""
    try:
        return start_session_analysis(session_id, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Session analysis", exc)
        raise


@router.get("/api/sessions/{session_id}/session-analysis/job")
def session_analysis_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query("")) -> Dict[str, Any]:
    try:
        return get_session_analysis_job(session_id, jobId, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Session analysis", exc)
        raise


@router.post("/api/sessions/{session_id}/analysis")
def overall_analysis(session_id: SessionId, request: AnalysisRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start an Overall Analysis job.

    Returns either the full serialised workspace (if already cached for
    the requested language) or { jobId, jobKind, jobStatus, language } so
    the frontend can poll `/analysis/job`.
    """
    try:
        return start_overall_analysis(session_id, request.output_language, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Analysis", exc)
        raise


@router.get("/api/sessions/{session_id}/analysis/job")
def overall_analysis_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), language: str = Query("English"), outputsOnly: bool = Query(False)) -> Dict[str, Any]:
    try:
        return get_overall_analysis_job_data(session_id, jobId, language, organization_id=caller.read_scope, outputs_only=outputsOnly)
    except Exception as exc:
        raise_http_error("Analysis", exc)
        raise


@router.post("/api/sessions/{session_id}/faq")
def faq(session_id: SessionId, request: AnalysisRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start a FAQ job. See `overall_analysis` for the contract; poll `/faq/job`."""
    try:
        return start_faq(session_id, request.output_language, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("FAQ", exc)
        raise


@router.post("/api/sessions/{session_id}/decisions")
def decisions(session_id: SessionId, request: AnalysisRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start a "Decisions and commitments" job. See `overall_analysis` for the contract; poll `/decisions/job`."""
    try:
        return start_decisions(session_id, request.output_language, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Decisions", exc)
        raise


@router.get("/api/sessions/{session_id}/decisions/job")
def decisions_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), language: str = Query("English"), outputsOnly: bool = Query(False)) -> Dict[str, Any]:
    try:
        return get_decisions_job_data(session_id, jobId, language, organization_id=caller.read_scope, outputs_only=outputsOnly)
    except Exception as exc:
        raise_http_error("Decisions", exc)
        raise


DECISIONS_RESOURCE = "Decisions"


@router.get("/api/sessions/{session_id}/decisions")
def read_decisions(session_id: SessionId, caller: Caller = Depends(get_caller), language: str = Query("English")) -> Dict[str, Any]:
    """Follow-up's decision cards: the saved decisions and commitments in one
    language, with the speakers named as saved now
    (`api_logic.get_session_decisions`). Read-only, no model call."""
    try:
        return get_session_decisions(session_id, language, organization_id=caller.read_scope)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": DECISIONS_RESOURCE, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"resource": DECISIONS_RESOURCE, "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Decisions read failed for session_id=%s", session_id)
        raise HTTPException(
            status_code=500, detail={"resource": DECISIONS_RESOURCE, "message": "The decisions could not be read."}
        ) from exc


@router.post("/api/sessions/{session_id}/team-digest")
def team_digest(session_id: SessionId, request: AnalysisRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start a "Team digest" job. See `overall_analysis` for the contract; poll `/team-digest/job`."""
    try:
        return start_team_digest(session_id, request.output_language, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Team digest", exc)
        raise


@router.get("/api/sessions/{session_id}/team-digest/job")
def team_digest_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), language: str = Query("English"), outputsOnly: bool = Query(False)) -> Dict[str, Any]:
    try:
        return get_team_digest_job_data(session_id, jobId, language, organization_id=caller.read_scope, outputs_only=outputsOnly)
    except Exception as exc:
        raise_http_error("Team digest", exc)
        raise


@router.post("/api/sessions/{session_id}/attendee-followup")
def attendee_followup(session_id: SessionId, request: AnalysisRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start an "Attendee follow-up" job. See `overall_analysis` for the contract; poll `/attendee-followup/job`."""
    try:
        return start_attendee_followup(session_id, request.output_language, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Attendee follow-up", exc)
        raise


@router.get("/api/sessions/{session_id}/attendee-followup/job")
def attendee_followup_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), language: str = Query("English"), outputsOnly: bool = Query(False)) -> Dict[str, Any]:
    try:
        return get_attendee_followup_job_data(session_id, jobId, language, organization_id=caller.read_scope, outputs_only=outputsOnly)
    except Exception as exc:
        raise_http_error("Attendee follow-up", exc)
        raise


@router.get("/api/sessions/{session_id}/faq/job")
def faq_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), language: str = Query("English"), outputsOnly: bool = Query(False)) -> Dict[str, Any]:
    try:
        return get_faq_job_data(session_id, jobId, language, organization_id=caller.read_scope, outputs_only=outputsOnly)
    except Exception as exc:
        raise_http_error("FAQ", exc)
        raise


@router.post("/api/sessions/{session_id}/deep-analysis")
def deep_analysis(session_id: SessionId, request: AnalysisRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start a Deep Analysis job. See `overall_analysis` for the contract."""
    try:
        return start_deep_analysis(session_id, request.output_language, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Deep analysis", exc)
        raise


@router.get("/api/sessions/{session_id}/deep-analysis/job")
def deep_analysis_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), language: str = Query("English"), outputsOnly: bool = Query(False)) -> Dict[str, Any]:
    try:
        return get_deep_analysis_job_data(session_id, jobId, language, organization_id=caller.read_scope, outputs_only=outputsOnly)
    except Exception as exc:
        raise_http_error("Deep analysis", exc)
        raise


VERSIONS_RESOURCE = "Earlier versions"


@router.get("/api/sessions/{session_id}/output-versions")
def output_versions(
    session_id: SessionId,
    caller: Caller = Depends(get_caller),
    kind: str = Query(...),
    language: str = Query("English"),
    tone: str = Query("professional"),
) -> Dict[str, Any]:
    """Follow-up's "Earlier versions": one output's saved versions, newest
    first (`api_logic.get_session_output_versions`). Read-only, no model call."""
    try:
        return get_session_output_versions(session_id, kind, language=language, tone=tone, organization_id=caller.read_scope)
    except UnknownOutputKindError as exc:
        raise HTTPException(status_code=400, detail={"resource": VERSIONS_RESOURCE, "message": str(exc)}) from exc
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": VERSIONS_RESOURCE, "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Output versions read failed for session_id=%s", session_id)
        raise HTTPException(
            status_code=500, detail={"resource": VERSIONS_RESOURCE, "message": "The earlier versions could not be read."}
        ) from exc


@router.get("/api/sessions/{session_id}/analysis-pdf")
def analysis_pdf(session_id: SessionId, caller: Caller = Depends(get_caller), kind: str = Query(...), language: str = Query("English")) -> Response:
    try:
        return pdf_response(build_analysis_pdf(session_id, kind, language, organization_id=caller.read_scope))
    except Exception as exc:
        raise_http_error("Analysis PDF", exc)
        raise


@router.post("/api/sessions/{session_id}/smart-recap")
def smart_recap(session_id: SessionId, request: SmartRecapRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start a Smart Recap job. Same contract as overall_analysis, keyed by tone."""
    try:
        return start_smart_recap(session_id, request.tone, organization_id=caller.read_scope)
    except SurpriseOffError as exc:
        raise HTTPException(status_code=403, detail={"resource": "Smart Recap", "message": str(exc)}) from exc
    except Exception as exc:
        raise_http_error("Smart Recap", exc)
        raise


VOICE_RESOURCE = "Surprise me show"


def _voice_error(exc: Exception) -> None:
    if isinstance(exc, SessionNotFoundError):
        raise HTTPException(status_code=404, detail={"resource": VOICE_RESOURCE, "message": str(exc)}) from exc
    if isinstance(exc, SurpriseOffError):
        raise HTTPException(status_code=403, detail={"resource": VOICE_RESOURCE, "message": str(exc)}) from exc
    if isinstance(exc, SurpriseVoiceNoTakeError):
        raise HTTPException(status_code=409, detail={"resource": VOICE_RESOURCE, "message": str(exc)}) from exc
    logger.exception("Surprise show request failed for a session")
    raise HTTPException(
        status_code=500, detail={"resource": VOICE_RESOURCE, "message": "The show could not be read. Please try again."}
    ) from exc


@router.post("/api/sessions/{session_id}/surprise-show")
def surprise_show_start(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """The user clicked "Make the show": queue the one worker job that makes
    what the show is missing (the take, the script, the storyboard, the
    voice; paid), or return the show when it is made. No body: everything
    comes from the session as saved, never from the browser."""
    try:
        return start_surprise_show(session_id, organization_id=caller.read_scope)
    except Exception as exc:
        _voice_error(exc)
        raise


@router.get("/api/sessions/{session_id}/surprise-show")
def surprise_show_status(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """The show: the take and its beats, the script's lines by beat and clip
    (never their words), the voice, and the job's progress. Calls no provider."""
    try:
        return get_surprise_show(session_id, organization_id=caller.read_scope)
    except Exception as exc:
        _voice_error(exc)
        raise


@router.get("/api/sessions/{session_id}/surprise-show/clip")
def surprise_voice_clip(
    session_id: SessionId,
    caller: Caller = Depends(get_caller),
    key: str = Query(..., pattern=CLIP_KEY_PATTERN),
) -> Response:
    """One line's mp3, by its cache key. Private: never kept by a shared cache."""
    try:
        clip = get_surprise_voice_clip(session_id, key, organization_id=caller.read_scope)
    except Exception as exc:
        _voice_error(exc)
        raise
    if not clip:
        raise HTTPException(status_code=404, detail={"resource": VOICE_RESOURCE, "message": "This clip isn't there."})
    return Response(
        content=clip["bytes"],
        media_type=clip["mime"],
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/api/sessions/{session_id}/smart-recap/job")
def smart_recap_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), tone: str = Query("professional"), outputsOnly: bool = Query(False)) -> Dict[str, Any]:
    try:
        return get_smart_recap_job_data(session_id, jobId, tone, organization_id=caller.read_scope, outputs_only=outputsOnly)
    except Exception as exc:
        raise_http_error("Smart Recap", exc)
        raise


@router.get("/api/sessions/{session_id}/smart-recap-pdf")
def smart_recap_pdf(session_id: SessionId, caller: Caller = Depends(get_caller), tone: str = Query(...)) -> Response:
    try:
        return pdf_response(build_smart_recap_pdf(session_id, tone, organization_id=caller.read_scope))
    except Exception as exc:
        raise_http_error("Smart Recap PDF", exc)
        raise


@router.post("/api/sessions/{session_id}/content-repurposing")
def content_repurposing(session_id: SessionId, request: AnalysisRequest, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start a Content Repurposing job. Same contract as overall_analysis."""
    try:
        return start_content_repurposing(session_id, request.output_language, organization_id=caller.read_scope)
    except Exception as exc:
        raise_http_error("Content Repurposing", exc)
        raise


@router.get("/api/sessions/{session_id}/content-repurposing/job")
def content_repurposing_job(session_id: SessionId, caller: Caller = Depends(get_caller), jobId: str = Query(""), language: str = Query("English"), outputsOnly: bool = Query(False)) -> Dict[str, Any]:
    try:
        return get_content_repurposing_job_data(session_id, jobId, language, organization_id=caller.read_scope, outputs_only=outputsOnly)
    except Exception as exc:
        raise_http_error("Content Repurposing", exc)
        raise


@router.get("/api/sessions/{session_id}/content-repurposing-pdf")
def content_repurposing_pdf(session_id: SessionId, caller: Caller = Depends(get_caller), language: str = Query("English"), item: str = Query(...)) -> Response:
    try:
        return pdf_response(build_content_repurposing_pdf(session_id, language, item, organization_id=caller.read_scope))
    except Exception as exc:
        raise_http_error("Content Repurposing PDF", exc)
        raise
