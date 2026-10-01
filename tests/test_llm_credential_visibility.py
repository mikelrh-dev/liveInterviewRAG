"""A clone with no LLM key must be told so before its first turn, not after.

THE PROBLEM
-----------
Neither provider key is validated at startup: ``backend/config.py:55-56`` both
default to ``""``. So a fresh clone boots cleanly, the page loads, the reader
presses the microphone, and the FIRST turn fails with whatever the provider
happens to say -- a quota message, a 400, a model name -- none of which names
the one fact that would have explained it. The instruction to configure a key
is real and it is in the README, 200 lines above the button.

That is not a validation bug. Validating a credential at boot is a design
decision with a real cost, and the honest cheap alternative is to SHOW the
state. So this file pins that the state is shown, and pins the boundary that
makes showing it safe.

WHAT IS AND IS NOT PINNED
-------------------------
Pinned:

  * ``GET /api/config`` publishes ``llm_configured``.
  * It is a boolean, and it is derived from BOTH providers -- Google AI is
    tried first and OpenRouter is the fallback, so either one suffices.
  * **No credential crosses the wire.** The response body is checked against
    actual configured values, so a future field cannot start publishing one.
  * The page warns on ``=== false`` and ONLY on ``=== false``.

Not pinned: the wording of the notice. Copy changes; the fact that the reader
is told does not.

THE TWO ASYMMETRIES, WHICH ARE THE POINT
-----------------------------------------
1. ``False`` is actionable -- go set a variable. ``True`` is not a
   verification, because a key can be present and wrong, and a page that
   claims "configured, you are fine" has claimed something it cannot know.
   So the boolean says "no key is set", never "your key works".
2. An ABSENT field means an older server, not a missing key. A page that
   treated a field it does not know as ``false`` would invent a credential
   error out of a schema difference, which is the same defect as printing a
   latency figure no code produced.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import config as config_mod
from backend import main as main_mod
from backend.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "frontend" / "app.js"

#: Values that must never appear in a response body. Set as real keys below, so
#: this is a check against a live configured deployment rather than a mock.
CANARY_GOOGLE = "AIzaSyCANARYnotarealkey0000000000000000"
CANARY_OPENROUTER = "sk-or-v1-canary0000000000000000000000000"


def _client() -> TestClient:
    return TestClient(main_mod.app)


def _config_with(monkeypatch, **env: str) -> Config:
    """A ``Config`` built under an exact environment, not the import-time one."""
    for name in ("GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Config()


class TestTheEndpointPublishesWhetherAnyProviderIsConfigured:
    def test_the_field_is_published(self, isolated_write_targets):
        """The page can only ask on a request it already makes."""
        payload = _client().get("/api/config").json()

        assert "llm_configured" in payload, (
            "GET /api/config does not publish whether an LLM key is set, so the "
            "page cannot tell the reader before the first turn. The endpoint is "
            "already fetched at load; this is the field it was missing."
        )

    def test_it_is_a_boolean_not_a_credential_or_a_guess(self, isolated_write_targets):
        payload = _client().get("/api/config").json()

        assert isinstance(payload["llm_configured"], bool), (
            f"llm_configured is {type(payload['llm_configured']).__name__}, not a "
            "bool. A truthy string or a null would leave the page guessing, and "
            "guessing is what this field exists to stop."
        )

    @pytest.mark.parametrize(
        "env, expected",
        [
            ({}, False),
            ({"GOOGLE_API_KEY": "x"}, True),
            ({"OPENROUTER_API_KEY": "x"}, True),
            ({"GOOGLE_API_KEY": "x", "OPENROUTER_API_KEY": "y"}, True),
        ],
        ids=["neither", "google-only", "openrouter-only", "both"],
    )
    def test_either_provider_is_enough(
        self, monkeypatch, isolated_write_targets, env, expected
    ):
        """Google AI is tried first, OpenRouter is the fallback.

        Either one can answer, so requiring both would tell a correctly
        configured single-provider deployment that it is broken.
        """
        _config_with(monkeypatch, **env)

        # The endpoint reads the module-level singleton, so it has to be the
        # one that is patched -- asserting on a fresh Config() alone would
        # check an object the request never sees.
        monkeypatch.setattr(
            config_mod.config, "GOOGLE_API_KEY", env.get("GOOGLE_API_KEY", "")
        )
        monkeypatch.setattr(
            config_mod.config,
            "OPENROUTER_API_KEY",
            env.get("OPENROUTER_API_KEY", ""),
        )

        assert _client().get("/api/config").json()["llm_configured"] is expected

    def test_no_credential_crosses_the_wire(
        self, monkeypatch, isolated_write_targets
    ):
        """The strongest assertion in this file.

        The endpoint is public and rate-limit exempt, and it is fetched on load
        by every reader. A field that echoed, truncated or hashed a key would
        be a new exfiltration surface added for a cosmetic feature, so the
        response is checked against the real configured values rather than
        against a shape.
        """
        monkeypatch.setattr(config_mod.config, "GOOGLE_API_KEY", CANARY_GOOGLE)
        monkeypatch.setattr(
            config_mod.config, "OPENROUTER_API_KEY", CANARY_OPENROUTER
        )

        body = _client().get("/api/config").text

        for canary, name in (
            (CANARY_GOOGLE, "GOOGLE_API_KEY"),
            (CANARY_OPENROUTER, "OPENROUTER_API_KEY"),
        ):
            assert canary not in body, (
                f"GET /api/config echoed the configured {name} value. This "
                "endpoint is public, exempt from the rate limiter and fetched "
                "on load; the UI needs a boolean, not a credential."
            )
        for fragment in (canary[:12] for canary in (CANARY_GOOGLE, CANARY_OPENROUTER)):
            assert fragment not in body, (
                f"a {len(fragment)}-character prefix of a configured key appears "
                "in the response. A truncated or hashed key is still the key."
            )
        # And the field is present, so the check above is not passing because
        # the endpoint went quiet.
        assert json.loads(body)["llm_configured"] is True


class TestThePageSaysItBeforeTheFirstTurn:
    def test_the_config_load_is_where_the_notice_is_raised(self):
        """It has to ride the request the page already makes, at load.

        Waiting for the first turn to fail is the defect. Waiting for a second
        request is nearly as bad: the first turn is the one that fails.
        """
        source = APP_JS.read_text(encoding="utf-8")

        loader = _function_body(source, "populateStaticSidebar")
        assert "warnIfNoLLMCredential(cfg)" in loader, (
            "populateStaticSidebar() no longer raises the credential notice, so "
            "the page stops telling the reader at load. The /api/config response "
            "is already in hand at that point; there is no reason to wait."
        )

    def test_it_warns_only_on_an_explicit_false(self):
        """An absent field is an older server, not a missing key.

        Inventing a credential error out of a field this page does not know is
        the same defect as printing a latency nobody measured.
        """
        source = APP_JS.read_text(encoding="utf-8")
        body = _function_body(source, "warnIfNoLLMCredential")

        assert re.search(r"cfg\.llm_configured\s*!==\s*false", body), (
            "the notice must be gated on `!== false`, so an older server that "
            "does not publish the field produces silence rather than an "
            "invented credential error.\nFunction was:\n" + body
        )

    def test_the_notice_names_both_variables_and_the_file_to_edit(self):
        """A notice that does not say what to do has been moved, not fixed."""
        source = APP_JS.read_text(encoding="utf-8")
        body = _function_body(source, "warnIfNoLLMCredential")

        assert "GOOGLE_API_KEY" in body, (
            "the notice does not name GOOGLE_API_KEY. It is the provider tried "
            "first, so it is the one a reader is most likely to set."
        )
        assert "OPENROUTER_API_KEY" in body, (
            "the notice does not name OPENROUTER_API_KEY, so a reader who "
            "cannot get a Google key is not told the fallback exists."
        )
        assert ".env" in body, (
            "the notice does not say WHICH file to edit. A reader told to "
            "'configure a key' without a filename has to go looking."
        )

    def test_it_is_shown_once_and_not_repeated_per_config_load(self):
        """`init()` can fire the fetch again; a transcript is not a log file."""
        source = APP_JS.read_text(encoding="utf-8")
        body = _function_body(source, "warnIfNoLLMCredential")

        assert "llmCredentialWarningShown" in body, (
            "the notice is not guarded against being shown twice. It is "
            "rendered into the transcript, so a second copy is permanent noise "
            "the reader cannot remove."
        )


def _function_body(source: str, name: str) -> str:
    """A JS function's body, found by its own name rather than by line number.

    Located the way the rest of this repository locates code under comment: a
    line-number citation breaks the day an unrelated edit above it lands, and
    this file's whole subject is comments.
    """
    match = re.search(
        rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source
    )
    assert match is not None, (
        f"frontend/app.js has no function {name}(). The credential notice was "
        "deleted or renamed, so nothing tells the reader before the first turn."
    )

    depth, start = 1, match.end()
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise AssertionError(f"{name}() is not brace-balanced; cannot read its body")
