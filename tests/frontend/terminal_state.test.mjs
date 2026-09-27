/**
 * Terminal-state tests for the interview turn lifecycle.
 *
 * The contract under test: a turn settles EXACTLY ONCE, whichever of
 * `done` / `error` / `interview_end` / stream-EOF arrives, and later
 * signals are no-ops. Before the fix, an SSE `error` event with no chunk id
 * threw out of the read loop into a catch block that called
 * stopInterview(), so a single failed TTS sentence killed the interview; and
 * a truncated stream left `allChunksReceived` false forever, so the mic
 * never restarted and the audio indicator spun on.
 *
 * These run with Node's built-in runner -- no dependencies, no package.json:
 *   node --test tests/frontend/
 *
 * The unit under test is extracted verbatim from frontend/app.js so the test
 * exercises production source rather than a copy. The DOM side effects it
 * triggers are injected as hooks and asserted on; the DOM code that *calls*
 * the hooks is verified structurally by tests/test_sse_terminal_state.py.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const APP_JS = join(here, "..", "..", "frontend", "app.js");

/**
 * Pull a top-level function declaration out of app.js source and evaluate it
 * in isolation. app.js is a browser script that touches `document` at module
 * scope, so it cannot be imported directly; extracting the one pure function
 * keeps this test honest (real source) without needing a DOM or a bundler.
 */
function extractFunction(name) {
    const source = readFileSync(APP_JS, "utf8");
    const signature = `function ${name}(`;
    const start = source.indexOf(signature);
    assert.notEqual(start, -1, `${name}() not found in frontend/app.js`);

    const brace = source.indexOf("{", start);
    let depth = 0;
    for (let i = brace; i < source.length; i++) {
        if (source[i] === "{") depth++;
        else if (source[i] === "}") {
            depth--;
            if (depth === 0) {
                return source.slice(start, i + 1);
            }
        }
    }
    throw new Error(`unbalanced braces extracting ${name}()`);
}

function loadSettler() {
    // eslint-disable-next-line no-new-func
    const factory = new Function(`${extractFunction("createTurnSettler")}; return createTurnSettler;`);
    return factory();
}

// ─── Exactly-once settlement ─────────────────────────────────────────────

test("settles on the first terminal signal and returns true", () => {
    const calls = [];
    const turn = loadSettler()({ onSettle: (r) => calls.push(r) });

    assert.equal(turn.isSettled(), false);
    assert.equal(turn.settle("done"), true);
    assert.equal(turn.isSettled(), true);
    assert.deepEqual(calls, ["done"]);
});

test("a second terminal signal is a no-op", () => {
    const calls = [];
    const turn = loadSettler()({ onSettle: (r) => calls.push(r) });

    turn.settle("done");
    assert.equal(turn.settle("error"), false);
    assert.equal(turn.settle("eof"), false);
    assert.equal(turn.settle("interview_end"), false);

    // Exactly one teardown, whichever signal arrived first.
    assert.deepEqual(calls, ["done"]);
});

test("every terminal reason settles the turn", () => {
    for (const reason of ["done", "error", "interview_end", "eof"]) {
        const calls = [];
        const turn = loadSettler()({ onSettle: (r) => calls.push(r) });
        turn.settle(reason);
        assert.equal(turn.isSettled(), true, `${reason} did not settle the turn`);
        assert.deepEqual(calls, [reason]);
    }
});

test("EOF settles a truncated stream so the turn is not stranded", () => {
    // Simulates: server dies mid-generation, no terminal event ever arrives.
    const calls = [];
    const turn = loadSettler()({ onSettle: (r) => calls.push(r) });

    // No terminal event. Only EOF.
    turn.settle("eof");

    assert.equal(turn.isSettled(), true);
    assert.deepEqual(calls, ["eof"]);
});

test("an error arriving after done does not double-teardown", () => {
    const calls = [];
    const turn = loadSettler()({ onSettle: (r) => calls.push(r) });

    turn.settle("done");
    turn.settle("error");

    assert.deepEqual(calls, ["done"], "the error branch tore the turn down twice");
});

test("an error arriving before done still settles exactly once", () => {
    const calls = [];
    const turn = loadSettler()({ onSettle: (r) => calls.push(r) });

    turn.settle("error");
    turn.settle("done");

    assert.deepEqual(calls, ["error"]);
});

test("tolerates a missing hooks object", () => {
    // Defensive: a caller that forgets to inject hooks must not crash the
    // stream teardown.
    const turn = loadSettler()();
    assert.equal(turn.settle("done"), true);
    assert.equal(turn.settle("done"), false);
});

test("tolerates hooks without an onSettle callback", () => {
    const turn = loadSettler()({});
    assert.equal(turn.settle("done"), true);
    assert.equal(turn.settle("eof"), false);
});

// ─── Ordering: first signal wins, deterministically ──────────────────────

test("settlement is not re-entrant", () => {
    // onSettle calls settle() again (as a re-entrant teardown would). The
    // already-set flag must keep it a no-op instead of recursing.
    const calls = [];
    const turn = loadSettler()({
        onSettle: (reason) => {
            calls.push(reason);
            turn.settle("reentrant");
        },
    });

    turn.settle("done");

    assert.deepEqual(calls, ["done"]);
});
