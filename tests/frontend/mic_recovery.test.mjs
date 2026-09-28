/**
 * A turn that cannot start must leave the page in a state the user can act on.
 *
 * THE DEFECT
 * ----------
 * `getUserMedia` rejecting landed in `startRecording`'s catch, which reported
 * the message and called `setState("idle")` -- and stopped there. So:
 *
 *   - the avatar read idle, because the state machine had been moved,
 *   - `isInterviewActive` was still true, because nothing set it false,
 *   - and the mic button was still drawn as the control that ENDS a running
 *     interview, still named "Detener entrevista".
 *
 * Three indicators, one of them true. Recovery existed and was invisible: press
 * the button, watch the interview appear to end, press it again, and only then
 * does a new one start. A user who denied the microphone by accident, or whose
 * device was briefly busy, is left on a control that lies about what it does.
 *
 * The unsupported-codec branch had the same incoherence for the same reason --
 * both reported, moved the state, and left the running-interview bookkeeping
 * untouched -- so both are fixed by the same helper.
 *
 * THE RECOVERY IS TESTED THROUGH THE BUTTON
 * -----------------------------------------
 * Not by asserting a flag. The claim is that one press of the one control on
 * the page starts a new interview, so the test binds the real handler to the
 * real button, presses it, and watches the network.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom, producedBy } from "./dom.mjs";

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/** A window that has just been asked to start an interview, for real. */
function startEnv() {
    const env = createDom();
    env.recorder.fakes.FakeMediaRecorder.supported = ["audio/webm;codecs=opus"];
    const fn = env.loadApp(
        [
            "setStatus",
            "setState",
            "setMicLabel",
            "micLabel",
            "chooseMimeType",
            "ensureAudioContext",
            "startVad",
            "stopVad",
            "startRecording",
            "startListening",
            "toggleInterview",
            "startInterview",
            "initAudio",
            "resetInterviewView",
            "addMessage",
            "updateSessionInfo",
            "updateTurnCount",
            "createTurnState",
        ],
        { isInterviewActive: false },
    );
    // startInterview() -> resetInterviewView() reads the real turn state.
    fn.state.turnState = fn.createTurnState();
    // The handler init() binds, bound by hand so the press is the real thing.
    env.document.getElementById("btn-mic").addEventListener("click", fn.toggleInterview);
    env.onFetch(() => ({
        ok: true,
        status: 200,
        json: async () => ({ conversation_id: "conv-1", welcome_message: "Bienvenido" }),
    }));
    return { env, fn };
}

/** The state a user reads off the mic control. */
function micButton(env) {
    const btn = env.document.getElementById("btn-mic");
    return {
        label: btn.getAttribute("aria-label"),
        active: btn.classList.contains("active"),
        micIconHidden: btn.querySelector(".mic-icon").classList.contains("hidden"),
        stopIconHidden: btn.querySelector(".stop-icon").classList.contains("hidden"),
    };
}

test("a denied microphone does not leave the interview marked as running", async () => {
    // The real start path, not a hand-set flag: the conversation is created, the
    // button is put into its running state, and then the microphone is refused.
    const { env, fn } = startEnv();
    env.recorder.getUserMediaRejected = new Error("Permission denied");

    await fn.startInterview();
    await settle();
    await settle();

    assert.equal(
        fn.state.isInterviewActive,
        false,
        "the microphone was denied and nothing was ever recorded, yet the " +
            "interview is still marked as running",
    );
    env.close();
});

test("the mic control agrees with the avatar after a denial", async () => {
    const { env, fn } = startEnv();
    env.recorder.getUserMediaRejected = new Error("Permission denied");

    await fn.startInterview();
    await settle();
    await settle();

    assert.equal(
        env.document.body.dataset.state,
        "idle",
        "precondition: the avatar should be reading idle",
    );

    const shown = micButton(env);
    assert.equal(
        shown.label,
        "Iniciar entrevista",
        `the mic button still reads "${shown.label}" while the avatar reads idle. ` +
            "It promises to end an interview that is not running",
    );
    assert.equal(shown.active, false, "the mic button is still lit as a running interview");
    assert.equal(shown.micIconHidden, false, "the mic icon is hidden on a stopped interview");
    assert.equal(shown.stopIconHidden, true, "the stop icon is showing on a stopped interview");
    env.close();
});

test("one press of the mic retries after a denial", async () => {
    // The recovery, through the real button. Before the fix this press did
    // nothing the candidate could see: the interview was "active", so the
    // click ran stopInterview, and only a second press started a new one.
    const { env, fn } = startEnv();
    env.recorder.getUserMediaRejected = new Error("Permission denied");

    await fn.startInterview();
    await settle();
    await settle();
    const before = env.fetches.length;

    env.document
        .getElementById("btn-mic")
        .dispatchEvent(new env.window.MouseEvent("click", { bubbles: true }));
    await settle();
    await settle();
    await settle();

    const created = env.fetches
        .slice(before)
        .filter((call) => call.url === "/api/conversation");

    assert.equal(
        created.length,
        1,
        `one press of the mic after a denial produced ${created.length} new ` +
            `conversations (requests: ${JSON.stringify(
                env.fetches.slice(before).map((c) => c.url),
            )}). Recovery from a denied microphone must be a single press`,
    );
    env.close();
});

test("an unsupported codec leaves the page in the same recoverable state", async () => {
    // The same incoherence, one branch over: the line reported the problem, the
    // state machine went idle, and the running-interview bookkeeping was left
    // exactly as the microphone branch left it.
    const { env, fn } = startEnv();
    env.recorder.fakes.FakeMediaRecorder.supported = [];

    await fn.startInterview();
    await settle();
    await settle();

    const shown = micButton(env);
    assert.equal(
        shown.label,
        "Iniciar entrevista",
        `a browser that cannot record leaves the mic reading "${shown.label}"`,
    );
    assert.equal(
        fn.state.isInterviewActive,
        false,
        "a browser that cannot record still counts as an active interview",
    );
    env.close();
});

test("the mic control still describes a genuinely running interview", async () => {
    // The control case. Fixing the denial path must not flatten the button into
    // permanently saying "start": while an interview really is running it has to
    // keep naming the action it will perform.
    const { env, fn } = startEnv();

    await fn.startInterview();
    await settle();
    await settle();

    assert.equal(fn.state.isRecording, true, "precondition: the recorder should be running");

    const shown = micButton(env);
    assert.equal(shown.label, "Detener entrevista", "the running mic lost its name");
    assert.equal(shown.active, true, "the running mic is not lit");
    assert.equal(shown.stopIconHidden, false, "the stop icon is hidden while recording");
    env.close();
});
