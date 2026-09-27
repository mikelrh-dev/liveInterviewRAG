/**
 * The orb honours `prefers-reduced-motion`; the stylesheet already did.
 *
 * `@media (prefers-reduced-motion: reduce)` in style.css collapses every CSS
 * animation to a single near-zero frame. avatar.js ignored it: `animate()`
 * unconditionally re-armed `requestAnimationFrame` before doing any work, so
 * the largest element on the screen — a 380px sphere breathing and rotating
 * at 60 fps — kept moving for a user who had asked the OS to stop motion.
 *
 * The contract is narrow on purpose. Reduced motion means *no unrequested
 * animation*, not *no avatar*: the neutral and talking videos, the state
 * colours and the volume response are how the avatar communicates, and all
 * three must keep working. What has to stop is the free-running loop — the
 * idle breathing and the perpetual `rotation.z` creep.
 *
 * avatar.js is a browser IIFE that reaches for `document` at module scope, so
 * it cannot be imported. Following the pattern in turn_state.test.mjs, the
 * functions under test are lifted verbatim out of the source and driven with
 * injected stand-ins for the globals they read.
 *
 *   node --test tests/frontend/
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const AVATAR_JS = join(here, "..", "..", "frontend", "avatar.js");
const source = readFileSync(AVATAR_JS, "utf8");

/** Lift a top-level function declaration out of avatar.js, verbatim. */
function extractFunction(name) {
    const start = source.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `${name}() not found in frontend/avatar.js`);

    const brace = source.indexOf("{", start);
    let depth = 0;
    for (let i = brace; i < source.length; i++) {
        if (source[i] === "{") depth++;
        else if (source[i] === "}") {
            depth--;
            if (depth === 0) return source.slice(start, i + 1);
        }
    }
    throw new Error(`unbalanced braces extracting ${name}()`);
}

/**
 * Evaluate an extracted function with stand-ins for the globals it reads. The
 * parameter names ARE the global names, so the extracted source resolves them
 * to the injected values instead of reaching for the real ones.
 */
function loadWithGlobals(name, globals) {
    const names = Object.keys(globals);
    const factory = new Function(
        ...names,
        `${extractFunction(name)}; return ${name};`,
    );
    return factory(...names.map((key) => globals[key]));
}

/** Minimal WebGL-ish doubles: enough for renderFrame() to walk its whole body. */
function sceneStubs() {
    const material = () => ({
        opacity: 0,
        color: { hex: null, setHex(value) { this.hex = value; return this; } },
    });
    return {
        renderer: { renders: 0, render() { this.renders += 1; } },
        scene: {},
        camera: {},
        outerHalo: {
            scale: { x: 1, setScalar(v) { this.x = v; } },
            material: material(),
        },
        ringGroup: {
            rotation: { z: 0 },
            children: [0, 1, 2].map(() => ({
                scale: { setScalar() {} },
                material: material(),
                userData: { baseRadius: 1, phase: 0, active: false },
            })),
        },
    };
}

/**
 * `animate` delegates to `renderFrame`, so both are lifted from source and
 * wired together exactly as the IIFE wires them — the loop decision and the
 * draw are exercised as one composed unit, against the real code.
 */
function loopHarness({ reducedMotion, idlePhase = 1.0 }) {
    const stubs = sceneStubs();
    const frames = [];

    const globals = {
        isInitialized: true,
        reducedMotion,
        currentVolume: 0,
        currentBoost: 1,
        // A parameter cannot be observed after the call, so the breathing is
        // asserted through its effect on the halo scale, not the phase itself.
        idlePhase,
        frameHandle: null,
        performance: { now: () => 1000 },
        requestAnimationFrame: (cb) => { frames.push(cb); return frames.length; },
        cancelAnimationFrame: () => frames.push("cancelled"),
        ...stubs,
    };

    const renderFrame = loadWithGlobals("renderFrame", globals);
    const animate = loadWithGlobals("animate", { ...globals, renderFrame });

    return { stubs, frames, animate, globals };
}

// ─── The loop must not run when motion is reduced ──────────────────────────

