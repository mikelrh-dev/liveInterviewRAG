/**
 * The silence detector has to hear a pause in a room that has a floor of its own.
 *
 * THE DEFECT
 * ----------
 * Commit 2172b98 replaced the absolute `RMS_THRESHOLD` with a threshold relative
 * to an estimate of the room's noise, and paired it with a guard: a frame only
 * counts as silence if the turn has been LOUDER than the threshold at some point.
 * Its comment claims the pair is sufficient:
 *
 *     "A tracker that has run away to the signal's own level therefore cannot
 *      produce a cut, because the loudest thing recorded is the signal and the
 *      threshold is twice the floor."
 *
 * That is sound in one direction and wrong in the other. It does correctly say a
 * runaway floor cannot cause a SPURIOUS cut. It does not follow that a real pause
 * is still seen -- because the guard compares two numbers that are both tracking
 * the same signal:
 *
 *     noiseFloor  -> voice     (every tracker that converges reaches the signal)
 *     threshold   = 2 x floor -> 2 x voice
 *     loudPeakRms = voice      (a running max, so it stays at the voice)
 *     guard:      voice > 2 x voice   FALSE, on every remaining frame
 *
 * Once that is false the `else` branch clears `silenceStart` on every frame, so
 * the silence timer is reset sixty times a second and the turn cannot end for as
 * long as the recording runs.
 *
 * WHY IT IS NOT A NARROW EDGE CASE
 * --------------------------------
 * It is not "a pause shorter than the timeout slipped through". The guard does
 * not recover: `loudPeakRms` is a running maximum and `noiseFloor` rises toward
 * the voice, so the gap only widens. A 1.2 s pause and a 4 s pause are equally
 * invisible, and the detector is off for the rest of the turn.
 *
 * WHY 340 NODE TESTS DID NOT SEE IT
 * ---------------------------------
 * Every suite that touched the VAD drove a controlled analyser and asserted on
 * `noiseFloor`, on the recorder, or on the cap. Not one passed a SEQUENCE of RMS
 * values through `vadLoop` and asserted that a cut happened. So a change whose
 * declared purpose was "cut in a noisy room" could pass the whole suite having
 * changed no observable behaviour at all. The first test here is end-to-end
 * through the real loop for that reason, and the second is the arithmetic on its
 * own, so a future change cannot hide the failure inside a longer integration.
 *
 * WHAT IS PINNED HERE
 * -------------------
 *   1. a pause at ~6 dB of SNR is a cut (the case that was broken)
 *   2. the guard stays REACHABLE, on every frame, through continuous speech
 *   3. the three behaviours that already worked: a quiet room still cuts, a room
 *      that suddenly goes quiet still cuts, and continuous speech is never
 *      mistaken for a pause
 *
 * WHAT THESE TESTS DELIBERATELY DO NOT KNOW
 * ------------------------------------------
 * Nothing here names how the threshold is built. Every assertion is on an
 * observable: the recorder was stopped, or it was not, or the guard the loop
 * itself evaluates was false for a run of frames. A fix that satisfies them is
 * free to keep the floor, drop it, or replace it, and a test that had to know
 * the shape of the fix would be pinning one solution rather than the behaviour.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom, producedBy, readAppJs } from "./dom.mjs";

/** One animation frame at 60 fps. */
const FRAME_MS = 17;

/**
 * A module constant, read out of the shipped source.
 *
 * Absent reads as `undefined` rather than asserting, on purpose: a test that is
 * about BEHAVIOUR has to fail on the behaviour, and dying on a constant this
 * suite invented would make it a test of the fix rather than of the defect. The
 * arithmetic that needs a value says so in its own failure message.
 *
 * The value pattern accepts a decimal point and an exponent. `[\d_]+` alone
 * stops at the first `.`, so `RMS_THRESHOLD = 0.015` read as `0` -- and a
 * threshold of zero makes every frame read as speech, which fails the quiet-room
 * control for a reason that has nothing to do with the detector.
 */
function readConstant(name) {
    const match = readAppJs().match(
        new RegExp(
            `const\\s+${name}\\s*=\\s*` +
                `(?:(\\d+(?:\\.\\d+)?)\\s*\\/\\s*(\\d+(?:\\.\\d+)?)|(\\d+(?:\\.\\d+)?(?:e[-+]?\\d+)?))`,
            "i",
        ),
    );
    if (!match) return undefined;
    if (match[3] !== undefined) return Number(match[3].replace(/_/g, ""));
    return Number(match[1]) / Number(match[2]);
}

