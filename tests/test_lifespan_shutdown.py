"""Regression test: application shutdown must not raise.

The lifespan shutdown path called ``llm.close_http_clients()`` as if ``llm``
were a bound module, but only ``LLMService`` (the class) was ever imported, so
``llm`` was never a name in that scope. Every real shutdown raised NameError
before reaching the log line, leaving the shared keep-alive HTTP clients open.

This test runs the real lifespan as a context manager so the shutdown half
actually executes. A ``TestClient(app)`` constructed without ``with`` never
starts the lifespan, which is why the bug survived the existing suite.
"""

import pytest


def test_shutdown_completes_without_raising():
    """Entering and leaving the app context must not raise on shutdown."""
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200


def test_llm_http_clients_are_closed_on_shutdown(monkeypatch):
    """The shared keep-alive client must actually be closed at shutdown."""
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services import llm as llm_module

    closed = []
    monkeypatch.setattr(
        llm_module, "close_http_clients", lambda: closed.append(True)
    )

    with TestClient(app):
        pass

    assert closed == [True], "close_http_clients() was not called on shutdown"


@pytest.mark.parametrize("path", ["/api/health", "/api/config"])
def test_lifespan_runs_for_context_manager_usage(path):
    """Sanity: the app boots under a context manager, not just on direct call."""
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        assert client.get(path).status_code == 200
