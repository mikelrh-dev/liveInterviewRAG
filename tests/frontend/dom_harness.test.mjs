/**
 * The DOM harness has to be trustworthy before anything it proves counts.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The frontend suite reported 186 green while 6 of 6 mutations to the DOM-wiring
 * layer passed undetected. The cause was not missing tests: it was that the
 * suites could not see a document, so they had asserted on source text instead,
 * and one of them asserted on a stub it had written itself.
 *
 * A harness fixes that, and a harness has its own version of the same disease.
 * Two were found while building it, and both are pinned here:
 *
 *   1. A lifted function that mentions a top-level declaration which was not
 *      loaded throws a ReferenceError -- and if it throws inside app.js's own
 *      try block, the app's `catch` reports it as a microphone denial. The
 *      suite then reads a missing binding as a product failure. That happened:
 *      `startVad` hands `vadLoop` to requestAnimationFrame rather than calling
 *      it, so a call-shaped dependency scan missed it.
 *
 *   2. The resolver over-matched and published app.js's NESTED declarations
 *      (`stop`, `check`, `paint`, `move`, `describe`) as globals. A lifted body
 *      calling `stop()` would then resolve to somebody else's `stop`.
 *
 * Both are silent. Neither throws in a way a reader would connect to the
 * harness, which is exactly why they need tests of their own.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { extractFunction, readAppJs, readIndexHtml } from "./harness.mjs";
import { baseState, createDom, DOM_HANDLES, producedBy } from "./dom.mjs";

const appJs = readAppJs();

/** Every top-level function declaration in app.js. */
function topLevelNames(source) {
    const names = new Set();
    const pattern = /(?:^|\n)(?:async[ \t]+)?function[ \t]+([A-Za-z_$][\w$]*)[ \t]*\(/g;
    let match;
    while ((match = pattern.exec(source)) !== null) names.add(match[1]);
    return names;
}

test("the harness loads the real escapeHtml, not a lookalike", () => {
    // The whole point of the harness. The old suite injected its own
    // escapeHtml and asserted that renderContext escaped, so replacing the
    // shipped one with `return String(text)` left 186/186 green.
    const env = createDom();
    const { escapeHtml } = env.loadApp(["escapeHtml"]);

    assert.match(
        escapeHtml.toString(),
        /textContent/,
        "the loaded escapeHtml does not look like the shipped one, so this " +
            "suite is not testing the shipped code",
    );
    assert.equal(
        env.document.querySelectorAll("script, style, link").length > 0,
        true,
        "the window has no document parsed from index.html, so the DOM is not real",
    );
    env.close();
});

test("every function a lifted body mentions is on the window", () => {
    // Guards disease (1). Any top-level declaration named inside a loaded body
    // must resolve, because app.js would have had it in scope.
    const env = createDom();
    const declared = topLevelNames(appJs);
    const names = [
        "startRecording",
        "startVad",
        "startInterview",
        "stopInterview",
        "tryPlayNextChunk",
        "processRecordingStream",
        "setState",
        "fetchContext",
        "initDisclaimer",
    ];

    for (const entry of names) {
        const fn = env.loadApp([entry]);
        for (const resolved of fn.resolved) {
            assert.equal(
                typeof env.window[resolved],
                "function",
                `${entry}() pulled in ${resolved}, which is not on the window`,
            );
        }
        // And the specific case that actually bit: a function passed as a value
        // rather than called.
        assert.equal(
            typeof env.window.vadLoop,
            "function",
            "startVad hands vadLoop to requestAnimationFrame instead of calling " +
                "it, so it must be resolved as a reference and not as a call",
        );
    }
    env.close();
});

test("the resolver publishes only top-level declarations", () => {
    // Guards disease (2). app.js has nested `stop`, `check`, `paint`, `move`
    // and `describe` declarations; publishing those as globals lets a lifted
    // body resolve a same-named call to the wrong function.
    const nested = ["stop", "check", "paint", "describe"];
    const env = createDom();
    const fn = env.loadApp(["createTurnNarrator", "createRetryPolicy", "startVad"]);

    const declared = topLevelNames(appJs);
    for (const name of fn.resolved) {
        assert.ok(
            declared.has(name),
            `${name} is not a top-level declaration of app.js, so the resolver ` +
                "published a nested function as a global",
        );
    }
    for (const name of nested) {
        if (declared.has(name)) continue;
        assert.equal(
            fn.resolved.includes(name),
            false,
            `the nested ${name}() was published as a global`,
        );
    }
    env.close();
});

test("every module-level state the tests rely on is declared by the harness", () => {
    // app.js's own `let` state, so a lifted body never reads a binding the
    // harness forgot. Derived from the source so it cannot drift from it.
    const declared = new Set();
    const pattern = /^let[ \t]+([A-Za-z_$][\w$]*)[ \t]*=/gm;
    let match;
    while ((match = pattern.exec(appJs)) !== null) declared.add(match[1]);

    const state = baseState();

    const missing = [...declared].filter((name) => !(name in state));
    assert.deepEqual(
        missing,
        [],
        `app.js declares these module-level bindings that the harness does not: ` +
            `${missing.join(", ")}. A lifted body reading one would resolve it to ` +
            "undefined, or throw, and the error would be blamed on the app",
    );
});

test("the harness supplies the real element for every DOM handle", () => {
    const env = createDom();
    const html = readIndexHtml();

    for (const [name, selector] of Object.entries(DOM_HANDLES)) {
        assert.ok(
            env.document.querySelector(selector),
            `${name} -> "${selector}" matches nothing in index.html, so a test ` +
                "using it would get undefined and blame the app",
        );
        // Only meaningful for a bare id selector; a descendant selector like
        // `#btn-mic .mic-icon` has no literal form in the markup.
        if (/^#[A-Za-z0-9_-]+$/.test(selector)) {
            assert.ok(
                html.includes(`id="${selector.slice(1)}"`),
                `${name} -> "${selector}" is not an id in the shipped markup`,
            );
        }
    }
    env.close();
});

test("a produced value is built by the window, not copied", () => {
    // Class instances cannot be serialised -- their methods live on the
    // prototype -- so a fake AudioContext handed in as a plain object would lose
    // `resume()` and the test would fail on `resume is not a function`.
    const env = createDom();
    const fn = env.loadApp(["ensureAudioContext"], {
        audioContext: producedBy(() => new env.recorder.fakes.FakeAudioContext()),
    });

    assert.equal(
        typeof env.window.audioContext.resume,
        "function",
        "the AudioContext lost its methods, so the fake was serialised instead of " +
            "built inside the window",
    );
    assert.equal(typeof fn.ensureAudioContext, "function");
    env.close();
});

test("the real index.html is what gets parsed", () => {
    // A test that passes its own markup can be green against a page nobody
    // ships. This one deliberately cannot: the elements it asserts on are the
    // ones index.html declares.
    const env = createDom();
    for (const id of [
        "btn-mic",
        "status",
        "conversation",
        "context-content",
        "context-panel",
        "context-toggle",
        "audio-blocked-overlay",
        "disclaimer-overlay",
        "disclaimer-accept",
        "orbital-ring",
        "waveform",
        "avatar-talking-video",
    ]) {
        assert.ok(
            env.document.getElementById(id),
            `#${id} is missing, so the harness is not parsing the shipped index.html`,
        );
    }
    env.close();
});

test("extractFunction still refuses to invent a function that is not there", () => {
    // The failure mode the harness must NOT have: silently returning something
    // for a name that does not exist, which would let a test pass against a
    // stub while believing it tested the app.
    assert.throws(
        () => extractFunction("definitelyNotAFunctionInAppJs", appJs),
        /not found/,
        "extractFunction accepted a name that does not exist in app.js",
    );
});
