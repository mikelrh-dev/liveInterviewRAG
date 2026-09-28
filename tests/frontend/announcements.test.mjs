/**
 * What a screen-reader user is told, and when.
 *
 * Neither `#conversation` nor `#status` carried `aria-live`, so nothing in this
 * page was ever announced. The two need different settings, and the difference
 * is the whole content of this file.
 *
 * `#status` is a single line that changes four to six times per turn over about
 * ten seconds -- upload, transcribe, generate, speak, mic back -- and after
 * 992511d each of those is a claim the pipeline actually observed. That is the
 * single most useful thing this page can say out loud, and `polite` is the only
 * correct politeness: the user asked a question and wants to know it was heard,
 * not to be interrupted mid-thought by a progress line. `aria-atomic="true"`
 * because the line is replaced wholesale, and a diff would read as a fragment.
 *
 * `#conversation` is a stream of turns, so `aria-atomic` must stay false --
 * re-reading the entire transcript on every change would be unusable. But the
 * answer arrives by being *appended to* as the LLM streams, and a polite region
 * announces every one of those appends: a five-sentence answer re-read as a
 * growing prefix five to ten times. So the streamed bubble is muted with
 * `aria-live="off"` while it streams, and unmuted exactly once at turn
 * completion, which is what `finalizeAnswer` is for. The user hears the
 * question as it is added, silence while the answer is written, and then the
 * whole answer, once.
 *
 * The mic button had the fixed name "Iniciar entrevista" for the life of the
 * page, including while it was the control that stopped a running interview.
 * A start/stop control names the action it will perform, so the name is a
 * function of the state.
 *
 *   node --test tests/frontend/
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { extractFunction, loadWithGlobals, readAppJs } from "./harness.mjs";
import { createDom } from "./dom.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(here, "..", "..", "frontend");
const appJs = readAppJs();
const html = readFileSync(join(FRONTEND, "index.html"), "utf8");

/** The open tag of the element carrying `id`, comments removed. */
function openTag(id) {
    const at = html.indexOf(`id="${id}"`);
    assert.notEqual(at, -1, `#${id} not found in frontend/index.html`);
    const start = html.lastIndexOf("<", at);
    return html.slice(start, html.indexOf(">", at) + 1);
}

/**
 * The units under test, lifted lazily.
 *
 * At module scope an extraction failure throws while the file is being
 * imported, and Node reports one failed "test" for the whole suite -- so a
 * missing unit hides the twelve other assertions that would have run. Here each
 * test fails on its own.
 */
const lazy = (name) => () => loadWithGlobals(name, {});
const micLabel = lazy("micLabel");
const finalizeAnswer = lazy("finalizeAnswer");

// ─── 1. #status: the stage line, spoken ─────────────────────────────────────

test("the status line is a polite live region", () => {
    assert.match(
        openTag("status"),
        /aria-live="polite"/,
        "#status is not a live region, so the four stages the pipeline now " +
            "reports are visible and unheard",
    );
});

test("the status line is polite, never assertive", () => {
    // Assertive interrupts whatever the user is reading. A progress line that
    // cuts across a sentence the user is reviewing is worse than silence, and
    // this one changes five times a turn.
    assert.doesNotMatch(
        openTag("status"),
        /aria-live="assertive"|aria-live="rude"/,
        "#status is an assertive live region, so the pipeline interrupts the " +
            "user every time a stage changes",
    );
});

test("the whole status line is read, not a diff of it", () => {
    // Every stage replaces the previous string. Without aria-atomic the
    // announcement is the changed fragment, which for these strings is often a
    // single word.
    assert.match(
        openTag("status"),
        /aria-atomic="true"/,
        "#status is not atomic, so a stage change is announced as the words " +
            "that differ from the last one",
    );
});

// ─── 2. #conversation: turns, announced once ───────────────────────────────

test("the transcript is a polite live region", () => {
    assert.match(
        openTag("conversation"),
        /aria-live="polite"/,
        "#conversation is not a live region, so an answer arriving is never " +
            "announced at all",
    );
    assert.doesNotMatch(
        openTag("conversation"),
        /aria-live="assertive"/,
        "#conversation interrupts on every turn",
    );
});

test("the transcript never re-reads itself", () => {
    // aria-atomic defaults to false, which is what a growing transcript needs.
    // Asserting the absence rather than writing aria-atomic="false" out keeps
    // the markup free of an attribute that says what the default already says.
    assert.doesNotMatch(
        openTag("conversation"),
        /aria-atomic="true"/,
        "#conversation is atomic, so every turn re-reads the entire transcript " +
            "from the top",
    );
});

