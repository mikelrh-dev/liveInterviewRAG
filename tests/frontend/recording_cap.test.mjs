/**
 * A recording that outgrows the upload ceiling must be cut, not rejected.
 *
 * THE DEATH CHAIN
 * ---------------
 * `MAX_AUDIO_DURATION = 30` (backend/config.py) was enforced by nobody. The only
 * real ceiling is `MAX_AUDIO_SIZE = 5 MiB`, and the recorder is configured for
 * `audioBitsPerSecond: 128000`, so that is roughly 5.4 minutes of audio.
 *
 * Into that ceiling, the voice-activity detector is the thing that fails. It
 * ends a turn after `SILENCE_TIMEOUT_MS = 1200` of RMS below `RMS_THRESHOLD =
 * 0.015` -- and in a room with background noise the RMS never drops, so the VAD
 * never fires. `mediaRecorder` is never stopped, the blob grows, and:
 *
 *     5 MiB reached -> 413 -> 413 is not retryable -> stopInterview()
 *
 * The candidate loses the interview and the whole recording. The failure is not
 * "the turn was too long"; it is that nothing bounded the recording locally.
 *
 * So the cap is applied where the data is: the page stops the recorder itself,
 * the same way a press of STOP would, and uploads what it has. A candidate who
 * talks for four minutes gets a truncated answer and keeps the interview.
 *
 * AND A 413 THAT STILL ARRIVES IS NOT FATAL
 * ------------------------------------------
 * The cap cannot be the only defence -- a browser can encode faster than the
 * budget assumes, and the proxy has its own ceiling. So the 413 path is fixed
 * too: it used to end the session, and now it reports the real reason and hands
 * the mic back.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom, producedBy, readAppJs } from "./dom.mjs";

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/** The page's own constants, read from the shipped source. */
function appConstants() {
    const source = readAppJs();
    const read = (name) => {
        const match = source.match(new RegExp(`const\\s+${name}\\s*=\\s*([\\d_]+)`));
        assert.ok(match, `${name} is not a numeric const in frontend/app.js`);
        return Number(match[1].replace(/_/g, ""));
    };
    return {
        maxRecordingMs: read("MAX_RECORDING_MS"),
        timesliceMs: read("RECORDING_TIMESLICE_MS"),
    };
}

/** A page that has been asked to record, with the clock under our control. */
function recordingEnv({ maxRecordingMs } = {}) {
    const env = createDom({ clock: true });
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
            "addMessage",
        ],
        { isInterviewActive: true, ...(maxRecordingMs ? { MAX_RECORDING_MS: maxRecordingMs } : {}) },
    );

    return { env, fn };
}

test("the harness enforces the same ceiling the page does", () => {
    // A harness that tested a different number would pass while the page
    // enforced another, so the two are compared rather than assumed.
    const { maxRecordingMs } = appConstants();
    const { env, fn } = recordingEnv();
    assert.equal(fn.state.MAX_RECORDING_MS, maxRecordingMs);
    env.close();
});

test("the recorder is asked for a timeslice, so the recording is measurable", async () => {
    // Without a timeslice `dataavailable` only fires on stop, which is how a
    // blob grows to 5 MiB with nothing observing it.
    const { timesliceMs } = appConstants();
    const { env, fn } = recordingEnv();

    await fn.startRecording();
    const recorder = env.recorder.fakes.FakeMediaRecorder;
    void recorder;

    const instance = env.window.mediaRecorder;
    assert.ok(instance, "precondition: a recorder should exist");
    assert.equal(
        instance.startedWithTimeslice,
        timesliceMs,
        "the recorder was started without a timeslice, so nothing can observe " +
            "the recording until it stops",
    );
    env.close();
});

test("a recording past the ceiling is cut and uploaded, not left to grow", async () => {
    const { maxRecordingMs } = appConstants();
    const { env, fn } = recordingEnv();

    await fn.startRecording();
    const before = env.recorder.stops;
    assert.equal(fn.state.isRecording, true, "precondition: should be recording");

    env.clock.tick(maxRecordingMs);
    await settle();

    assert.equal(
        env.recorder.stops,
        before + 1,
        "the recorder was never stopped: the blob keeps growing until the " +
            "server rejects it with a 413 and the interview is lost",
    );
    assert.equal(fn.state.isRecording, false, "the page still believes it is recording");
    env.close();
});

test("the candidate is told the recording was cut, not left guessing", async () => {
    const { maxRecordingMs } = appConstants();
    const { env, fn } = recordingEnv();

    await fn.startRecording();
    env.clock.tick(maxRecordingMs);
    await settle();

    const status = env.document.getElementById("status").textContent;
    assert.match(
        status,
        /l[ií]mite|m[iá]ximo|demasiado/i,
        `the candidate is told nothing about the cut; the status reads "${status}"`,
    );
    env.close();
});