/**
 * The VAD's module constants, as globals for the lifted code.
 *
 * `loadApp` lifts function declarations, not the `const`s they close over, so
 * every constant the VAD reads has to be handed over explicitly. A constant the
 * shipped source does not declare is passed as `undefined` rather than omitted:
 * a lifted body that does not mention it never reads it, and a lifted body that
 * does is then reported as a ReferenceError naming the name.
 */
function moduleConstants() {
    const constants = {};
    for (const name of [
        "SILENCE_TIMEOUT_MS",
        "RMS_THRESHOLD",
        "NOISE_FLOOR_MULTIPLE",
        "NOISE_FLOOR_RISE_RATE",
        "NOISE_FLOOR_FALL_RATE",
        "PEAK_HEADROOM",
    ]) {
        constants[name] = readConstant(name);
    }
    return constants;
}

/**
 * Put a chosen RMS on the fake analyser and report what was actually produced.
 *
 * `getByteTimeDomainData` writes bytes, so full scale is 1/128 of an amplitude.
 * A symmetric square wave realises exactly the requested amplitude, and the
 * REALIZED value is what every assertion uses -- otherwise a test can pass
 * against a number the quantiser never produced. `recording_duration.test.mjs`
 * carries the same helper and the same reasoning.
 */
function setRms(analyser, requested) {
    const peak = Math.min(127, Math.max(1, Math.round(requested * 128)));
    const high = 128 + peak;
    const low = 128 - peak;
    analyser.getByteTimeDomainData = (arr) => {
        for (let i = 0; i < arr.length; i++) {
            arr[i] = i * 2 < arr.length / 2 ? high : low;
        }
    };
    return peak / 128;
}

/** The analyser `initAudio` would have built; `loadApp` runs no bootstrap. */
function makeAnalyser() {
    return {
        fftSize: 64,
        frequencyBinCount: 32,
        smoothingTimeConstant: 0.8,
        connectedTo: null,
        getByteTimeDomainData: (arr) => arr.fill(128),
        getByteFrequencyData: (arr) => arr.fill(0),
        connect: (dest) => {
            this.connectedTo = dest;
        },
        disconnect: () => {},
    };
}

/** A recording turn in flight, with the clock and every frame under test control. */
async function recordingEnv() {
    const env = createDom({ clock: true, raf: true });
    env.recorder.fakes.FakeMediaRecorder.supported = ["audio/webm;codecs=opus"];

    const fn = env.loadApp(
        [
            "startRecording",
            "stopRecording",
            "setStatus",
            "setState",
            "chooseMimeType",
            "ensureAudioContext",
            "startVad",
            "stopVad",
            // Named so it comes back callable. `vadLoop` is reached as a
            // dependency of `startVad` anyway, and the guard test below drives
            // the threshold function directly -- see the note there for why it
            // must not also step the animation frame.
            "updateNoiseFloor",
            "startVisualizationLoop",
            "stopVisualizationLoop",
            "addMessage",
            "populateStaticSidebar",
            "setText",
        ],
        {
            isInterviewActive: true,
            analyserNode: producedBy(makeAnalyser),
            noiseFloor: 0,
            loudPeakRms: 0,
            ...moduleConstants(),
        },
    );

    await fn.startRecording();
    const analyser = fn.state.analyserNode;
    assert.ok(analyser, "precondition: the VAD should have an analyser to read");

    // `setState` arms the orb's own rAF loop, and that loop shares the queue.
    // Left in place, `step()` alternates between two callbacks, so "one VAD
    // frame" quietly becomes one and a half frames of something else. Cancelled
    // rather than drained: it re-requests itself forever.
    fn.stopVisualizationLoop();
    return { env, fn, analyser };
}

/**
 * Run VAD frames at a fixed level, advancing the clock with them.
 *
 * The clock moves because the silence timer is wall time, not a frame count. A
 * "pause" of one frame followed by a long tick is a timing no browser produces.
 *
 * @returns the level the byte quantiser actually produced
 */
