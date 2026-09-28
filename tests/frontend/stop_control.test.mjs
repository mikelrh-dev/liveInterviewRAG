/**
 * END has to actually end.
 *
 * THE DEFECT
 * ----------
 * `stopInterview()` flipped `isInterviewActive`, stopped the recorder, released
 * the microphone, reset the button and wrote "Entrevista finalizada" into the
 * transcript. It did not stop the audio that was already playing, did not clear
 * the chunks queued behind it, and did not touch the SSE read loop.
 *
 * So pressing END mid-answer did what the user asked and nothing else: the
 * interview kept talking, kept streaming tokens, and kept appending to a
 * transcript the user had been told was closed. The one control that exists to
 * stop the thing was unable to stop the thing.
 *
 * `streaming.py` claims queued audio is dropped when the session tears down.
 * On the client that was false in both directions: nothing dropped the queue,
 * and nothing cancelled the stream.
 *
 * WHAT IS ASSERTED
 * ----------------
 * The three things still running when END is pressed. Each is driven through
 * the real function in the real DOM, and each is read off the object that was
 * left running -- not off the source.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom, producedBy } from "./dom.mjs";

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/** A window mid-answer: one chunk playing, one queued behind it. */
function playingEnv() {
    const env = createDom();
    env.recorder.fakes.FakeMediaRecorder.supported = ["audio/webm;codecs=opus"];

    const candidate = env.document.createElement("div");
    candidate.className = "message candidate";
    const bubble = env.document.createElement("div");
    bubble.className = "bubble";
    candidate.appendChild(bubble);
    env.document.getElementById("conversation").appendChild(candidate);

    const fn = env.loadApp(
        [
            "tryPlayNextChunk",
            "abandonCurrentChunk",
            "checkAllDone",
            "addAudioIndicator",
            "removeAudioIndicator",
            "advancePastSkippedChunks",
            "setStatus",
            "setState",
            "setMicLabel",
            "addMessage",
            "ensureAudioContext",
            "stopInterview",
            "createTurnNarrator",
        ],
        {
            audioContext: producedBy(() => new env.recorder.fakes.FakeAudioContext()),
            currentCandidateDiv: candidate,
            isInterviewActive: true,
            audioQueue: [
                { id: 0, url: "/audio/chunk-0.mp3" },
                { id: 1, url: "/audio/chunk-1.mp3" },
            ],
            nextChunkId: 0,
        },
    );

    const narrator = fn.createTurnNarrator({
        onStatus: (text, className) => fn.setStatus(text, className),
    });
    fn.state.turnNarrator = narrator;

    return { env, fn, narrator };
}

test("END silences the answer that is still playing", async () => {
    const { env, fn } = playingEnv();
    fn.tryPlayNextChunk();
    await settle();

    assert.equal(env.audios.length, 1, "no chunk started, so this proves nothing");
    assert.equal(env.audios[0].paused, false, "the chunk is not playing to begin with");

    fn.stopInterview();

    assert.equal(
        env.audios[0].paused,
        true,
        "the chunk that was playing when END was pressed is still playing: the " +
            "interviewer keeps talking after the candidate stopped it",
    );
    assert.equal(
        fn.state.isAudioPlaying,
        false,
        "the player is still latched after END, so the next turn's first chunk " +
            "returns at the guard and is never heard",
    );
    env.close();
});

test("END drops the chunks still queued behind it", async () => {
    const { env, fn } = playingEnv();
    fn.tryPlayNextChunk();
    await settle();

    assert.equal(
        fn.state.audioQueue.length,
        1,
        "the queue did not have a second chunk, so there is nothing to drop",
    );

    fn.stopInterview();
    await settle();
    await settle();

    // Spread into a Node array: the queue is a jsdom-realm Array, and
    // deepStrictEqual compares prototypes, so comparing it to a literal [] here
    // would report a prototype mismatch on an empty queue.
    assert.deepEqual(
        [...fn.state.audioQueue],
        [],
        `END left ${fn.state.audioQueue.length} chunk(s) queued, and they are ` +
            "played into a transcript the user believes is closed",
    );
    assert.equal(
        env.audios.length,
        1,
        `END started the ${env.audios.length - 1} queued chunk(s) instead of ` +
            "dropping them",
    );
    env.close();
});

