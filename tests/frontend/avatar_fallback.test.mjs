/**
 * The orb's degradation path has to degrade.
 *
 * THE DEFECT
 * ----------
 * `avatar.js` reads its canvas once, at module scope:
 *
 *     const canvas = document.getElementById('orb-canvas');
 *
 * and never checks it. `init()` is wrapped in a try/catch whose catch calls
 * `showFallback()`, and `showFallback()` does `canvas.classList.add('hidden')`.
 * So on a page without `#orb-canvas` -- which is what a template edit, a
 * partial render, or a browser that dropped the element produces -- the catch
 * handler throws a TypeError on null, and that throw escapes `init()`.
 *
 * The whole file exists to fail soft: its own header says "Graceful degradation
 * if Three.js fails -- videos still play", and `init()` returns a boolean for
 * exactly that purpose. Instead, the failure path is the one path that throws,
 * and it throws at the caller, which is `initAvatarOrb()` in app.js -- so the
 * degradation code is what breaks the page it was written to protect.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createDom } from "./dom.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const AVATAR_JS = readFileSync(join(here, "..", "..", "frontend", "avatar.js"), "utf8");

/** Load avatar.js into a window, and report whether init() returned cleanly. */
function loadAvatar({ withCanvas = true, three = false } = {}) {
    const env = createDom({
        html: withCanvas
            ? undefined
            : readFileSync(join(here, "..", "..", "frontend", "index.html"), "utf8").replace(
                  /<canvas id="orb-canvas"><\/canvas>/,
                  "",
              ),
    });
    // A THREE that constructs and a WebGL context are not available here, so
    // init() is expected to fail -- the question is only HOW it fails.
    if (three) {
        env.window.THREE = {
            Scene: function () {},
            PerspectiveCamera: function () {},
            Group: function () { this.children = []; this.add = function (c) { this.children.push(c); }; },
            Mesh: function () {},
            MeshBasicMaterial: function () {},
            SphereGeometry: function () {},
            TorusGeometry: function () {},
            AdditiveBlending: 2,
        };
        env.window.HTMLCanvasElement.prototype.getContext = () => ({});
    }
    env.window.eval(AVATAR_JS);
    return env;
}

test("the module still publishes its public API", () => {
    // Precondition for everything here: if avatar.js failed to load, the tests
    // below would be asserting about nothing.
    const env = loadAvatar();
    assert.equal(typeof env.window.AvatarOrb, "object", "window.AvatarOrb was not published");
    for (const name of ["init", "setVolume", "setBlend", "setState", "resize", "boost"]) {
        assert.equal(
            typeof env.window.AvatarOrb[name],
            "function",
            `AvatarOrb.${name} is missing`,
        );
    }
    env.close();
});

test("a page without the canvas fails soft", () => {
    // The defect. init() is documented to return false when Three.js is not
    // available; with no canvas it threw instead, out of the function whose job
    // is to never throw.
    const env = loadAvatar({ withCanvas: false });

    let result;
    assert.doesNotThrow(
        () => {
            result = env.window.AvatarOrb.init();
        },
        "init() threw on a page with no #orb-canvas. The degradation path " +
            "dereferences the canvas it is degrading FROM, so the throw escapes " +
            "into app.js and takes the page with it",
    );
    assert.equal(
        result,
        false,
        `init() returned ${JSON.stringify(result)} on a page with no canvas; ` +
            "false is the documented answer and is what initAvatarOrb checks",
    );
    env.close();
});

test("the orb reports itself as uninitialised after a soft failure", () => {
    // The other half. `isInitialized()` is what app.js consults on every state
    // change; if a failed init left it true, setState would call into a scene
    // that was never built.
    const env = loadAvatar({ withCanvas: false });

    env.window.AvatarOrb.init();

    assert.equal(
        env.window.AvatarOrb.isInitialized(),
        false,
        "a failed init left isInitialized() true, so app.js will keep calling " +
            "into an orb that was never built",
    );
    env.close();
});

test("the methods are safe to call after a failed init", () => {
    // app.js guards most of its orb calls with isInitialized(), but not the
    // calls it makes from a resize handler or a frame that was already in
    // flight. None of them may throw.
    const env = loadAvatar({ withCanvas: false });
    env.window.AvatarOrb.init();

    for (const [name, args] of [
        ["setVolume", [0.5]],
        ["setBlend", [0.5]],
        ["setState", ["speaking"]],
        ["boost", [1.5]],
        ["resize", [400, 400]],
    ]) {
        assert.doesNotThrow(
            () => env.window.AvatarOrb[name](...args),
            `AvatarOrb.${name}() threw after a failed init`,
        );
    }
    env.close();
});

test("a missing canvas is reported, not swallowed", () => {
    // The diagnosis has to name the cause. init()'s catch logs `e.message`, so
    // an absent canvas has to raise with a message that says so -- otherwise
    // every such failure is logged as "Three.js not loaded" or "WebGL not
    // supported", which sends the reader to the wrong subsystem entirely.
    //
    // Three.js and WebGL are stubbed as working, so the canvas is the only
    // thing left that can fail. That is the situation being described: the
    // library loaded fine and the page is missing an element.
    const env = loadAvatar({ withCanvas: false, three: true });
    const warnings = [];
    env.window.console.warn = (...args) => warnings.push(args.map(String).join(" "));

    env.window.AvatarOrb.init();

    assert.ok(
        warnings.some((line) => /canvas/i.test(line)),
        `the failure was logged as ${JSON.stringify(warnings)}: a missing element ` +
            "and an unsupported GPU are different defects and must not read the " +
            "same in the log",
    );
    env.close();
});
