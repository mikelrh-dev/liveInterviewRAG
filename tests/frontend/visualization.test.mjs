/**
 * The page's permanent 60 fps loops, and what they allocate to do nothing.
 *
 * FOUR DEFECTS, ONE SHAPE
 * -----------------------
 * `startVisualizationLoop()` armed a `requestAnimationFrame` chain on load and
 * never stopped it, and `startVad()` armed a second one. Both ran for the life
 * of the page, in every state, including the idle one where there is nothing to
 * visualise and no audio to visualise it from. A candidate who closed the tab
 * between interviews and came back tomorrow paid 60 frames a second for it.
 *
 * Inside that loop, per frame:
 *
 *   - `new Uint8Array(analyserNode.fftSize)` allocated a fresh buffer sixty
 *     times a second, for a buffer whose contents are overwritten from the
 *     analyser before anything reads it. Sixty allocations a second of garbage
 *     that exists only to be filled and dropped.
 *   - `AvatarOrb.setBlend(ttsVolume)` was called every frame, and setBlend
 *     writes a CSS custom property on `#portal-ring`. So the orb's blend was
 *     restated sixty times a second even when the volume had not moved, and
 *     every write invalidates style for the element.
 *
 * And separately, `setState` seeked the talking video to 0.3s on EVERY
 * transition to `speaking`. `setState("speaking")` runs once per TTS chunk, not
 * once per turn, so the mouth restarted from the same frame once per sentence.
 *
 * HOW IT IS OBSERVED
 * ------------------
 * `requestAnimationFrame` is replaced with a steppable one, so "the loop asked
 * for another frame" is a counter the test reads rather than a timing race. The
 * buffer hoisting is observed through the analyser itself: it is handed an
 * array, and if the same array comes back next frame the buffer is hoisted.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom, producedBy } from "./dom.mjs";

/** A window in the given state, with a controllable orb and analyser. */
function vizEnv({ state = "listening", analyser = true } = {}) {
    const env = createDom({ raf: true });

    const orb = {
        initialised: true,
        volumes: [],
        blends: [],
        boosts: [],
        states: [],
        isInitialized() {
            return this.initialised;
        },
        setVolume(v) {
            this.volumes.push(v);
        },
        setBlend(v) {
            this.blends.push(v);
        },
        boost(v) {
            this.boosts.push(v);
        },
        setState(v) {
            this.states.push(v);
        },
        resize() {},
    };
    env.window.AvatarOrb = orb;

    const seenBuffers = [];
    const fakeAnalyser = env.recorder.fakes.FakeAudioContext.prototype.createAnalyser.call(
        new env.recorder.fakes.FakeAudioContext(),
    );
    fakeAnalyser.getByteTimeDomainData = (arr) => {
        // The observation: an array handed to the analyser. The same array next
        // frame means the buffer was hoisted instead of reallocated.
        seenBuffers.push(arr);
        arr.fill(128);
    };
    fakeAnalyser.getByteFrequencyData = (arr) => arr.fill(0);

    // The blend is driven by the TTS analyser, not the mic one, so a test that
    // wants to move the volume has to move this.
    const ttsAnalyser = env.recorder.fakes.FakeAudioContext.prototype.createAnalyser.call(
        new env.recorder.fakes.FakeAudioContext(),
    );
    let ttsFill = 128;
    ttsAnalyser.getByteTimeDomainData = (arr) => arr.fill(ttsFill);
    ttsAnalyser.getByteFrequencyData = (arr) => arr.fill(0);

    const fn = env.loadApp(
        [
            "startVisualizationLoop",
            "setState",
            "initAvatarOrb",
            "updateWaveform",
            "updateVuMeter",
            "getVuBars",
            "initAudio",
            "ensureAudioContext",
        ],
        {
            currentState: state,
            analyserNode: analyser ? producedBy(() => fakeAnalyser) : null,
            audioContext: producedBy(() => new env.recorder.fakes.FakeAudioContext()),
            ttsAnalyser: producedBy(() => ttsAnalyser),
            ttsVolumeBuffer: producedBy(() => new env.window.Uint8Array(ttsAnalyser.fftSize)),
            micTimeBuffer: null,
        },
    );

    return {
        env,
        fn,
        orb,
        seenBuffers,
        /** Make the orb report a successful init, as a real WebGL orb would. */
        orbInitialises: (ok = true) => {
            orb.initialised = ok;
        },
        /** Change the TTS output level the loop reads. */
        setTtsLevel: (fill) => {
            ttsFill = fill;
        },
    };
}

test("the loop does not allocate a new buffer every frame", () => {
    const { env, fn, seenBuffers } = vizEnv();

    fn.startVisualizationLoop();
    env.raf.stepTimes(5);

    assert.ok(
        seenBuffers.length >= 2,
        `the analyser was only handed ${seenBuffers.length} buffer(s) in 5 frames, ` +
            "so this is not exercising the per-frame path",
    );
    assert.equal(
        seenBuffers[0],
        seenBuffers[seenBuffers.length - 1],
        "a different Uint8Array was allocated on a later frame. The buffer is " +
            "filled from the analyser before anything reads it, so allocating it " +
            "per frame is sixty allocations a second of pure garbage",
    );
    env.close();
});

