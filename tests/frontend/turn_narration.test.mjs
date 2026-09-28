/**
 * Turn narration tests: the status line must describe the stage that is
 * actually running, not the one that was running when the request was sent.
 *
 * The defect: the recruiter's turn is speak -> "Enviando audio" -> 8-12
 * seconds -> audio. That string is wrong for almost all of the wait, because
 * the server spends that time transcribing, retrieving and generating. The
 * pipeline distinguishes five SSE events (`transcription`, `token`,
 * `audio_url`, `done`, `error`) and the dispatcher branched on all of them
 * while calling `setStatus` in none of them. The status was frozen at
 * `app.js:1477` for the whole turn. Two symptoms shared the root cause:
 * `showTyping()` ran before the request was even sent, so "the AI is
 * writing" was on screen while Whisper was still working, and the settler
 * wrote "Escuchando…" on `done`, which claims the mic is back while audio
 * chunks are still queued and playing.
 *
 * What is asserted here is BEHAVIOUR, not copy. The Spanish strings are
 * deliberately not pinned: a test that asserts a literal breaks on every
 * wording edit and proves nothing about the logic. What is pinned is that
 * the stages are *distinguishable*, that a stage never appears before it has
 * started, and that "speaking" outlives `done` for exactly as long as audio
 * is still outstanding.
 *
 * The unit under test is `createTurnNarrator`, extracted verbatim from
 * frontend/app.js via tests/frontend/harness.mjs, so the shipped source is
 * what runs. The DOM side effects it drives are injected as a hook and
 * asserted on; the wiring that must call it is verified structurally, the
 * same way tests/test_sse_terminal_state.py does.
 *
 *   node --test tests/frontend/
 */

import test from "node:test";
import assert from "node:assert/strict";
import { loadWithGlobals, readAppJs } from "./harness.mjs";
import { createDom, producedBy } from "./dom.mjs";

const appJs = readAppJs();

/** Drain the microtask queue so an awaited chain has fully settled. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

// ─── helpers ───────────────────────────────────────────────────────────────

/**
 * A narrator whose status output is recorded instead of written to the DOM.
 *
 * loadWithGlobals returns the extracted *function*, so the hooks object is its
 * first call. createTurnNarrator takes nothing but those hooks; passing a
 * second argument silently leaves `hooks` undefined, which reads as "the line
 * never changes" rather than as an arity mistake.
 */
function loadNarrator() {
    const emitted = [];
    const buildNarrator = loadWithGlobals("createTurnNarrator", {});
    const narrator = buildNarrator({
        onStatus: (text, className) => emitted.push({ text, className }),
    });
    return { narrator, emitted };
}

/**
 * The body of the SSE dispatcher, and each of its branches.
 *
 * A branch is the text from one `type === "..."` marker to the next, which is
 * exactly the span in which a call site belongs to that event. Deliberately
 * comment-blind: several comments here name branches and would otherwise be
 * mistaken for call sites.
 */
function dispatchBranches() {
    const start = appJs.indexOf("async function processRecordingStream");
    assert.notEqual(start, -1, "processRecordingStream() not found in app.js");
    const body = appJs.slice(start);
    const end = body.indexOf("    } catch (e) {");
    const scope = end === -1 ? body : body.slice(0, end);

    const marks = [];
    const marker = /type === "([a-z_]+)"/g;
    let m;
    while ((m = marker.exec(scope)) !== null) {
        marks.push({ name: m[1], at: m.index });
    }

    const branches = {};
    marks.forEach((mark, i) => {
        const stop = i + 1 < marks.length ? marks[i + 1].at : scope.length;
        branches[mark.name] = scope.slice(mark.at, stop);
    });
    return { scope, branches };
}

/** The region of the dispatcher that runs before the request leaves. */
function prologue() {
    const { scope } = dispatchBranches();
    const at = scope.indexOf("await fetchWithBackoff");
    assert.notEqual(at, -1, "the streaming request must be a fetchWithBackoff call");
    return scope.slice(0, at);
}

// ─── 1. the stages are distinguishable ─────────────────────────────────────

test("a fresh narrator has not started any stage and says nothing", () => {
    const { narrator, emitted } = loadNarrator();

    assert.equal(narrator.stage(), null, "a turn has not begun before it begins");
    assert.equal(narrator.text(), null, "an unstarted turn must render no text");
    assert.deepEqual(emitted, [], "no status may be written before a stage starts");
});