function frames(env, analyser, level, count) {
    const rms = setRms(analyser, level);
    for (let i = 0; i < count; i++) {
        if (!env.raf.step()) break;
        env.clock.tick(FRAME_MS);
    }
    return rms;
}

/** Frames of room-only long enough for a pause to be recognised, plus a margin. */
function silenceFrames() {
    const timeout = readConstant("SILENCE_TIMEOUT_MS");
    assert.ok(
        Number.isFinite(timeout) && timeout > 0,
        "SILENCE_TIMEOUT_MS is missing from frontend/app.js, so the length of a " +
            "recognised pause is unknown and no control below can mean anything",
    );
    return Math.ceil(timeout / FRAME_MS) + 40;
}

/** dB of signal-to-noise between two levels the quantiser actually produced. */
const snrDb = (voice, noise) => 20 * Math.log10(voice / noise);

// ─── 1. The case that was broken ─────────────────────────────────────────────

test("a pause at 6 dB of SNR ends the turn", async () => {
    // The whole point, end to end through the real loop.
    //
    // 0.05 of room noise under 0.10 of voice is about 6 dB of SNR: an ordinary
    // interview in an ordinary room, not a laboratory edge case. The candidate
    // talks for two seconds -- long enough for any exponential tracker to have
    // climbed to the level of their own voice -- and then stops. The room does
    // not stop.
    const { env, fn, analyser } = await recordingEnv();
    const before = env.recorder.stops;

    const speech = frames(env, analyser, 0.1, 120);
    const room = frames(env, analyser, 0.05, 4);
    const snr = snrDb(speech, room);
    assert.ok(
        snr > 5 && snr < 7,
        `the test room is not the room this test is about: ${snr.toFixed(1)} dB of ` +
            "SNR is outside the 5-7 dB band",
    );
    assert.equal(
        env.recorder.stops,
        before,
        "precondition: the candidate is still talking, so nothing may be cut yet",
    );

    // 1.5 s of room, which is the duration the requirement names.
    frames(env, analyser, 0.05, Math.ceil(1500 / FRAME_MS));

    assert.equal(
        env.recorder.stops,
        before + 1,
        `two seconds of ${speech.toFixed(4)} voice over ${room.toFixed(4)} of room ` +
            `noise -- ${snr.toFixed(1)} dB of SNR -- and a 1.5 s pause did not end the ` +
            "turn. The recording ran on, the blob kept growing, and the turn ended " +
            "at the recording cap rather than at the pause: a symptom with no cause " +
            "a candidate can point at.",
    );
    assert.equal(fn.state.isRecording, false, "the page still believes it is recording");
    env.close();
});

test("a cut does not depend on the pause outlasting the timeout", async () => {
    // The failure is not "a short pause slipped through". The guard compares the
    // peak against a threshold built from a floor tracking the same signal, so
    // once it is false it stays false and the timer is cleared on every frame.
    // Proof that a pause four times the timeout is as invisible as one that just
    // clears it.
    //
    // The room here is at 4.4 dB of SNR, not 6, and that is the point. At 6 dB
    // the old code could still recover, because the floor FALLS during the pause
    // at NOISE_FLOOR_FALL_RATE and 2 x floor eventually dips under the peak --
    // which takes about 0.5 s, so a four-second pause scraped in. The ceiling
    // below that recovery is the real limit: the guard needs 2 x noise < peak,
    // i.e. SNR above 6.02 dB, and at 4.4 dB no amount of waiting satisfies it.
    const { env, analyser } = await recordingEnv();
    const before = env.recorder.stops;

    frames(env, analyser, 0.08, 120);
    frames(env, analyser, 0.05, Math.ceil(4000 / FRAME_MS));

    assert.equal(
        env.recorder.stops,
        before + 1,
        "four seconds of room at 4.4 dB of SNR produced no cut. The floor falls " +
            "during a pause, so the old code could scrape in on a long one -- but " +
            "the floor settles at the room's own level, and there 2 x floor is " +
            "above the peak at any SNR under 6.02 dB. That is the ceiling, and no " +
            "amount of waiting gets under it.",
    );
    env.close();
});

// ─── 2. The guard has to stay reachable ──────────────────────────────────────

