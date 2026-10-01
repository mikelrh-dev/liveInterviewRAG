/**
 * A truncated answer must LOOK truncated.
 *
 * THE DEFECT
 * ----------
 * When the LLM provider dies mid-answer the backend emitted the tokens, one
 * `audio_url`, an `error` and then `done {}`. The client committed that empty
 * payload, so:
 *
 *   * `turnState.commit({})` returned `null`, and the turn counter -- which is
 *     driven by nothing else -- never advanced. The sidebar stayed at the turn
 *     before the one the candidate just heard.
 *
 *   * nothing in the transcript said the answer stopped half way through. The
 *     error bubble said the response could not be generated, and the answer
 *     bubble next to it still read as a finished reply. A recruiter skimming
 *     the transcript is reading a half-answer as the whole answer.
 *
 * These tests drive `processRecordingStream` with the wire that the real
 * pipeline produces on that failure, so the assertions are about the DOM the
 * candidate ends up looking at rather than about a function in isolation.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom, producedBy } from "./dom.mjs";

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/** The exact event sequence the failing turn puts on the wire. */
function truncatedWire({ incomplete = true } = {}) {
    return [
        { event: "transcription", data: { text: "hableme de fraud detector" } },
        { event: "token", data: { text: "Tengo experiencia senior en deteccion de fraude. " } },
        { event: "token", data: { text: "Lideré un equipo de ocho personas. " } },
        { event: "audio_url", data: { id: 0, url: "/audio/conv-1/sentence_0.mp3" } },
        { event: "error", data: { detail: "No se pudo generar la respuesta." } },
        {
            event: "done",
            data: incomplete
                ? { n: 0, has_context: false, incomplete: true }
                : { n: 0, has_context: false },
        },
    ];
}

/**
 * A window whose `/message/stream` replays `frames` and then reaches EOF.
 *
 * The reader hands over the whole wire in one chunk and closes on the next
 * read, which is the ordinary shape of a short SSE response.
 */
function streamingEnv(frames) {
    const env = createDom();
    env.recorder.fakes.FakeMediaRecorder.supported = ["audio/webm;codecs=opus"];

    const wire =
        frames
            .map((frame) => `data: ${JSON.stringify(frame)}\n\n`)
            .join("") + "\n";
    let reads = 0;

    env.onFetch(() => ({
        ok: true,
        status: 200,
        body: {
            getReader: () => ({
                read: () => {
                    reads += 1;
                    if (reads === 1) {
                        return Promise.resolve({
                            done: false,
                            value: new env.window.Uint8Array(Buffer.from(wire, "utf8")),
                        });
                    }
                    return Promise.resolve({ done: true, value: undefined });
                },
            }),
        },
    }));

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
            "markAnswerIncomplete",
            "finalizeAnswer",
            "updateTurnCount",
            "fetchContext",
            "resetAudioQueue",
            "tryPlayNextChunk",
            "abandonCurrentChunk",
            "checkAllDone",
            "addAudioIndicator",
            "removeAudioIndicator",
            "advancePastSkippedChunks",
            "ensureAudioContext",
            "addMessage",
            "showTyping",
            "hideTyping",
            "setStatus",
            "setState",
            "setMicLabel",
            "stopInterview",
        ],
        {
            audioContext: producedBy(() => new env.recorder.fakes.FakeAudioContext()),
            audioChunks: producedBy(
                () => [new env.window.Blob(["audio"], { type: "audio/webm" })],
            ),
            selectedMimeType: "audio/webm;codecs=opus",
            conversationId: "conv-1",
            isInterviewActive: true,
            latencyReadout: producedBy(() =>
                env.window.createLatencyReadout(env.document.getElementById("latency")),
            ),
            healthStatus: producedBy(() =>
                env.window.createHealthStatus(
                    env.document.getElementById("sidebar-status"),
                ),
            ),
            writeOncePolicy: producedBy(() =>
                env.window.createRetryPolicy({ replayable: false }),
            ),
            turnState: producedBy(() => env.window.createTurnState()),
        },
    );

    return { env, fn };
}

