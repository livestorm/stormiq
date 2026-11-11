"""One session: cover image, cached workspace, the v1 record, fetches, the transcript job and download, speaker labels, the people sync."""

from __future__ import annotations

import json
from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from livestorm_app.api_logic import (
    SessionNotFoundError,
    fetch_all_session_data,
    fetch_session_base_data,
    fetch_session_transcript_data,
    get_cached_workspace,
    FigureCheckNotFoundError,
    NothingLocatedError,
    QuestionNotFoundError,
    QuestionNotSavableError,
    decide_session_located_answer,
    get_session_figure_checks,
    save_session_figure_check,
    get_session_chapters,
    get_session_chat_messages,
    get_session_open_questions_csv,
    get_session_question_desk,
    get_session_transcript_view,
    get_session_record,
    get_transcript_download,
    get_transcript_job_status_data,
    save_session_question_reply,
    save_speaker_labels,
)
from livestorm_app.auth import (
    Caller,
    get_caller,
)
from livestorm_app.db import (
    fetch_cover_image,
)
from livestorm_app.people_sync import (
    PeopleSyncCapReached,
    PeopleSyncUnavailable,
    RosterSessionNotFound,
    read_roster_status,
    request_people_sync,
)
from livestorm_app.routes.common import SessionId, logger, raise_http_error
from livestorm_app.routes.models import (
    FetchSessionRequest,
    FigureCheckRequest,
    LocatedDecisionRequest,
    QuestionReplyRequest,
    SpeakerLabelsRequest,
)

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


@router.get("/api/sessions/{session_id}/record")
def read_session_record(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """The v1 Report's data for one session: the clock, the roster header, the
    Pulse and the measures (`api_logic.get_session_record`). The classic app
    doesn't call it."""
    try:
        return get_session_record(session_id, organization_id=caller.read_scope)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": "Session record", "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Session record failed for session_id=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail={"resource": "Session record", "message": "The session record could not be built."},
        ) from exc


@router.get("/api/sessions/{session_id}/record/chat")
def read_session_chat_messages(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """The chat's text by evidence id (`C0001`), for the v1 Report's counts
    that open their messages (`api_logic.get_session_chat_messages`). No
    author. The classic app doesn't call it."""
    try:
        return get_session_chat_messages(session_id, organization_id=caller.read_scope)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": "Session chat", "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Session chat failed for session_id=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail={"resource": "Session chat", "message": "The session's chat could not be read."},
        ) from exc


FIGURES_RESOURCE = "Figure checks"


