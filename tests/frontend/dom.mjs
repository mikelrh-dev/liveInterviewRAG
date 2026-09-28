/**
 * A real DOM for the Node frontend suites.
 *
 * WHY THIS EXISTS
 * ---------------
 * `harness.mjs` lifts a function out of app.js and evaluates it with stand-ins
 * for the globals it reads. That is honest for a pure function and worthless for
 * wiring: there is no document, so `renderContext` could not write a chip, and
 * the suites made up for it with a hand-written object that only had
 * `innerHTML`. The cost was paid in the one test that mattered most: the
 * evidence-pill suite injected its OWN `escapeHtml` and then asserted that
 * `renderContext` escaped. Replacing the real `escapeHtml` with
 * `return String(text)` left that suite green, because the function under
 * protection was never loaded.
 *
 * The defect was not a missing test, it was a missing DOM. A test that cannot
 * see the document cannot watch the wiring.
 *
 * WHAT THIS IS
 * ------------
 * The real `frontend/index.html` parsed by jsdom, plus the browser APIs jsdom
 * does not implement, installed as *controllable* fakes. Nothing here restates
 * app.js: `loadApp()` evaluates the shipped function bodies inside this window,
 * so `#status`, `#context-content`, `#btn-mic` and the rest are the real
 * elements the shipped code reaches for.
 *
 * The fakes are controllable on purpose. Defect 1 is only reproducible if a
 * test can make `AudioContext.resume()` reject; that is a decision, not an
 * accident, and a fake that always succeeded could not express it.
 *
 * WHAT THIS DELIBERATELY DOES NOT DO
 * ----------------------------------
 * It does not run `app.js` as a script. `init()` fetches /api/config, starts a
 * 1 Hz timer and wires listeners on load, which would make every assertion race
 * the page's own bootstrap. `loadApp()` evaluates named function declarations
 * into the window instead: real code, real DOM, no bootstrap.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import { JSDOM, VirtualConsole } from "jsdom";
import { extractFunction, readAppJs, readIndexHtml } from "./harness.mjs";

/**
 * The module-level `let` state of app.js, at its initial values.
 *
 * A lifted function resolves its free variables against the window, so anything
 * it reads has to exist. These are the real initialisers from the top of
 * app.js, not a guess: `loadApp` is given a state object and every name here is
 * declared as a global `var`, which is what makes the bridge two-way (a test
 * reads `state.isAudioPlaying` and sees the value the function just wrote).
 *
 * Tests pass the handful of keys they care about; the rest stay neutral. That
 * is safe only because a function that mutates a queue asserts on the queue, so
 * the test is forced to say what it is mutating.
 */
export function baseState(overrides = {}) {
    return {
        conversationId: null,
        mediaRecorder: null,
        audioChunks: [],
        isRecording: false,
        isProcessing: false,
        isInterviewActive: false,
        isUserScrolledUp: false,
        audioContext: null,
        analyserNode: null,
        mediaStream: null,
        micSourceNode: null,
        audioBlocked: false,
        selectedMimeType: "",
        ttsAnalyser: null,
        ttsVolumeBuffer: null,
        vadAnimationId: null,
        silenceStart: null,
        hasSpoken: false,
        currentState: "idle",
        waveformBars: [],
        waveformAnimationId: null,
        currentCandidateDiv: null,
        audioQueue: [],
        nextChunkId: 0,
        skippedChunkIds: new Set(),
        isAudioPlaying: false,
        allChunksReceived: false,
        sessionStartTime: null,
        vuBarsCache: null,
        ...overrides,
    };
}

/** Serialise a state value into source that can be assigned with `var x = ...`. */
function toSource(value, nodeIndex) {
    if (value === undefined) return "undefined";
    if (value === null) return "null";
    // A DOM node cannot be re-created from source, so it is published on the
    // window and referenced by index. This is how a test hands the real
    // `#context-content` / `#status` / `#btn-mic` to the lifted function, which
    // is the whole point of having a DOM.
    if (isNode(value)) return `window.__domRefs[${nodeIndex}]`;
    if (value instanceof Set) {
        return `new Set(${JSON.stringify([...value])})`;
    }
    if (typeof value === "function") return value.toString();
    if (Array.isArray(value)) return JSON.stringify(value);
    return JSON.stringify(value);
}

function isNode(value) {
    return Boolean(value) && typeof value === "object" && typeof value.nodeType === "number";
}

/**
 * A controllable `AudioContext` / `MediaRecorder` / `getUserMedia` triple.
 *
 * Every one of these exists in the test only so a failure can be *scheduled*:
 * `resumeRejected = true` reproduces the lost-user-gesture rejection that
 * wedges a turn, and `getUserMedia` rejecting is the microphone-denied path.
 */
