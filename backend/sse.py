"""Server-Sent Event formatting, and the keepalive that keeps the wire alive.

The envelope is defined in exactly one function so that the wire format has one
owner. Every event the backend emits travels through ``sse_format``, which is
also why ``tests/test_sse_contract.py`` can scan for a single call shape: a new
event type cannot be introduced without appearing in that scan.

The keepalive is the other half of that module's job, and it is deliberately NOT
an ``sse_format`` call. A keepalive is a comment frame, so it carries no event
name, adds nothing to the contract above, and requires no frontend branch --
which is the property that makes it safe to emit during a stall, because a stall
is exactly when the client can least afford a new event type.

``tests/test_sse_keepalive.py`` owns the reasoning and the two-sided guard
against nginx reverting one half of the fix while the other stays.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

#: Seconds the stream may stay silent before a keepalive comment frame is
#: emitted. Chosen against two ceilings, both of which this repository owns:
#:
#: * nginx's ``proxy_read_timeout`` in ``nginx/interview.conf`` (300s), which
#:   must comfortably outlive several of these -- asserted by
#:   ``test_the_read_timeout_outlives_several_keepalives``;
#: * nginx's own 60s default, which the keepalive must stay under so that the
#:   frame prevents the stock timeout too, and not only the configured one.
#:
#: It is NOT an operator-tunable. It is a protocol timing, and the two values it
#: has to agree with are both in the repository, so the test can hold them
#: together instead of asking a human to remember.
SSE_KEEPALIVE_INTERVAL_SECONDS = 15.0


def sse_format(event: str, data: dict) -> str:
    """Format as Server-Sent Event data line.

    Produces::

        data: {"event": "<event>", "data": <json>}\n\n
    """
    payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


def sse_keepalive() -> str:
    """A comment-only SSE frame: ``": keepalive\\n\\n"``.

    Per the SSE grammar a line beginning with ``:`` is a comment and is ignored
    by ``EventSource`` and by every hand-rolled reader -- including this
    repository's, which skips any line that does not start with ``data: ``
    (``frontend/app.js:2579``). So the frame costs 13 bytes, keeps an idle
    connection warm, and requires no client to know it exists.
    """
    return ": keepalive\n\n"


async def with_keepalive(
    stream: AsyncIterator[str], interval: float | None = None
) -> AsyncIterator[str]:
    """Yield ``stream``'s chunks, emitting a keepalive whenever it stalls.

    This is the only place that knows a silence is a problem, and it is the only
    place that can fix it. The alternative -- a keepalive at each known-slow
    await -- has to be repeated at every one of them and silently misses the
    next one added later, which is how a guard decays. Here the rule is stated
    once: if nothing has been produced for ``interval`` seconds, say something.

    The inner iterator is pulled on its own task, because a timeout on
    ``__anext__`` is the only way to distinguish "slow" from "finished" without
    a second source of truth. That task is created once and kept across
    iterations, so a slow producer is never cancelled and restarted -- which
    would reset the clock and defeat the interval entirely.

    ``interval`` defaults are resolved from the module constant *inside* the
    body, not as a default argument. Bound at ``def`` time it would be frozen
    for the life of the process: untunable and unpatchable, so no test could
    shorten it and every behavioural test here would need real 15-second sleeps.
    """
    if interval is None:
        interval = SSE_KEEPALIVE_INTERVAL_SECONDS

    iterator = stream.__aiter__()
    loop = asyncio.get_running_loop()
    pending: asyncio.Task | None = None
    deadline = loop.time() + interval

    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            timeout = max(0.0, deadline - loop.time())
            finished, _ = await asyncio.wait({pending}, timeout=timeout)

            if not finished:
                yield sse_keepalive()
                deadline = loop.time() + interval
                continue

            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            deadline = loop.time() + interval
            yield chunk
    finally:
        # Every exit converges here: the stream ended, the producer raised, the
        # consumer walked away, or the task was cancelled. The two cleanups are
        # not interchangeable.
        if pending is not None:
            # A `__anext__` in flight when the wrapper is torn down. Cancelling
            # it throws into the producer at its current await, which is what
            # runs a generator's `finally` from the inside.
            pending.cancel()
        # A wrapper closed while suspended at one of its OWN yields has no
        # `__anext__` in flight, and the producer is then suspended at its
        # `yield` with nothing to resume it -- so its `finally` never runs. For
        # `build_stream` that finally is not optional: it reaps the turn's
        # in-flight TTS tasks and unlinks the staged upload. Without this call
        # every client that disconnects mid-turn leaks a file and a synthesis
        # task, which is the exact failure the inner module documents at
        # streaming.py:722-789.
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except asyncio.CancelledError:
                # This wrapper is itself being cancelled, so the loop will not
                # run the reap. The cancel above is already delivered; the
                # task's death is the only thing that could report otherwise.
                logger.debug(
                    "Stream wrapper torn down mid-teardown with the producer's "
                    "close outstanding",
                    exc_info=True,
                )
            except Exception:
                logger.debug(
                    "Closing the wrapped stream raised; the wrapper's exit is "
                    "already decided",
                    exc_info=True,
                )
