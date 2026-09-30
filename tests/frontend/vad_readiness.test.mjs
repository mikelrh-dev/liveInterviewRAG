/**
 * A turn's audio stack must be READY, and the guard has to test for readiness.
 *
 * THE DEFECT
 * ----------
 * `initAudio()` guarded on the wrong variable:
 *
 *     895  async function initAudio() {
 *     896      if (audioContext) return;          // <- existence, not readiness
 *     897
 *     898      try {
 *     899          audioContext = new (window.AudioContext || ... )();
 *     900          if (audioContext.state === "suspended") {
 *     901              throw new Error("AudioContext blocked");
 *     902          }
 *     903
 *     904          analyserNode = audioContext.createAnalyser();   // unreachable
 *
 * `audioContext` is assigned on 899, BEFORE the throw that 901 can raise. So one
 * failure left it truthy, and from then on 896 returned early on every call:
 * `analyserNode` stayed `null` for the rest of the page's life, and no amount of
 * retrying could reach 904.
 *
 * WHAT THAT COSTS
 * ---------------
 * `startVad()` opens with `if (!analyserNode) return;`, so the VAD never starts.
 * The VAD is what decides a turn has ENDED -- the candidate's silence. Without
 * it the only bound left is the recording cap, which is the wrong shape of
 * answer: it cuts the audio at the limit instead of submitting it at the pause,
 * so every turn runs to the ceiling and the candidate waits out a silence the
 * page cannot hear. The symptom is a `console.warn` nobody reads.
 *
 * The recovery was half-built and aimed at the wrong half. The overlay is bound
 * to `resumeAudioContext`, which resumed the CONTEXT -- and never built the
 * analyser that was missing. It also only treated `"suspended"`, so a context
 * that came back `"closed"` was equally unrecoverable, with the same guard
 * blocking it.
 *
 * WHY NO EXISTING TEST CAUGHT IT
 * -----------------------------
 * Two suites name `analyserNode`, and both inject their own
 * (`producedBy(makeAnalyser)`), bypassing `initAudio` entirely. Nothing observed
 * `initAudio` CONSTRUCTING the analyser, which is the only thing that can fail
 * here. That is what this file is for.
 *
 * THE READINESS CRITERION
 * -----------------------
 * A VAD turn needs the analyser; the context is only the thing the analyser is
 * made from. So "ready" is "analyserNode built", and the guard says exactly
 * that. The context is an INPUT to readiness and never a proxy for it -- which
 * is the whole bug: the guard treated its input as its output. Every other
 * consumer agrees: `startRecording` gates on `audioContext && analyserNode`
 * together, `startVad` gates on `analyserNode` alone, and the visualisation loop
 * gates on `analyserNode`. The guard was the only place that asked the wrong
 * question.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom } from "./dom.mjs";

/**
 * A page whose audio stack is loaded but not yet built.
 *
 * `statesOnConstruct` is the sequence of states successive contexts report, so a
 * failure can be scheduled: `["suspended"]` is a browser that never grants the
 * gesture, and appending `"running"` is the click that finally grants it.
 */
function audioEnv(statesOnConstruct, options = {}) {
    const env = createDom({ raf: true, ...options });
    env.recorder.statesOnConstruct = statesOnConstruct;
    const fn = env.loadApp(["initAudio", "resumeAudioContext", "startVad"]);
    // The binding init() makes at app.js:883, reproduced rather than simulated,
    // so the recovery is reached by pressing the overlay and not by calling the
    // handler directly -- which would prove nothing about whether the two are
    // still wired together.
    env.document
        .getElementById("audio-blocked-overlay")
        .addEventListener("click", fn.resumeAudioContext);
    return { env, fn };
}

/** Click the overlay, the way a candidate clicks it. */
async function clickOverlay(env) {
    env.document
        .getElementById("audio-blocked-overlay")
        .dispatchEvent(new env.window.MouseEvent("click", { bubbles: true }));
    // The handler is async; one turn of the macrotask queue lets an awaited
    // resume() settle before the test looks.
    await new Promise((resolve) => setTimeout(resolve, 0));
}