test("the guard stays reachable through continuous speech", () => {
    // The defect in its shortest form: one number compared against another, over
    // a run of frames.
    //
    // The guard is the `loudPeakRms > threshold` that `vadLoop` evaluates before
    // it will call a frame a pause. It is what stops the noise-floor estimate
    // from ending a turn while the candidate is still talking, and it is also
    // what has to let a real pause through. If it is false on every frame of a
    // run of continuous speech then the pause branch is unreachable, and the
    // detector has been switched off without anyone moving a threshold.
    //
    // Driven through the SHIPPED `updateNoiseFloor`, one call per frame exactly
    // as `vadLoop` makes it, so a test cannot pass against arithmetic app.js no
    // longer performs. The animation frame is deliberately NOT stepped here: the
    // loop calls `updateNoiseFloor` itself, and calling it a second time per
    // frame would advance the floor twice and measure a page that does not
    // exist. The peak is maintained on the same two lines the loop uses.
    const env = createDom({ clock: true });
    const fn = env.loadApp(["updateNoiseFloor"], {
        noiseFloor: 0,
        loudPeakRms: 0,
        ...moduleConstants(),
    });

    for (const level of [0.05, 0.08, 0.1, 0.14, 0.16]) {
        fn.state.noiseFloor = 0;
        let peak = 0;
        let deadRun = 0;
        let worstDeadRun = 0;
        let floor = 0;

        for (let i = 0; i < 600; i++) {
            // The peak is passed in, not read out, because that is the order
            // `vadLoop` uses: the peak is a running maximum over frames already
            // seen, so it is this frame's input and not this frame's output.
            const threshold = fn.updateNoiseFloor(level, peak);
            floor = fn.state.noiseFloor;
            if (level >= threshold && level > peak) peak = level;

            if (peak > 0 && peak <= threshold) {
                deadRun++;
                if (deadRun > worstDeadRun) worstDeadRun = deadRun;
            } else {
                deadRun = 0;
            }
        }

        assert.equal(
            worstDeadRun,
            0,
            `the guard was false for ${worstDeadRun} consecutive frames -- ` +
                `${((worstDeadRun * FRAME_MS) / 1000).toFixed(1)} s -- of unbroken ` +
                `speech at ${level}. On every one of those frames the loop cannot ` +
                "conclude anything, so it clears the silence timer, and a run of " +
                "them in the middle of a turn means the turn cannot end for ANY " +
                "pause. Whatever builds the threshold has to keep it strictly below " +
                "the loudest thing the turn has seen; it reached " +
                `${floor.toFixed(4)} against a peak of ${peak.toFixed(4)}, which is ` +
                `${(floor / peak).toFixed(2)}x the peak.`,
        );
    }
    env.close();
});

// ─── 3. The three behaviours that must not break ──────────────────────────────

test("CONTROL: a quiet room still cuts on an ordinary pause", async () => {
    // The absolute threshold has to survive as the lower bound of the pair, or
    // the fix has traded a broken noisy room for a broken quiet one.
    const { env, analyser } = await recordingEnv();
    const before = env.recorder.stops;

    frames(env, analyser, 0.25, 3);
    frames(env, analyser, 0.0008, silenceFrames());

    assert.equal(
        env.recorder.stops,
        before + 1,
        "a pause in a quiet room no longer ends the turn",
    );
    env.close();
});

test("CONTROL: a room that suddenly goes quiet still cuts", async () => {
    // A door, a colleague stepping out. The floor has to come DOWN fast, or the
    // page stays deaf for the rest of the interview -- which is what justifies
    // the floor falling faster than it rises.
    const { env, analyser } = await recordingEnv();
    const before = env.recorder.stops;

    frames(env, analyser, 0.2, 5);
    const room = frames(env, analyser, 0.05, 30);
    const absolute = readConstant("RMS_THRESHOLD");
    assert.ok(
        room > absolute,
        `precondition: the room (${room}) is louder than the absolute threshold ` +
            `(${absolute}), so this is the sudden-drop case and not the quiet one`,
    );

    frames(env, analyser, 0.0008, silenceFrames());

    assert.equal(
        env.recorder.stops,
        before + 1,
        "the room dropped 36 dB and the turn still did not end, so the floor does " +
            "not come down fast enough to hear the room change",
    );
    env.close();
});