test("transcribing and generating are told apart from each other", () => {
    // The regression guard for the defect itself. A test that only asserted
    // "a string is present" would have passed against the frozen status; the
    // thing that was wrong was precisely that every stage read the same.
    const { narrator } = loadNarrator();

    narrator.begin();
    narrator.transcribing();
    const whileTranscribing = narrator.text();

    narrator.generating();
    const whileGenerating = narrator.text();

    assert.ok(whileTranscribing, "the transcribing stage must render something");
    assert.ok(whileGenerating, "the generating stage must render something");
    assert.notEqual(
        whileTranscribing,
        whileGenerating,
        "transcribing and generating are different stages of an 8-12 second " +
            "wait, so they cannot share one status string",
    );
});

test("every stage of one normal turn is a distinct string", () => {
    // One narrator, driven the way the dispatcher drives it. `complete()`
    // deliberately does not change the line while audio is outstanding, so
    // the stages are compared by name rather than by sampling every step.
    const { narrator } = loadNarrator();
    const seen = new Map();
    for (const step of [
        (n) => n.begin(),
        (n) => n.transcribing(),
        (n) => n.generating(),
        (n) => n.speaking(),
        (n) => n.complete(),
        (n) => n.audioState(0, false),
    ]) {
        step(narrator);
        const stage = narrator.stage();
        if (!seen.has(stage)) seen.set(stage, narrator.text());
    }

    assert.deepEqual(
        [...seen.keys()].sort(),
        ["generating", "listening", "speaking", "transcribing", "uploading"],
        "a normal turn should pass through exactly these stages",
    );
    assert.equal(
        new Set(seen.values()).size,
        seen.size,
        `each stage must read differently, got: ${JSON.stringify([...seen])}`,
    );
});

test("a stage never appears before it has started", () => {
    // The honest-absence half of the defect: the old UI showed "Enviando
    // audio" (and a typing bubble) for the entire wait, claiming an upload
    // that finished in milliseconds and writing that had not begun. After
    // begin() the turn is in the upload stage and nothing else is reachable.
    const { narrator, emitted } = loadNarrator();
    narrator.begin();

    assert.equal(narrator.stage(), "uploading");
    assert.equal(
        emitted.length,
        1,
        "the upload is announced once, by the one thing that observed it",
    );
});

test("the upload stage is not mistaken for a later stage", () => {
    const { narrator } = loadNarrator();
    narrator.begin();
    const uploadText = narrator.text();

    const { narrator: later } = loadNarrator();
    later.begin();
    later.transcribing();
    assert.notEqual(
        uploadText,
        later.text(),
        "'sending' and 'transcribing' are different claims about the server",
    );
});

// ─── 2. speaking outlives `done` ───────────────────────────────────────────

test("speaking survives `done` while audio is still queued", () => {
    // `done` means generation completed. It does NOT mean playback did. The
    // old settler wrote "Escuchando…" the moment `done` arrived, which
    // claimed the mic was back while the candidate was still being spoken to.
    const { narrator } = loadNarrator();
    narrator.begin();
    narrator.transcribing();
    narrator.generating();
    narrator.speaking();
    const whileSpeaking = narrator.text();

    narrator.complete();
    assert.equal(
        narrator.stage(),
        "speaking",
        "done arrived but audio is still outstanding, so the turn has not " +
            "finished being heard",
    );
    assert.equal(narrator.text(), whileSpeaking);

    // ...and the mic is not announced while a chunk is still playing.
    narrator.audioState(2, true);
    assert.equal(narrator.stage(), "speaking");
    assert.equal(narrator.text(), whileSpeaking);
});

test("speaking clears to listening only when the queue drains and playback ends", () => {
    const { narrator } = loadNarrator();
    narrator.begin();
    narrator.transcribing();
    narrator.generating();
    narrator.speaking();
    const whileSpeaking = narrator.text();

    narrator.complete();
    narrator.audioState(1, false);
    assert.equal(narrator.stage(), "speaking", "one chunk is still queued");
    assert.equal(narrator.text(), whileSpeaking);

    narrator.audioState(0, true);
    assert.equal(
        narrator.stage(),
        "speaking",
        "the queue is empty but a chunk is still playing, so it is not over",
    );

    narrator.audioState(0, false);
    assert.equal(narrator.stage(), "listening", "queue empty and nothing playing");
    assert.notEqual(narrator.text(), whileSpeaking);
});

test("an answer with no audio is not left claiming to be spoken", () => {
    const { narrator } = loadNarrator();
    narrator.begin();
    narrator.transcribing();
    narrator.generating();

    narrator.complete();
    assert.equal(
        narrator.stage(),
        "listening",
        "generation finished and there is nothing to play, so the turn is over",
    );
});