test("the orb's blend is not restated when the volume has not moved", () => {
    // A constant volume -- silence, or a held note. The blend cannot change, so
    // writing the CSS custom property again cannot change anything either, and
    // every write invalidates style for the element.
    const { env, fn, orb } = vizEnv();

    fn.startVisualizationLoop();
    env.raf.stepTimes(5);

    assert.ok(env.raf.requested > 0, "precondition: the loop should be running");
    assert.equal(
        orb.blends.length,
        1,
        `setBlend was called ${orb.blends.length} times for 5 frames of the same ` +
            "volume, each one rewriting a CSS custom property on the orb",
    );
    env.close();
});

test("a changing volume does still reach the orb", () => {
    // The control: suppressing the repeat must not suppress the change.
    // The levels are chosen inside the range where the curve has not already
    // clamped to 1 -- a loud signal and a slightly less loud one both read as
    // full volume, so they would test nothing.
    const { env, fn, orb, setTtsLevel } = vizEnv();

    fn.startVisualizationLoop();
    env.raf.stepTimes(1);
    const first = orb.blends.length;

    setTtsLevel(120);
    env.raf.stepTimes(1);
    const louder = orb.blends.length;

    setTtsLevel(128);
    env.raf.stepTimes(1);

    assert.ok(
        louder > first,
        `the blend never moved when the volume did: ${JSON.stringify(orb.blends)}`,
    );
    assert.ok(
        orb.blends.length > louder,
        `the blend did not follow the volume back down: ${JSON.stringify(orb.blends)}`,
    );
    env.close();
});

test("the loop stops when the page goes idle", () => {
    // The leak. Driven through setState rather than the helper, because the
    // claim is that the state machine disarms the loop -- not that a function
    // exists that could.
    const { env, fn } = vizEnv({ state: "idle" });

    fn.setState("listening");
    env.raf.stepTimes(3);
    assert.ok(env.raf.pending > 0, "precondition: the loop should be running");

    fn.setState("idle");
    env.raf.stepTimes(5);

    assert.equal(
        env.raf.pending,
        0,
        `${env.raf.pending} frame(s) are still queued after the page went idle: ` +
            "the loop is still running for the life of the page",
    );
    env.close();
});

test("a page that is idle on load does not start animating", () => {
    // The half of the leak nobody notices, and it starts on page load rather
    // than on END: the page opens idle, so arming the loop there costs 60
    // frames a second before the first interview is ever started.
    //
    // Driven through initAvatarOrb, which is what runs at load, so this is the
    // real path rather than a helper called by hand.
    const { env, fn } = vizEnv({ state: "idle" });
    // `initAvatarOrb` calls the orb's own init(), which a real WebGL orb
    // answers with success.
    env.window.AvatarOrb.init = () => true;

    fn.initAvatarOrb();
    env.raf.resetCounters();
    env.raf.stepTimes(3);

    assert.equal(
        env.raf.requested,
        0,
        `${env.raf.requested} frame(s) were requested on an idle page at load`,
    );
    env.close();
});

test("the next turn animates again after an idle one", () => {
    // The other direction, and the one a naive "cancel it in idle" gets wrong:
    // a loop that cannot be restarted never animates again.
    const { env, fn } = vizEnv({ state: "idle" });

    fn.setState("listening");
    env.raf.stepTimes(2);
    fn.setState("idle");
    assert.equal(env.raf.pending, 0, "precondition: the loop should be stopped");

    fn.setState("listening");
    env.raf.resetCounters();
    env.raf.stepTimes(2);

    assert.ok(
        env.raf.requested > 0,
        "the loop could not be started again, so the next turn animates nothing",
    );
    env.close();
});

test("two turns in the same state do not run two chains", () => {
    const { env, fn } = vizEnv({ state: "idle" });

    fn.setState("listening");
    fn.setState("listening");
    env.raf.resetCounters();
    env.raf.stepTimes(1);

    assert.equal(
        env.raf.requested,
        1,
        `one step queued ${env.raf.requested} frames. Two chains are running, so ` +
            "every frame of work is being done twice",
    );
    env.close();
});

// ─── The talking video ────────────────────────────────────────────────────

/** A window that counts how many times the talking video is seeked. */
function videoEnv() {
    const env = createDom();
    const video = env.document.getElementById("avatar-talking-video");
    let seeks = 0;
    Object.defineProperty(video, "currentTime", {
        configurable: true,
        get: () => 0,
        set: () => {
            seeks++;
        },
    });
    const fn = env.loadApp(["setState", "applyStatusClasses", "setStatus"]);
    return { env, fn, video, seeks: () => seeks };
}

test("the talking video is not rewound once per sentence", () => {
    // setState("speaking") is called by tryPlayNextChunk -- once per TTS chunk,
    // not once per turn. Seeking on each of those restarts the mouth from the
    // same 0.3s frame at the start of every sentence.
    const { env, fn, seeks } = videoEnv();

    fn.setState("speaking");
    fn.setState("speaking");
    fn.setState("speaking");

    assert.equal(
        seeks(),
        1,
        `three transitions into the speaking state seeked the video ${seeks()} ` +
            "times. The mouth restarts once per sentence",
    );
    env.close();
});

test("re-entering speaking after a pause seeks again", () => {
    // The control. Suppressing the repeat must not leave the video parked at
    // whatever frame it had when the turn ended.
    const { env, fn, seeks } = videoEnv();

    fn.setState("speaking");
    fn.setState("listening");
    fn.setState("speaking");

    assert.equal(
        seeks(),
        2,
        `two separate turns into speaking produced ${seeks()} seek(s): the video ` +
            "is left wherever the previous turn finished",
    );
    env.close();
});
