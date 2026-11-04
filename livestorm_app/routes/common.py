"""Shared pieces of the HTTP layer: the session id path type, error responses, PDF responses, cookie flags."""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any, Dict

from fastapi import HTTPException, Request, Response
from fastapi import Path as ApiPath

from livestorm_app.api_logic import (
    SessionNotFoundError,
    format_service_error,
)
from livestorm_app.auth import (
    running_on_render,
)

logger = logging.getLogger(__name__)

# Livestorm session ids are UUIDs. Accept any short URL-safe id so an
# upstream format change doesn't break lookups, but reject anything
# path-like or oversized before it reaches the DB or a filename.
SessionId = Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]


def raise_http_error(resource_label: str, exc: Exception, status_code: int = 400) -> None:
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


def pdf_response(result: Dict[str, Any]) -> Response:
    filename = _UNSAFE_FILENAME_CHARS.sub("-", str(result.get("filename") or "")).strip("-.") or "stormiq-export.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=result["content"], media_type="application/pdf", headers=headers)


def cookie_secure(request: Request) -> bool:
    """Secure cookies everywhere except plain-HTTP local development.

    Render terminates TLS at its proxy, so the app sees plain HTTP; the
    RENDER env var marks production regardless of scheme.
    """
    if running_on_render():
        return True
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded_proto == "https" or request.url.scheme == "https"