test("the queue draining mid-generation does not announce the mic", () => {
    const { narrator } = loadNarrator();
    narrator.begin();
    narrator.transcribing();
    narrator.generating();
    const whileGenerating = narrator.text();

    // A short answer can be fully played before `done` arrives. Draining is
    // not completion.
    narrator.speaking();
    narrator.complete();
    narrator.audioState(0, false);
    assert.equal(narrator.stage(), "listening");

    const { narrator: mid } = loadNarrator();
    mid.begin();
    mid.transcribing();
    mid.generating();
    mid.audioState(0, false);
    assert.equal(
        mid.stage(),
        "generating",
        "an empty queue before done means the queue is ahead of generation",
    );
    assert.equal(mid.text(), whileGenerating);
});

test("repeated signals for the current stage do not re-render it", () => {
    const { narrator, emitted } = loadNarrator();
    narrator.begin();
    narrator.speaking();
    const before = emitted.length;

    narrator.speaking();
    narrator.speaking();
    assert.equal(
        emitted.length,
        before,
        "re-announcing the stage the UI is already showing is churn",
    );
});

test("a fatal error is not overwritten by the queue draining afterwards", () => {
    const { narrator } = loadNarrator();
    narrator.begin();
    narrator.transcribing();
    narrator.generating();
    narrator.speaking();

    narrator.failed();
    const failure = narrator.text();
    assert.ok(failure, "a failure must render something");

    narrator.complete();
    narrator.audioState(0, false);
    assert.equal(
        narrator.stage(),
        "failed",
        "the turn failed; a later queue drain must not paper over that",
    );
    assert.equal(narrator.text(), failure);
});

test("a skipped audio chunk is reported without ending the turn", () => {
    const { narrator } = loadNarrator();
    narrator.begin();
    narrator.transcribing();
    narrator.generating();
    narrator.speaking();
    const whileSpeaking = narrator.text();

    narrator.chunkSkipped();
    assert.notEqual(
        narrator.text(),
        whileSpeaking,
        "a chunk the candidate could not hear is worth saying out loud",
    );

    // The turn is not over: later audio still plays, and done still closes it.
    narrator.speaking();
    assert.equal(narrator.text(), whileSpeaking);
    narrator.complete();
    narrator.audioState(0, false);
    assert.equal(narrator.stage(), "listening");
});

test("a narrator tolerates being built without a hook", () => {
    // checkAllDone() can fire outside a turn; a throw there would take the
    // playback teardown down with it.
    const narrator = loadWithGlobals("createTurnNarrator", {})();
    assert.doesNotThrow(() => {
        narrator.begin();
        narrator.transcribing();
        narrator.generating();
        narrator.speaking();
        narrator.complete();
        narrator.audioState(0, false);
        narrator.failed();
    });
    assert.equal(narrator.stage(), "failed");
});

test("every stage carries a class name that is a real string or nothing", () => {
    // setStatus(text, className) interpolates className straight into the
    // class attribute. The two error call sites passed boolean `true`, which
    // produced the class "true" -- so the red error styling those call sites
    // were reaching for never applied.
    const { narrator, emitted } = loadNarrator();
    narrator.begin();
    narrator.transcribing();
    narrator.generating();
    narrator.speaking();
    narrator.chunkSkipped();
    narrator.complete();
    narrator.audioState(0, false);
    narrator.failed();

    assert.ok(emitted.length >= 5, "every stage change must reach the status line");
    for (const { className } of emitted) {
        if (className === undefined || className === null) continue;
        assert.equal(
            typeof className,
            "string",
            `a non-string class name reaches the class attribute verbatim: ${String(className)}`,
        );
    }
});

// ─── the copy itself ───────────────────────────────────────────────────────

test("the status copy is plain professional Spanish", () => {
    const { narrator } = loadNarrator();
    const texts = [];
    for (const drive of [
        (n) => n.begin(),
        (n) => n.transcribing(),
        (n) => n.generating(),
        (n) => n.speaking(),
        (n) => n.failed(),
        (n) => n.chunkSkipped(),
    ]) {
        const { narrator: n } = loadNarrator();
        drive(n);
        texts.push(n.text());
    }
    const { narrator: done } = loadNarrator();
    done.begin();
    done.transcribing();
    done.generating();
    done.speaking();
    done.complete();
    done.audioState(0, false);
    texts.push(done.text());

    for (const text of texts) {
        assert.ok(text && text.length > 0, "every rendered stage has copy");
        assert.doesNotMatch(text, /[!¡]/, `no exclamation marks: ${text}`);
        assert.doesNotMatch(
            text,
            /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u,
            `no emoji in a portfolio UI: ${text}`,
        );
    }
});

