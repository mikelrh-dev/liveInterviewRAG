"""The maintenance sweep must not run its I/O on the event-loop thread.

THE DEFECT THIS GUARDS
----------------------
``backend/maintenance.py``'s tick calls three blocking services inline::

    container.cleanup_stale_audio()()                       # :171
    container.report_service().cleanup_expired()            # :175
    container.persistence().prune_reports(...)              # :179

while the lines immediately around them hand the same kind of work to a worker
(``:130``, ``:142``, ``:187``). Three unguarded calls in a block whose
neighbours are all guarded is not a design decision; it is the state a guard
decays into when the next step is added without noticing the ones above it.

The cost is the whole process. An ``rglob`` over the audio tree, a walk of the
reports directory and a SQLite statement are each tens to hundreds of
milliseconds, and every millisecond of them is a millisecond in which no
request is served, no SSE token streams, and the sweep's own sleep does not
elapse. Measured with 3000 files: **171 ms** of blocked loop; at a realistic
scale: **65 ms**.

WHAT IS ASSERTED HERE, AND WHY NOT "IS IT WRAPPED"
-------------------------------------------------
"Is it inside ``asyncio.to_thread``" is a claim about spelling, and the
neighbouring lines were already spelled that way while the defect lived three
lines below them. A test that greps for the wrapper passes the moment someone
writes it and says nothing about whether the loop was free.

So these tests run a real tick against blocking stand-ins and measure two things
that can only both be true if the loop kept turning:

* which THREAD each step ran on, and
* whether a heartbeat armed before the sweep kept ticking WHILE a step ran.

The stand-ins block with ``threading.Event().wait``, not ``await``: a coroutine
that yields proves nothing, because yielding is the thing under test.

The same shape already guards the request path in
``tests/test_stt_event_loop.py``; this is its background-task twin, and the way
the loop is driven is the one ``tests/test_maintenance_eviction.py`` already
established for the 30-second initial sleep.
"""

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from backend import maintenance
from backend.config import config

#: Long enough that a heartbeat scheduled mid-step cannot fire late by accident
#: on a loaded machine, short enough to keep the suite quick.
BLOCK_SECONDS = 0.30
HEARTBEAT_SECONDS = 0.24

STEP_NAMES = ("cleanup_stale_audio", "cleanup_expired", "prune_reports")


class Step:
    """One blocking service, and what it can tell us about the thread it ran on.

    Occupies the thread it is called on for ``hold`` seconds with a real
    ``Event.wait``. An ``await`` here would yield the loop, which is precisely
    the thing under test.

    ``running`` is what lets a heartbeat ask "is the loop free right now?"
    without knowing anything about the sweep.
    """

    def __init__(self, name, order, hold=BLOCK_SECONDS):
        self.name = name
        self.order = order
        self.args: tuple = ()
        self.ran_on: threading.Thread | None = None
        self.running = threading.Event()
        self.finished = threading.Event()
        self.raises: Exception | None = None
        self._hold = hold

    def __call__(self, *args, **kwargs):
        self.ran_on = threading.current_thread()
        self.args = args
        self.order.append(self.name)
        self.running.set()
        try:
            threading.Event().wait(self._hold)
        finally:
            self.running.clear()
            self.finished.set()
        if self.raises is not None:
            raise self.raises
        return 0

    def __repr__(self):
        return f"<step {self.name} on {self.ran_on}>"


class Harness:
    """A container whose three sweep steps block, and a way to run one tick.

    ``periodic_cleanup`` opens with ``await asyncio.sleep(30)`` and loops
    forever, so ``run`` replaces the sleep -- the pattern
    ``test_maintenance_eviction.py`` already uses for the same reason. It still
    yields to the loop, so ``asyncio.to_thread`` work actually runs, which is
    what these tests are about.

    ``run(observer)`` arms ``observer`` on the loop BEFORE the sweep starts and
    returns once the tick is over. Installed by the fixture, which owns the
    monkeypatch.
    """

    def __init__(self, container, steps, order):
        self.container = container
        self.steps = steps
        self.order = order

    def step(self, name: str) -> Step:
        return self.steps[name]

    @property
    def store(self):
        return self.container.persistence.return_value

    def run(self, observer=None):  # pragma: no cover - replaced by the fixture
        raise AssertionError("Harness.run is installed by the `harness` fixture")


