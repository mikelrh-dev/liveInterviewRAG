/**
 * A recording must end for a reason the page can explain.
 *
 * THE CHAIN NOBODY HAD NAMED
 * --------------------------
 * The VAD ends a turn after `SILENCE_TIMEOUT_MS` of RMS below `RMS_THRESHOLD =
 * 0.015` -- an ABSOLUTE threshold. In a room with background noise the RMS
 * never drops that low, so the VAD never fires. The recorder is never stopped,
 * the blob grows to `MAX_AUDIO_SIZE`, nginx answers 413, 413 is not retryable,
 * and the candidate loses the interview and the whole recording.
 *
 * A fixed threshold cannot fix that, because there is no fixed number that is
 * both above a quiet room and below a loud one. The threshold has to be
 * relative: the page estimates the room's own noise floor and cuts when the RMS
 * falls below a multiple of it -- but only when the turn has been LOUDER than
 * that, because a floor that tracks the signal eventually reaches the
 * candidate's own voice. Those are two separate problems, and the noisy-room
 * case below is the one the old code could not survive.
 *
 * AND THE LIMIT IS THE SERVER'S, NOT THE PAGE'S
 * ---------------------------------------------
 * `MAX_AUDIO_DURATION` (backend/config.py) is published by `GET /api/config`.
 * The page applies it, and falls back to a local number only if that request
 * fails, because `init()` does not await it and the first recording of a
 * session is exactly the one that needs a cap.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom, producedBy, readAppJs } from "./dom.mjs";

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/** One animation frame's worth of time at 60 fps, in ms. */
const FRAME_MS = 17;

/** The page's own recording and VAD constants, read from the shipped source. */
function appConstants() {
    const source = readAppJs();
    // Absent reads as `undefined` rather than asserting, so a test that is
    // about BEHAVIOUR fails on the behaviour instead of dying on a missing
    // constant. The presence of the constants is asserted on its own, by the
    // test that exists for it.
    //
    // The value pattern accepts a decimal point and an exponent. `[\d_]+` alone
    // stops at the first `.`, so `RMS_THRESHOLD = 0.015` read as `0` -- and a
    // threshold of zero makes every frame read as speech, which fails the
    // quiet-room control for a reason that has nothing to do with the code.
    const read = (name) => {
        const match = source.match(
            new RegExp(`const\\s+${name}\\s*=\\s*([\\d_]+(?:\\.\\d+)?(?:e[-+]?\\d+)?)`, "i"),
        );
        return match ? Number(match[1].replace(/_/g, "")) : undefined;
    };
    return {
        fallbackMs: read("MAX_RECORDING_MS"),
        silenceTimeoutMs: read("SILENCE_TIMEOUT_MS"),
        rmsThreshold: read("RMS_THRESHOLD"),
        floorMultiple: read("NOISE_FLOOR_MULTIPLE"),
        riseRate: read("NOISE_FLOOR_RISE_RATE"),
        fallRate: read("NOISE_FLOOR_FALL_RATE"),
        // PEAK_HEADROOM is a RATIO, written as `2 / 3` in app.js because that is
        // the arithmetic it is. `read` above stops at the `/`, so it is read by
        // its own pattern rather than by widening `read` for one caller: a reader
        // that accepts both a plain number and a quotient has to decide what to
        // do when both are absent, and the failure would be a NaN threshold
        // rather than a named error.
        peakHeadroom: readRatio("PEAK_HEADROOM"),
    };
}

/** A constant written as a quotient, e.g. `const PEAK_HEADROOM = 2 / 3`. */
function readRatio(name) {
    const match = readAppJs().match(
        new RegExp(`const\\s+${name}\\s*=\\s*(\\d+(?:\\.\\d+)?)\\s*/\\s*(\\d+(?:\\.\\d+)?)`, "i"),
    );
    return match ? Number(match[1]) / Number(match[2]) : undefined;
}