test("no stage invents a number, a percentage or an ETA", () => {
    // The old rail shipped the literal "LATENCY 12ms" next to real metrics
    // (tests/frontend/telemetry.test.mjs). There is no per-stage timing to
    // drive a progress figure, and inventing one is the same fabrication.
    const { narrator } = loadNarrator();
    const texts = [];
    for (const drive of [
        (n) => n.begin(),
        (n) => n.transcribing(),
        (n) => n.generating(),
        (n) => n.speaking(),
        (n) => n.failed(),
        (n) => n.chunkSkipped(),
    ]) {
        const { narrator: n } = loadNarrator();
        drive(n);
        texts.push(n.text());
    }
    for (const text of texts) {
        assert.doesNotMatch(text, /\d/, `no figures in a status line: ${text}`);
        assert.doesNotMatch(
            text,
            /%|ETA|restante|aprox/i,
            `no invented progress: ${text}`,
        );
    }
});

// ─── the wiring: one owner, in the right branch ────────────────────────────

test("the dispatcher drives the narrator from the transcription branch", () => {
    const { branches } = dispatchBranches();
    assert.match(
        branches.transcription,
        /narrator\.\w*transcri/i,
        "the transcription event is the only signal that STT finished; the " +
            "status has to move there or it stays frozen for the whole wait",
    );
});

test("the dispatcher drives the narrator from the token branch", () => {
    const { branches } = dispatchBranches();
    assert.match(
        branches.token,
        /narrator\.\w*generat/i,
        "the first token is the only signal that generation started",
    );
});

test("the dispatcher drives the narrator from the audio_url branch", () => {
    const { branches } = dispatchBranches();
    assert.match(
        branches.audio_url,
        /narrator\.\w*speak/i,
        "the first audio event is the only signal that the answer is audible",
    );
});

test("the dispatcher drives the narrator from the done branch", () => {
    const { branches } = dispatchBranches();
    assert.match(
        branches.done,
        /narrator\.\w*complete/i,
        "done closes generation; without it the speaking state never clears",
    );
});

test("both halves of the error branch report through the narrator", () => {
    const { branches } = dispatchBranches();
    assert.match(
        branches.error,
        /narrator\.\w*(failed|error)/i,
        "a fatal SSE error must reach the status line",
    );
    assert.match(
        branches.error,
        /narrator\.\w*(chunkSkipped|skip)/i,
        "a skipped TTS chunk is currently silent: the candidate hears a gap " +
            "and nothing explains it",
    );
});

test("the settler no longer claims the mic is back", () => {
    // This is the second symptom. On `done` the settler wrote "Escuchando…"
    // unconditionally, while audioQueue still held unplayed chunks.
    const start = appJs.indexOf("createTurnSettler({");
    assert.notEqual(start, -1, "the settler call site must exist");
    const callSite = appJs.slice(start, appJs.indexOf("try {", start));

    assert.doesNotMatch(
        callSite,
        /Escuchando/,
        "the onSettle hook must not announce the mic: it runs on `done`, " +
            "which says nothing about whether playback has finished",
    );
});

