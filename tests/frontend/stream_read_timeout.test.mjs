/**
 * A stream that stops producing must not strand the interview.
 *
 * THE DEFECT
 * ----------
 * The read loop was `while (true) { await reader.read(); ... }` with no
 * deadline. The server keeps the wire alive with a comment frame every 15 s
 * (`SSE_KEEPALIVE_INTERVAL_SECONDS`), which is a SERVER timer: it proves the
 * server's event loop is turning. It proves nothing about the socket, and it
 * proves nothing at all when what is stuck IS the server -- which is the case
 * the guard exists for.
 *
 * On the documented development path (`config.py` points at `RUNBOOK.md:18`,
 * bare uvicorn with no proxy) a wedged server hangs the client forever:
 * `isProcessing` stays true, the mic stays disabled, no message appears, and
 * only END or a reload recovers. Behind the proxy nginx's `proxy_read_timeout`
 * closes the socket at 300 s, so the same hang is merely slower.
 *
 * WHY THIS IS NOT THE RETRY POLICY
 * --------------------------------
 * `fetchWithBackoff` classifies by HTTP status, and its whole design is
 * "re-issue the request". A stalled read is not that: the request was
 * delivered, the pipeline probably ran, and a turn may already be committed.
 * Replaying it is the exact ambiguity `writeOncePolicy` exists to refuse. So
 * the policy is not extended, and the timeout is its own thing — it ends the
 * turn, it does not retry it.
 *
 * WHY A PER-READ DEADLINE
 * -----------------------
 * Not a total budget. A turn is upload + Whisper + RAG + LLM + TTS, which
 * legitimately runs for minutes, and a total budget would kill a slow but
 * healthy turn. A live server produces bytes at least every 15 s, so a read
 * that returns nothing for longer than a few keepalives cannot be a live
 * server. The deadline is therefore re-armed on every read.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { baseState } from "./dom.mjs";
import { loadWithGlobals, readAppJs } from "./harness.mjs";

/** A controllable clock: nothing fires until the test says so. */
function fakeTimers() {
    let next = 0;
    const scheduled = new Map();
    return {
        setTimer(fn, ms) {
            const id = ++next;
            scheduled.set(id, { fn, ms });
            return id;
        },
        clearTimer(id) {
            scheduled.delete(id);
        },
        fire(id) {
            const entry = scheduled.get(id);
            assert.ok(entry, `timer ${id} was already cleared or never armed`);
            scheduled.delete(id);
            entry.fn();
        },
        fireAll() {
            for (const id of [...scheduled.keys()]) this.fire(id);
        },
        live() {
            return scheduled.size;
        },
        delays() {
            return [...scheduled.values()].map((e) => e.ms);
        },
    };
}

const MESSAGE = "El servidor dejó de enviar datos.";
const loadDeadline = (timers, timeoutMs = 1000) =>
    loadWithGlobals("createStreamDeadline", {})({
        timeoutMs,
        message: MESSAGE,
        code: "stream-read-timeout",
        setTimer: timers.setTimer,
        clearTimer: timers.clearTimer,
    });

/** A reader that resolves with whatever it is handed, once. */
function readerOf(results) {
    const queue = [...results];
    return {
        reads: 0,
        async read() {
            this.reads += 1;
            const next = queue.shift();
            if (next === undefined) return { done: true, value: undefined };
            return next;
        },
    };
}

/** A reader that never resolves: a wedged server. */
function wedgedReader() {
    return { reads: 0, read: () => new Promise(() => {}) };
}

// ─── A read that answers is passed through untouched ───────────────────────

test("a value that arrives before the deadline is returned as-is", async () => {
    const timers = fakeTimers();
    const deadline = loadDeadline(timers);
    const reader = readerOf([{ done: false, value: "data: x\n\n" }]);

    const result = await deadline.read(reader);

    assert.deepEqual(result, { done: false, value: "data: x\n\n" });
});

