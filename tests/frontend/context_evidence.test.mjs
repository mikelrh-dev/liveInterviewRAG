/**
 * The evidence panel must never show another turn's passages.
 *
 * THE DEFECT
 * ----------
 * `fetchContext` read
 *
 *     if (!res.ok) {
 *         // Context endpoint fails silently — hide panel, no error
 *         return;
 *     }
 *
 * and the comment describes something the code does not do. Nothing is hidden.
 * The function returns, and `#context-content` still holds whatever
 * `renderContext` wrote for the PREVIOUS turn.
 *
 * So a 404 leaves turn N-1's evidence on screen inside a panel that now belongs
 * to turn N. That is the worst failure this panel is capable of: the passages
 * look authoritative, they are attributed to the current turn, and they are
 * wrong. A recruiter reading them is reading the evidence for a different
 * answer, and nothing on the page says so.
 *
 * A 404 is not an exotic status here. `turnState.contextTurn()` deliberately
 * declines to infer a turn number, and a number the server does not have is
 * exactly what produces one.
 *
 * The network-failure branch had the same shape: `console.warn` and nothing
 * else.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom } from "./dom.mjs";

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/** A window with the evidence pipeline loaded, showing turn 1's evidence. */
function panelEnv() {
    const env = createDom();
    const fn = env.loadApp(
        ["fetchContext", "renderContext", "escapeHtml"],
        { conversationId: "conv-1" },
    );
    const content = env.document.getElementById("context-content");

    render(fn, [
        { score: 0.91, text: "EVIDENCIA DE LA PREGUNTA ANTERIOR", source: "cv.md" },
    ]);
    return { env, fn, content };
}

function render(fn, chunks) {
    fn.renderContext(chunks);
    return fn;
}

test("precondition: the panel really is showing the previous turn", () => {
    const { env, content } = panelEnv();
    assert.match(
        content.textContent,
        /EVIDENCIA DE LA PREGUNTA ANTERIOR/,
        "the fixture did not put the previous turn's evidence on screen, so the " +
            "rest of this suite would pass for the wrong reason",
    );
    env.close();
});

test("a 404 for this turn does not leave the previous turn's evidence standing", async () => {
    const { env, fn, content } = panelEnv();
    env.onFetch(() => ({ ok: false, status: 404, json: async () => ({}) }));

    await fn.fetchContext(2);
    await settle();

    assert.doesNotMatch(
        content.textContent,
        /EVIDENCIA DE LA PREGUNTA ANTERIOR/,
        'turn 1\'s evidence is still on screen after turn 2\'s fetch 404d. The ' +
            "panel looks authoritative and is attributing the previous answer's " +
            "passages to this one",
    );
    env.close();
});

test("a 500 for this turn does not leave the previous turn's evidence standing", () => {
    // The status is not the point; the stale content is. A 404 is just the
    // easiest way to get there.
    const { env, fn, content } = panelEnv();
    env.onFetch(() => ({ ok: false, status: 500, json: async () => ({}) }));

    return fn.fetchContext(2).then(() => {
        assert.doesNotMatch(content.textContent, /EVIDENCIA DE LA PREGUNTA ANTERIOR/);
        env.close();
    });
});

test("a dropped connection does not leave the previous turn's evidence standing", async () => {
    // The other branch, and the more likely one: the `catch` logged a warning
    // and left the panel exactly as it was.
    const { env, fn, content } = panelEnv();
    env.onFetch(() => {
        throw new TypeError("Failed to fetch");
    });

    await fn.fetchContext(2);
    await settle();

    assert.doesNotMatch(
        content.textContent,
        /EVIDENCIA DE LA PREGUNTA ANTERIOR/,
        "the request failed outright and the previous turn's evidence is still " +
            "on screen",
    );
    env.close();
});

test("a failure says so, rather than showing nothing at all", async () => {
    // Clearing the panel is necessary but not sufficient. An empty box invites
    // the reader to conclude the retriever found nothing, which is a different
    // claim from "we never found out", and this panel exists to make claims
    // about provenance.
    const { env, fn, content } = panelEnv();
    env.onFetch(() => ({ ok: false, status: 404, json: async () => ({}) }));

    await fn.fetchContext(2);
    await settle();

    assert.ok(
        content.querySelector(".context-empty"),
        `after a failure the panel is ${JSON.stringify(content.textContent)}: no ` +
            "message, so an empty rail reads as 'nothing was retrieved'",
    );
    env.close();
});

test("an empty result still says nothing was retrieved", async () => {
    // The control, and the distinction that matters. An empty list is a real
    // answer from the retriever and keeps its own wording; a failure is a
    // different fact and must not borrow it.
    const { env, fn, content } = panelEnv();
    env.onFetch(() => ({ ok: false, status: 404, json: async () => ({}) }));
    await fn.fetchContext(2);
    await settle();
    const afterFailure = content.textContent;

    env.onFetch(() => ({ ok: true, status: 200, json: async () => [] }));
    await fn.fetchContext(3);
    await settle();

    assert.notEqual(
        content.textContent,
        afterFailure,
        "a successful empty result is indistinguishable from a failed fetch, so " +
            "the panel cannot tell 'nothing retrieved' from 'never found out'",
    );
    env.close();
});

test("a successful result still renders", async () => {
    // The control. Fixing the failure path must not break the success path.
    const { env, fn, content } = panelEnv();
    env.onFetch(() => ({
        ok: true,
        status: 200,
        json: async () => [{ score: 0.5, text: "EVIDENCIA NUEVA", source: "cv.md" }],
    }));

    await fn.fetchContext(3);
    await settle();

    assert.match(
        content.textContent,
        /EVIDENCIA NUEVA/,
        `a successful fetch rendered "${content.textContent}"`,
    );
    assert.doesNotMatch(content.textContent, /EVIDENCIA DE LA PREGUNTA ANTERIOR/);
    env.close();
});