test("the mic comes back after the cap, so the interview continues", async () => {
    const { maxRecordingMs } = appConstants();
    const { env, fn } = recordingEnv();

    await fn.startRecording();
    env.clock.tick(maxRecordingMs);
    await settle();

    // Not "processing": this harness captures no audio, so the turn takes
    // processRecordingStream's documented empty-chunk early return and the
    // avatar goes idle. What matters is that the page is no longer LISTENING --
    // the recording is over and the mic is free -- and that the session is
    // alive. Before the cap existed the page stayed in `listening` with a blob
    // growing behind it, which is the state this defect lived in.
    assert.notEqual(
        env.document.body.dataset.state,
        "listening",
        "the avatar still reads 'listening' after the recorder was stopped",
    );
    assert.equal(
        fn.state.isInterviewActive,
        true,
        "hitting the ceiling ended the interview; a candidate who talked for " +
            "four minutes should lose the turn, not the session",
    );
    env.close();
});

test("a shorter recording is never cut", async () => {
    const { maxRecordingMs } = appConstants();
    const { env, fn } = recordingEnv();

    await fn.startRecording();
    const before = env.recorder.stops;
    env.clock.tick(maxRecordingMs - 1000);
    await settle();

    assert.equal(env.recorder.stops, before, "the ceiling fired early");
    assert.equal(fn.state.isRecording, true);
    env.close();
});

// ─── The 413 that still arrives ─────────────────────────────────────────────

/** A turn whose upload is rejected by the server, as nginx or the app would. */
function rejectedTurnEnv() {
    const env = createDom();
    env.recorder.fakes.FakeMediaRecorder.supported = ["audio/webm;codecs=opus"];

    const fn = env.loadApp(
        [
            "processRecordingStream",
            "fetchWithBackoff",
            "createRetryPolicy",
            "createLatencyReadout",
            "createHealthStatus",
            "createTurnSettler",
            "createTurnState",
            "createTurnNarrator",
            "stopInterview",
            "addMessage",
            "showTyping",
            "hideTyping",
            "removeAudioIndicator",
            "finalizeAnswer",
            "updateTurnCount",
            "fetchContext",
            "resetAudioQueue",
            "setStatus",
            "setState",
            "setMicLabel",
        ],
        {
            audioContext: producedBy(() => new env.recorder.fakes.FakeAudioContext()),
            audioChunks: producedBy(() => [
                new env.window.Blob(["audio"], { type: "audio/webm" }),
            ]),
            selectedMimeType: "audio/webm;codecs=opus",
            conversationId: "conv-1",
            isInterviewActive: true,
            latencyReadout: producedBy(() =>
                env.window.createLatencyReadout(env.document.getElementById("latency")),
            ),
            healthStatus: producedBy(() =>
                env.window.createHealthStatus(env.document.getElementById("sidebar-status")),
            ),
            writeOncePolicy: producedBy(() =>
                env.window.createRetryPolicy({ replayable: false }),
            ),
            turnState: producedBy(() => env.window.createTurnState()),
        },
    );

    // 413 is not retryable, so this is returned on the first attempt.
    env.onFetch(() => ({
        ok: false,
        status: 413,
        headers: { get: () => null },
        json: async () => ({ detail: "Request body too large" }),
    }));

    return { env, fn };
}

test("a 413 no longer ends the interview", async () => {
    const { env, fn } = rejectedTurnEnv();

    await fn.processRecordingStream();
    await settle();
    await settle();
    await settle();

    assert.equal(
        fn.state.isInterviewActive,
        true,
        "a 413 ended the session. The upload was rejected on a size the page " +
            "cannot control, and the candidate lost every turn recorded so far.",
    );
    env.close();
});

test("CONTROL: a 413 was already leaving the page usable", async () => {
    const { env, fn } = rejectedTurnEnv();

    await fn.processRecordingStream();
    await settle();
    await settle();
    await settle();

    assert.equal(
        env.document.getElementById("btn-mic").disabled,
        false,
        "the mic button is still disabled after a 413, so the page looks busy " +
            "forever",
    );
    assert.notEqual(
        env.document.body.dataset.state,
        "processing",
        "the avatar is still reading 'processing' after a rejected upload",
    );
    env.close();
});

test("CONTROL: a 413 was already being reported to the candidate", async () => {
    const { env, fn } = rejectedTurnEnv();

    await fn.processRecordingStream();
    await settle();
    await settle();
    await settle();

    const transcript = env.document.getElementById("conversation").textContent;
    assert.match(
        transcript,
        /413|demasiadogrande/i,
        `the candidate is told nothing about the rejection; the transcript ` +
            `reads "${transcript}"`,
    );
    env.close();
});