test("the streamed answer is muted while it is being written", () => {
    // Otherwise a polite region announces each LLM token, and the user hears a
    // five-sentence answer as a growing prefix five to ten times over.
    const add = extractFunction("addMessage", appJs);
    const candidate = add.slice(add.indexOf('type === "candidate"'));

    assert.match(
        candidate,
        /aria-live/,
        "the candidate bubble is never muted, so a polite #conversation " +
            "announces the answer once per token as it is written",
    );
    assert.doesNotMatch(
        add.slice(0, add.indexOf('type === "candidate"')),
        /aria-live/,
        "a non-candidate message is muted too, so the user never hears their " +
            "own question read back",
    );
});

test("a turn with no answer never reaches the unmuting step", () => {
    // An empty transcription answers `error` + `done` with no `token`, so
    // currentCandidateDiv is still null. finalizeAnswer must tolerate that --
    // it runs on the terminal path of every turn, including the ones that
    // produced nothing. A div that never got a bubble is the same situation
    // one step further along, so it is tolerated too.
    const noBubble = { querySelector: () => null };
    const noParagraph = { querySelector: () => ({ querySelector: () => null }) };

    assert.doesNotThrow(() => finalizeAnswer()(null), "finalizeAnswer(null) threw");
    assert.doesNotThrow(
        () => finalizeAnswer()(noBubble),
        "finalizeAnswer threw on a message with no bubble",
    );
    assert.doesNotThrow(
        () => finalizeAnswer()(noParagraph),
        "finalizeAnswer threw on a bubble with no paragraph",
    );
});

test("finishing an answer unmutes it and lets it speak", () => {
    const answer = fakeAnswer();
    finalizeAnswer()(answer.div);

    assert.equal(
        answer.attrs["aria-live"],
        undefined,
        "the answer is still muted, so the user never hears the finished reply",
    );
    assert.equal(answer.readText(), "Una respuesta completa.", "the answer text was lost");
});

test("the muting is lifted BEFORE the re-commit, or the re-commit is silent", () => {
    // This is the whole ordering. A live region announces on a mutation; a
    // mutation made while `aria-live="off"` is still in force is a mutation
    // nobody hears, so lifting the mute last produces a completely silent
    // answer. Reading the order out of the recorded mutations is the only way
    // to see it.
    const answer = fakeAnswer();
    finalizeAnswer()(answer.div);

    const mute = answer.log.findIndex((e) => e === "removeAttribute:aria-live");
    const speak = answer.log.findIndex((e) => e.startsWith("textContent="));

    assert.notEqual(mute, -1, "the muting was never lifted");
    assert.notEqual(speak, -1, "the text was never re-committed, so nothing is announced");
    assert.ok(
        mute < speak,
        `the muting was lifted at step ${mute + 1} and the text re-committed at ` +
            `step ${speak + 1}: the re-commit happened while the region was ` +
            "still muted, so the answer is announced to nobody",
    );
});

test("finishing an answer drops the blinking cursor", () => {
    // `.typing-cursor` is appended per token and never removed, so it used to
    // blink forever at the end of every answer. The re-commit is what clears
    // it, and the answer being final is the only moment that is correct.
    const answer = fakeAnswer();
    finalizeAnswer()(answer.div);

    assert.equal(answer.cursor.removed, true, "the cursor kept blinking after the answer ended");
});