function installAudioFakes(window, recorder) {
    class FakeAudioContext {
        constructor() {
            this.state = "running";
            this.destination = { kind: "destination" };
            this.fftSize = 2048;
        }
        createAnalyser() {
            const node = {
                fftSize: 2048,
                frequencyBinCount: 1024,
                smoothingTimeConstant: 0.8,
                connectedTo: null,
                getByteTimeDomainData: (arr) => arr.fill(128),
                getByteFrequencyData: (arr) => arr.fill(0),
                connect: (dest) => (node.connectedTo = dest),
                disconnect: () => {},
            };
            return node;
        }
        createMediaStreamSource(stream) {
            return { mediaStream: stream, connect: () => {}, disconnect: () => {} };
        }
        createMediaElementSource(el) {
            return { mediaElement: el, connect: () => {} };
        }
        /**
         * The load-bearing fake. Setting `resumeRejected` makes this reject the
         * way a context whose user gesture was lost really does, which is the
         * only way to reach the unhandled rejection.
         */
        resume() {
            recorder.resumeCalls++;
            if (recorder.resumeRejected) {
                const error = new Error("resume() rejected");
                error.name = "NotAllowedError";
                return Promise.reject(error);
            }
            this.state = "running";
            return Promise.resolve();
        }
        close() {
            this.state = "closed";
            return Promise.resolve();
        }
    }

    class FakeMediaRecorder {
        static isTypeSupported(mime) {
            return FakeMediaRecorder.supported.includes(mime);
        }
        static supported = [];

        constructor(stream, options) {
            this.stream = stream;
            this.mimeType = (options && options.mimeType) || "";
            this.state = "inactive";
            this.ondataavailable = null;
            this.onstop = null;
        }
        start() {
            this.state = "recording";
        }
        stop() {
            this.state = "inactive";
            recorder.stops++;
            if (this.onstop) this.onstop();
        }
    }

    const mediaStream = {
        getTracks: () => [{ stop: () => recorder.tracksStopped++, readyState: "live" }],
    };

    window.AudioContext = FakeAudioContext;
    window.webkitAudioContext = FakeAudioContext;
    window.MediaRecorder = FakeMediaRecorder;
    window.Audio = window.Audio || function () {};

    recorder.fakes = { FakeAudioContext, FakeMediaRecorder, mediaStream };
    return recorder;
}

/**
 * Build a window with the real markup and the APIs jsdom omits.
 *
 * @param {object} [options]
 * @param {string} [options.html]        markup to parse (defaults to the shipped index.html)
 * @param {boolean} [options.visual]     enable requestAnimationFrame (default true)
 */