/**
 * Put a chosen RMS on the fake analyser.
 *
 * `getByteTimeDomainData` fills a byte array, so an amplitude is only reachable
 * to 1/128 of full scale. A symmetric square wave makes the realized RMS exactly
 * equal to the amplitude, and the realized value is returned so every assertion
 * downstream is made against what the code actually saw rather than what was
 * asked for -- a test that asserts on the request would pass while the
 * quantization put the frame on the wrong side of the threshold.
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

/**
 * A mic analyser whose time-domain data the test decides.
 *
 * `startRecording` does not build one -- `initAudio` does, at page load, and
 * `loadApp` deliberately does not run the bootstrap. So the analyser is seeded
 * the same way the other suites seed the AudioContext: as a factory evaluated
 * inside the window, because `getByteTimeDomainData` is the only thing standing
 * between this suite and a test that asserts on the fake instead of on app.js.
 */
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

/**
 * The app.js module constants the lifted code reads, as globals.
 *
 * `loadApp` lifts function declarations, not the `const`s they close over, so
 * every constant the VAD and the cap read has to be handed over explicitly. They
 * are read out of the shipped source rather than restated, so a test cannot
 * assert against a tuning value app.js no longer uses -- which would be a green
 * test about a page that does not exist.
 */
function moduleConstants() {
    const c = appConstants();
    return {
        MAX_RECORDING_MS: c.fallbackMs,
        maxRecordingMs: c.fallbackMs,
        SILENCE_TIMEOUT_MS: c.silenceTimeoutMs,
        RMS_THRESHOLD: c.rmsThreshold,
        NOISE_FLOOR_MULTIPLE: c.floorMultiple,
        NOISE_FLOOR_RISE_RATE: c.riseRate,
        NOISE_FLOOR_FALL_RATE: c.fallRate,
        // The ceiling `vadLoop` puts on the threshold, as a fraction of the turn's
        // loudest frame. Handed over because `updateNoiseFloor` reads it, and a
        // lifted body with a missing binding throws inside the animation frame --
        // which surfaces as a VAD failure in every test in the file at once, with
        // a ReferenceError that names the constant instead of the behaviour.
        PEAK_HEADROOM: c.peakHeadroom,
    };
}

/** A page that has been asked to record, with the clock and the frames ours. */
function recordingEnv({ serverLimitSeconds } = {}) {
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

    if (serverLimitSeconds !== undefined) {
        env.onFetch((url) => {
            assert.match(url, /\/api\/config$/, `unexpected request: ${url}`);
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    tts_voice: "es-ES-AlvaroNeural",
                    stt_model: "small",
                    stt_device: "cpu",
                    llm_model: "x/y",
                    google_model: "gemini",
                    max_audio_duration: serverLimitSeconds,
                }),
            };
        });
    }

    return { env, fn };
}

/** Start a recording and return the analyser the VAD will read. */
async function started(env, fn) {
    await fn.startRecording();
    const analyser = fn.state.analyserNode;
    assert.ok(analyser, "precondition: the VAD should have an analyser to read");

    // `setState` also arms the orb's render loop, and that loop shares the same
    // requestAnimationFrame queue. Left in place, `env.raf.step()` alternates
    // between the two callbacks and "two VAD frames" quietly becomes three
    // frames of something else -- a test whose arithmetic depends on
    // registration order. So it is cancelled here, and every frame stepped from
    // this point on is a VAD frame. Cancelled rather than drained: the loop
    // re-requests itself, so draining it never terminates.
    fn.stopVisualizationLoop();
    return analyser;
}

/**
 * Run `frames` VAD frames at a fixed RMS, advancing the clock as it goes.
 *
 * The clock moves because the silence timer is measured in wall time, not in
 * frames: a "pause" that is one frame followed by a long tick tests a timing no
 * browser ever produces.
 *
 * @returns the realized RMS
 */
