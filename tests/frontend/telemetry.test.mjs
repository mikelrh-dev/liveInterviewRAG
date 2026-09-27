/**
 * Telemetry honesty tests: nothing in the rail may be a prop.
 *
 * The defect: `index.html` shipped the literal string "LATENCY 12ms" and the
 * literal string "All Systems Online". Neither was measured. They sat directly
 * beside the LLM/STT/TTS model names, which *are* genuinely fetched from
 * GET /api/config — so a technical reader had no way to tell which rail
 * entries were instrumentation and which were decoration. On a portfolio piece
 * that is the one thing a reviewer will check, and the answer was "you made
 * them up".
 *
 * What is asserted here:
 *   1. The markup contains no hardcoded number and no unconditional claim.
 *   2. The latency pill shows a real measured number, and only once measured.
 *   3. An unmeasured or in-flight turn shows NO number at all.
 *   4. A new turn never inherits the previous turn's number.
 *   5. The system status is driven by GET /api/health, and a failed check can
 *      never produce "All Systems Online".
 *
 *   node --test tests/frontend/
 */

import test from "node:test";
import assert from "node:assert/strict";
import { loadWithGlobals, readIndexHtml } from "./harness.mjs";

const html = readIndexHtml();

// ─── helpers ───────────────────────────────────────────────────────────────

/** A stand-in for a text node: records exactly what was written to it. */
function fakeText() {
    return { textContent: "", className: "" };
}

/** A stand-in for #latency, which app.js treats as a plain text target. */
function fakePill() {
    return { textContent: "" };
}

/** A stand-in for #sidebar-status, which holds a dot span and a label span. */
function fakeStatusEl() {
    const dot = fakeText();
    const label = fakeText();
    return {
        dot,
        label,
        querySelector(sel) {
            if (sel === ".dot") return dot;
            if (sel === ".status-text") return label;
            return null;
        },
    };
}

/** A controllable clock, so the assertions never depend on real timing. */
function fakeClock(start = 0) {
    let t = start;
    return () => (t += 0);
}

function clockAt(values) {
    let i = 0;
    return () => values[Math.min(i++, values.length - 1)];
}

function loadReadout(el, clock) {
    return loadWithGlobals("createLatencyReadout", {})(el, clock);
}

function loadHealth(el, opts) {
    return loadWithGlobals("createHealthStatus", {})(el, opts || {});
}

const DIGIT = /\d/;

// ─── 1. The markup carries no invented telemetry ───────────────────────────

test("the markup ships no hardcoded latency number", () => {
    assert.doesNotMatch(
        html,
        /LATENCY\s*\d/i,
        "index.html still declares a literal latency figure that nothing measures",
    );
    assert.doesNotMatch(
        html,
        /<span[^>]*class="latency"[^>]*>[^<]*\d/,
        "the latency pill's initial text contains a digit",
    );
});

test("the markup ships no unconditional health claim", () => {
    assert.doesNotMatch(
        html,
        /All Systems Online/i,
        "index.html still asserts a system state that no code can falsify",
    );
});

test("the rail starts in a state that claims nothing", () => {
    // Before the first health check resolves, the status must not already be
    // green: the initial dot class must not be dot-green.
    const block = html.slice(
        html.indexOf('id="sidebar-status"'),
        html.indexOf('id="sidebar-status"') + 400,
    );
    assert.match(block, /id="sidebar-status"/, "the status rail is gone");
    assert.doesNotMatch(
        block,
        /dot-green/,
        "the status rail is green before any health check has run",
    );
});

test("the markup explains why no value is hardcoded there", () => {
    // The next person to open this file must not "helpfully" put a number back.
    const at = html.indexOf('class="latency"');
    assert.notEqual(at, -1, "the latency pill is gone from the header");
    const preceding = html.slice(Math.max(0, at - 1200), at);
    assert.match(
        preceding,
        /<!--/,
        "the latency pill has no explanatory comment above it",
    );
    assert.match(
        preceding,
        /performance\.now|measur|midiendo|nothing is hardcoded/i,
        "the comment above the latency pill does not say the value is measured " +
            "in app.js, so a static number is one edit away from creeping back",
    );
});

// ─── 2. A measured turn shows the real number ──────────────────────────────

test("a measured turn displays the number it actually measured", () => {
    const pill = fakePill();
    // begin() reads the clock first, firstToken() second: start at 1000ms,
    // first token at 2234ms, so the turn really cost 1234ms.
    const readout = loadReadout(pill, clockAt([1000, 2234]));

    readout.begin();
    readout.firstToken();

    assert.equal(readout.ttft(), 1234, "the readout lost the measured value");
    assert.match(pill.textContent, /1\.23/, "the pill did not show the real figure");
});

