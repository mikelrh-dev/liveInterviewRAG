"""The eviction sweep must survive one conversation it cannot age.

THE DEFECT THIS GUARDS
----------------------
``periodic_cleanup`` decided staleness with a single list comprehension over
every conversation::

    stale_ids = [
        cid for cid, c in conversations.items()
        if datetime.fromisoformat(c.get("last_activity_at", "")) < cutoff
    ]

``fromisoformat("")`` raises ``ValueError``, so ONE conversation missing the key
raised out of the comprehension, out of the eviction step, and into the guard at
the bottom of the loop -- which logged it and moved on to the next sweep. The
step evicted nothing, for anyone, on every tick, forever, while the sweep
reported itself healthy. Both the in-memory store and the DB rows grew without
bound and nothing said so at the level anyone reads.

It is not reachable over HTTP today: both writers of the field
(``create_conversation`` and ``touch_activity``) set it, and the column is
``NOT NULL``. That is why this is a resilience defect and not a live exploit --
but the blast radius is total and the failure is silent, which is the worst
combination there is.

WHAT IS ASSERTED HERE
---------------------
Not "the parse does not raise" -- that is the implementation. What matters:

* the OTHER conversations are still evicted, with the bad one present;
* the sweep still evicts on a LATER tick, i.e. the damage is not one-shot;
* the record it could not age is reported, naming it, so an operator sees it;
* a conversation whose age is unknown is NOT evicted on a guess -- destroying a
  live interview to satisfy a TTL is the worse failure;
* an ISO form this interpreter's ``fromisoformat`` mishandles is still aged,
  which is the second and third ways the same step used to abort.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import pytest

import backend.main as main_mod


def _stale_iso() -> str:
    return (datetime.utcnow() - timedelta(hours=99)).isoformat()


def _fresh_iso() -> str:
    return datetime.utcnow().isoformat()


def _conversation(cid, last_activity_at, created_at=_stale_iso()):
    """A conversation record shaped like the ones the pipeline really builds.

    ``created_at`` defaults to a stale stamp because that is what makes a
    record genuinely evictable in these tests, and because it is the fallback
    the sweep uses when ``last_activity_at`` cannot be read -- a case that
    deserves its own assertions rather than an accident of the fixture.
    """
    record = {
        "id": cid,
        "messages": [{"user_text": "q", "response_text": "a", "audio_url": ""}],
        "turns": [
            {"n": 0, "user_text": "q", "assistant_text": "a", "chunks_used": []}
        ],
        "summary": "",
    }
    if created_at is not None:
        record["created_at"] = created_at
    if last_activity_at is not None:
        record["last_activity_at"] = last_activity_at
    return record


@pytest.fixture
def sweep(monkeypatch):
    """Drive the real sweep for a fixed number of ticks, then stop it.

    ``periodic_cleanup`` opens with ``await asyncio.sleep(30)``, so the loop
    cannot be reached in a test without either waiting or replacing the sleep.
    Replacing it is what ``test_isolation.py`` already does for the same
    reason; the sleep still yields to the loop so ``asyncio.to_thread`` work
    (the report and the DB eviction) actually runs.
    """
    real_sleep = asyncio.sleep
    tick_count = {"n": 1}
    calls = {"n": 0}

    async def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] > tick_count["n"]:
            # One past the last tick's trailing sleep: the loop has finished
            # its work and is parked where it parks between ticks.
            raise GeneratorExit
        await real_sleep(0)

    monkeypatch.setattr(main_mod.asyncio, "sleep", fake_sleep)
    # The audio sweep is not what this file is about and it walks a real tree.
    monkeypatch.setattr(main_mod, "cleanup_stale_audio", lambda: None)
    monkeypatch.setattr(main_mod.report_service, "generate", lambda *a, **k: None)

    def _run(ticks_requested=1):
        tick_count["n"] = ticks_requested
        calls["n"] = 0
        with pytest.raises((GeneratorExit, RuntimeError, StopAsyncIteration)):
            asyncio.run(main_mod.periodic_cleanup(interval_seconds=0))
        return calls["n"]

    main_mod.conversations.clear()
    yield _run
    main_mod.conversations.clear()


class TestOneBadRecordDoesNotStopTheSweep:
    def test_stale_conversations_are_still_evicted(self, sweep):
        """The core of it: the good ones go, the bad one does not stop them."""
        main_mod.conversations.update({
            "stale-a": _conversation("stale-a", _stale_iso()),
            "no-stamp-at-all": _conversation("no-stamp-at-all", None, None),
            "stale-b": _conversation("stale-b", _stale_iso()),
        })

        sweep()

        assert "stale-a" not in main_mod.conversations, (
            "a stale conversation survived because an unrelated record could "
            "not be aged"
        )
        assert "stale-b" not in main_mod.conversations, (
            "the whole step aborted: one unparseable last_activity_at stopped "
            f"eviction for every conversation; still in memory: "
            f"{sorted(main_mod.conversations)}"
        )

    def test_the_sweep_keeps_working_on_every_later_tick(self, sweep):
        """Not a one-shot abort: the failure recurred on every tick, forever."""
        main_mod.conversations["no-stamp-at-all"] = _conversation(
            "no-stamp-at-all", None, None
        )

        sweep(ticks_requested=1)
        main_mod.conversations["stale-after-first-tick"] = _conversation(
            "stale-after-first-tick", _stale_iso()
        )
        sweep(ticks_requested=1)

        assert "stale-after-first-tick" not in main_mod.conversations, (
            "a conversation that went stale after the first tick was never "
            "evicted, so the damage is permanent and silent"
        )

    def test_the_record_it_could_not_age_is_reported_naming_it(self, sweep, caplog):
        """"One bad record" must never mean "one quietly skipped record"."""
        main_mod.conversations["unidentifiable-conversation"] = _conversation(
            "unidentifiable-conversation", None, None
        )

        with caplog.at_level(logging.WARNING, logger="backend.maintenance"):
            sweep()

        reports = [
            r for r in caplog.records
            if "unidentifiable-conversation" in r.getMessage()
            and r.levelno >= logging.WARNING
        ]
        assert reports, (
            "a conversation whose age cannot be read was skipped without a "
            "word. That is the same silence as before, just narrower: "
            f"{[r.getMessage() for r in caplog.records]}"
        )


class TestWhatHappensToTheBadRecord:
    def test_a_record_with_no_usable_timestamp_is_kept_not_guessed(self, sweep):
        """Deleting a live interview to satisfy a TTL is the worse failure.

        The sweep cannot know how old this conversation is, so it does not know
        whether the interview is over. Skipping it is recoverable -- the
        store-side ``prune_conversations`` reads the NOT NULL column and still
        removes the rows -- while evicting a conversation the candidate is in the
        middle of is not.
        """
        main_mod.conversations["live-but-unstamped"] = _conversation(
            "live-but-unstamped", None, None
        )

        sweep()

        assert "live-but-unstamped" in main_mod.conversations, (
            "a conversation with no readable timestamp was evicted anyway; the "
            "sweep must not destroy a conversation it cannot age"
        )

    def test_an_unreadable_stamp_falls_back_to_created_at(self, sweep, caplog):
        """``created_at`` is the fallback, and it is never optimistic.

        Both fields are written by the same call and ``created_at`` is never
        newer, so the fallback can only make a conversation look older -- the
        safe direction for a retention sweep.
        """
        main_mod.conversations.update({
            "garbage-but-old": _conversation(
                "garbage-but-old", "not a timestamp at all", created_at=_stale_iso()
            ),
            "garbage-but-fresh": _conversation(
                "garbage-but-fresh", "not a timestamp at all", created_at=_fresh_iso()
            ),
        })

        with caplog.at_level(logging.WARNING, logger="backend.maintenance"):
            sweep()

        assert "garbage-but-old" not in main_mod.conversations, (
            "an unreadable last_activity_at with an old created_at must still "
            "be aged out; the fallback is what stops one bad field from "
            "freezing the whole sweep"
        )
        assert "garbage-but-fresh" in main_mod.conversations, (
            "the fallback must not evict a conversation that is not stale"
        )
        assert any(
            "garbage-but-old" in r.getMessage() and r.levelno >= logging.WARNING
            for r in caplog.records
        ), "the unreadable field itself must be reported, not just the outcome"

    @pytest.mark.parametrize(
        "stamp",
        [
            pytest.param("2020-01-01T00:00:00Z", id="zulu-suffix"),
            pytest.param("2020-01-01 00:00:00", id="space-separator"),
        ],
    )
    def test_an_iso_form_this_interpreter_rejects_is_still_aged(self, sweep, stamp):
        """The same step, aborted a second and third way.

        ``fromisoformat`` on Python < 3.11 rejects a ``Z`` suffix outright, and
        on 3.11+ it accepts it and returns an AWARE datetime, which then raises
        ``TypeError`` when compared against the naive UTC cutoff it is being
        compared with. Either way one record stopped the whole step.
        """
        cid = f"stale-{stamp[-3:].strip()}"
        main_mod.conversations[cid] = _conversation(cid, stamp)

        sweep()

        assert cid not in main_mod.conversations, (
            f"last_activity_at={stamp!r} is a valid ISO timestamp this "
            "interpreter mishandles, and it stopped the eviction step"
        )

    def test_a_fresh_conversation_is_still_kept(self, sweep):
        """The resilience work must not become "evict everything"."""
        main_mod.conversations.update({
            "no-stamp-at-all": _conversation("no-stamp-at-all", None, None),
            "fresh": _conversation("fresh", _fresh_iso()),
        })

        sweep()

        assert "fresh" in main_mod.conversations, (
            "the in-progress conversation was evicted alongside the stale ones"
        )


class TestTheSweepStillWritesThrough:
    def test_an_evicted_conversation_loses_its_rows_too(self, sweep):
        """Memory and the store must not disagree about who was evicted.

        The DB-side prune in the same tick reads the NOT NULL column and is
        unaffected by a malformed in-memory record, so this is where a
        half-completed sweep would show up: the row gone from one store and
        still present in the other.
        """
        store = main_mod.persistence
        store.initialize()

        main_mod.conversations["no-stamp-at-all"] = _conversation(
            "no-stamp-at-all", None, None
        )
        for cid in ("stale-a", "stale-b"):
            main_mod.conversations[cid] = _conversation(cid, _stale_iso())
            store.record_turn(
                cid,
                {"n": 0, "user_text": "q", "assistant_text": "a", "chunks_used": []},
                {"user_text": "q", "response_text": "a", "audio_url": ""},
            )

        sweep()

        for cid in ("stale-a", "stale-b"):
            assert store.load_conversation(cid) is None, (
                f"{cid} left memory but its rows are still in the store"
            )
