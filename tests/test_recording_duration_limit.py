"""One recording limit, published by the server and obeyed by the page.

THE DEFECT
----------
``MAX_AUDIO_DURATION`` was read by nobody. Its one appearance in the code was
its own assignment in ``backend/config.py``; the only real ceiling on a recording
was ``MAX_AUDIO_SIZE`` -- 5 MiB, which at the recorder's own
``audioBitsPerSecond: 128000`` is 5.4 minutes. So the page had invented its own
number instead (``MAX_RECORDING_MS = 240000``), and nothing anywhere tied the
two together.

And the ceiling that did exist was not reachable: in a room with background
noise the VAD's absolute ``RMS_THRESHOLD`` never reads as silence, so the turn
never ends, the blob grows to 5 MiB, nginx answers 413, 413 is not retryable,
and the candidate loses the interview and the whole recording. That chain is
covered on the page side in ``tests/frontend/recording_duration.test.mjs``;
this module is the half that runs on the server.

WHAT IS ASSERTED HERE
---------------------
1.  The limit has one source of truth. ``config.MAX_AUDIO_DURATION`` is
    published by ``GET /api/config``, and the number the page falls back to when
    that request fails is derived from it rather than being a second constant
    that can drift.
2.  The duration the server names is the duration it enforces. A ceiling of 60 s
    is only honest if 60 is what the page cuts at, and the byte ceiling has to
    sit *above* the duration ceiling -- otherwise the byte gate fires first, the
    candidate gets a 413 instead of a clean cut, and the whole exercise was
    pointless.
3.  The measured duration is used, not thrown away. ``faster-whisper`` returns
    ``info.duration`` from the decode the transcription already paid for, so
    reading it costs nothing and no second decode is introduced.
4.  The messages a candidate can read name the real limit. The 422 detail used
    to claim thirty seconds; it now names the configured duration, and it says
    which check rejected the body.
5.  The reverse proxy stops arguing from a ceiling that does not exist. Its
    timeout comment asserted that "no code enforces a recording duration" --
    true when this suite was written, and a fresh lie the moment it stopped
    being true.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main as main_mod
from backend.config import Config, config
from backend.services.stt import STTService
from backend.uploads import MAX_AUDIO_SIZE, stage_upload

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "frontend" / "app.js"
NGINX_CONF = REPO_ROOT / "nginx" / "interview.conf"

#: The recorder's own setting, in ``frontend/app.js``. Parsed rather than
#: restated, because the coherence claim below is only worth making if it is
#: made against the number the page actually asks for: restating it here would
#: let the two drift and the test would keep passing.
_AUDIO_BITS_PER_SECOND = re.compile(
    r"audioBitsPerSecond\s*:\s*(\d+)", re.MULTILINE
)
_RECORDER_BITRATE = 128_000


def _recorder_bytes_per_second() -> int:
    """The bytes a second of the page's own recording occupies."""
    match = _AUDIO_BITS_PER_SECOND.search(APP_JS.read_text(encoding="utf-8"))
    assert match is not None, (
        "frontend/app.js no longer asks MediaRecorder for a bitrate, so the "
        "relationship between the duration ceiling and the byte ceiling can no "
        "longer be checked. The byte ceiling is the one that fires first, so "
        "losing the bitrate silently removes the only warning that the two "
        "have stopped agreeing."
    )
    return int(match.group(1)) // 8


def _page_fallback_ms() -> int:
    """``MAX_RECORDING_MS`` as shipped: the cap the page uses with no server.

    Read out of the source rather than imported, because the claim under test
    is precisely that this number and ``MAX_AUDIO_DURATION`` are the same
    limit. Importing the JavaScript would not be possible, and a Python copy
    would be a second source of truth for the very thing being tested.
    """
    source = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"const\s+MAX_RECORDING_MS\s*=\s*(\d+)", source)
    assert match is not None, (
        "frontend/app.js declares no MAX_RECORDING_MS. Without a page-side "
        "fallback a failed /api/config would leave the recording unbounded, "
        "which is the failure this whole limit exists to prevent."
    )
    return int(match.group(1))


# ── The one number ───────────────────────────────────────────────────────────


