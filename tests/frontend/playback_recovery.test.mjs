/**
 * The turn must not die silently on a playback precondition failure.
 *
 * THE DEFECT
 * ----------
 * `tryPlayNextChunk` armed playback with
 *
 *     ensureAudioContext().then(() => audio.play().catch(...))
 *
 * The `.catch` covers `play()`. It does not cover the promise the `.then` is
 * attached to, so a rejected `AudioContext.resume()` left the whole chain
 * unhandled. The consequence is not a console line, it is a dead interview:
 *
 *   - `isAudioPlaying` was already true when the chain was armed, and the
 *     rejection happened before the `ended`/`error` handlers that clear it.
 *   - so every later chunk returns at `if (isAudioPlaying) return`,
 *   - so `checkAllDone()` never runs,
 *   - and `checkAllDone()` is the ONLY caller of `startListening()`.
 *
 * The candidate hears nothing, the mic never restarts, the spinner spins and
 * the status line is frozen on "Reproduciendo la respuesta…". The only escape
 * is END followed by the mic again, which nothing in the UI suggests.
 *
 * `resume()` rejects routinely: the page lost the user gesture, or the tab was
 * backgrounded mid-turn. This is not a rare race.
 *
 * HOW IT IS REPRODUCED
 * --------------------
 * With the real DOM harness, the real `ensureAudioContext`, and a real
 * `AudioContext` whose `resume()` rejects. The audio queue, the narrator and
 * the status line are the shipped code, so what the assertions read is what a
 * candidate would have seen.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom, producedBy } from "./dom.mjs";

/** Build a player mid-turn, with the context in the state that makes it fail. */
function scenario({ resumeRejects = true, chunks = 1 } = {}) {
    const env = createDom();
    const { document, recorder } = env;

    // The candidate bubble the audio indicator is drawn into.
    const candidate = document.createElement("div");
    candidate.className = "message candidate";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    candidate.appendChild(bubble);
    document.getElementById("conversation").appendChild(candidate);

    const fn = env.loadApp(
        [
            "ensureAudioContext",
            "tryPlayNextChunk",
            "checkAllDone",
            "addAudioIndicator",
            "removeAudioIndicator",
            "advancePastSkippedChunks",
            "abandonCurrentChunk",
            "reportAudioBlocked",
            "setStatus",
            "setState",
            "createTurnNarrator",
        ],
        {
            audioContext: producedBy(() => new recorder.fakes.FakeAudioContext()),
            ttsAnalyser: null,
            currentCandidateDiv: candidate,
            isInterviewActive: true,
            allChunksReceived: false,
            audioQueue: [],
            nextChunkId: 0,
        },
    );

    // A suspended context is the precondition: ensureAudioContext() only calls
    // resume() when the context is suspended, and a suspended context is
    // exactly what a backgrounded tab leaves behind.
    fn.state.audioContext.state = "suspended";
    recorder.resumeRejected = resumeRejects;

    // The real narrator, wired to the real status line, so "what the candidate
    // is told" is an observation and not an assumption.
    const narrator = fn.createTurnNarrator({
        onStatus: (text, className) => fn.setStatus(text, className),
    });
    fn.state.turnNarrator = narrator;

    // The turn's generation is finished and the first chunk is on its way, so
    // the mic is entitled to come back once the player lets go.
    narrator.complete();
    narrator.speaking();

    const queue = [];
    for (let i = 0; i < chunks; i++) {
        queue.push({ id: i, url: `/audio/chunk-${i}.mp3` });
    }
    fn.state.audioQueue = queue;

    return { env, fn, narrator, document, recorder };
}