test("the end of the stream is not a timeout", async () => {
    const timers = fakeTimers();
    const deadline = loadDeadline(timers);

    const result = await deadline.read(readerOf([{ done: true, value: undefined }]));

    assert.equal(result.done, true);
});

test("the timer is disarmed on a read that answers", async () => {
    // The leak a naive `Promise.race` has: the pending timer outlives the read
    // and fires into the next turn.
    const timers = fakeTimers();
    const deadline = loadDeadline(timers);

    await deadline.read(readerOf([{ done: false, value: "a" }]));

    assert.equal(timers.live(), 0, "a fired-later timer was left armed");
});

// ─── A read that never answers is a terminal, identifiable failure ─────────

test("a wedged stream is cut off rather than waited on forever", async () => {
    const timers = fakeTimers();
    const deadline = loadDeadline(timers);
    const pending = deadline.read(wedgedReader());

    timers.fireAll();

    await assert.rejects(pending, (e) => {
        assert.equal(e.message, MESSAGE, "the failure does not say what happened");
        return true;
    });
});

test("the failure is identifiable as a timeout and nothing else", async () => {
    // The catch has to tell this apart from a deliberate END: `turnAborted`
    // means the user finished the interview, and reporting a server failure
    // into a session they were told was over is its own lie.
    const timers = fakeTimers();
    const deadline = loadDeadline(timers);
    const pending = deadline.read(wedgedReader());

    timers.fireAll();

    await assert.rejects(pending, (e) => {
        assert.equal(e.code, "stream-read-timeout");
        assert.equal(e.status, undefined, "an HTTP status here would invite a retry");
        return true;
    });
});

test("the deadline is the configured one", async () => {
    const timers = fakeTimers();
    loadDeadline(timers, 45000).read(wedgedReader());

    assert.deepEqual(timers.delays(), [45000]);
});

// ─── It is per read, so a slow but healthy turn survives ───────────────────

test("each read gets a fresh deadline", async () => {
    // A streamed answer is many reads. A single total budget would fire during
    // the second chunk of a healthy turn.
    const timers = fakeTimers();
    const deadline = loadDeadline(timers, 1000);
    const reader = readerOf([
        { done: false, value: "1" },
        { done: false, value: "2" },
        { done: false, value: "3" },
    ]);

    for (let i = 0; i < 3; i += 1) {
        const result = await deadline.read(reader);
        assert.equal(result.value, String(i + 1));
    }

    assert.equal(reader.reads, 3);
    assert.equal(timers.live(), 0);
});

test("a read that arrives late in its own window still counts", async () => {
    const timers = fakeTimers();
    const deadline = loadDeadline(timers, 1000);
    const reader = readerOf([{ done: false, value: "late" }]);

    const result = await deadline.read(reader);
    timers.fireAll();

    assert.equal(result.value, "late", "the deadline fired after the read resolved");
});

// ─── The call site wires it up and recovers ───────────────────────────────

test("the read loop goes through the deadline, not straight to the reader", () => {
    const source = readAppJs();
    const loop = source.slice(
        source.indexOf("const reader = res.body.getReader()"),
        source.indexOf("// Stream EOF."),
    );

    assert.ok(loop, "the read loop was not found by its anchors");
    assert.match(loop, /await\s+streamDeadline\.read\(reader\)/, loop);
    assert.doesNotMatch(
        loop,
        /await\s+reader\.read\(\)/,
        "the loop still calls reader.read() directly, so the deadline is " +
            "constructed and then bypassed",
    );
});

/**
 * The turn's error handler, from the catch's own log line to the end of its
 * `finally`. Anchored on the two ends of the block rather than on `} finally {
 *`, which appears earlier in the file and would slice to nothing.
 */
function catchHandler() {
    const source = readAppJs();
    const from = source.indexOf('console.error("SSE pipeline error:"');
    const to = source.indexOf("turnAbortController = null;", from);
    assert.ok(from !== -1 && to > from, "the SSE error handler was not found");
    return source.slice(from, to);
}

