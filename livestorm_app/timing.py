"""Per-request timing for API routes.

Logs one line per `/api/*` request to stderr:

    timing method=GET route=/api/sessions/{session_id} status=200 ms=84.2 bytes=1834402

`route` is the route template (never a session id or query string), so lines
aggregate per endpoint in Render's log search. `bytes` is the uncompressed
body size; Render's edge compresses responses on the way to the browser.
Health checks are skipped: Render polls them every few seconds.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict, MutableMapping

logger = logging.getLogger("stormiq.timing")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

SKIPPED_PATHS = frozenset({"/api/health"})


class RequestTimingMiddleware:
    """Pure ASGI middleware (no body buffering, safe for streaming responses)."""

    def __init__(self, app: ASGIApp, path_prefix: str = "/api/") -> None:
        self.app = app
        self.path_prefix = path_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path") or "")
        if scope.get("type") != "http" or not path.startswith(self.path_prefix) or path in SKIPPED_PATHS:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        state: Dict[str, int] = {"status": 500, "bytes": 0}

        async def send_and_measure(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = int(message["status"])
            elif message["type"] == "http.response.body":
                state["bytes"] += len(message.get("body") or b"")
            await send(message)

        try:
            await self.app(scope, receive, send_and_measure)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            logger.info(
                "timing method=%s route=%s status=%d ms=%.1f bytes=%d",
                scope.get("method"),
                template,
                state["status"],
                (time.perf_counter() - started) * 1000,
                state["bytes"],
            )