/** Run one whole turn and let every queued microtask land. */
async function runTurn(t, frames) {
    const { env, fn } = streamingEnv(frames);
    // Registered rather than called at the end of each test: jsdom's window
    // keeps an animation-frame loop alive, so an assertion that throws before
    // the explicit close leaves a handle that never lets `node --test` exit.
    // The suite then hangs instead of reporting the failure.
    t.after(() => env.close());
    fn.processRecordingStream();
    for (let i = 0; i < 12; i++) await settle();
    return { env, fn };
}

function candidateBubble(env) {
    return env.document.querySelector("#conversation .message.candidate");
}

// ─── The counter ───────────────────────────────────────────────────────────

test("the turn counter advances even though the answer was cut short", async (t) => {
    const { env } = await runTurn(t, truncatedWire());

    assert.match(
        env.document.getElementById("sidebar-turns").textContent,
        /Turnos:\s*1/,
        "the sidebar still counts the turn before the one the candidate heard: " +
            "`done {}` carries no `n`, so nothing advanced the counter",
    );
    env.close();
});

// ─── The mark on the transcript ────────────────────────────────────────────

test("the truncated answer says it was cut short", async (t) => {
    const { env } = await runTurn(t, truncatedWire());

    const bubble = candidateBubble(env);
    assert.ok(bubble, "no candidate answer was rendered at all");

    const mark = bubble.querySelector(".answer-incomplete");
    assert.ok(
        mark,
        "the answer reads as a finished reply. Nothing on the transcript says " +
            "the twin stopped generating, so a half-answer is presented as the " +
            "whole one: " +
            bubble.textContent.trim(),
    );
    assert.match(
        mark.textContent,
        /incompleta/i,
        `the mark does not say the answer was incomplete: ${mark.textContent}`,
    );
    env.close();
});

test("the answer text the candidate saw is still there", async (t) => {
    const { env } = await runTurn(t, truncatedWire());

    const bubble = candidateBubble(env);
    assert.match(
        bubble.textContent,
        /Lideré un equipo de ocho personas\./,
        "the mark replaced the answer instead of labelling it",
    );
    env.close();
});

// ─── A complete turn is not labelled ───────────────────────────────────────

test("the label does not blame the model's generation alone", async (t) => {
    // The same mark is now also carried by an answer the model generated IN FULL
    // and that only partly reached the speaker, so a label that names generation
    // as the cause is false on the turn the mark exists to describe. The label
    // is the one thing on the candidate's screen that says so.
    const { env } = await runTurn(t, truncatedWire());

    const mark = candidateBubble(env).querySelector(".answer-incomplete");
    assert.ok(mark, "precondition: the answer carries the mark at all");

    assert.match(
        mark.textContent,
        /audio|escuchar/i,
        "the label tells the candidate the model stopped generating, which is not " +
            "what happened when only part of the answer could be spoken: " +
            mark.textContent,
    );
    env.close();
});

test("a finished answer is not marked incomplete", async (t) => {
    // The mirror image, and the one that matters if this ever regresses the
    // other way: a mark that appears on every turn stops meaning anything.
    const { env } = await runTurn(t, truncatedWire({ incomplete: false }));

    const bubble = candidateBubble(env);
    assert.ok(bubble, "no candidate answer was rendered at all");
    assert.equal(
        bubble.querySelector(".answer-incomplete"),
        null,
        "a turn that completed was labelled as truncated: " + bubble.textContent.trim(),
    );
    env.close();
});

// ─── The unit itself ───────────────────────────────────────────────────────

test("marking is idempotent: one label per answer, however many times it runs", async (t) => {
    const { env, fn } = await runTurn(t, truncatedWire());
    const bubble = candidateBubble(env);

    fn.markAnswerIncomplete(bubble);
    fn.markAnswerIncomplete(bubble);

    assert.equal(
        bubble.querySelectorAll(".answer-incomplete").length,
        1,
        "the answer is labelled twice, which reads as a rendering bug",
    );
    env.close();
});

test("marking an answer that is not there is a no-op, not a crash", () => {
    const env = createDom();
    const fn = env.loadApp(["markAnswerIncomplete"]);

    assert.doesNotThrow(() => fn.markAnswerIncomplete(null));
    assert.doesNotThrow(() => fn.markAnswerIncomplete(env.document.createElement("div")));
    env.close();
});
