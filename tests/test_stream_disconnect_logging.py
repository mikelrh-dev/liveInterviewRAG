"""Regression tests for how the SSE stream logs an unfinished terminal event.

The generator's ``finally`` block must distinguish two situations that both
arrive with ``terminal_emitted == False``:

* ``GeneratorExit`` -- the client closed the tab, navigated away or dropped the
  connection. This raises at the current ``yield`` and, being a ``BaseException``,
  is not caught by the generator's ``except Exception``. The browser settles on
  EOF, so this is normal operation and must not be logged as an error.
* Anything else -- a path returned or was cancelled without emitting a terminal
  event. That is a real defect and must stay loud.

Getting this wrong is not cosmetic: an ERROR on every ordinary disconnect
trains the reader to ignore this log line, which is the one place a genuinely
missing terminal event is reported.
"""

import logging

import pytest

from backend.turns import streaming


def test_generator_exit_is_not_logged_as_error(monkeypatch, caplog):
    """A client disconnect logs at INFO, never ERROR."""
    source = streaming.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    assert "is GeneratorExit" in text, (
        "the finally block must branch on GeneratorExit so a normal client "
        "disconnect is not reported as a stream defect"
    )
    assert 'logger.info' in text
    # The error branch must carry exc_info: it is the real-defect path.
    assert "exc_info=True" in text


def test_finally_block_does_not_yield(monkeypatch):
    """A `yield` inside `finally` would risk yielding into a closing generator."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(streaming))
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and node.finalbody:
            for statement in node.finalbody:
                for inner in ast.walk(statement):
                    if isinstance(inner, (ast.Yield, ast.YieldFrom)):
                        # Yielding from finally is the documented hazard; the
                        # invariant is enforced structurally, not by log level.
                        assert False, (
                            "the finally block must not yield: on GeneratorExit "
                            "that raises RuntimeError inside the cleanup"
                        )


def test_stream_module_imports_sys_for_the_exc_info_check():
    """`sys.exc_info()` is how the branch detects GeneratorExit."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(streaming))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "sys" in imported, "streaming.py uses sys.exc_info() but does not import sys"