test("CONTROL: continuous speech is never mistaken for a pause", async () => {
    // The other half of the guard, and the reason it exists.
    //
    // One level per RECORDING, because a turn is a recording: `startVad` resets
    // both the floor and the peak per turn, so the peak a threshold is measured
    // against never carries across one. Chaining five escalating levels through
    // a single recording would be testing a scenario the page cannot produce --
    // a speaker 3 dB below their own peak for a full turn IS a pause by any
    // definition this detector can have, and the honest boundary is measured in
    // the test below rather than asserted away here.
    for (const level of [0.05, 0.08, 0.1, 0.14, 0.16]) {
        const { env, fn, analyser } = await recordingEnv();
        const before = env.recorder.stops;

        frames(env, analyser, level, 600);

        assert.equal(
            env.recorder.stops,
            before,
            `ten seconds of unbroken speech at ${level} ended the turn. The floor ` +
                "has climbed to the speaker's own level and the threshold is now " +
                "above the voice being produced, so the candidate was cut off " +
                "mid-word.",
        );
        assert.equal(
            fn.state.isRecording,
            true,
            `the page stopped recording after ten seconds of speech at ${level}`,
        );
        env.close();
    }
});

test("CONTROL: the modulation of real speech is not a pause", async () => {
    // A speaker is not a constant amplitude. Their level rises and falls with
    // emphasis, and a detector that cannot see through that will end the turn in
    // the middle of a sentence. Five dB of modulation at half-second periods is
    // within what an unamplified microphone delivers, and none of it may read as
    // silence: each dip is far shorter than SILENCE_TIMEOUT_MS, so the timer has
    // to be reset by the loud frames between them.
    const { env, fn, analyser } = await recordingEnv();
    const before = env.recorder.stops;

    for (let i = 0; i < 40; i++) {
        frames(env, analyser, 0.16, 29);
        frames(env, analyser, 0.09, 29);
    }

    assert.equal(
        env.recorder.stops,
        before,
        "20 s of speech modulated by 5 dB at half-second periods ended the turn. " +
            "The dips are far shorter than the silence timeout, so a pause cannot " +
            "be claimed without the loud frames in between resetting the timer.",
    );
    assert.equal(fn.state.isRecording, true, "the page stopped recording mid-answer");
    env.close();
});

test("the pause boundary sits at the headroom fraction, and it is reported", async () => {
    // The one number a reader of this file needs: how far below its own peak a
    // speaker has to fall for the detector to call it a pause.
    //
    // It is `PEAK_HEADROOM`, by construction rather than by tuning -- the
    // threshold is capped at that fraction of the peak, so a frame at the
    // fraction is exactly ON the threshold, and `vadLoop` counts a frame as
    // silence only when it falls strictly below. Two things follow, and they
    // pull in opposite directions, so both are measured rather than asserted:
    //
    //   - a dip shallower than the fraction never reads as silence, at any length
    //   - a dip deeper than it reads as silence only after SILENCE_TIMEOUT_MS
    //     of CONTINUOUS dip, which is what keeps a 1.2 s word gap safe
    const { env, analyser } = await recordingEnv();
    const before = env.recorder.stops;

    frames(env, analyser, 0.16, 120);
    const peak = 0.16;
    // A shade ABOVE 2/3 of the peak, held far longer than the timeout.
    const shallow = Math.round(peak * 0.75 * 128) / 128;
    frames(env, analyser, shallow, 600);

    assert.equal(
        env.recorder.stops,
        before,
        `a level of ${shallow} is 75% of the turn's peak of ${peak} and was held for ` +
            "ten seconds, which is eight silence timeouts. That is not a pause.",
    );
    env.close();

    // And the same level held past the timeout IS a pause, so the control above
    // is not passing because the detector has stopped working altogether.
    const second = await recordingEnv();
    frames(second.env, second.analyser, 0.16, 120);
    const deep = Math.round(peak * 0.6 * 128) / 128;
    frames(second.env, second.analyser, deep, 600);

    assert.equal(
        second.env.recorder.stops,
        1,
        `a level of ${deep} is 60% of the turn's peak and was held for ten seconds, ` +
            "which no speaker does. If that produced no cut either, the control " +
            "above would pass for the wrong reason -- with a detector that never " +
            "fires, not one that measures the right boundary.",
    );
    second.env.close();
});