test("the pill labels which latency it is showing", () => {
    // "12ms" implied a generic number. An honest label names the measurement.
    const pill = fakePill();
    const readout = loadReadout(pill, clockAt([0, 2000]));
    readout.begin();
    readout.firstToken();
    assert.match(pill.textContent, /TTFT/i, "the pill does not name the metric");
});

test("time to first byte and time to first token are measured separately", () => {
    const pill = fakePill();
    const readout = loadReadout(pill, clockAt([0, 120, 900]));
    readout.begin();
    readout.firstByte();
    readout.firstToken();

    assert.equal(readout.ttfb(), 120);
    assert.equal(readout.ttft(), 900);
});

// ─── 3. Unmeasured shows no number ─────────────────────────────────────────

test("before anything is measured the pill shows no number", () => {
    const pill = fakePill();
    loadReadout(pill, fakeClock());

    assert.doesNotMatch(
        pill.textContent,
        DIGIT,
        "a fresh pill invented a measurement",
    );
});

test("while a turn is in flight the pill shows no number", () => {
    const pill = fakePill();
    const readout = loadReadout(pill, clockAt([0]));
    readout.begin();

    assert.doesNotMatch(
        pill.textContent,
        DIGIT,
        "an in-flight turn is displaying a latency as if it were known",
    );
    assert.match(pill.textContent, /midiendo/i, "the in-flight state is not labelled");
});

test("a stray token with no turn in flight measures nothing", () => {
    // An event arriving outside a turn must never manufacture a measurement.
    const pill = fakePill();
    const readout = loadReadout(pill, clockAt([0, 50, 90]));
    readout.firstToken();
    readout.firstByte();

    assert.equal(readout.ttft(), null);
    assert.equal(readout.ttfb(), null);
    assert.doesNotMatch(pill.textContent, DIGIT);
});

test("a failed turn drops the number instead of leaving it standing", () => {
    const pill = fakePill();
    const readout = loadReadout(pill, clockAt([0, 800]));
    readout.begin();
    readout.firstToken();
    assert.match(pill.textContent, /0\.80/);

    readout.abandon();

    assert.doesNotMatch(
        pill.textContent,
        DIGIT,
        "a failed turn left the previous measurement on screen as if it were live",
    );
});

// ─── 4. Each turn gets its own number ──────────────────────────────────────

test("a new turn never inherits the previous turn's number", () => {
    const pill = fakePill();
    const readout = loadReadout(pill, clockAt([0, 900, 1000]));
    readout.begin();
    readout.firstToken();
    assert.match(pill.textContent, /0\.90/);

    readout.begin();

    assert.doesNotMatch(
        pill.textContent,
        DIGIT,
        "the new turn is showing the previous turn's latency as its own",
    );
    assert.equal(readout.ttft(), null, "the previous turn's value survived the reset");
    assert.equal(readout.ttfb(), null);
});

test("a second turn reports its own figure, not the first turn's", () => {
    const pill = fakePill();
    const readout = loadReadout(pill, clockAt([0, 900, 1000, 1250]));
    readout.begin();
    readout.firstToken();

    readout.begin();
    readout.firstToken();

    assert.equal(readout.ttft(), 250);
    assert.match(pill.textContent, /0\.25/);
    assert.doesNotMatch(pill.textContent, /0\.90/);
});

test("a completed turn keeps its measured number on screen", () => {
    // settle() ends the stopwatch without discarding the result: after a
    // successful turn the candidate should be able to read what it cost.
    const pill = fakePill();
    const readout = loadReadout(pill, clockAt([0, 1500]));
    readout.begin();
    readout.firstToken();
    readout.settle();

    assert.equal(readout.ttft(), 1500, "settle() threw away a real measurement");
    assert.match(pill.textContent, /1\.50/);
});

test("a turn that ends without a token resolves instead of spinning forever", () => {
    // An empty transcription answers `error` + `done` with no `token` event.
    // Without settle() the pill would sit on "midiendo…" for the rest of the
    // session — a loading state that can never resolve.
    const pill = fakePill();
    const readout = loadReadout(pill, clockAt([0]));
    readout.begin();
    assert.match(pill.textContent, /midiendo/i);

    readout.settle();

    assert.doesNotMatch(
        pill.textContent,
        DIGIT,
        "a turn that produced no token is showing a number",
    );
    assert.doesNotMatch(
        pill.textContent,
        /midiendo/i,
        "the pill is stuck in a measuring state that can never resolve",
    );
});

// ─── 5. The status rail is driven by real health data ──────────────────────