class TestTheLimitHasOneSourceOfTruth:
    def test_the_default_is_sixty_seconds(self):
        """60, not 30.

        The byte ceiling permits 5.4 minutes at the recorder's own bitrate, so
        30 s was not a considered limit -- it was a number nobody had checked
        against anything. And with the VAD working again the duration ceiling
        is a safety net rather than the mechanism, so the honest default is the
        one a real interview answer fits inside.
        """
        with pytest.MonkeyPatch.context() as patch:
            patch.delenv("MAX_AUDIO_DURATION", raising=False)
            assert Config().MAX_AUDIO_DURATION == 60

    def test_the_config_endpoint_publishes_it(self, isolated_write_targets):
        """The page reads it from the endpoint it already calls on load."""
        client = TestClient(main_mod.app)
        payload = client.get("/api/config").json()

        assert "max_audio_duration" in payload, (
            "GET /api/config does not publish the recording duration limit, so "
            "the page has to invent one and the two drift apart. The endpoint is "
            "already fetched at load; this is the field it was missing."
        )
        assert payload["max_audio_duration"] == config.MAX_AUDIO_DURATION

    def test_the_page_fallback_is_this_limit_and_not_a_second_one(self):
        """No second constant: the fallback is the same number.

        The page must be able to bound a recording before its first
        ``/api/config`` has answered -- ``init()`` does not await the fetch, and
        the very first recording is the one that needs the cap. So a fallback
        is unavoidable. What is not acceptable is a fallback that disagrees
        with the limit the server advertises: then the same recording is
        refused in one deployment and cut in another, and neither number is the
        one an operator configured.
        """
        assert _page_fallback_ms() == config.MAX_AUDIO_DURATION * 1000, (
            f"frontend/app.js falls back to {_page_fallback_ms()} ms while the "
            f"server advertises {config.MAX_AUDIO_DURATION * 1000} ms "
            "(MAX_AUDIO_DURATION). Two numbers for one limit means the page "
            "and the server disagree about the same recording."
        )

    def test_the_byte_ceiling_sits_above_the_duration_ceiling(self):
        """The duration limit has to be reachable, or the 413 still comes first.

        The byte gate is the one that runs before anything is parsed, so if the
        duration ceiling is above the byte-implied duration then a candidate
        who talks past the advertised limit gets a bare 413 and loses the turn,
        which is the defect this limit was added to end.
        """
        bytes_per_second = _recorder_bytes_per_second()
        duration_bytes = config.MAX_AUDIO_DURATION * bytes_per_second

        assert duration_bytes <= MAX_AUDIO_SIZE, (
            f"MAX_AUDIO_DURATION={config.MAX_AUDIO_DURATION} s is "
            f"{duration_bytes} bytes at the recorder's own "
            f"{bytes_per_second} B/s, which is above MAX_AUDIO_SIZE "
            f"({MAX_AUDIO_SIZE} bytes). The page would let the recording run "
            "past the limit the server advertises and nginx would answer 413 "
            f"at about {MAX_AUDIO_SIZE / bytes_per_second:.0f} s, which is the "
            "exact death chain this limit replaces."
        )


# ── The message a candidate actually reads ───────────────────────────────────


