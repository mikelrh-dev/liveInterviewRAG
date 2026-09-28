/**
 * The status line has to be able to say that something failed, in colour.
 *
 * THREE DEFECTS, ONE ROOT CAUSE
 * -----------------------------
 * `#status` is the only line on the page that reports state, and it had three
 * separate faults that compounded on exactly the three failure paths a
 * candidate can hit:
 *
 *   1. `setStatus(msg, true)` wrote the class list `hud-status true`. `true` is
 *      a JavaScript boolean that leaked into a class attribute, and no stylesheet
 *      in the project has a `.true` rule -- so the call was reaching for an
 *      error style that does not exist.
 *   2. `setState()` then executed `statusEl.className = "hud-status"` on the
 *      very next line, discarding whatever `setStatus` had just written. Even a
 *      correctly spelled class would have lasted zero frames.
 *   3. The stylesheet had rules for `.listening`, `.processing` and `.error`
 *      and none for `.speaking` -- the longest phase of every turn had no
 *      colour at all.
 *
 * The root cause is not any of those three. It is that `#status.className` had
 * two writers, in two different functions, neither of which knew about the
 * other. Everything here drives the real functions in the real DOM and reads
 * the real class list, because "the error styling reaches the user" is only
 * observable after both writers have run.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createDom, producedBy } from "./dom.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "..", "..", "frontend", "style.css"), "utf8");

/** The declarations of `selector`, with stylesheet comments removed. */
function decls(selector) {
    const rules = css.replace(/\/\*[\s\S]*?\*\//g, "");
    const at = rules.indexOf(selector);
    assert.notEqual(at, -1, `\`${selector}\` has no rule in style.css`);
    const brace = rules.indexOf("{", at + selector.length - 1);
    return rules.slice(brace + 1, rules.indexOf("}", brace));
}

/** A window with the status functions loaded and the real #status in it. */
function statusEnv() {
    const env = createDom();
    const fn = env.loadApp(["setStatus", "setState"]);
    return { env, fn, status: env.document.getElementById("status") };
}

// ─── 1. One writer of the class list ──────────────────────────────────────

test("an error status is not overwritten by the state that follows it", () => {
    // The exact sequence every failure path in this file performs: report the
    // error, then move the state machine. Before the fix the second call
    // replaced the class list outright, so the error survived zero frames.
    const { env, fn, status } = statusEnv();

    fn.setStatus("Acceso denegado", true);
    assert.equal(
        status.classList.contains("error"),
        true,
        `setStatus(msg, true) produced the class list "${status.className}", which ` +
            "carries no error state at all",
    );

    fn.setState("idle");
    assert.equal(
        status.classList.contains("error"),
        true,
        `setState() wiped the error immediately: the class list is now ` +
            `"${status.className}"`,
    );
    env.close();
});

test("the boolean and the class name mean the same thing", () => {
    // `true` is the historical spelling at three call sites; "error" is what
    // the narrator passes and what the stylesheet has a rule for. Both must
    // land on the same state, or the next edit can quietly pick the other one.
    for (const spelling of [true, "error"]) {
        const { env, status } = statusEnv();
        env.loadApp(["setStatus", "setState"]).setStatus("fallo", spelling);
        assert.ok(
            status.classList.contains("error"),
            `setStatus(msg, ${JSON.stringify(spelling)}) did not mark the line as ` +
                `an error: class list is "${status.className}"`,
        );
        assert.ok(
            !status.classList.contains("true"),
            `setStatus(msg, ${JSON.stringify(spelling)}) leaked the literal ` +
                `class "true" into the DOM`,
        );
        env.close();
    }
});

test("a plain status clears a previous error", () => {
    // The other direction. A stale error is its own lie: the page must stop
    // shouting about a failure the candidate has already moved past.
    const { env, status } = statusEnv();
    env.loadApp(["setStatus", "setState"]).setStatus("fallo", true);
    env.loadApp(["setStatus", "setState"]).setStatus("Escuchando…");

    assert.equal(
        status.classList.contains("error"),
        false,
        `the error survived a normal status: class list is "${status.className}"`,
    );
    env.close();
});

test("the state class and the error flag never contradict each other", () => {
    // A matrix rather than one ordering. Whatever the call order, the class list
    // is `hud-status`, plus the current state if it is not idle, plus `error`
    // if the last message was a failure -- and nothing else. This is the
    // property that two writers cannot hold, so it is the one worth pinning.
    const orders = [
        ["setStatus:fail", "setState:listening", "setState:processing"],
        ["setState:listening", "setStatus:fail", "setState:listening"],
        ["setState:processing", "setState:listening", "setStatus:fail"],
        ["setStatus:ok", "setState:speaking", "setStatus:fail", "setState:processing"],
        ["setState:listening", "setState:speaking", "setStatus:ok"],
    ];

    for (const order of orders) {
        const { env, status, fn } = statusEnv();
        for (const step of order) {
            const [what, arg] = step.split(":");
            // The text records what the line last said, so the expectation below
            // is derived from what a user would read rather than from a flag.
            if (what === "setStatus") {
                fn.setStatus(arg === "fail" ? "fallo" : "texto", arg === "fail" ? true : undefined);
            } else {
                fn.setState(arg);
            }
        }

        const expected = ["hud-status"];
        if (fn.state.currentState !== "idle") expected.push(fn.state.currentState);
        if (status.textContent === "fallo") expected.push("error");
        expected.sort();

        assert.deepEqual(
            status.className.split(/\s+/).filter(Boolean).sort(),
            expected,
            `after [${order.join(" -> ")}] the class list is "${status.className}"`,
        );
        env.close();
    }
});

// ─── 2. Every state the machine can be in has a colour ────────────────────

test("every state the machine can report is styled in the stylesheet", () => {
    // `speaking` is the longest phase of every turn and had no rule at all, so
    // the one state where the candidate is definitely getting an answer looked
    // identical to the one where nothing is happening.
    for (const state of ["listening", "processing", "error", "speaking"]) {
        const rule = decls(`#status.${state}`);
        assert.match(
            rule,
            /color:\s*var\(--/,
            `#status.${state} has no colour, so "${state}" is drawn exactly like ` +
                "idle and the status line cannot report the phase",
        );
    }
});

test("the state actually reaches the element when the machine moves", () => {
    // A rule that exists is not the claim; the class arriving is.
    const { env, status, fn } = statusEnv();
    for (const state of ["listening", "speaking", "processing"]) {
        fn.setState(state);
        assert.ok(
            status.classList.contains(state),
            `setState("${state}") did not put "${state}" on #status: class list ` +
                `is "${status.className}"`,
        );
    }
    fn.setState("idle");
    assert.ok(
        !status.classList.contains("idle"),
        `#status still carries "idle", which no rule styles: "${status.className}"`,
    );
    env.close();
});

// ─── 3. The three paths a candidate can actually hit ──────────────────────

/** A window with the recording path loaded, mid-interview. */
function recordingEnv() {
    const env = createDom();
    const fn = env.loadApp(
        [
            "setStatus",
            "setState",
            "setMicLabel",
            "chooseMimeType",
            "startVad",
            "stopVad",
            "startRecording",
            "ensureAudioContext",
        ],
        {
            audioContext: producedBy(() => new env.recorder.fakes.FakeAudioContext()),
            isInterviewActive: true,
            mediaStream: producedBy(() => env.recorder.fakes.mediaStream),
        },
    );
    return { env, fn, status: env.document.getElementById("status") };
}

test("a denied microphone is reported in red, and stays reported", async () => {
    const { env, fn, status } = recordingEnv();
    // A browser that CAN record, so this reaches the permission failure rather
    // than the codec branch: no stream yet, and getUserMedia refuses.
    env.recorder.fakes.FakeMediaRecorder.supported = ["audio/webm;codecs=opus"];
    env.recorder.getUserMediaRejected = new Error("Permission denied");
    fn.state.mediaStream = null;

    await fn.startRecording();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(
        env.recorder.getUserMediaCalls,
        1,
        "getUserMedia was never asked, so this did not test a denied microphone",
    );
    assert.equal(
        status.classList.contains("error"),
        true,
        `the microphone was denied and #status is "${status.className}": the ` +
            "candidate is told the text but not shown the failure",
    );
    assert.match(
        status.textContent,
        /micrófono/i,
        `the status line does not name the microphone: it says ` +
            `"${status.textContent}"`,
    );
    env.close();
});

test("an unsupported codec is reported in red, and stays reported", async () => {
    const { env, fn, status } = recordingEnv();
    // No codec the browser claims to support: the exact condition the branch
    // exists for, and the one that used to leave the line in the default class.
    // A live stream is already in hand, so this is a codec failure and not a
    // permission one.
    env.recorder.fakes.FakeMediaRecorder.supported = [];
    fn.state.selectedMimeType = "";

    await fn.startRecording();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(
        env.recorder.getUserMediaCalls,
        0,
        "the microphone was re-requested, so this did not test an unsupported codec",
    );
    assert.equal(
        status.classList.contains("error"),
        true,
        `the browser cannot record and #status is "${status.className}"`,
    );
    env.close();
});

test("a conversation that cannot be created is reported in red", async () => {
    // The third path: the POST to /api/conversation fails, so there is no
    // interview to run and nothing on the page would otherwise say so.
    const env = createDom();
    const fn = env.loadApp(
        [
            "setStatus",
            "setState",
            "setMicLabel",
            "initAudio",
            "resetInterviewView",
            "addMessage",
            "updateSessionInfo",
            "updateTurnCount",
            "startListening",
            "startRecording",
            "startVad",
            "stopVad",
            "chooseMimeType",
            "ensureAudioContext",
            "startInterview",
        ],
        { isInterviewActive: false },
    );
    env.onFetch(() => ({ ok: false, status: 503, json: async () => ({}) }));

    await fn.startInterview();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const status = env.document.getElementById("status");
    assert.equal(
        status.classList.contains("error"),
        true,
        `creating the conversation failed and #status is "${status.className}": ` +
            "the page sits on its initial text pretending nothing happened",
    );
    assert.equal(
        fn.state.isInterviewActive,
        false,
        "the interview is marked active after a failed start",
    );
    env.close();
});