test("completion is announced by the one settler, not by an event branch", () => {
    // A terminal path that settles without announcing leaves a blind user
    // waiting on an answer that arrived in silence. Driving it from the settler
    // means `done`, `interview_end`, `error` and EOF all get it.
    const at = appJs.indexOf("createTurnSettler({");
    const hook = appJs.slice(at, appJs.indexOf("try {", at));

    assert.match(
        hook,
        /finalizeAnswer\(/,
        "the turn's terminal bookkeeping does not finish the answer, so the " +
            "answer stays muted and is never announced",
    );
});

test("the typing indicator is not announced", () => {
    // A direct consequence of making #conversation a live region: this node is
    // appended into that region on every turn. It is three empty dots inside a
    // decorative avatar -- there is no text in it to announce -- and it says
    // nothing that #status has not already said out loud one line earlier. Left
    // live, it adds an announcement with no content to every single turn.
    const show = extractFunction("showTyping", appJs);

    assert.match(
        show,
        /setAttribute\(\s*"aria-hidden",\s*"true"/,
        "the typing indicator is added inside the live transcript and is not " +
            "hidden from it, so it is announced on every turn with nothing to say",
    );
});

// ─── 3. The mic names the action it performs ───────────────────────────────

test("the idle mic starts an interview", () => {
    assert.equal(micLabel()(false), "Iniciar entrevista");
});

test("the running mic stops the interview", () => {
    // The defect: one fixed string for both states, so a screen-reader user was
    // told the button starts an interview at the moment it is the only control
    // that can end one.
    assert.equal(
        micLabel()(true),
        "Detener entrevista",
        "the running mic still claims it starts the interview",
    );
    assert.notEqual(
        micLabel()(true),
        micLabel()(false),
        "one name for both states is the defect, not the fix",
    );
});

test("the markup's initial name is the idle one", () => {
    // Cross-check: the attribute in index.html and the function in app.js must
    // agree, or the page lies for the moment before the first interview.
    assert.match(
        openTag("btn-mic"),
        new RegExp(`aria-label="${micLabel()(false)}"`),
        "the mic's initial aria-label does not match what micLabel(false) " +
            "returns, so the page names a state that has not been applied yet",
    );
});

/**
 * A window with the interview lifecycle loaded, so the mic's name can be read
 * off the real element instead of searched for in the source.
 */
function interviewEnv() {
    const env = createDom();
    env.recorder.fakes.FakeMediaRecorder.supported = ["audio/webm;codecs=opus"];
    const fn = env.loadApp(
        [
            "startInterview",
            "stopInterview",
            "initAudio",
            "resetInterviewView",
            "addMessage",
            "updateSessionInfo",
            "updateTurnCount",
            "setMicLabel",
            "micLabel",
            "setStatus",
            "setState",
            "chooseMimeType",
            "ensureAudioContext",
            "startVad",
            "stopVad",
            "startRecording",
            "startListening",
            "createTurnState",
        ],
        { isInterviewActive: false },
    );
    fn.state.turnState = fn.createTurnState();
    env.onFetch(() => ({
        ok: true,
        status: 200,
        json: async () => ({ conversation_id: "conv-1", welcome_message: "Hola" }),
    }));
    return { env, fn, name: () => env.document.getElementById("btn-mic").getAttribute("aria-label") };
}

test("both transitions set the name", async () => {
    // Was a regex over the two function bodies looking for `setMicLabel(true)`
    // and `setMicLabel(false)`. It asserted the spelling of the rename, so it
    // broke the moment the rename moved into the function that owns the button's
    // presentation -- and would have been equally happy if the call sat in a
    // branch that never runs. The claim underneath is about the name on the
    // element, so that is what is read.
    const { env, fn, name } = interviewEnv();

    assert.equal(name(), "Iniciar entrevista", "precondition: the idle name is wrong");

    await fn.startInterview();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(name(), "Detener entrevista", "starting does not rename the mic");

    fn.stopInterview();
    assert.equal(name(), "Iniciar entrevista", "stopping does not rename the mic");
    env.close();
});

test("the rename waits for the interview that actually starts", async () => {
    // startInterview() returns early when the conversation cannot be created.
    // Renaming before that would leave a button offering to stop an interview
    // that does not exist -- the same class of lie, one turn earlier, and the
    // one a screen-reader user is worst served by.
    const { env, fn, name } = interviewEnv();
    env.onFetch(() => ({ ok: false, status: 503, json: async () => ({}) }));

    await fn.startInterview();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(
        name(),
        "Iniciar entrevista",
        `a start that failed leaves the mic reading "${name()}": a stop control ` +
            "on screen for an interview that does not exist",
    );
    env.close();
});

test("the mic carries no aria-pressed on top of its name", () => {
    // Over-ARIA. A toggle button can announce either its changing name or a
    // pressed state; a control that does both says two things about one fact,
    // and the name is the one a user can act on.
    assert.doesNotMatch(
        openTag("btn-mic"),
        /aria-pressed/,
        "#btn-mic carries aria-pressed as well as a state-dependent name; the " +
            "name already reports the state",
    );
});

// ─── helpers ────────────────────────────────────────────────────────────────

/**
 * A stand-in for a streamed answer bubble, recording every mutation in order.
 *
 * The order is the point: `finalizeAnswer` has to lift the muting before it
 * re-commits the text, and a fake that only exposes the end state cannot show
 * that.
 */
function fakeAnswer(text = "Una respuesta completa.") {
    const log = [];
    const attrs = { "aria-live": "off" };
    const cursor = {
        removed: false,
        remove() {
            this.removed = true;
            log.push("cursor.remove");
        },
    };

    const p = {
        _text: text,
        get textContent() {
            return this._text;
        },
        set textContent(value) {
            this._text = value;
            log.push(`textContent=${value}`);
        },
        querySelector: (sel) => (sel === ".typing-cursor" ? cursor : null),
    };

    const bubble = {
        attrs,
        setAttribute: (name, value) => {
            log.push(`setAttribute:${name}=${value}`);
            attrs[name] = value;
        },
        removeAttribute: (name) => {
            log.push(`removeAttribute:${name}`);
            delete attrs[name];
        },
        getAttribute: (name) => attrs[name],
        querySelector: (sel) => (sel === "p" ? p : null),
    };

    return {
        log,
        attrs,
        cursor,
        p,
        bubble,
        div: { querySelector: (sel) => (sel === ".bubble" ? bubble : null) },
        readText: () => p.textContent,
    };
}