def _oversize_detail() -> str:
    """The 422 detail for a body over ``MAX_AUDIO_SIZE``."""
    import io

    from fastapi import HTTPException
    from starlette.datastructures import Headers, UploadFile

    upload = UploadFile(
        file=io.BytesIO(b"x" * (MAX_AUDIO_SIZE + 1)),
        filename="recording.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )
    with pytest.raises(HTTPException) as caught:
        import asyncio

        asyncio.run(stage_upload("conv-1", upload))
    assert caught.value.status_code == 422
    return str(caught.value.detail)


class TestTheErrorMessageNamesTheRealLimit:
    def test_it_names_the_configured_duration(self, monkeypatch):
        """The number in the message is the configured one, whatever it is.

        A message with the duration hardcoded beside a configurable constant is
        the same defect as the original one, one layer over: the constant moves
        and the prose does not.
        """
        monkeypatch.setattr(config, "MAX_AUDIO_DURATION", 90)
        detail = _oversize_detail()
        assert "90" in detail, (
            f"the message does not name the configured 90 s limit: {detail!r}. "
            "The number has to come from MAX_AUDIO_DURATION or it is another "
            "constant nobody can move."
        )

    def test_it_stops_claiming_thirty_seconds(self):
        """The default is 60, so a message claiming 30 is a new lie."""
        detail = _oversize_detail()
        assert "30 second" not in detail, (
            f"the message still claims a 30-second ceiling: {detail!r}. The "
            "limit is 60 s and is enforced, so the number must be the one that "
            "is enforced."
        )

    def test_it_says_which_check_rejected_the_body(self):
        """Bytes rejected the body. The message must not imply otherwise.

        ``stage_upload`` has no decoded audio at this point -- the container
        header has not been parsed -- so any claim about the recording's length
        in seconds is an inference it cannot support.
        """
        detail = _oversize_detail()
        assert "size" in detail.lower(), (
            f"the message does not say the rejection was on size: {detail!r}. "
            "This check counts bytes; a candidate reading it has to know which "
            "limit they hit."
        )
        assert str(MAX_AUDIO_SIZE) in detail, (
            f"the message does not name the byte ceiling it applied: {detail!r}"
        )


# ── The duration faster-whisper already measured ─────────────────────────────


def _loaded_service(transcribe_result) -> STTService:
    """A service whose model is already loaded, with one recorded call."""
    from unittest.mock import MagicMock

    service = STTService()
    model = MagicMock()
    model.transcribe.return_value = transcribe_result
    service._model = model
    service.model_transcribe = model.transcribe
    return service


class _Segment:
    def __init__(self, text: str) -> None:
        self.text = text


class _Info:
    def __init__(self, duration: float) -> None:
        self.duration = duration


class TestTheMeasuredDurationIsUsedRatherThanDecodedTwice:
    def test_it_reports_the_duration_the_decode_already_returned(self, tmp_path):
        service = _loaded_service(([_Segment(" hola ")], _Info(2.5)))
        audio = tmp_path / "in.webm"
        audio.write_bytes(b"audio")

        measured = service.transcribe_measured(audio)

        assert measured.text == "hola"
        assert measured.duration_seconds == 2.5
        service.model_transcribe.assert_called_once()

    def test_transcribe_still_returns_plain_text(self, tmp_path):
        """The existing call sites keep working; nothing has to change shape."""
        service = _loaded_service(([_Segment(" hola ")], _Info(2.5)))
        audio = tmp_path / "in.webm"
        audio.write_bytes(b"audio")

        assert service.transcribe(audio) == "hola"

    def test_a_recording_past_the_limit_is_reported_against_the_real_one(
        self, tmp_path, caplog
    ):
        """The knob is read, so moving it changes what the server says.

        ``MAX_AUDIO_DURATION`` used to be a number nothing consumed. If the
        measured duration is not compared against it anywhere, the same defect
        is still here wearing a different hat.
        """
        service = _loaded_service(([_Segment(" hola ")], _Info(312.4)))
        service.max_duration_seconds = 60
        audio = tmp_path / "in.webm"
        audio.write_bytes(b"audio")

        with caplog.at_level("WARNING"):
            measured = service.transcribe_measured(audio)

        assert measured.duration_seconds == 312.4
        warnings = " ".join(record.getMessage() for record in caplog.records)
        assert "312.4" in warnings and "60" in warnings, (
            "a recording over the advertised limit was transcribed without a "
            f"word about it. The log reads: {warnings!r}. Both numbers belong "
            "in it -- the measured one and the one that was exceeded."
        )

    def test_a_recording_inside_the_limit_says_nothing(self, tmp_path, caplog):
        service = _loaded_service(([_Segment(" hola ")], _Info(12.0)))
        service.max_duration_seconds = 60
        audio = tmp_path / "in.webm"
        audio.write_bytes(b"audio")

        with caplog.at_level("WARNING"):
            service.transcribe_measured(audio)

        assert not [r for r in caplog.records if r.levelname == "WARNING"], (
            "an ordinary 12-second answer produced a warning: the limit is "
            "supposed to be a safety net, not an event."
        )

    def test_the_wired_service_actually_carries_the_limit(self):
        """The composition root passes it, so the default is not a test fiction.

        A limit set only by a test's constructor argument proves nothing about
        the deployed process. This is the assertion that the configured value
        reaches the only component that can measure a duration.
        """
        assert main_mod.stt_service.max_duration_seconds == config.MAX_AUDIO_DURATION


# ── The reverse proxy's own arithmetic ───────────────────────────────────────


@pytest.fixture(scope="module")
def conf() -> str:
    """The reverse proxy's shipped configuration."""
    return NGINX_CONF.read_text(encoding="utf-8")


class TestTheProxyNoLongerArguesFromACeilingThatIsGone:
    def test_it_does_not_deny_that_a_duration_limit_exists(self, conf):
        """The paragraph that called the ceiling fictional is now the fiction."""
        offenders = [
            line.strip()
            for line in conf.splitlines()
            if re.search(r"no code enforces a recording duration", line, re.I)
        ]
        assert not offenders, (
            "nginx/interview.conf still says no code enforces a recording "
            "duration. One does now -- MAX_AUDIO_DURATION, published by "
            "/api/config and cut at by the page -- so the comment has become "
            "the same false claim it was written to remove:\n  "
            + "\n  ".join(offenders)
        )

    def test_it_names_the_limit_it_actually_has_to_outlast(self, conf):
        """The timeout is argued against a real number, not a remembered one."""
        assert "MAX_AUDIO_DURATION" in conf, (
            "nginx/interview.conf never names the duration limit whose bound it "
            "claims to clear. Without the name the claim is unfalsifiable again "
            "-- which is how the thirty-second version survived as long as it "
            "did."
        )

    def test_the_read_timeout_still_clears_the_duration_limit(self, conf):
        """If the limit moves, the ceiling above it has to move with it."""
        match = re.search(r"proxy_read_timeout\s+(\d+)s", conf)
        assert match is not None, "proxy_read_timeout is missing from /api/"
        assert int(match.group(1)) > config.MAX_AUDIO_DURATION, (
            f"proxy_read_timeout is {match.group(1)}s and the recording limit "
            f"is {config.MAX_AUDIO_DURATION}s. A recording at the limit has to "
            "be transcribed and answered inside that window, so the timeout "
            "cannot be at or below it."
        )

    def test_the_keepalive_argument_survives(self, conf):
        """The real justification is untouched; only the false premise moved."""
        assert "SSE_KEEPALIVE" in conf