@router.get("/api/sessions/{session_id}/record/figures")
def read_session_figure_checks(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Before you share: the figures said on stage to check, with the host's
    answers (`api_logic.get_session_figure_checks`). No model call."""
    try:
        return get_session_figure_checks(session_id, organization_id=caller.read_scope)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": FIGURES_RESOURCE, "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Figure checks read failed for session_id=%s", session_id)
        raise HTTPException(
            status_code=500, detail={"resource": FIGURES_RESOURCE, "message": "The figures to check could not be read."}
        ) from exc


@router.put("/api/sessions/{session_id}/record/figures")
def update_session_figure_check(
    session_id: SessionId, request: FigureCheckRequest, caller: Caller = Depends(get_caller)
) -> Dict[str, Any]:
    """Answer one figure check, or reopen it (`choice` null), and return the
    checks (`api_logic.save_session_figure_check`). Scoped like every
    session route: another org's session looks missing."""
    connection = caller.connection if isinstance(caller.connection, dict) else {}
    try:
        return save_session_figure_check(
            session_id,
            request.key,
            request.choice,
            organization_id=caller.read_scope,
            updated_by=str(connection.get("user_id") or "") or None,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": FIGURES_RESOURCE, "message": str(exc)}) from exc
    except FigureCheckNotFoundError as exc:
        raise HTTPException(status_code=409, detail={"resource": FIGURES_RESOURCE, "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Figure check save failed for session_id=%s", session_id)
        raise HTTPException(
            status_code=500, detail={"resource": FIGURES_RESOURCE, "message": "The figure check could not be saved."}
        ) from exc


@router.get("/api/sessions/{session_id}/record/chapters")
def read_session_chapters(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """The session's chapters and their show notes, from what the session
    analysis stored (`api_logic.get_session_chapters`). No model call."""
    try:
        return get_session_chapters(session_id, organization_id=caller.read_scope)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": "Session chapters", "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Session chapters failed for session_id=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail={"resource": "Session chapters", "message": "The session's chapters could not be read."},
        ) from exc


@router.get("/api/sessions/{session_id}/record/transcript")
def read_session_transcript_view(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """The Transcript tab's room view: speech, chat and questions as text by
    id, with authors' short names (`api_logic.get_session_transcript_view`).
    The classic app doesn't call it."""
    try:
        return get_session_transcript_view(session_id, organization_id=caller.read_scope)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": "Session transcript", "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Session transcript view failed for session_id=%s", session_id)
        raise HTTPException(
            status_code=500,
            detail={"resource": "Session transcript", "message": "The session's transcript could not be read."},
        ) from exc


QUESTION_DESK_RESOURCE = "Question desk"


def _question_desk_failed(session_id: str, exc: Exception, what: str) -> HTTPException:
    logger.exception("Question desk %s failed for session_id=%s", what, session_id)
    return HTTPException(
        status_code=500,
        detail={"resource": QUESTION_DESK_RESOURCE, "message": f"The session's questions could not be {what}."},
    )


@router.get("/api/sessions/{session_id}/record/questions")
def read_session_question_desk(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """The Follow-up tab's question desk (`api_logic.get_session_question_desk`):
    every audience question with its status and the host's saved reply,
    authors as short names that fail closed."""
    try:
        return get_session_question_desk(session_id, organization_id=caller.read_scope)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": QUESTION_DESK_RESOURCE, "message": str(exc)}) from exc
    except Exception as exc:
        raise _question_desk_failed(session_id, exc, "read") from exc


@router.put("/api/sessions/{session_id}/record/questions")
def update_session_question(
    session_id: SessionId, request: QuestionReplyRequest, caller: Caller = Depends(get_caller)
) -> Dict[str, Any]:
    """Mark one question answered (or not) with an optional reply, and
    return the desk (`api_logic.save_session_question_reply`). Scoped like
    every session route: another org's session looks missing."""
    connection = caller.connection if isinstance(caller.connection, dict) else {}
    try:
        return save_session_question_reply(
            session_id,
            request.question_id,
            answered=request.answered,
            reply=request.reply,
            organization_id=caller.read_scope,
            updated_by=str(connection.get("user_id") or "") or None,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": QUESTION_DESK_RESOURCE, "message": str(exc)}) from exc
    except QuestionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": QUESTION_DESK_RESOURCE, "message": str(exc)}) from exc
    except QuestionNotSavableError as exc:
        raise HTTPException(status_code=409, detail={"resource": QUESTION_DESK_RESOURCE, "message": str(exc)}) from exc
    except Exception as exc:
        raise _question_desk_failed(session_id, exc, "saved") from exc


@router.put("/api/sessions/{session_id}/record/questions/located")
def decide_located_answer(
    session_id: SessionId, request: LocatedDecisionRequest, caller: Caller = Depends(get_caller)
) -> Dict[str, Any]:
    """Confirm an answer located in the talk, or set it aside ("Not this"),
    and return the desk (`api_logic.decide_session_located_answer`). Scoped
    like every session route: another org's session looks missing."""
    connection = caller.connection if isinstance(caller.connection, dict) else {}
    try:
        return decide_session_located_answer(
            session_id,
            request.question_id,
            confirm=request.decision == "confirm",
            organization_id=caller.read_scope,
            updated_by=str(connection.get("user_id") or "") or None,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": QUESTION_DESK_RESOURCE, "message": str(exc)}) from exc
    except QuestionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": QUESTION_DESK_RESOURCE, "message": str(exc)}) from exc
    except (QuestionNotSavableError, NothingLocatedError) as exc:
        raise HTTPException(status_code=409, detail={"resource": QUESTION_DESK_RESOURCE, "message": str(exc)}) from exc
    except Exception as exc:
        raise _question_desk_failed(session_id, exc, "saved") from exc


_CSV_FILE_NAMES = {"open": "open-questions", "all": "questions", "answers": "questions-and-answers"}


@router.get("/api/sessions/{session_id}/record/questions.csv")
def download_open_questions_csv(
    session_id: SessionId,
    caller: Caller = Depends(get_caller),
    scope: Literal["open", "all", "answers"] = Query("open"),
) -> Response:
    """The desk's questions with who asked them, as a CSV download
    (`api_logic.get_session_open_questions_csv`): `scope` `open` (default),
    `all` (with each one's status) or `answers` (with its status and its
    answer, for a follow-up email). Name and company on every session, never
    an email. Private, never cached."""
    try:
        text = get_session_open_questions_csv(session_id, organization_id=caller.read_scope, scope=scope)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"resource": QUESTION_DESK_RESOURCE, "message": str(exc)}) from exc
    except Exception as exc:
        raise _question_desk_failed(session_id, exc, "exported") from exc
    return Response(
        # A BOM, so Excel reads accented names as UTF-8.
        content="﻿" + text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="stormiq-{_CSV_FILE_NAMES[scope]}-{session_id}.csv"',
            "Cache-Control": "private, no-store",
        },
    )


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
            local_dev=caller.local_dev,
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
            job_connection_id=caller.job_connection_id(),
            read_scope=caller.read_scope,
            organization_id=caller.org_id,
            local_dev=caller.local_dev,
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
            "Content-Disposition": f'attachment; filename="stormiq-transcript-{session_id}.json"',
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


PEOPLE_SYNC_RESOURCE = "People sync"


def _session_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"resource": PEOPLE_SYNC_RESOURCE, "message": "No cached session found."})


@router.get("/api/sessions/{session_id}/roster-status")
def get_roster_status(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """The v1 roster's header and the latest people sync (`people_sync.read_roster_status`),
    polled by the v1 Report while a sync runs. Never a person."""
    try:
        return read_roster_status(session_id, organization_id=caller.read_scope)
    except RosterSessionNotFound as exc:
        raise _session_not_found() from exc
    except Exception as exc:
        raise_http_error(PEOPLE_SYNC_RESOURCE, exc)
        raise


@router.post("/api/sessions/{session_id}/roster-sync")
def start_roster_sync(session_id: SessionId, caller: Caller = Depends(get_caller)) -> Dict[str, Any]:
    """Start reading the session's people from Livestorm in the worker
    (`people_sync.request_people_sync`): a retry after an error, or a refresh.
    Starts nothing for a session of another org, within the cooldown, or over
    the org's daily call cap (429)."""
    try:
        return request_people_sync(
            session_id,
            read_scope=caller.read_scope,
            caller_org_id=caller.org_id,
            job_connection_id=caller.job_connection_id(),
            local_dev=caller.local_dev,
        )
    except RosterSessionNotFound as exc:
        raise _session_not_found() from exc
    except PeopleSyncCapReached as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "resource": PEOPLE_SYNC_RESOURCE,
                "message": str(exc),
                "details": {"resource": PEOPLE_SYNC_RESOURCE, "dailyCallCap": exc.limit, "callsLast24h": exc.used},
            },
        ) from exc
    except PeopleSyncUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"resource": PEOPLE_SYNC_RESOURCE, "message": str(exc), "details": {"resource": PEOPLE_SYNC_RESOURCE}},
        ) from exc
    except Exception as exc:
        raise_http_error(PEOPLE_SYNC_RESOURCE, exc)
        raise
