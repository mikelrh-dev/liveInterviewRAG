/**
 * The Context panel must say which of the two things it is showing.
 *
 * THE DEFECT
 * ----------
 * A FAQ cache hit answers from a fixed string in `response_cache.py`: no LLM
 * runs and no context string is ever built. The server still retrieved two
 * chunks for the question — purely so the panel had something to draw — stored
 * them on the turn, and reported `has_context: true`, which is the one field
 * whose entire job is "this turn has context".
 *
 * This file's neighbours then described those chips as "the passages the answer
 * was actually built from — the credibility argument of the whole panel". For
 * roughly 18 of the most common interview questions that sentence is false, and
 * false in the direction a recruiter acts on: they are reading provenance.
 *
 * THE SHAPE OF THE FIX
 * --------------------
 * `done` now carries `context_grounding`: `"grounded"` when the RAG really did
 * write the answer, `"related"` when the passages were retrieved for the panel
 * alone. The panel still refreshes on a cache hit — suppressing it would leave
 * the previous turn's passages standing inside a panel that now belongs to this
 * one, which `fetchContext` already calls the worst failure it is capable of.
 * What changes is the WORDING: a `related` panel says so, in the panel, where
 * the reader is.
 *
 * An ABSENT `context_grounding` reads as grounded, and that is the correct
 * default rather than a lenient one: every answer an older server sends was
 * either RAG-written or a cache hit with no claim to make, and assuming the
 * optimistic reading for the first case is the only one that does not weaken
 * the panel's meaning for the answer that IS grounded.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { readAppJs, loadWithGlobals } from "./harness.mjs";

/** A context element that keeps whatever was written into it. */
function fakeContext() {
    return { html: "", set innerHTML(value) { this.html = value; } };
}

const CHUNKS = [
    { text: "El primer proyecto fue un detector de fraude.", source: "wiki/projects/fraud-detector.md", score: 0.72 },
    { text: "Trabajo con FastAPI y SQLite.", source: "wiki/skills/backend.md", score: 0.41 },
];

function render(grounding, chunks = CHUNKS) {
    return loadWithGlobals("renderContext", { escapeHtml: (s) => s, contextContent: fakeContext() })(
        chunks,
        grounding,
    );
}

const loadTurnState = () => loadWithGlobals("createTurnState", {})();

// ─── The panel says which of the two it is showing ─────────────────────────

test("a related panel does not claim the answer was built from them", () => {
    const context = fakeContext();
    loadWithGlobals("renderContext", { escapeHtml: (s) => s, contextContent: context })(
        CHUNKS,
        "related",
    );

    // Not "the panel must not mention building": the honest version of this
    // panel SAYS the answer was not built from them, and that sentence has to
    // survive. What must not survive is an UNQUALIFIED claim, so the check is
    // that every mention of building is immediately negated.
    assert.match(
        context.html,
        /no se constru[^<]*a partir de ellas/i,
        `the panel does not disclaim the provenance it is displaying: ${context.html}`,
    );
    for (const mention of context.html.matchAll(/constru\w*/gi)) {
        const before = context.html.slice(Math.max(0, mention.index - 6), mention.index);
        assert.equal(
            before,
            "no se ",
            `the panel claims, without saying otherwise, that the answer was ` +
                `built from these: ${context.html}`,
        );
    }
});

test("a related panel says what they actually are", () => {
    const context = fakeContext();
    loadWithGlobals("renderContext", { escapeHtml: (s) => s, contextContent: context })(
        CHUNKS,
        "related",
    );

    assert.match(
        context.html,
        /relacionad|relacion/i,
        `nothing on the panel tells the reader these are related rather than ` +
            `the source: ${context.html}`,
    );
});

test("a related panel still shows the passages", () => {
    // The point of the nuance over `has_context: false`. Hiding them would
    // leave the previous turn's standing here instead, which is worse.
    const context = fakeContext();
    loadWithGlobals("renderContext", { escapeHtml: (s) => s, contextContent: context })(
        CHUNKS,
        "related",
    );

    assert.match(context.html, /fraud-detector/, context.html);
    assert.match(context.html, /chunk-pill/, context.html);
});

test("a grounded panel makes no such apology", () => {
    const context = fakeContext();
    loadWithGlobals("renderContext", { escapeHtml: (s) => s, contextContent: context })(
        CHUNKS,
        "grounded",
    );

    assert.doesNotMatch(context.html, /relacionad|relacion/i, context.html);
    assert.match(context.html, /chunk-pill/, context.html);
});

test("an absent grounding is read as grounded", () => {
    // An older server. Every answer it sends that has chunks is RAG-written,
    // so the optimistic reading is the correct default.
    const context = fakeContext();
    loadWithGlobals("renderContext", { escapeHtml: (s) => s, contextContent: context })(
        CHUNKS,
        undefined,
    );

    assert.doesNotMatch(context.html, /relacionad|relacion/i, context.html);
});