test("the orb does not schedule another frame when motion is reduced", () => {
    const { frames, animate } = loopHarness({ reducedMotion: true });

    animate();

    assert.deepEqual(
        frames,
        [],
        "a permanent requestAnimationFrame kept spinning the orb at 60 fps " +
            "for a user who asked the OS to reduce motion",
    );
});

test("the orb still draws its reduced-motion frame", () => {
    // Stopping the loop must not stop the avatar: the state colour and the
    // current volume have to be on screen, once, without animating.
    const { stubs, animate } = loopHarness({ reducedMotion: true });

    animate();

    assert.equal(stubs.renderer.renders, 1, "reduced motion drew no frame at all");
});

test("reduced motion freezes the idle breathing and the rotation creep", () => {
    const { stubs, animate } = loopHarness({ reducedMotion: true });
    const rotationBefore = stubs.ringGroup.rotation.z;

    animate();

    assert.equal(
        stubs.ringGroup.rotation.z,
        rotationBefore,
        "ringGroup.rotation.z advanced: continuous rotation under reduced motion",
    );
});

test("the loop keeps running when motion is not reduced", () => {
    const { frames, animate } = loopHarness({ reducedMotion: false });

    animate();

    assert.equal(
        frames.length,
        1,
        "the orb stopped animating for a user who has NOT asked for reduced motion",
    );
});

test("the idle breathing runs only when motion is allowed", () => {
    // sin(idlePhase * 0.7) at phase 1.0 is 0.64, so with motion allowed the
    // halo scale leaves 1.0; reduced, the breath term is pinned to 0 and the
    // scale is exactly the resting 1.0 on every frame.
    const live = loopHarness({ reducedMotion: false });
    live.animate();
    live.animate();

    const still = loopHarness({ reducedMotion: true });
    still.animate();
    still.animate();

    assert.notEqual(
        live.stubs.outerHalo.scale.x,
        1,
        "the halo never breathes — motion is off for everyone, not just those who asked",
    );
    assert.equal(
        still.stubs.outerHalo.scale.x,
        1,
        "the halo kept breathing under reduced motion",
    );
});


// ─── The preference is read live, not snapshotted at load ──────────────────

test("the preference is read from the media query, not baked in", () => {
    // A one-shot read at init would go stale the moment the user changed the
    // OS setting mid-session, which is exactly when it matters.
    assert.match(
        source,
        /const REDUCED_MOTION_QUERY = '\(prefers-reduced-motion: reduce\)'/,
        "avatar.js does not name the prefers-reduced-motion query",
    );
    assert.match(
        source,
        /window\.matchMedia\(REDUCED_MOTION_QUERY\)/,
        "avatar.js does not read the prefers-reduced-motion media query",
    );
    assert.match(
        source,
        /addEventListener\('change', onMotionPreferenceChange\)/,
        "avatar.js never subscribes to changes of the motion preference",
    );
    assert.match(
        source,
        /cancelAnimationFrame/,
        "avatar.js cannot stop a frame it has already scheduled",
    );
});

test("switching the preference on stops the loop and back starts it", () => {
    const calls = [];
    const onPreferenceChange = loadWithGlobals("onMotionPreferenceChange", {
        reducedMotion: false,
        stopLoop: () => calls.push("stop"),
        startLoop: () => calls.push("start"),
    });

    onPreferenceChange({ matches: true });
    onPreferenceChange({ matches: false });

    assert.deepEqual(
        calls,
        ["stop", "start"],
        "the loop did not follow the preference the user just changed",
    );
});

// ─── The avatar keeps its job under reduced motion ─────────────────────────

test("the orb still reports its state while motion is reduced", () => {
    const stubs = sceneStubs();
    const painted = [];
    const setState = loadWithGlobals("setState", {
        isInitialized: true,
        reducedMotion: true,
        renderFrame: () => painted.push(stubs.outerHalo.material.color.hex),
        ringGroup: stubs.ringGroup,
        outerHalo: stubs.outerHalo,
    });

    setState("speaking");

    assert.equal(
        stubs.outerHalo.material.color.hex,
        0x8b5cf6,
        "the orb no longer changes colour to say it is speaking",
    );
    assert.deepEqual(
        painted,
        [0x8b5cf6],
        "the new state colour was set but never drawn: with the loop stopped " +
            "there is no next frame to pick it up",
    );
});

