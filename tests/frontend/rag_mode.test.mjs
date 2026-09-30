/**
 * A retrieval pipeline that fell back to TF-IDF must not be painted green.
 *
 * THE DEFECT
 * ----------
 * `RAGPipeline.initialize` catches every exception from `SentenceTransformer`
 * and falls back to TF-IDF. Nothing downstream noticed: `GET /api/health`
 * answered `status: "ok"` with `rag_chunks: 20` — a chunk count that is
 * identical either way — and `describe()` painted a green dot reading
 *
 *     "Sistema OK · RAG 20 chunks"
 *
 * So a deployment that had lost its embedding model reported itself healthy to
 * the recruiter, and every answer it gave was being retrieved from a weaker
 * space than the one the product claims to use.
 *
 * WHAT IS ASSERTED
 * ----------------
 * Only that the failure is visible. Whether TF-IDF is actually worse at
 * retrieval is NOT measured here: it has not been measured, and the brief for
 * this fix is explicitly to stop the degradation from being silent rather than
 * to characterise it. So no test in this file claims a quality difference — the
 * claim being made is the narrower and provable one, "the page stops saying
 * everything is fine".
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { loadWithGlobals } from "./harness.mjs";

/** A stand-in for a text node: records exactly what was written to it. */
function fakeText() {
    return { textContent: "", className: "" };
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

function loadHealth(el, payload) {
    return loadWithGlobals("createHealthStatus", {})(el, {
        fetch: async () => ({ ok: true, status: 200, json: async () => payload }),
    });
}

/** A healthy payload, plus whatever the test wants to change about it. */
function healthPayload(overrides = {}) {
    return {
        status: "ok",
        whisper_loaded: true,
        rag_chunks: 20,
        candidate_loaded: true,
        ...overrides,
    };
}

test("a TF-IDF pipeline is not painted green", async () => {
    const el = fakeStatusEl();
    const health = loadHealth(el, healthPayload({ rag_mode: "tfidf" }));

    const view = await health.check();

    assert.notEqual(
        view.tone,
        "dot-green",
        "a pipeline that fell back to TF-IDF is being reported as healthy",
    );
});

test("the rail says the retrieval is degraded", async () => {
    const el = fakeStatusEl();
    const health = loadHealth(el, healthPayload({ rag_mode: "tfidf" }));

    const view = await health.check();

    assert.match(
        view.text,
        /tf-?idf|degradad/i,
        `the rail gives the candidate no way to know retrieval changed: ` +
            `"${view.text}"`,
    );
    assert.doesNotMatch(
        view.text,
        /Sistema OK/,
        `the rail still reads as fully healthy: "${view.text}"`,
    );
});

test("a healthy pipeline is still green", async () => {
    // The control. Turning the rail amber on every deployment would make the
    // amber mean nothing.
    const el = fakeStatusEl();
    const health = loadHealth(el, healthPayload({ rag_mode: "embeddings" }));

    const view = await health.check();

    assert.equal(view.tone, "dot-green");
    assert.match(view.text, /20/);
});

test("a server that predates rag_mode is not declared broken", async () => {
    // /api/health gained the field in this change. A payload without it is an
    // older server, not a failed one, and the rail must not go amber over a
    // field that is simply absent.
    const el = fakeStatusEl();
    const { rag_mode, ...withoutMode } = healthPayload();
    void rag_mode;
    const health = loadHealth(el, withoutMode);

    const view = await health.check();

    assert.equal(view.tone, "dot-green");
});

test("an uninitialized pipeline is not reported as embeddings", async () => {
    // The pipeline reports a third state before initialize() runs. Claiming
    // "embeddings" there would be the same kind of lie this fix removes.
    const el = fakeStatusEl();
    const health = loadHealth(el, healthPayload({ rag_mode: "uninitialized" }));

    const view = await health.check();

    assert.notEqual(view.tone, "dot-green");
    assert.doesNotMatch(view.text, /Sistema OK/);
});