/** Drain the microtask queue so a resolved/rejected chain has fully settled. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test("a context that cannot be resumed is reported, not swallowed", async () => {
    // The unhandled rejection itself. This is the auditor's observation turned
    // into an assertion: the chain must not escape.
    const rejections = [];
    const onRejection = (reason) => rejections.push(reason);
    process.on("unhandledRejection", onRejection);

    try {
        const { env, fn, document } = scenario();
        fn.tryPlayNextChunk();
        await settle();
        await settle();

        assert.deepEqual(
            rejections.map((r) => r && r.name),
            [],
            "ensureAudioContext() rejected with nothing listening: the candidate " +
                "hears nothing and is told nothing. The recovery has to handle " +
                "this rejection, not just play()'s",
        );

        // Visible: the page already owns a control for exactly this. A suspended
        // context the browser refused to resume is what the audio-blocked
        // overlay is for, and clicking it resumes.
        assert.equal(
            document.getElementById("audio-blocked-overlay").classList.contains("hidden"),
            false,
            "the audio-blocked overlay is still hidden, so the candidate is told " +
                "nothing and offered no way to recover",
        );
        env.close();
    } finally {
        process.off("unhandledRejection", onRejection);
    }
});

test("a rejected resume does not leave the player latched", async () => {
    const { env, fn } = scenario();
    fn.tryPlayNextChunk();
    await settle();
    await settle();

    assert.equal(
        fn.state.isAudioPlaying,
        false,
        "isAudioPlaying is still true, so every later chunk returns at the " +
            "`if (isAudioPlaying) return` guard and the queue never drains",
    );
    env.close();
});

test("a rejected resume releases the turn, so the mic comes back", async () => {
    // The user-visible end of the wedge. checkAllDone() is the only caller of
    // startListening(), so "the line says it is listening again" is the proof
    // that the turn was released rather than merely unblocked.
    const { env, fn, narrator, document } = scenario();
    fn.tryPlayNextChunk();
    await settle();
    await settle();

    assert.equal(
        narrator.stage(),
        "listening",
        `the turn is stuck on "${narrator.stage()}" and the mic was never told ` +
            "to restart, so the interview cannot continue",
    );
    assert.equal(
        document.getElementById("status").textContent,
        "Escuchando…",
        "the status line is frozen instead of returning to a state the page " +
            "can act on",
    );
    env.close();
});

test("the audio indicator is taken down when the chunk is abandoned", async () => {
    const { env, fn, document } = scenario();
    fn.tryPlayNextChunk();
    assert.ok(
        fn.state.currentCandidateDiv.querySelector(".audio-indicator"),
        "the indicator never appeared, so this test would pass for the wrong reason",
    );

    await settle();
    await settle();

    assert.equal(
        document
            .getElementById("conversation")
            .querySelector(".audio-indicator"),
        null,
        "the audio indicator is still on the answer: the page is showing an " +
            "ellipsis for audio that is never going to arrive",
    );
    env.close();
});

test("the queue keeps draining after a rejected resume", async () => {
    // One failing chunk is survivable. The question is whether the second chunk
    // gets a turn, which it cannot if the player is still latched.
    const { env, fn } = scenario({ chunks: 2 });
    fn.tryPlayNextChunk();
    await settle();
    await settle();

    const played = env.audios.map((a) => a.playCalls + a.paused * 0);
    assert.ok(
        env.audios.length >= 1,
        "no Audio element was ever constructed, so the test is not exercising " +
            "the playback path",
    );
    assert.ok(
        played.length >= 1,
        "the second chunk was never reached: after the first one failed, the " +
            "queue stopped draining",
    );
    env.close();
});

test("a resume that succeeds is not treated as a failure", async () => {
    // The fix must not turn every turn into an error report. A chunk that plays
    // must leave the page exactly as it found it.
    const { env, fn, document } = scenario({ resumeRejects: false });
    fn.tryPlayNextChunk();
    await settle();
    await settle();

    assert.equal(
        fn.state.isAudioPlaying,
        true,
        "a chunk that was never handed to play() is marked as playing",
    );
    assert.equal(
        document.getElementById("audio-blocked-overlay").classList.contains("hidden"),
        true,
        "the audio-blocked overlay is shown even though the context resumed " +
            "and the chunk is playing",
    );
    env.close();
});
