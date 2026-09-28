"""The nginx reverse proxy must not be stricter than the application.

A config-consistency test, NOT an integration test: nginx is not runnable in
this suite, so nothing here proves nginx's behaviour. What it proves is the
narrower and sufficient claim -- that the number written in the config file is
at least the number the code enforces. The gap it exists to close is real and
silent: with no ``client_max_body_size`` at all, nginx applies its own 1 MB
default and rejects a legitimate 1-5 MB recording with a bare 413 before the
application's own 5 MB ceiling is ever consulted. Nothing about that failure
points at the cause, and nothing in the Python suite would notice, because the
application's ceiling is genuinely correct -- it is simply never reached.

Deriving the expectation from ``MAX_AUDIO_SIZE`` rather than hardcoding 5m is
the whole point. If someone raises or lowers the ceiling in the code, this test
fails until the config is moved with it, instead of the two layers drifting
apart silently and the drift only surfacing in production.
"""

import re
from pathlib import Path

import pytest

from backend.uploads import MAX_AUDIO_SIZE

REPO_ROOT = Path(__file__).resolve().parents[1]
NGINX_CONF = REPO_ROOT / "nginx" / "interview.conf"

#: nginx's own default when the directive is absent entirely. Worth naming so
#: the failure message can say what the value is being compared against.
NGINX_DEFAULT_MAX_BODY = 1024 * 1024

_SIZE_RE = re.compile(r"client_max_body_size\s+(\d+)([kKmMgG]?)\s*;")
_MULTIPLIERS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def _strip_comments(source: str) -> str:
    """Drop ``#`` comments, so an example in prose cannot be read as a directive."""
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )



def _braced_block(source: str, header_re: str) -> str:
    """The body of the first ``location`` whose header matches ``header_re``.

    Brace-matched from the block's own opening brace, so nested blocks (e.g. an
    ``if`` inside ``location``) are skipped rather than ending the scan early.
    """
    match = re.search(header_re, source)
    if match is None:
        raise AssertionError(f"no block matching {header_re!r} in {NGINX_CONF}")
    # Search from match.end() - 1, not match.end(): header_re consumes the
    # opening brace, so searching forward from the match start would skip past
    # it and return some later block entirely.
    start = source.index("{", match.end() - 1)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces in the block matching {header_re!r}")


def _directive_bytes(block: str) -> int | None:
    """The ``client_max_body_size`` value in bytes, or None if unset.

    Returns None rather than asserting, so a caller can report the *absence* of
    the directive as the distinct failure it is (nginx's implicit 1 MB) instead
    of a confusing parse error.
    """
    match = _SIZE_RE.search(_strip_comments(block))
    if match is None:
        return None
    return int(match.group(1)) * _MULTIPLIERS[match.group(2).lower()]


@pytest.fixture(scope="module")
def conf() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


class TestTheParserItself:
    """Guard the guard.

    This parser was first written with an unanchored-per-line `^` and no
    re.MULTILINE, so it could never match a directive inside a block starting
    with `{`. It returned None for the real config *and* for a correct one, and
    the test it powered went on failing after the config was fixed. A harness
    that cannot read the thing it is asserting about is worse than no harness:
    it looks like a standing failure and teaches everyone to ignore it.

    So the parser is asserted against synthetic input first. If these fail, no
    conclusion may be drawn from the tests below.
    """

    def test_reads_a_directive_from_a_realistic_block(self):
        block = "{\n    proxy_pass http://127.0.0.1:8000;\n    client_max_body_size 5m;\n}"
        assert _directive_bytes(block) == 5 * 1024**2

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("{ client_max_body_size 5m; }", 5 * 1024**2),
            ("{ client_max_body_size 1M; }", 1024**2),
            ("{ client_max_body_size 512k; }", 512 * 1024),
            ("{ client_max_body_size 1048576; }", 1048576),
        ],
    )
    def test_parses_every_size_form_nginx_accepts(self, text, expected):
        assert _directive_bytes(text) == expected

    def test_absent_directive_reads_as_none_not_as_zero(self):
        assert _directive_bytes("{ proxy_pass http://127.0.0.1:8000; }") is None

    def test_a_commented_out_directive_is_not_counted(self):
        block = "{\n    # client_max_body_size 5m;\n    proxy_pass http://x;\n}"
        assert _directive_bytes(block) is None

    def test_finds_the_named_location_and_not_a_merely_mentioned_path(self):
        conf = NGINX_CONF.read_text(encoding="utf-8")
        block = _braced_block(conf, r"location\s+/audio/\s*\{")
        assert "alias" in block, "the extractor returned the wrong block"


class TestUploadCeilingAgreesAcrossLayers:
    def test_api_location_sets_the_directive_at_all(self, conf):
        """Absent is the actual bug, and it is not visible in the file.

        A reader skimming this config sees nothing wrong: there is no
        ``client_max_body_size`` line to look stale. The failure only exists at
        runtime, as nginx's default. So the directive's presence is asserted
        separately from its value.
        """
        api = _braced_block(conf, r"location\s+/api/\s*\{")
        assert _directive_bytes(api) is not None, (
            "location /api/ sets no client_max_body_size, so nginx silently "
            f"applies its {NGINX_DEFAULT_MAX_BODY}-byte default and rejects "
            "any recording above it before the application sees the request"
        )

    def test_api_ceiling_is_at_least_the_application_ceiling(self, conf):
        api = _braced_block(conf, r"location\s+/api/\s*\{")
        declared = _directive_bytes(api)

        assert declared is not None, "client_max_body_size is missing from /api/"
        assert declared >= MAX_AUDIO_SIZE, (
            f"nginx allows {declared} bytes but the application accepts "
            f"{MAX_AUDIO_SIZE} (MAX_AUDIO_SIZE). The outer layer wins, so every "
            f"upload between {declared} and {MAX_AUDIO_SIZE} bytes is rejected "
            "by nginx with a 413 the application cannot explain."
        )

    def test_exceeding_the_application_ceiling_is_nginx_s_job_to_allow(self, conf):
        """The ceiling is not the ceiling: it is a gate that must not pre-empt.

        Matching exactly would also be correct, but asserting the floor keeps a
        future, looser nginx from being a silent hole in the limit while
        documenting which layer is authoritative.
        """
        api = _braced_block(conf, r"location\s+/api/\s*\{")
        declared = _directive_bytes(api)

        assert declared is not None
        assert declared > NGINX_DEFAULT_MAX_BODY, (
            f"nginx allows {declared} bytes, which is below its own 1 MB "
            "default -- the upload ceiling has been tightened by accident"
        )
