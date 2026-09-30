/**
 * The page must not paint green over a body the server called degraded.
 *
 * THE DEFECT THIS EXTENDS
 * -----------------------
 * `GET /api/health` computed nothing: `status` was the literal `"ok"`, written
 * before the code looked at anything. A service with an unreachable database,
 * an empty RAG index and no API key answered `200 {"status":"ok"}`, so an
 * external monitor had nothing to interpret.
 *
 * The endpoint now derives `status` from the facts it already publishes. That
 * only helps if the page agrees with it, and this file is the half of the
 * contract that lives in the browser: `describe()` used to classify on four
 * individual fields, so any degradation those four do not cover — the store
 * being unreachable, most obviously — was rendered as a green dot while the
 * endpoint said `degraded`. The page would then contradict the server it is
 * reading.
 *
 * The rule here is one-directional, and it is the same one the rest of this
 * suite applies: an ABSENT field is never a problem. `/api/health` gained
 * `status` and `persistence` later than some deployments were built, and a
 * server that has not heard of a field is an older server, not a broken one.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { loadWithGlobals } from "./harness.mjs";

function fakeText() {
    return { textContent: "", className: "" };
}

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

/** A healthy payload from the current build, plus whatever the test changes. */
function healthPayload(overrides = {}) {
    return {
        status: "ok",
        problems: [],
        whisper_loaded: true,
        rag_chunks: 20,
        rag_mode: "embeddings",
        candidate_loaded: true,
        persistence: "ok",
        ...overrides,
    };
}

test("a healthy service is still painted green", async () => {
    // The control. An amber rail on every deployment would make amber mean
    // nothing, and this is the failure mode of every "report the degradation"
    // fix.
    const el = fakeStatusEl();
    const health = loadHealth(el, healthPayload());

    const view = await health.check();

    assert.equal(view.tone, "dot-green", view.text);
    assert.match(view.text, /Sistema OK/);
});

test("an unreachable store is not painted green", async () => {
    const el = fakeStatusEl();
    const health = loadHealth(
        el,
        healthPayload({ status: "degraded", problems: ["persistence"], persistence: "error" }),
    );

    const view = await health.check();

    assert.notEqual(view.tone, "dot-green", view.text);
    assert.doesNotMatch(view.text, /Sistema OK/, view.text);
});

test("the rail names the store, not just 'degraded'", async () => {
    const el = fakeStatusEl();
    const health = loadHealth(
        el,
        healthPayload({ status: "degraded", problems: ["persistence"], persistence: "error" }),
    );

    const view = await health.check();

    assert.match(
        view.text,
        /base de datos|persistencia|almacenamiento/i,
        `the candidate is told the system is degraded but not what is wrong: ` +
            `"${view.text}"`,
    );
});

test("a store the operator switched off is not a fault", async () => {
    // `PERSISTENCE_ENABLED=false` means nothing that would have been written is
    // missing. Amber over that would punish an operator for a decision.
    const el = fakeStatusEl();
    const health = loadHealth(el, healthPayload({ persistence: "disabled" }));

    const view = await health.check();

    assert.equal(view.tone, "dot-green", view.text);
});

test("the rail never contradicts the server's own verdict", async () => {
    // The invariant, and the reason for reading `status` at all: a body the
    // server called degraded must not render green, even for a degradation
    // this build has no specific wording for.
    const el = fakeStatusEl();
    const health = loadHealth(
        el,
        healthPayload({
            status: "degraded",
            problems: ["something_this_build_has_never_heard_of"],
        }),
    );

    const view = await health.check();

    assert.notEqual(view.tone, "dot-green", view.text);
    assert.match(view.text, /degradad/i, view.text);
});

test("a server that predates `status` is not declared broken", async () => {
    // Same rule the rag_mode tests apply, for the same reason: an absent field
    // is an older server, not a failed one.
    const el = fakeStatusEl();
    const { status, problems, persistence, ...older } = healthPayload();
    void status;
    void problems;
    void persistence;
    const health = loadHealth(el, older);

    const view = await health.check();

    assert.equal(view.tone, "dot-green", view.text);
});

test("a malformed status does not paint green either", async () => {
    // `status: true` is not a health claim this build knows how to read, and
    // "anything that is not positively healthy is not reported as healthy" is
    // the rule describe() already documents for every other field.
    const el = fakeStatusEl();
    const health = loadHealth(el, healthPayload({ status: 1 }));

    const view = await health.check();

    assert.notEqual(view.tone, "dot-green", view.text);
});