test("a timeout releases the pending read instead of orphaning it", () => {
    // `Promise.race` abandons the losing promise. The read it abandons is
    // still holding the response body, so the turn's abort controller has to
    // be cancelled or the socket stays open behind a dead page.
    assert.match(
        catchHandler(),
        /STREAM_READ_TIMEOUT_CODE[\s\S]{0,2000}?turnAbortController\.abort\(\)/,
        "the timeout path does not abort the controller, so the abandoned " +
            "read keeps the response body open",
    );
});

test("a timeout is not reported as the user ending the interview", () => {
    // The `if (turnAborted) return` guard is the deliberate-END path. A server
    // that stopped answering did not end anything, and must not be allowed to
    // set that flag.
    assert.doesNotMatch(
        catchHandler(),
        /STREAM_READ_TIMEOUT_CODE[\s\S]{0,2000}?turnAborted\s*=\s*true/,
        "the timeout path marks the turn as deliberately aborted, so the " +
            "failure is never surfaced to the candidate",
    );
});

test("the deadline outlives several server keepalives", () => {
    // The cross-side half. A live server emits a comment frame every
    // SSE_KEEPALIVE_INTERVAL_SECONDS, so a client deadline at or below one
    // interval would kill every healthy turn whose first chunk is still being
    // transcribed. The constant is read out of the Python source rather than
    // restated: a restated pair could agree with itself while the server moved.
    const { interval } = readServerKeepalive();
    const declared = readClientTimeoutMs();

    assert.ok(declared, "app.js no longer declares a stream read timeout");
    assert.ok(
        declared >= interval * 3,
        `the client gives up after ${declared}ms of silence, but the server ` +
            `sends a keepalive every ${interval * 1000}ms, so a healthy turn ` +
            "can be killed mid-flight. Raise the client deadline, not lower the " +
            "keepalive.",
    );
});

test("the harness state matches the constants app.js actually ships", () => {
    // `dom.mjs` has to restate the three settings for lifted code to see them,
    // and a restated number is only safe while something checks it against the
    // shipped source. The existing precedent is `MAX_RECORDING_MS`, asserted the
    // same way in recording_cap.test.mjs.
    const source = readAppJs();
    const declared = (name) => {
        const at = source.indexOf(`${name} =`);
        assert.notEqual(at, -1, `app.js no longer declares ${name}`);
        return source.slice(at, source.indexOf(";", at));
    };

    assert.ok(
        declared("STREAM_READ_TIMEOUT_MS").includes(String(baseState().STREAM_READ_TIMEOUT_MS)),
        `dom.mjs's STREAM_READ_TIMEOUT_MS is not app.js's: ${baseState().STREAM_READ_TIMEOUT_MS} vs ` +
            declared("STREAM_READ_TIMEOUT_MS"),
    );
    assert.ok(
        declared("STREAM_READ_TIMEOUT_CODE").includes(
            `"${baseState().STREAM_READ_TIMEOUT_CODE}"`,
        ),
        `dom.mjs's STREAM_READ_TIMEOUT_CODE is not app.js's: ${declared("STREAM_READ_TIMEOUT_CODE")}`,
    );
    assert.ok(
        source.includes(baseState().STREAM_READ_TIMEOUT_MESSAGE.slice(0, 40)),
        "dom.mjs's STREAM_READ_TIMEOUT_MESSAGE is not the one app.js ships",
    );
});

function readServerKeepalive() {
    const source = readFileSync(
        new URL("../../backend/sse.py", import.meta.url),
        "utf8",
    );
    const match = source.match(/SSE_KEEPALIVE_INTERVAL_SECONDS\s*=\s*([\d.]+)/);
    assert.ok(match, "backend/sse.py no longer declares its keepalive interval");
    return { interval: Number(match[1]) };
}

function readClientTimeoutMs() {
    const match = readAppJs().match(
        /STREAM_READ_TIMEOUT_MS\s*=\s*(\d+)\s*;?\s*(\/\/[^\n]*)?/,
    );
    return match ? Number(match[1]) : null;
}
