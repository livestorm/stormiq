"""Serve the built Vue app: hashed assets with long-lived caching, everything else falls back to index.html."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()


FRONTEND_DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


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

class ImmutableStaticFiles(StaticFiles):
    """Static files whose names change whenever their content does.

    Vite puts a content hash in every file name under /assets, so browsers
    may keep them for a year without revalidating; a deploy ships new names.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


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


@router.get("/{full_path:path}")
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