test("a failed initAudio is retried and ends with a built analyser", async () => {
    // The gesture the first attempt was missing arrives on the second call, so
    // the context built by that call comes back running.
    const { env, fn } = audioEnv(["suspended", "running"]);

    await fn.initAudio();
    assert.equal(
        fn.state.analyserNode,
        null,
        "precondition: the first attempt is blocked, so no analyser exists yet",
    );
    assert.equal(
        fn.state.audioContext.state,
        "suspended",
        "precondition: the context was still created before the throw, which is " +
            "what made the old guard return early on every later call",
    );

    await fn.initAudio();

    assert.ok(
        fn.state.analyserNode,
        "the retry returned without building an analyser, so the VAD can never " +
            "start: startVad() opens with `if (!analyserNode) return;`",
    );
    assert.equal(fn.state.analyserNode.fftSize, 64, "the mic analyser's fftSize");
    assert.ok(
        // ArrayBuffer.isView, not instanceof: the buffer is built from the
        // jsdom realm's Uint8Array, which is a different constructor from this
        // file's, so `instanceof` would report a false negative for a buffer
        // that is in fact correct.
        ArrayBuffer.isView(fn.state.micTimeBuffer) &&
            fn.state.micTimeBuffer.length === fn.state.analyserNode.fftSize,
        "the hoisted time-domain buffer is sized from the analyser, and vadLoop " +
            "allocates it on demand if it is missing",
    );
    env.close();
});

test("the VAD can schedule a frame once the analyser exists", async () => {
    // The consequence rather than the flag: a built analyser is only worth having
    // if the loop that reads it can now run.
    const { env, fn } = audioEnv(["suspended", "running"]);

    await fn.initAudio();
    await fn.initAudio();
    assert.ok(fn.state.analyserNode, "precondition: the retry built the analyser");

    env.raf.resetCounters();
    fn.state.isRecording = true;
    fn.startVad();

    assert.equal(
        env.raf.requested,
        1,
        "startVad() scheduled no frame, so the loop that decides when the " +
            "candidate stopped speaking never runs",
    );
    assert.equal(env.raf.pending, 1, "the scheduled frame is queued to run");
    assert.notEqual(fn.state.vadAnimationId, null, "the frame id was recorded");
    env.close();
});

test("the overlay's recovery builds the analyser that was missing", async () => {
    // The half-built repair, aimed at the wrong half: it resumed the context and
    // left `analyserNode` null, so the turn it was rescuing still could not end.
    const { env, fn } = audioEnv(["suspended", "running"]);

    await fn.initAudio();
    assert.equal(
        fn.state.analyserNode,
        null,
        "precondition: initAudio left the analyser unbuilt",
    );

    await clickOverlay(env);

    assert.ok(
        fn.state.analyserNode,
        "the overlay resumed the context but never built the analyser, so the " +
            "VAD stayed unable to decide when the candidate stopped speaking",
    );
    assert.equal(
        fn.state.audioContext.state,
        "running",
        "precondition: the context itself did come back",
    );
    assert.equal(
        env.document.getElementById("audio-blocked-overlay").classList.contains("hidden"),
        true,
        "the overlay should hide once the context is running",
    );
    env.close();
});

test("a closed context is recovered into a built analyser too", async () => {
    // `resumeAudioContext` only ever treated "suspended". A context that came
    // back "closed" cannot be resumed at all, so the old handler did nothing --
    // and the old guard blocked a retry exactly as it blocked the suspended case.
    //
    // Reached as a real sequence: a blocked init leaves a context with no
    // analyser, the browser then reclaims that context, and the candidate's
    // click on the overlay is the gesture that finally grants audio.
    const { env, fn } = audioEnv(["suspended", "running"]);
    await fn.initAudio();
    await fn.state.audioContext.close();

    assert.equal(
        fn.state.audioContext.state,
        "closed",
        "precondition: the context is closed and cannot be resumed",
    );
    assert.equal(
        fn.state.analyserNode,
        null,
        "precondition: and the analyser is still missing",
    );

    await clickOverlay(env);

    assert.ok(
        fn.state.analyserNode,
        "a closed context cannot be resumed, so the click has to build a " +
            "replacement under the gesture -- and no analyser was built",
    );
    assert.equal(
        fn.state.audioContext.state,
        "running",
        "the replacement context should come back running after the click",
    );
    env.close();
});

test("a healthy context builds the analyser once and keeps it", async () => {
    // The control. Readiness has to be a fix, not a rebuild: a guard that always
    // re-entered would stack a fresh context per press, and the mic source is
    // connected to ONE analyser for the life of a stream (see startRecording).
    const { env, fn } = audioEnv(null);

    await fn.initAudio();
    const analyser = fn.state.analyserNode;
    assert.ok(analyser, "a healthy context should build the analyser");
    assert.equal(env.recorder.contextsCreated, 1, "one context for the first call");

    await fn.initAudio();
    await fn.initAudio();

    assert.equal(
        fn.state.analyserNode,
        analyser,
        "the analyser was replaced on a retry, so a stream already connected to " +
            "it would be reading a node nothing writes to",
    );
    assert.equal(
        env.recorder.contextsCreated,
        1,
        `a ready stack built ${env.recorder.contextsCreated} contexts; the guard is ` +
            "supposed to make the second and third calls no-ops",
    );
    env.close();
});