function speak(env, analyser, requested, frames = 1) {
    const rms = setRms(analyser, requested);
    for (let i = 0; i < frames; i++) {
        env.raf.step();
        env.clock.tick(FRAME_MS);
    }
    return rms;
}

/** Frames of quiet long enough for a pause to be recognised, plus a margin. */
function silenceFrames() {
    return Math.ceil(appConstants().silenceTimeoutMs / FRAME_MS) + 30;
}

// ─── The noise floor ────────────────────────────────────────────────────────

test("the floor is named constants, not tuned magic numbers", () => {
    const { floorMultiple, riseRate, fallRate } = appConstants();
    for (const [name, value] of Object.entries({
        NOISE_FLOOR_RISE_RATE: riseRate,
        NOISE_FLOOR_FALL_RATE: fallRate,
    })) {
        assert.ok(
            Number.isFinite(value) && value > 0 && value < 1,
            `${name} is missing from frontend/app.js, is not a number, or is not ` +
                "a fraction below 1. The floor's behaviour is three tuning " +
                "decisions, and each has to be something a reader can change.",
        );
    }
    assert.ok(
        fallRate > riseRate,
        `the floor falls (${fallRate}) faster than it rises (${riseRate}). A ` +
            "floor that rises as fast as it falls tracks the candidate instead " +
            "of the room, and the turn is then cut mid-word.",
    );
    assert.ok(
        floorMultiple > 1,
        `NOISE_FLOOR_MULTIPLE is ${floorMultiple}. A multiple of 1 or less puts ` +
            "the cut threshold at or below the room's own level, so every frame " +
            "reads as silence and the turn ends on the first pause it is given.",
    );
});

test("CONTROL: a quiet room still cuts on a normal pause", async () => {
    // The behaviour that already worked, kept honest. If this regresses, the
    // relative floor has broken the easy case while fixing the hard one.
    const { env, fn } = recordingEnv();
    const analyser = await started(env, fn);

    speak(env, analyser, 0.25, 3);
    const before = env.recorder.stops;
    speak(env, analyser, 0.0008, silenceFrames());

    assert.equal(
        env.recorder.stops,
        before + 1,
        "a pause in a quiet room no longer ends the turn",
    );
    env.close();
});

test("THE ONE THAT MATTERS: background noise stops defeating the cut", async () => {
    const { rmsThreshold, floorMultiple } = appConstants();
    const { env, fn } = recordingEnv();
    const analyser = await started(env, fn);
    const before = env.recorder.stops;

    // Somebody talks, then stops talking. The room does not.
    const speech = speak(env, analyser, 0.2, 5);
    const noise = setRms(analyser, 0.06);
    assert.ok(
        noise > rmsThreshold && noise < speech,
        `the test room is not a room: the noise (${noise}) has to sit above the ` +
            `absolute threshold (${rmsThreshold}) and below the speech ` +
            `(${speech}) for this case to mean anything`,
    );

    // Long enough for the floor to characterise the room, short enough that the
    // silence timer has not yet expired.
    const settled = 30;
    speak(env, analyser, 0.06, settled);

    const floor = fn.state.noiseFloor;
    assert.equal(
        env.recorder.stops,
        before,
        `the recorder was stopped after only ${settled} frames of room noise, ` +
            "before the pause could be one. A turn must not end on the room " +
            "alone.",
    );
    assert.ok(
        floor > rmsThreshold,
        `the noise floor never rose: it reads ${floor}, which is below the ` +
            `absolute threshold ${rmsThreshold}. The floor is not estimating ` +
            "the room, so the cut below is not being earned.",
    );
    assert.ok(
        floor * floorMultiple > noise,
        `a floor of ${floor} times ${floorMultiple} is ${floor * floorMultiple}, ` +
            `which does not clear the room's ${noise}. The multiple cannot ` +
            "separate speech from noise at this floor.",
    );
    assert.ok(
        floor * floorMultiple < speech,
        `a floor of ${floor} times ${floorMultiple} is above the speech level ` +
            `(${speech}), so the candidate's own voice would read as silence and ` +
            "the turn would be cut while they are still talking.",
    );

    speak(env, analyser, 0.06, silenceFrames());

    assert.equal(
        env.recorder.stops,
        before + 1,
        "the recording never ended in a noisy room. The RMS stayed above the " +
            "absolute threshold for the whole turn, so the VAD could not see " +
            "the pause, the blob kept growing, and the server answered 413 -- " +
            "which ends the interview and throws the audio away.",
    );
    assert.equal(fn.state.isRecording, false, "the page still believes it is recording");
    env.close();
});