test("the pre-request prologue announces the upload, not the writing", () => {
    const prologueText = prologue();
    assert.doesNotMatch(
        prologueText,
        /showTyping\(/,
        "the typing bubble appeared before the request was even sent, so " +
            "'the AI is writing' was on screen while the server was still " +
            "transcribing",
    );
    assert.match(
        prologueText,
        /narrator\.\w*begin/,
        "the prologue is the only honest moment to announce the upload",
    );
});

test("the typing indicator waits for the first token", () => {
    const { branches } = dispatchBranches();
    assert.match(
        branches.token,
        /showTyping\(/,
        "the first token is when the answer actually starts being written",
    );
});

test("no SSE branch writes the status line directly any more", () => {
    // The defect was structural: four branches called nothing, so the status
    // could not change. Routing through one narrator makes a branch that
    // forgets to report show up here instead of in a 10 second wait.
    const { branches } = dispatchBranches();
    for (const [name, body] of Object.entries(branches)) {
        assert.doesNotMatch(
            body,
            /setStatus\(/,
            `the ${name} branch writes the status line directly, so it has a ` +
                "second owner of the same text",
        );
    }
});

test("the status line has exactly one owner per turn", () => {
    // A narrator per turn, created next to the settler, so narration state
    // cannot leak across turns the way the settle flag used to.
    const { scope } = dispatchBranches();
    assert.match(
        scope,
        /const narrator = createTurnNarrator\(/,
        "one narrator per turn, alongside the settler",
    );

    const factory = appJs.slice(
        appJs.indexOf("function createTurnNarrator("),
    );
    const start = factory.indexOf("{");
    let depth = 0;
    let end = start;
    for (let i = start; i < factory.length; i++) {
        if (factory[i] === "{") depth++;
        else if (factory[i] === "}") {
            depth--;
            if (depth === 0) {
                end = i;
                break;
            }
        }
    }
    const body = factory.slice(0, end);
    assert.doesNotMatch(
        body,
        /setStatus\(|document\.|statusEl/,
        "the narrator renders through its hook; touching the DOM directly " +
            "would make it untestable",
    );
});

test("the queue drain the narrator is told about is the one the client tracks", () => {
    // The question "when is it actually over?" has an existing answer in this
    // file: checkAllDone() gates on allChunksReceived && queue empty &&
    // nothing playing. The narrator must be driven from that same state, not
    // from a second, subtly different notion of "finished".
    const start = appJs.indexOf("function checkAllDone(");
    assert.notEqual(start, -1, "checkAllDone() must still exist");
    const body = appJs.slice(start, start + 900);

    assert.match(body, /allChunksReceived/, "generation finished");
    assert.match(body, /audioQueue\.length === 0/, "queue drained");
    assert.match(body, /!isAudioPlaying/, "playback finished");
    assert.match(
        body,
        /audioState\(/,
        "the narrator must be told by this same state, or 'listening' can " +
            "arrive before the audio has actually stopped",
    );
});

test("the player's own ended handler is what clears the speaking state", async () => {
    // This used to be a regex over 400 characters of app.js anchored on
    // `addEventListener("ended"`, asserting that `isAudioPlaying = false` and
    // `checkAllDone()` appeared nearby. It passed while the work lived in the
    // listener and failed the moment the work moved into a named function it
    // called — which is what a test that reads the source is: a test of the
    // spelling, not of the behaviour. The claim underneath it is real and worth
    // keeping, so it is made against the real player instead.
    //
    // A chunk is played, the narrator is told, and then the audio element fires
    // `ended`. The turn must be declared over: the player idle, the narrator
    // back on "listening", and the mic entitled to restart.
    const env = createDom();
    const { document } = env;
    // A browser that can record, so startListening() takes its real path rather
    // than reporting an unsupported codec.
    env.recorder.fakes.FakeMediaRecorder.supported = ["audio/webm;codecs=opus"];

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
            "abandonCurrentChunk",
            "addAudioIndicator",
            "removeAudioIndicator",
            "advancePastSkippedChunks",
            "setStatus",
            "setState",
            "createTurnNarrator",
            "startListening",
            "startRecording",
            "startVad",
            "stopVad",
            "chooseMimeType",
        ],
        {
            audioContext: producedBy(() => new env.recorder.fakes.FakeAudioContext()),
            currentCandidateDiv: candidate,
            isInterviewActive: true,
            allChunksReceived: true,
            audioQueue: [{ id: 0, url: "/audio/chunk-0.mp3" }],
            nextChunkId: 0,
        },
    );

    const narrator = fn.createTurnNarrator({
        onStatus: (text, className) => fn.setStatus(text, className),
    });
    fn.state.turnNarrator = narrator;
    narrator.complete();
    narrator.speaking();

    fn.tryPlayNextChunk();
    await settle();

    assert.equal(narrator.stage(), "speaking", "the chunk never started playing");
    assert.equal(fn.state.isAudioPlaying, true, "the player is not busy");
    assert.equal(env.audios.length, 1, "no Audio element was constructed");

    // The real event, on the real element the shipped code created.
    env.audios[0].finish();
    await settle();

    assert.equal(
        fn.state.isAudioPlaying,
        false,
        "the ended handler did not release the player, so the queue stays latched",
    );
    assert.equal(
        narrator.stage(),
        "listening",
        `the turn is still on "${narrator.stage()}" after the audio finished, so ` +
            "the mic is never told to come back",
    );
    assert.equal(
        fn.state.isRecording,
        true,
        "checkAllDone() ran but the mic did not come back, so the interview " +
            "cannot continue even though the turn was declared over",
    );
    env.close();
});
