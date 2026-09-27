"""HTTP middleware: the pre-parse body guard and the per-client rate limiter.

Both are transport concerns that run before any route, which is why they are
middleware rather than dependencies: the body guard has to answer *before* the
handler parses anything, or it is too late.
"""

import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.client_ip import resolve_client_ip
from backend.conversation import _rate_limit_store
from backend.uploads import MAX_AUDIO_SIZE

logger = logging.getLogger(__name__)


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
        if request.url.path.startswith("/api/"):
            client_ip = resolve_client_ip(request)
            now = time.time()
            timestamps = _rate_limit_store.get(client_ip, [])

            # Remove old entries outside the window
            timestamps = [t for t in timestamps if now - t < self.window]

            if len(timestamps) >= self.max_requests:
                logger.warning("Rate limit hit for IP: %s", client_ip)
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many requests. Please wait before trying again."
                    },
                )

            timestamps.append(now)
            _rate_limit_store[client_ip] = timestamps

        response = await call_next(request)
        return response