export function createDom(options = {}) {
    const { html = readIndexHtml(), visual = true } = options;

    const virtualConsole = new VirtualConsole();
    const dom = new JSDOM(html, {
        runScripts: "outside-only",
        pretendToBeVisual: visual,
        url: "http://localhost/",
        virtualConsole,
    });
    const window = dom.window;
    const document = window.document;

    const recorder = {
        resumeCalls: 0,
        resumeRejected: false,
        stops: 0,
        tracksStopped: 0,
        getUserMediaCalls: 0,
        getUserMediaRejected: null,
    };

    // ── APIs jsdom does not implement ──────────────────────────────────────
    window.matchMedia =
        window.matchMedia ||
        ((query) => ({
            matches: false,
            media: query,
            addEventListener: () => {},
            removeEventListener: () => {},
            addListener: () => {},
            removeListener: () => {},
        }));

    const audioFakes = installAudioFakes(window, recorder);

    Object.defineProperty(window.navigator, "mediaDevices", {
        configurable: true,
        value: {
            getUserMedia() {
                recorder.getUserMediaCalls++;
                if (recorder.getUserMediaRejected) {
                    return Promise.reject(recorder.getUserMediaRejected);
                }
                return Promise.resolve(audioFakes.mediaStream);
            },
        },
    });

    // jsdom's HTMLMediaElement.play() throws "Not implemented" and returns
    // undefined, which would make `play().catch(...)` a TypeError rather than a
    // playback decision. The talking/neutral videos are given controllable
    // media elements so a test can assert what was asked of them.
    const media = [];
    window.HTMLMediaElement.prototype.play = function play() {
        this.playCalls = (this.playCalls || 0) + 1;
        media.push(this);
        if (this.playRejected) return Promise.reject(this.playRejection || new Error("NotAllowedError"));
        return Promise.resolve();
    };
    window.HTMLMediaElement.prototype.pause = function pause() {
        this.pauseCalls = (this.pauseCalls || 0) + 1;
    };

    /**
     * A controllable `Audio(url)`, because that is how the turn actually plays
     * an answer: app.js constructs one per TTS chunk and listens for `ended`.
     * A test drives a turn by calling `finish()` on the instance it was handed.
     */
    const audios = [];
    class FakeAudio {
        constructor(url) {
            this.url = url;
            this.listeners = new Map();
            this.paused = false;
            this.playCalls = 0;
            this.playRejected = false;
            this.currentTime = 0;
            this.playbackRate = 1;
            this.src = url;
            audios.push(this);
        }
        addEventListener(type, fn, opts) {
            const list = this.listeners.get(type) || [];
            list.push({ fn, once: Boolean(opts && opts.once) });
            this.listeners.set(type, list);
        }
        play() {
            this.playCalls++;
            if (this.playRejected) {
                const error = new Error("play() failed");
                error.name = "NotAllowedError";
                return Promise.reject(error);
            }
            this.paused = false;
            return Promise.resolve();
        }
        pause() {
            this.paused = true;
        }
        /** Drive the chunk to its natural end. */
        finish() {
            this.fire("ended");
        }
        fire(type) {
            const list = this.listeners.get(type) || [];
            this.listeners.set(
                type,
                list.filter((entry) => !entry.once),
            );
            for (const entry of list) entry.fn({ type });
        }
    }
    window.Audio = FakeAudio;

    // ── A controllable fetch ────────────────────────────────────────────────
    const calls = [];
    let handler = () => ({ ok: true, status: 200, json: async () => ({}) });
    window.fetch = (url, init) => {
        calls.push({ url: String(url), init });
        return Promise.resolve(handler(String(url), init));
    };

    // TextDecoder is not exposed on the jsdom window but exists in Node; the SSE
    // read loop needs it, and it holds no DOM state of its own.
    if (!window.TextDecoder) window.TextDecoder = TextDecoder;

    const env = {
        dom,
        window,
        document,
        media,
        audios,
        fetches: calls,
        recorder,
        virtualConsole,
        /** Route the next fetch(es). `respond` maps url -> response-ish object. */
        onFetch(fn) {
            handler = fn;
        },
        /** Reset the fetch handler to a 200 with an empty JSON body. */
        resetFetch() {
            handler = () => ({ ok: true, status: 200, json: async () => ({}) });
        },
        /** Let queued microtasks (and any awaited promise chains) run. */
        async flush(times = 6) {
            for (let i = 0; i < times; i++) await Promise.resolve();
        },
        /**
         * Evaluate real app.js function declarations inside this window.
         *
         * The bodies are verbatim slices of the shipped source (via
         * `extractFunction`), so the code under test is the shipped code. The
         * state names are declared as globals so the lifted bodies resolve them
         * and the returned `state` bridge is live.
         *
         * @param {string[]} names   top-level function names from app.js
         * @param {object}   [state] module state overrides on top of baseState()
         */
        loadApp(names, state = {}) {
            const merged = baseState(state);
            // DOM nodes go out on the window first; the declarations below
            // reference them by index.
            const nodes = [];
            const indexOfNode = (value) => {
                if (!isNode(value)) return -1;
                const at = nodes.indexOf(value);
                if (at !== -1) return at;
                return nodes.push(value) - 1;
            };
            for (const value of Object.values(merged)) indexOfNode(value);
            window.__domRefs = nodes;

            const preamble = Object.entries(merged)
                .map(([key, value]) => `var ${key} = ${toSource(value, indexOfNode(value))};`)
                .join("\n");
            const bodies = names.map((name) => extractFunction(name)).join("\n\n");
            window.eval(`${preamble}\n${bodies}\n`);

            const fn = {};
            for (const name of names) {
                assertCallable(window[name], name);
                fn[name] = window[name];
            }
            fn.state = new Proxy(
                {},
                {
                    get: (_t, key) => window[key],
                    set: (_t, key, value) => {
                        window[key] = value;
                        return true;
                    },
                    has: (_t, key) => key in window,
                },
            );
            return fn;
        },
        close() {
            dom.window.close();
        },
    };
    return env;
}

function assertCallable(value, name) {
    if (typeof value !== "function") {
        throw new Error(
            `loadApp: ${name} is not a function on the window (got ${typeof value}). ` +
                `Either the extraction failed or it is not a top-level declaration.`,
        );
    }
}

export { extractFunction, readAppJs, readIndexHtml };