test("an empty result says nothing was retrieved, related or not", () => {
    const context = fakeContext();
    loadWithGlobals("renderContext", { escapeHtml: (s) => s, contextContent: context })(
        [],
        "related",
    );

    assert.match(context.html, /No se recuper/i, context.html);
});

test("a value this build does not know is not trusted as grounded", () => {
    // The page reads the field to decide what to SAY, so an unrecognised value
    // must fall on the honest side of the uncertainty rather than the
    // comfortable one. A typo on the wire cannot become a stronger claim.
    const context = fakeContext();
    loadWithGlobals("renderContext", { escapeHtml: (s) => s, contextContent: context })(
        CHUNKS,
        "granded",
    );

    assert.match(context.html, /relacionad|relacion/i, context.html);
});

// ─── The payload reaches the renderer ──────────────────────────────────────

test("the committed grounding is remembered with the turn", () => {
    const state = loadTurnState();
    state.commit({ n: 2, has_context: true, context_grounding: "related" });

    assert.equal(state.contextTurn(), 2);
    assert.equal(state.contextGrounding(), "related");
});

test("a grounded turn is remembered as grounded", () => {
    const state = loadTurnState();
    state.commit({ n: 3, has_context: true, context_grounding: "grounded" });

    assert.equal(state.contextGrounding(), "grounded");
});

test("a turn with no grounding field is grounded", () => {
    const state = loadTurnState();
    state.commit({ n: 4, has_context: true });

    assert.equal(state.contextGrounding(), "grounded");
});

test("reset clears the grounding with the turn", () => {
    const state = loadTurnState();
    state.commit({ n: 5, has_context: true, context_grounding: "related" });

    state.reset();

    assert.equal(state.contextTurn(), null);
    assert.equal(state.contextGrounding(), null);
});

test("a context-free turn leaves no grounding behind", () => {
    const state = loadTurnState();
    state.commit({ n: 6, has_context: true, context_grounding: "grounded" });
    state.commit({ n: 7, has_context: false });

    assert.equal(state.contextTurn(), null);
    assert.equal(
        state.contextGrounding(),
        null,
        "a turn with no passages must not leave a provenance claim queued",
    );
});

// ─── The source comment must stop asserting the thing that is false ────────

test("app.js does not describe the chips as the answer's provenance", () => {
    const source = readAppJs();

    assert.doesNotMatch(
        source,
        /the passages the answer was actually built from/i,
        "app.js still claims, in prose, that the chips are the answer's source — " +
            "which is false for every FAQ cache hit",
    );
});

// ─── The two sides must speak the same words ───────────────────────────────

test("the grounding vocabulary matches the one the server emits", () => {
    // The page cannot import backend/conversation.py, so the two names are
    // written twice. This is what stops the duplication from becoming a
    // divergence: the page's two comparisons and its constant, against the
    // literal strings the Python builder puts on the wire.
    const { GROUNDED, RELATED } = readServerVocabulary();
    const source = readAppJs();

    const context = fakeContext();
    loadWithGlobals("renderContext", { escapeHtml: (s) => s, contextContent: context })(
        CHUNKS,
        GROUNDED,
    );
    assert.doesNotMatch(
        context.html,
        /relacionad|relacion/i,
        `the server's "${GROUNDED}" renders as related here, so the two sides ` +
            "disagree about which value means what",
    );

    const other = fakeContext();
    loadWithGlobals("renderContext", { escapeHtml: (s) => s, contextContent: other })(
        CHUNKS,
        RELATED,
    );
    assert.match(other.html, /relacionad|relacion/i, `the server's "${RELATED}" renders as grounded here`);

    const state = loadTurnState();
    state.commit({ n: 0, has_context: true, context_grounding: GROUNDED });
    assert.equal(state.contextGrounding(), GROUNDED);
    state.commit({ n: 1, has_context: true, context_grounding: RELATED });
    assert.equal(state.contextGrounding(), RELATED);

    // The two lifted functions compare against bare strings, so the constant is
    // documentation unless something checks it. This does.
    assert.ok(
        source.includes(`GROUNDED: "${GROUNDED}"`),
        `app.js's GROUNDING.GROUNDED is not the server's "${GROUNDED}"`,
    );
    assert.ok(
        source.includes(`RELATED: "${RELATED}"`),
        `app.js's GROUNDING.RELATED is not the server's "${RELATED}"`,
    );
});

/**
 * The two names, read out of the Python source rather than restated here.
 *
 * A restated pair would be a third copy that could agree with itself while the
 * server said something else -- the same self-certifying shape
 * tests/test_readme_test_counts.py exists to refuse. So the literals are
 * extracted from ``backend/conversation.py``, which is where the payload is
 * built.
 */
function readServerVocabulary() {
    const source = readFileSync(
        new URL("../../backend/conversation.py", import.meta.url),
        "utf8",
    );
    const value = (name) => {
        const match = source.match(new RegExp(`^${name} = "([a-z]+)"`, "m"));
        assert.ok(match, `backend/conversation.py no longer defines ${name}`);
        return match[1];
    };
    return { GROUNDED: value("GROUNDED"), RELATED: value("RELATED") };
}