test("END takes the audio indicator down", async () => {
    const { env, fn } = playingEnv();
    fn.tryPlayNextChunk();
    await settle();
    assert.ok(
        env.document.getElementById("conversation").querySelector(".audio-indicator"),
        "the indicator never appeared, so this would pass for the wrong reason",
    );

    fn.stopInterview();

    assert.equal(
        env.document.getElementById("conversation").querySelector(".audio-indicator"),
        null,
        "the ellipsis is still on the answer after END: the page is showing that " +
            "audio is coming for an interview that has stopped",
    );
    env.close();
});

test("END does not re-open the turn it just closed", async () => {
    // The nastier order. `checkAllDone()` is the only caller of
    // `startListening()`, and the `finally` of the aborted read loop calls it
    // on the way out. So the hazard is not that the turn is described wrongly --
    // it is that ending an interview silently starts recording again, on an
    // interview the user has ended, with the mic light on.
    const { env, fn } = playingEnv();
    fn.tryPlayNextChunk();
    await settle();
    assert.equal(env.recorder.recorders, 0, "the mic was already recording to begin with");

    fn.stopInterview();
    await settle();
    await settle();

    assert.equal(
        env.recorder.recorders,
        0,
        `END restarted the recorder ${env.recorder.recorders} time(s): the ` +
            "interview is over on screen and still running",
    );
    assert.equal(fn.state.isRecording, false, "the page thinks it is still recording");
    assert.equal(
        fn.state.isInterviewActive,
        false,
        "isInterviewActive is true after END, so the next audio chunk restarts " +
            "the mic on a session the user closed",
    );
    env.close();
});

// ─── The stream ───────────────────────────────────────────────────────────

/** A window whose /message/stream never finishes on its own. */
function streamingEnv() {
    const env = createDom();
    env.recorder.fakes.FakeMediaRecorder.supported = ["audio/webm;codecs=opus"];

    let captured = null;
    let failRead = null;
    // A stream that stays open. `read()` only settles when the test says so,
    // which is what a real server-sent stream looks like from the client side.
    env.onFetch((url, init) => {
        captured = { url: String(url), init };
        return {
            ok: true,
            status: 200,
            body: {
                getReader: () => ({
                    read: () =>
                        new Promise((_resolve, reject) => {
                            failRead = reject;
                        }),
                }),
            },
        };
    });

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
            audioChunks: producedBy(() => [new env.window.Blob(["audio"], { type: "audio/webm" })]),
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

    return {
        env,
        fn,
        /** The init the real fetch was called with, once the request is out. */
        request: () => captured,
        /** Make the pending read fail the way an aborted stream does. */
        abortRead: (signal) => {
            if (!failRead) throw new Error("the read loop was never entered");
            const error = new Error("The operation was aborted");
            error.name = "AbortError";
            if (signal) failRead(error);
            else failRead(new Error("stopped"));
        },
    };
}

test("the turn's request carries a signal END can cancel", async () => {
    const { env, fn, request } = streamingEnv();
    fn.processRecordingStream();
    await settle();
    await settle();

    const req = request();
    assert.ok(req, "the stream was never requested, so this proves nothing");
    assert.ok(
        req.init && req.init.signal instanceof env.window.AbortSignal,
        "the /message/stream request carries no AbortSignal, so there is nothing " +
            "for END to cancel: the read loop runs to completion whatever the " +
            "candidate does",
    );
    env.close();
});

test("END aborts the read loop", async () => {
    const { env, fn, request, abortRead } = streamingEnv();
    fn.processRecordingStream();
    await settle();
    await settle();

    const signal = request().init.signal;
    assert.equal(signal.aborted, false, "the request is already aborted before END");

    fn.stopInterview();

    assert.equal(
        signal.aborted,
        true,
        "END did not abort the request, so the server keeps streaming and the " +
            "client keeps reading a turn the candidate has ended",
    );
    env.close();
});

test("an ended turn reports no failure into the closed transcript", async () => {
    // The read loop has to unwind quietly. Today every terminal path writes a
    // message, so pressing END while the server is still talking appends an
    // error to the transcript the user just closed -- the last thing written
    // into a finished interview is a failure the user did not cause.
    const { env, fn, request, abortRead } = streamingEnv();
    fn.processRecordingStream();
    await settle();
    await settle();

    fn.stopInterview();
    abortRead(request().init.signal);
    await settle();
    await settle();

    const messages = [...env.document.querySelectorAll("#conversation .message")];
    assert.equal(
        messages.filter((m) => m.classList.contains("error")).length,
        0,
        "the transcript of an ended interview ends with an error message: " +
            messages.map((m) => m.textContent.trim()).join(" | "),
    );
    env.close();
});