test("a healthy check reports the numbers it was given", async () => {
    const el = fakeStatusEl();
    const health = loadHealth(el, {
        fetch: async () => ({
            ok: true,
            json: async () => ({
                status: "ok",
                whisper_loaded: true,
                rag_chunks: 214,
                candidate_loaded: true,
            }),
        }),
    });

    const view = await health.check();

    assert.equal(view.tone, "dot-green");
    assert.match(el.label.textContent, /214/, "the real chunk count was not shown");
});

test("a failed health check does not claim all systems are online", async () => {
    const el = fakeStatusEl();
    const health = loadHealth(el, {
        fetch: async () => {
            throw new TypeError("Failed to fetch");
        },
    });

    const view = await health.check();

    assert.doesNotMatch(
        el.label.textContent,
        /all systems online/i,
        "an unreachable server is being reported as a healthy one",
    );
    assert.notEqual(view.tone, "dot-green");
});

test("a health check that errors still resolves", () => {
    // A rejected promise here would become an unhandled rejection at init and
    // could take the conversation down with it.
    const el = fakeStatusEl();
    const health = loadHealth(el, {
        fetch: async () => {
            throw new Error("boom");
        },
    });
    return health.check().then((view) => assert.ok(view));
});

test("an HTTP error from the health endpoint is not read as healthy", () => {
    const el = fakeStatusEl();
    const health = loadHealth(el, {
        fetch: async () => ({ ok: false, status: 503, json: async () => ({}) }),
    });
    return health.check().then((view) => {
        assert.notEqual(view.tone, "dot-green");
        assert.doesNotMatch(el.label.textContent, /all systems online/i);
    });
});

test("a degraded check names the part that is actually down", async () => {
    const el = fakeStatusEl();
    const health = loadHealth(el, {
        fetch: async () => ({
            ok: true,
            json: async () => ({
                whisper_loaded: false,
                rag_chunks: 214,
                candidate_loaded: true,
            }),
        }),
    });

    const view = await health.check();

    assert.equal(view.tone, "dot-amber");
    assert.match(el.label.textContent, /stt/i);
    assert.doesNotMatch(el.label.textContent, /all systems online/i);
});

test("an empty RAG index is degraded, not healthy", async () => {
    const el = fakeStatusEl();
    const health = loadHealth(el, {
        fetch: async () => ({
            ok: true,
            json: async () => ({
                whisper_loaded: true,
                rag_chunks: 0,
                candidate_loaded: true,
            }),
        }),
    });

    const view = await health.check();

    assert.equal(view.tone, "dot-amber", "a zero-chunk index was reported as healthy");
    assert.match(el.label.textContent, /rag/i);
});

test("an unreadable health payload is not treated as healthy", async () => {
    const el = fakeStatusEl();
    const health = loadHealth(el, {
        fetch: async () => ({ ok: true, json: async () => "not an object" }),
    });

    const view = await health.check();

    assert.notEqual(view.tone, "dot-green");
});

test("overlapping health checks do not stack into a request storm", () => {
    // /api/health shares the 10-req/min per-IP bucket with the conversation
    // endpoints, so a slow check must not be able to pile up behind itself.
    let calls = 0;
    let release;
    const gate = new Promise((r) => {
        release = r;
    });

    const el = fakeStatusEl();
    const health = loadHealth(el, {
        fetch: async () => {
            calls++;
            await gate;
            return {
                ok: true,
                json: async () => ({ whisper_loaded: true, rag_chunks: 1, candidate_loaded: true }),
            };
        },
    });

    const first = health.check();
    const second = health.check();
    const third = health.check();
    release();

    return Promise.all([first, second, third]).then(() => {
        assert.equal(calls, 1, "three overlapping checks produced more than one request");
    });
});

test("a rate-limited health check reports 'unverified', not 'down'", () => {
    // /api/health is behind the same limiter as the interview, so a 429 here
    // means we asked too often — the server is not necessarily unhealthy, and
    // saying it is down would be a different kind of fabrication.
    const el = fakeStatusEl();
    const health = loadHealth(el, {
        fetch: async () => ({ ok: false, status: 429, json: async () => ({}) }),
    });

    return health.check().then((view) => {
        assert.notEqual(view.tone, "dot-green");
        assert.match(el.label.textContent, /verific/i);
    });
});

test("the status rail is polled on a timer that can be cancelled", () => {
    const scheduled = [];
    const cleared = [];
    const el = fakeStatusEl();
    const health = loadHealth(el, {
        fetch: async () => ({ ok: true, json: async () => ({}) }),
        setTimer: (fn, ms) => {
            scheduled.push(ms);
            return scheduled.length;
        },
        clearTimer: (id) => cleared.push(id),
    });

    health.start();
    assert.ok(scheduled.length >= 1, "the health rail is never checked");

    health.stop();
    assert.ok(cleared.length >= 1, "stop() left the poll running");
});
