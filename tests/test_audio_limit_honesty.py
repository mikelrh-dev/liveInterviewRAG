"""An error message must not describe a ceiling the server does not enforce.

THE DEFECT
----------
``uploads.stage_upload`` rejects a body over ``MAX_AUDIO_SIZE`` -- 5 MiB -- with

    "Audio too long (max 30 seconds)"

which is false twice over. There is no 30-second limit anywhere: 30 comes from
``config.MAX_AUDIO_DURATION``, a setting nobody reads, and the check that fires
is a byte count. At the recorder's own ``audioBitsPerSecond: 128000`` a 5 MiB
ceiling is roughly 5.4 minutes of audio, so the message told the candidate to
shorten a recording that had thirty seconds of slack left in it.

The same fiction had spread into the reverse proxy.
``nginx/interview.conf`` justified its 300s ``proxy_read_timeout`` on "a 30s
audio ceiling": with no such ceiling, the arithmetic that motivated the value
was never true, and a reader checking the reasoning would find nothing to check.

WHY IT MATTERS MORE THAN A WORDING NIT
--------------------------------------
This message is the candidate's only account of why their turn failed, and it
is what they will act on. Telling someone to shorten a five-minute answer by a
factor of ten is worse than telling them nothing.
"""

import io
import re

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from backend.uploads import MAX_AUDIO_SIZE, stage_upload

NGINX_CONF = "nginx/interview.conf"


def _upload(payload: bytes, content_type: str = "audio/webm") -> UploadFile:
    # A real UploadFile: `content_type` is a property read off the headers, not
    # a constructor argument, so a hand-rolled stand-in here would have tested
    # the stand-in instead of the intake path.
    return UploadFile(
        file=io.BytesIO(payload),
        filename="recording.webm",
        headers=Headers({"content-type": content_type}),
    )


async def _oversize_detail() -> str:
    """The detail of the rejection for a body over the ceiling.

    Asserts the rejection itself, so a test cannot pass because something
    unrelated raised first and its message happened to satisfy the assertion.
    """
    with pytest.raises(HTTPException) as caught:
        await stage_upload("conv-1", _upload(b"x" * (MAX_AUDIO_SIZE + 1)))
    assert caught.value.status_code == 422
    return str(caught.value.detail)


class TestTheOversizeMessageDescribesTheRealLimit:
    async def test_it_does_not_claim_thirty_seconds(self):
        detail = await _oversize_detail()
        assert "30 second" not in detail, (
            f"the message claims a 30-second ceiling that nothing enforces: "
            f"{detail!r}. The check is a byte count, so the message has to be a "
            "byte count."
        )

    async def test_it_names_the_size_it_actually_enforces(self):
        detail = await _oversize_detail()
        assert str(MAX_AUDIO_SIZE) in detail or "5 MB" in detail or "5 MiB" in detail, (
            f"the message must name the limit that actually rejected the body "
            f"(MAX_AUDIO_SIZE = {MAX_AUDIO_SIZE}), got {detail!r}"
        )

    async def test_it_still_rejects(self):
        """The message was wrong; the rejection was not. Keep the rejection."""
        assert await _oversize_detail()


class TestTheProxyDoesNotArgueFromAFictionalCeiling:
    """nginx's timeout must be justified by something that is true."""

    def _conf(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / NGINX_CONF).read_text(
            encoding="utf-8"
        )

    def test_no_comment_claims_a_thirty_second_audio_ceiling(self):
        conf = self._conf()
        offenders = [
            line.strip()
            for line in conf.splitlines()
            if re.search(r"30s?\b.{0,40}audio|audio.{0,40}30s?\b", line, re.I)
        ]
        assert not offenders, (
            "the reverse proxy still justifies its timeout on a 30s audio "
            "ceiling that no code enforces:\n  " + "\n  ".join(offenders)
        )

    def test_the_read_timeout_is_still_justified_by_the_keepalive(self):
        """The real argument survives: SSE keepalives bound the stall."""
        assert "SSE_KEEPALIVE" in self._conf(), (
            "the timeout comment no longer names the keepalive it actually "
            "depends on, so the value has lost its only justification"
        )
