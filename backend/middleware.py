"""HTTP middleware: the pre-parse body guard and the per-client rate limiter.

Both are transport concerns that run before any route, which is why they are
middleware rather than dependencies: the body guard has to answer *before* the
handler parses anything, or it is too late.
"""

import logging
import math
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.client_ip import resolve_client_ip
from backend.conversation import _rate_limit_store
from backend.uploads import MAX_AUDIO_SIZE

logger = logging.getLogger(__name__)


# ─── What the rate limit is for ───────────────────────────────────────────────
#
# The budget exists to bound the work a candidate's *questions* can cause, so it
# is spent on questions and on nothing else. Two reads do not qualify:
#
#   GET /api/health                             a status dot, polled every 60 s
#   GET /api/conversation/{id}/context          the evidence panel for a turn
#   GET /api/config                             the sidebar's model list
#
# The turn is two requests wide, not one: ``frontend/app.js`` POSTs the turn and
# then calls ``fetchContext`` for the same turn. Charging both meant a turn cost
# two units, so at ``RATE_LIMIT_PER_MINUTE=10`` the sustainable rate was 5 turns
# per 60 s -- one turn every 12 s -- and a candidate who answers in 8 s started
# collecting 429s in the middle of a real interview.
#
# THE EXEMPTION IS A NAMED CONSTANT, NOT AN `if` IN THE HANDLER
# ----------------------------------------------------------
# It is written out here rather than inline so that putting a route back under
# the limiter is a deliberate edit to a list someone will read, instead of a
# condition discovered in a dispatch method. The prefix and suffix forms are
# separate on purpose: ``/api/conversation/`` is a PREFIX of the turn endpoints
# as well, so exempting that prefix would have freed the two most expensive
# routes in the application and left the limiter guarding only health checks.
RATE_LIMIT_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/health",
    "/api/config",
)

RATE_LIMIT_EXEMPT_SUFFIXES: tuple[str, ...] = (
    # /api/conversation/{id}/context -- matched on the tail, so the sibling
    # routes that share the prefix (POST .../message, .../message/stream) and
    # the conversation-creation POST stay charged.
    "/context",
)


def is_rate_limit_exempt(path: str) -> bool:
    """Whether this path is a read the interview's budget should not pay for."""
    return path.startswith(RATE_LIMIT_EXEMPT_PREFIXES) or path.endswith(
        RATE_LIMIT_EXEMPT_SUFFIXES
    )


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject oversized request bodies before they are parsed.

    Reading the body first and checking its length afterwards is too late: by
    that point the whole upload already sits in RAM, so a handful of large
    requests can exhaust memory. This middleware looks only at the declared
    ``Content-Length`` and short-circuits with 413 before any handler runs.

    A request with no ``Content-Length`` (chunked transfer encoding) is passed
    through untouched — buffering the stream here would defeat the purpose of
    the guard, and the in-route check (``uploads.stage_upload``) still catches
    those bodies afterwards.
    """

    def __init__(self, app, max_size: int = MAX_AUDIO_SIZE):
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next):
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except (TypeError, ValueError):
                # Malformed header: do not guess, let the request through.
                content_length = None
            if content_length is not None and content_length > self.max_size:
                logger.warning(
                    "Rejected oversized body: %d bytes declared (max %d) on %s",
                    content_length,
                    self.max_size,
                    request.url.path,
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            f"Request body too large (max {self.max_size} bytes)"
                        )
                    },
                )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter: N requests per minute per client.

    The bucket key is produced by :func:`resolve_client_ip`, so behind a
    loopback reverse proxy each real visitor gets their own bucket instead of
    the whole internet sharing the proxy's bucket.
    """

    def __init__(self, app, max_requests: int = 10, window: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window

    async def dispatch(self, request: Request, call_next):
        # Only rate-limit API endpoints
        if request.url.path.startswith("/api/") and not is_rate_limit_exempt(
            request.url.path
        ):
            client_ip = resolve_client_ip(request)
            now = time.time()
            timestamps = _rate_limit_store.get(client_ip, [])

            # Remove old entries outside the window
            timestamps = [t for t in timestamps if now - t < self.window]

            if len(timestamps) >= self.max_requests:
                # Seconds until the OLDEST surviving entry leaves the window.
                # That is when a slot actually frees: the list is ordered oldest
                # first, so this is the soonest moment the next request can be
                # served, and it is the one fact the client cannot compute for
                # itself. Rounded up, because truncating 0.4 s to 0 invites a
                # retry that is guaranteed to be rejected again.
                retry_after = max(
                    1, math.ceil(timestamps[0] + self.window - now)
                )
                logger.warning(
                    "Rate limit hit for IP: %s (%d in window, retry in %ds)",
                    client_ip,
                    len(timestamps),
                    retry_after,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many requests. Please wait before trying again."
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            timestamps.append(now)
            _rate_limit_store[client_ip] = timestamps

        response = await call_next(request)
        return response
