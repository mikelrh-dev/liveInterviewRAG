/**
 * A clone with no LLM key must be told before the first turn, not after.
 *
 * THE DEFECT THIS FIXES
 * ---------------------
 * Neither provider key is validated at startup — `backend/config.py:55-56`
 * both default to `""` — so a fresh clone with an unedited `.env` boots
 * perfectly and fails its FIRST turn with whatever the provider happens to
 * say: a quota, a 400, a model name. None of those name the one fact that
 * would have explained it, and the instruction to set a key is 200 lines
 * above the button in the README.
 *
 * THE RULE THIS SUITE ENFORCES
 * ----------------------------
 * `GET /api/config` publishes `llm_configured`, a boolean. `warnIfNoLLMCredential`
 * renders a message in the transcript when — and only when — that boolean is
 * explicitly `false`.
 *
 * The `only when` is the load-bearing half, and it is the same rule
 * health_degraded.test.mjs and rag_mode.test.mjs already apply for the same
 * reason: an ABSENT field is never a problem. A server that has not heard of
 * `llm_configured` is an older server. Turning its silence into a credential
 * error would be inventing a fault out of a schema difference — the same defect
 * as printing a latency figure no code produced.
 *
 * `llm_configured: true` is not the mirror image of `false`, either. It says a
 * key is present, not that the key works, so the page says nothing on `true`:
 * a key can be set and wrong, and a page that announced "configured, you are
 * fine" would be claiming something no code here can know.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { loadWithGlobals } from "./harness.mjs";

/**
 * The subject is `warnIfNoLLMCredential`, not `addMessage` — so `addMessage` is
 * a stub that records its calls rather than the real one, which needs a DOM and
 * would be testing the transcript renderer instead.
 */
function makeWarner(written) {
    const addMessage = (type, text) => {
        written.push({ type, text });
        return { textContent: text };
    };
    return loadWithGlobals("warnIfNoLLMCredential", {
        addMessage,
        scrollToBottom: () => {},
        llmCredentialWarningShown: false,
    });
}

function warn(cfg) {
    const written = [];
    makeWarner(written)(cfg);
    return written;
}

test("no key configured renders a message", () => {
    const written = warn({ llm_configured: false });

    assert.equal(written.length, 1, `expected one message, got ${JSON.stringify(written)}`);
});

test("the message is an error, so it is styled as one", () => {
    // `.message.error` is the only existing class that reads as a fault, and
    // style.css is not editable here. A "system" message would look like
    // narration and be scrolled past.
    const [message] = warn({ llm_configured: false });

    assert.equal(message.type, "error");
});

test("the message names the variables and the file to edit", () => {
    // A notice that says "configure a key" without naming the variables and the
    // filename has moved the problem, not solved it: the reader still has to
    // go and work out which of two providers and which file.
    const [message] = warn({ llm_configured: false });

    assert.match(message.text, /GOOGLE_API_KEY/, message.text);
    assert.match(message.text, /OPENROUTER_API_KEY/, message.text);
    assert.match(message.text, /\.env/, message.text);
});

test("a configured deployment is told nothing", () => {
    // The control. A notice on every load would train a reader to dismiss it,
    // and the one reader who has no key is exactly the one who needs to read
    // it. And `true` cannot mean "your key works" — a key can be set and wrong.
    assert.deepEqual(warn({ llm_configured: true }), []);
});

test("a server that predates the field is not told it has no key", () => {
    // The rule the rest of this suite applies. `undefined` is an older server,
    // not a missing credential.
    assert.deepEqual(warn({}), []);
    assert.deepEqual(warn({ llm_configured: undefined }), []);
    assert.deepEqual(warn({ llm_configured: null }), []);
});

test("a truthy non-boolean is not read as a missing key", () => {
    // `"false"` and `0` are what a hand-rolled serializer would produce. The
    // notice is gated on `=== false` precisely so neither of them can raise a
    // credential error the server did not report.
    assert.deepEqual(warn({ llm_configured: "false" }), []);
    assert.deepEqual(warn({ llm_configured: 0 }), []);
});

test("the notice is written once, not once per config load", () => {
    // The transcript is not a log file: a message rendered into it is permanent
    // noise the reader cannot remove. `init()` can fire this fetch more than
    // once, so the flag is what stops a second copy appearing.
    const written = [];
    const warner = makeWarner(written);

    warner({ llm_configured: false });
    assert.equal(written.length, 1);

    // Same closure, flag now set by the first call: a second config load must
    // add nothing.
    warner({ llm_configured: false });
    assert.equal(
        written.length,
        1,
        `the notice was rendered twice: ${JSON.stringify(written)}`,
    );
});