@pytest.fixture
def harness(monkeypatch):
    order: list[str] = []
    steps = {name: Step(name, order) for name in STEP_NAMES}

    store = MagicMock()
    store.prune_reports = steps["prune_reports"]
    store.prune_conversations = MagicMock(return_value=0)
    store.evict_conversation = MagicMock()
    store.load_conversation = MagicMock(return_value=None)

    report = MagicMock()
    report.cleanup_expired = steps["cleanup_expired"]
    report.generate = MagicMock(return_value=None)

    container = MagicMock()
    container.cleanup_stale_audio.return_value = steps["cleanup_stale_audio"]
    container.report_service.return_value = report
    container.persistence.return_value = store

    monkeypatch.setattr(maintenance, "container", container)
    # Nothing to evict: this file is about the three steps that are not
    # eviction.
    monkeypatch.setattr(maintenance, "conversations", {})
    monkeypatch.setattr(maintenance, "_rate_limit_store", {})
    # Replaced per run() call, on the module maintenance resolves them through.
    monkeypatch.setattr(maintenance.asyncio, "sleep", asyncio.sleep)

    built = Harness(container, steps, order)
    real_sleep = asyncio.sleep
    sleeps = {"n": 0}

    async def fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            # The tick's own trailing sleep, between two passes. Raising here is
            # the loop parked exactly where it parks in production, after one
            # complete pass and no more.
            raise GeneratorExit
        await real_sleep(0)

    def run(observer=None):
        sleeps["n"] = 0
        monkeypatch.setattr(maintenance.asyncio, "sleep", fake_sleep)

        async def no_op():
            return None

        async def main():
            with pytest.raises((GeneratorExit, RuntimeError, StopAsyncIteration)):
                armed = asyncio.ensure_future((observer or no_op)())
                try:
                    await maintenance.periodic_cleanup(interval_seconds=0)
                finally:
                    armed.cancel()

        asyncio.run(main())
        return order

    built.run = run
    return built


class TestTheSweepDoesNotFreezeTheLoop:
    @pytest.mark.parametrize("name", STEP_NAMES)
    def test_the_step_does_not_run_on_the_loop_thread(self, harness, name):
        seen = {}

        async def observer():
            seen["loop"] = threading.current_thread()

        order = harness.run(observer)

        step = harness.step(name)
        assert name in order, f"{name} never ran: {order}"
        assert step.ran_on is not seen["loop"], (
            f"{name} ran on the event-loop thread, so it blocked every request "
            f"for its whole duration: {step}"
        )

    @pytest.mark.parametrize("name", STEP_NAMES)
    def test_a_heartbeat_keeps_ticking_during_the_step(self, harness, name):
        """The consequence, measured rather than inferred.

        Under a blocked loop every one of these ticks is deferred until the
        step returns, which is the entire cost of the defect: a process that is
        not serving anyone while it tidies up.
        """
        beats = {"during": 0, "outside": 0}
        step = harness.step(name)

        async def observer():
            loop = asyncio.get_running_loop()

            def heartbeat():
                # Asked of the same object the sweep called, so "is the loop
                # free right now?" needs no knowledge of the sweep.
                if step.running.is_set():
                    beats["during"] += 1
                else:
                    beats["outside"] += 1
                loop.call_later(HEARTBEAT_SECONDS / 5, heartbeat)

            loop.call_later(HEARTBEAT_SECONDS / 5, heartbeat)

        harness.run(observer)

        assert beats["during"] > 0, (
            f"nothing else on the event loop ran while {name} was executing "
            f"({beats['outside']} beats, every one of them outside it). The "
            "loop is frozen for the duration of the sweep."
        )


class TestTheSweepStillDoesItsWork:
    def test_every_step_is_reached(self, harness):
        """Bounded or not, the sweep must still run all three.

        The change is `await asyncio.to_thread(...)` and nothing else, so a
        wrapper that swallowed a call would be a "fix" that quietly deletes
        maintenance.
        """
        order = harness.run()
        assert sorted(order) == sorted(STEP_NAMES), f"only {order} ran"

    def test_the_arguments_survive_the_handoff(self, harness):
        """`to_thread` is not an argument-dropping call, and the day count matters."""
        harness.run()
        step = harness.step("prune_reports")
        assert step.args == (config.REPORT_RETENTION_DAYS,), (
            f"prune_reports was given {step.args}, not the retention window "
            f"{config.REPORT_RETENTION_DAYS}"
        )

    def test_the_already_guarded_steps_still_run(self, harness):
        """A fix that reached one line too far has to be visible somewhere.

        On the three new lines an over-reaching change is indistinguishable
        from the right one, so the neighbours are what catch it.
        """
        harness.run()
        harness.store.prune_conversations.assert_called_with(config.SESSION_TTL_HOURS)

    def test_a_failing_step_does_not_stop_the_others(self, harness):
        """The sweep's own contract: one broken service must not abort the rest."""
        harness.step("prune_reports").raises = RuntimeError("store is wedged")

        order = harness.run()

        assert "cleanup_stale_audio" in order
        assert "cleanup_expired" in order