test("continuous speech is not mistaken for a pause", async () => {
    // The failure a floor alone cannot prevent. ANY tracker that converges to
    // the signal reaches the speaker's own level after a few seconds of
    // unbroken speech -- that is what converging means -- and a threshold at
    // twice the floor is then above the voice still being produced. So a frame
    // only counts as silence if the turn has been louder than the threshold at
    // some point, and this is the test for that second half.
    const { env, fn } = recordingEnv();
    const analyser = await started(env, fn);
    const before = env.recorder.stops;

    const speech = speak(env, analyser, 0.3, 600);

    assert.equal(
        env.recorder.stops,
        before,
        `ten seconds of unbroken speech at ${speech} ended the turn. The noise ` +
            "floor has climbed to the speaker's own level -- which every floor " +
            "that tracks the signal eventually does -- and with nothing to " +
            "compare it against, the candidate was cut off mid-word.",
    );
    assert.equal(fn.state.isRecording, true, "the page stopped recording mid-answer");
    env.close();
});

test("the floor comes back down when the room goes quiet", async () => {
    // Asymmetric on purpose: it has to fall quickly or a door closing leaves the
    // page deaf for the rest of the interview.
    const { floorMultiple } = appConstants();
    const { env, fn } = recordingEnv();
    const analyser = await started(env, fn);

    speak(env, analyser, 0.2, 5);
    speak(env, analyser, 0.06, 30);
    const loud = fn.state.noiseFloor;

    const quietRoom = speak(env, analyser, 0.0008, 30);
    const quiet = fn.state.noiseFloor;

    assert.ok(quiet < loud, `the floor did not fall: ${quiet} against ${loud}`);
    assert.ok(
        quiet * floorMultiple < quietRoom + 0.02,
        `the floor is still ${quiet} after the room went quiet, so the cut ` +
            `threshold (${quiet * floorMultiple}) is far above the silence at ` +
            `${quietRoom} and the pause will never be seen again`,
    );
    env.close();
});

// ─── The limit comes from the server ────────────────────────────────────────

test("the page's fallback is the number the server advertises", () => {
    // One limit, not two. The fallback exists because `init()` does not await
    // /api/config, and the first recording of a session is the one that needs a
    // cap -- but a fallback that disagrees with the server is a second limit,
    // and the same recording would be cut in one deployment and refused in
    // another.
    const { fallbackMs } = appConstants();
    const { env, fn } = recordingEnv();

    assert.equal(
        fn.state.maxRecordingMs,
        fallbackMs,
        "the page starts from its own constant rather than the value it fetched",
    );
    env.close();
});

test("the cap is the limit the server published, not the fallback", async () => {
    const { fallbackMs } = appConstants();
    const { env, fn } = recordingEnv({ serverLimitSeconds: 90 });

    await fn.populateStaticSidebar();
    assert.equal(
        fn.state.maxRecordingMs,
        90_000,
        `GET /api/config published 90 s and the page still enforces ` +
            `${fn.state.maxRecordingMs} ms. The server's limit is advisory, ` +
            "which means it is not a limit.",
    );

    await fn.startRecording();
    const before = env.recorder.stops;

    env.clock.tick(90_000);
    await settle();

    assert.equal(
        env.recorder.stops,
        before + 1,
        `the recording ran to 90 000 ms and was not cut. The fallback is ` +
            `${fallbackMs} ms, so this is the published limit being ignored.`,
    );
    assert.equal(fn.state.recordingCapped, true);
    env.close();
});

