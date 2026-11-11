"""AI outputs: start a job, poll it, and download the PDF, for each of the four flows."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query, Response

from livestorm_app.api_logic import (
    build_analysis_pdf,
    build_content_repurposing_pdf,
    build_smart_recap_pdf,
    get_content_repurposing_job_data,
    get_deep_analysis_job_data,
    get_decisions_job_data,
    get_faq_job_data,
    get_overall_analysis_job_data,
    get_session_analysis_job,
    get_smart_recap_job_data,
    start_content_repurposing,
    start_session_analysis,
    start_deep_analysis,
    start_decisions,
    start_faq,
    start_overall_analysis,
    start_smart_recap,
)
from livestorm_app.auth import (
    Caller,
    get_caller,
)
from livestorm_app.routes.common import SessionId, pdf_response, raise_http_error
from livestorm_app.routes.models import AnalysisRequest, SmartRecapRequest

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
    except Exception as exc:
        raise_http_error("Smart Recap", exc)
        raise


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
