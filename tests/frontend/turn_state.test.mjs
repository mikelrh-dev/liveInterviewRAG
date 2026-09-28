/**
 * Turn-state tests: the turn number comes from the server, not the DOM.
 *
 * The contract under test: `app.js` used to derive the turn by counting
 * `.message` elements, so from the second interview onward — the transcript is
 * never cleared — the counter ran ahead of the DB and the Context sidebar
 * asked for a turn that does not exist, 404ing in silence. The `done` payload
 * now names the turn the DB committed, and this state object is the only
 * thing that holds it. It never guesses: an unknown turn is `null`, which the
 * settler reads as "nothing to count, nothing to ask for".
 *
 * These run with Node's built-in runner -- no dependencies, no package.json:
 *   node --test tests/frontend/
 *
 * The units under test are extracted verbatim from frontend/app.js so the
 * tests exercise production source rather than a copy. The DOM side effects
 * they trigger are injected as parameters and asserted on; the code that
 * *calls* them is verified structurally by
 * tests/test_sse_terminal_state.py.
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

/**
 * Evaluate an extracted function with stand-ins for the globals it reads.
 *
 * The parameter names ARE the global names, so the extracted source resolves
 * them to the injected values instead of reaching for the real ones.
 */
function loadWithGlobals(name, globals) {
    const names = Object.keys(globals);
    const factory = new Function(
        ...names,
        `${extractFunction(name)}; return ${name};`,
    );
    return factory(...names.map((key) => globals[key]));
}

const loadTurnState = () => loadWithGlobals("createTurnState", {})();

// ─── The number is the server's, verbatim ──────────────────────────────────

test("reports the turn number the server committed", () => {
    const state = loadTurnState();

    assert.equal(state.commit({ n: 0, has_context: false }), 0);
    assert.equal(state.last(), 0);

    assert.equal(state.commit({ n: 7, has_context: false }), 7);
    assert.equal(state.last(), 7);
});

test("a turn number is never invented when the server sent none", () => {
    const state = loadTurnState();

    // `done` with no turn: a failed write, an empty transcription, an LLM that
    // died mid-stream. Guessing here is what 404s the Context sidebar.
    assert.equal(state.commit({}), null);
    assert.equal(state.last(), null);
    assert.equal(state.contextTurn(), null);
});

test("tolerates a missing payload entirely", () => {
    const state = loadTurnState();

    assert.equal(state.commit(undefined), null);
    assert.equal(state.last(), null);
});

test("rejects a turn number that is not a plain non-negative integer", () => {
    const state = loadTurnState();

    for (const n of [-1, 1.5, "3", null, Number.NaN]) {
        assert.equal(state.commit({ n, has_context: false }), null, `accepted n=${n}`);
        assert.equal(state.last(), null);
    }
});

// ─── The Context panel is only asked what exists ──────────────────────────

test("asks for context only when the turn has some", () => {
    const state = loadTurnState();

    state.commit({ n: 4, has_context: true });
    assert.equal(state.contextTurn(), 4);
});

test("does not ask for context the turn does not have", () => {
    const state = loadTurnState();

    // The sidebar already showed context for turn 3; a `done` that reports no
    // context for turn 4 must not leave 3 queued up as a request to make.
    state.commit({ n: 3, has_context: true });
    state.commit({ n: 4, has_context: false });

    assert.equal(state.contextTurn(), null, "a context-free turn must queue no request");
    assert.equal(state.last(), 4, "but the turn counter still advances");
});

// ─── A new interview starts clean ─────────────────────────────────────────

test("reset clears the state of the previous interview", () => {
    const state = loadTurnState();
    state.commit({ n: 5, has_context: true });

    state.reset();

    assert.equal(state.last(), null, "the second interview inherited a turn number");
    assert.equal(state.contextTurn(), null);
});

test("a commit after reset is the new interview's first turn", () => {
    const state = loadTurnState();
    state.commit({ n: 5, has_context: true });
    state.reset();

    assert.equal(state.commit({ n: 0, has_context: false }), 0);
    assert.equal(state.last(), 0);
});

test("resetInterviewView clears the transcript, the state and the counter", () => {
    const cleared = [];
    const counts = [];
    const state = loadTurnState();
    state.commit({ n: 9, has_context: true });

    loadWithGlobals("resetInterviewView", {
        conversation: { replaceChildren: () => cleared.push("cleared") },
        turnState: state,
        updateTurnCount: (n) => counts.push(n),
    })();

    assert.deepEqual(cleared, ["cleared"], "the old transcript was left on screen");
    assert.deepEqual(counts, [0], "the sidebar still showed the last interview");
    assert.equal(state.last(), null, "the new interview inherited a turn number");
    assert.equal(state.contextTurn(), null);
});