test("the cap is clean: the turn is uploaded, the interview continues", async () => {
    const { env, fn } = recordingEnv({ serverLimitSeconds: 90 });
    await fn.populateStaticSidebar();
    const analyser = await started(env, fn);
    // Talk the whole way through, so the cut cannot be mistaken for a pause.
    speak(env, analyser, 0.2, 10);

    env.clock.tick(90_000);
    await settle();

    assert.notEqual(
        env.document.body.dataset.state,
        "listening",
        "the avatar still reads 'listening' after the cap stopped the recorder",
    );
    assert.equal(
        fn.state.isInterviewActive,
        true,
        "hitting the cap ended the interview. A candidate who talks past the " +
            "limit loses the turn, not the session -- and definitely not " +
            "everything recorded so far.",
    );
    env.close();
});

test("a failed /api/config leaves the cap in place", async () => {
    // The fallback is not decoration. `init()` fires the sidebar fetch without
    // awaiting it, so a deployment that is slow, down or 500-ing still has to
    // bound the recording -- otherwise the very first turn is the one that
    // reaches 5 MiB and 413s.
    const { fallbackMs } = appConstants();
    const { env, fn } = recordingEnv();
    env.onFetch(() => {
        throw new Error("network down");
    });

    await fn.populateStaticSidebar();
    assert.equal(
        fn.state.maxRecordingMs,
        fallbackMs,
        "a failed /api/config moved the cap instead of leaving the fallback",
    );

    await fn.startRecording();
    const before = env.recorder.stops;
    env.clock.tick(fallbackMs);
    await settle();

    assert.equal(
        env.recorder.stops,
        before + 1,
        "with no server limit the recording was never cut, so it grew until " +
            "the 413 that ends the interview",
    );
    env.close();
});

test("a nonsense limit from the server is ignored, not obeyed", async () => {
    // A config endpoint that answers 0, or a string, must not disarm the cap.
    // Obeying it would be worse than ignoring /api/config entirely: the page
    // would stop bounding recordings on the strength of a bad response.
    const { fallbackMs } = appConstants();
    for (const bad of [0, -1, null, "90"]) {
        const { env, fn } = recordingEnv({ serverLimitSeconds: bad });
        await fn.populateStaticSidebar();
        assert.equal(
            fn.state.maxRecordingMs,
            fallbackMs,
            `the page took ${JSON.stringify(bad)} as a duration limit`,
        );
        env.close();
    }
});

test("the page's cap fits inside the byte ceiling, so the 413 never comes first", () => {
    // The byte gate runs before anything is parsed, so a duration limit above
    // the byte-implied duration is a limit the candidate never reaches: the 413
    // arrives first, the turn is refused, and the cap has bought nothing. The
    // bitrate is read from the source rather than restated, so changing it in
    // app.js cannot leave this test agreeing with a number the page no longer
    // uses.
    const { fallbackMs } = appConstants();
    const source = readAppJs();
    const match = source.match(/audioBitsPerSecond\s*:\s*(\d+)/);
    assert.ok(match, "app.js no longer sets a bitrate, so this check cannot run");
    const bytesPerSecond = Number(match[1]) / 8;
    const maxAudioSize = 5 * 1024 * 1024;

    assert.ok(
        (fallbackMs / 1000) * bytesPerSecond <= maxAudioSize,
        `the page bounds a recording at ${fallbackMs} ms, which is ` +
            `${(fallbackMs / 1000) * bytesPerSecond} bytes at ` +
            `${bytesPerSecond} B/s. That is above the ${maxAudioSize}-byte ` +
            "upload ceiling, so the 413 arrives before the cap does and the " +
            "duration limit is unreachable.",
    );
});
