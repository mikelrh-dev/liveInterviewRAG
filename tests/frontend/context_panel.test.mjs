/**
 * The context panel: it is a panel, not a toast.
 *
 * Three defects, one root cause each.
 *
 *   1. `fetchContext()` scheduled `setTimeout(..., 5000)` and then removed the
 *      `open` class. The panel therefore closed itself five seconds after every
 *      turn, which is a toast. The panel exists to prove an answer was grounded
 *      in the candidate's own material, and the one thing a recruiter does with
 *      evidence is read it again once the next turn lands.
 *   2. `.context-panel.open` was an empty rule carrying the comment
 *      "PR #2 will implement mobile slide-in", and `body.context-open` had
 *      rules only inside the <=768px block. The header toggle therefore did
 *      nothing at all on a desktop viewport: it added a class that styled
 *      nothing.
 *   3. The timer handle was never stored and never cleared, so a pending close
 *      could slam a panel the user had opened by hand.
 *
 * The fix is one owner. `createContextPanel` holds the open/closed state and is
 * the only thing allowed to change it; the DOM bridge `applyContextPanelState`
 * is the only thing allowed to write the classes and `aria-expanded`. The
 * auto-close is deleted rather than made polite, because there is no timing
 * that does not eventually fight a user who is still reading.
 *
 * The unit under test is lifted verbatim out of frontend/app.js by
 * tests/frontend/harness.mjs, so the shipped source is what runs. The wiring
 * that must call it, and the CSS that must honour it, are asserted
 * structurally -- there is no browser and no DOM in this suite.
 *
 *   node --test tests/frontend/
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { extractFunction, loadWithGlobals, readAppJs } from "./harness.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(here, "..", "..", "frontend");
const appJs = readAppJs();
const html = readFileSync(join(FRONTEND, "index.html"), "utf8");
const css = readFileSync(join(FRONTEND, "style.css"), "utf8");

/**
 * The stylesheet split at the mobile breakpoint.
 *
 * `desktop` is everything above the breakpoint, so a rule there provably
 * applies to a 1366px viewport. `mobile` is everything from it down, which is a
 * superset (it also covers the `@supports` fallback and the reduced-motion
 * block) -- correct for "this rule exists at phone width", too coarse to prove
 * anything about desktop, which is why the desktop claims read `desktop`.
 */
const BREAKPOINT = css.indexOf("@media (max-width: 768px)");
const desktop = css.slice(0, BREAKPOINT);
const mobile = css.slice(BREAKPOINT);

/**
 * Drop `//` and block comments.
 *
 * Several comments in app.js name the constructs they replaced — "setTimeout",
 * `classList.remove("open")` — so a naive substring scan reports a failure for
 * prose explaining the defect. The Python suite strips comments for the same
 * reason (see `_strip_js_comments` in tests/test_sse_terminal_state.py).
 */
function stripComments(source) {
    return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

/** The declarations of `selector` in `block`, comments removed. */
function decls(block, selector) {
    const at = block.indexOf(selector);
    assert.notEqual(at, -1, `\`${selector}\` has no rule in scope`);
    const brace = block.indexOf("{", at + selector.length - 1);
    const end = block.indexOf("}", brace);
    return block
        .slice(brace + 1, end)
        .replace(/\/\*[\s\S]*?\*\//g, "");
}

function loadPanel(initialOpen, seen) {
    const build = loadWithGlobals("createContextPanel", {});
    return build({ onChange: (open) => seen.push(open) }, initialOpen);
}

// ─── 1. One owner of the open state ─────────────────────────────────────────

test("the panel reports the state the layout put it in", () => {
    // The rail is part of the desktop grid, so it is open there; the phone
    // overlay covers the screen, so it is closed. Hardcoding either one is the
    // bug -- the state has to come from where the panel actually is.
    const desktopSeen = [];
    const mobileSeen = [];

    assert.equal(loadPanel(true, desktopSeen).isOpen(), true);
    assert.equal(loadPanel(false, mobileSeen).isOpen(), false);
});

test("construction publishes the initial state, so the DOM is written once", () => {
    const seen = [];
    loadPanel(false, seen);

    assert.deepEqual(
        seen,
        [false],
        "nothing was published at construction, so the classes and " +
            "aria-expanded would only appear after the first user click",
    );
});

test("opening an open panel changes nothing", () => {
    const seen = [];
    const panel = loadPanel(true, seen);

    assert.equal(panel.open(), false, "open() claimed a change it did not make");
    assert.deepEqual(seen, [true], "a redundant open re-wrote the DOM");
    assert.equal(panel.isOpen(), true, "a redundant open closed the panel");
});

test("closing a closed panel changes nothing", () => {
    const seen = [];
    const panel = loadPanel(false, seen);

    assert.equal(panel.close(), false);
    assert.deepEqual(seen, [false]);
    assert.equal(panel.isOpen(), false);
});

test("the toggle flips the state and reports both edges", () => {
    const seen = [];
    const panel = loadPanel(true, seen);

    assert.equal(panel.toggle(), true);
    assert.equal(panel.isOpen(), false);
    assert.equal(panel.toggle(), true);
    assert.equal(panel.isOpen(), true);

    assert.deepEqual(seen, [true, false, true]);
});

test("the state is held by the controller, not read back from the DOM", () => {
    // If the bridge is the only writer, the controller's own state is the truth
    // and a class left behind by anything else cannot drift out of sync.
    const seen = [];
    const panel = loadPanel(false, seen);

    panel.open();
    panel.close();
    panel.close();
    panel.open();

    assert.deepEqual(seen, [false, true, false, true]);
    assert.equal(panel.isOpen(), true);
});

// ─── 2. The DOM bridge writes classes and aria-expanded together ────────────

/** A stand-in for a classList that records the calls that reach it. */
function fakeClassList() {
    const set = new Set();
    return {
        set,
        toggle(name, force) {
            const on = force === undefined ? !set.has(name) : Boolean(force);
            if (on) set.add(name);
            else set.delete(name);
            return on;
        },
        add: (name) => set.add(name),
        remove: (name) => set.delete(name),
        contains: (name) => set.has(name),
    };
}

function loadBridge() {
    const panelClasses = fakeClassList();
    const bodyClasses = fakeClassList();
    const attrs = {};
    const apply = loadWithGlobals("applyContextPanelState", {
        contextPanel: { classList: panelClasses },
        contextToggle: { setAttribute: (k, v) => (attrs[k] = v) },
        document: { body: { classList: bodyClasses } },
    });
    return { apply, panelClasses, bodyClasses, attrs };
}

test("opening writes the class, the body class and aria-expanded in one move", () => {
    // aria-expanded is the only thing that tells a screen-reader user whether
    // the panel is showing. Written anywhere other than beside the class, it
    // is one edit away from describing a state that does not exist.
    const { apply, panelClasses, bodyClasses, attrs } = loadBridge();

    apply(true);

    assert.equal(panelClasses.contains("open"), true, ".open was not set");
    assert.equal(bodyClasses.contains("context-open"), true, "the body class was not set");
    assert.equal(attrs["aria-expanded"], "true", "aria-expanded did not track the class");
});

test("closing writes every one of them back", () => {
    const { apply, panelClasses, bodyClasses, attrs } = loadBridge();

    apply(true);
    apply(false);

    assert.equal(panelClasses.contains("open"), false);
    assert.equal(bodyClasses.contains("context-open"), false);
    assert.equal(attrs["aria-expanded"], "false");
});

// ─── 3. The auto-close is gone, not merely made polite ─────────────────────

test("fetchContext schedules no timer", () => {
    // The panel used to close itself five seconds after every turn. Any timer
    // here is a timer that will eventually slam a panel somebody is reading.
    const body = stripComments(extractFunction("fetchContext", appJs));

    assert.doesNotMatch(
        body,
        /setTimeout|setInterval/,
        "fetchContext still schedules a timed close; a panel that closes on a " +
            "timer is a toast, and a pending one can slam a panel the user " +
            "opened by hand",
    );
});

test("the open class has exactly one writer, and it is the bridge", () => {
    // One writer. Four scattered `classList.remove("open")` call sites is how the
    // toggle, the close button, the outside click and the timer each came to
    // own a piece of the state. The bridge is allowed to write it; nothing else
    // is, because a second writer is a second state machine.
    const bridge = extractFunction("applyContextPanelState", appJs);
    const lines = stripComments(appJs).split("\n");

    const writers = [];
    lines.forEach((line, i) => {
        if (!/classList\.(?:add|remove|toggle)\(\s*"open"/.test(line)) return;
        const inside = bridge.includes(line.trim());
        writers.push(inside ? null : `${i + 1}: ${line.trim()}`);
    });

    assert.ok(
        writers.some((w) => w === null),
        "applyContextPanelState() no longer writes the `open` class, so the " +
            "panel has no state at all",
    );
    assert.deepEqual(
        writers.filter(Boolean),
        [],
        "app.js writes the `open` class outside applyContextPanelState(); every " +
            "one of those sites is another owner of the panel's state",
    );
});

// ─── 4. Wiring: one escape hatch, at every breakpoint ──────────────────────

test("the initial state is read from the layout, not hardcoded", () => {
    // A phone gets a full-height overlay; a desktop gets a grid column. Picking
    // one of those in the markup and hoping is what made the toggle a no-op.
    assert.match(
        appJs,
        /function isOverlayLayout\(\)/,
        "app.js has no isOverlayLayout(); the panel cannot tell a phone from a " +
            "desktop, so it can neither start in the right state nor decide " +
            "when an outside click should dismiss it",
    );
    assert.match(
        extractFunction("isOverlayLayout", appJs),
        /matchMedia\([^)]*max-width:\s*768px/,
        "isOverlayLayout() does not test the same breakpoint the stylesheet " +
            "switches layout at",
    );
});

test("Escape closes the panel", () => {
    // On a phone the panel is a full-height overlay over the whole screen. A
    // dismissal that only exists as a small glyph in the corner is not a
    // dismissal a keyboard user has.
    assert.match(
        appJs,
        /keydown/,
        "no keydown listener exists, so Escape cannot dismiss the overlay",
    );
    const listener = /keydown[\s\S]{0,600}?Escape/;
    assert.match(
        appJs,
        listener,
        "the keydown handler does not handle Escape",
    );
});

test("an outside click dismisses the overlay and only the overlay", () => {
    // On a phone the panel covers the screen and a scrim sits behind it, so
    // clicking away is a dismissal. On a desktop the rail is a column of the
    // page: clicking in the transcript must not delete it. That asymmetry is
    // what makes the outside click correct rather than surprising.
    const source = appJs.slice(
        appJs.indexOf("document.addEventListener(\"click\""),
        appJs.indexOf("document.addEventListener(\"click\"") + 900,
    );
    assert.match(source, /isOverlayLayout\(\)/, "the outside click ignores the layout");
    assert.match(
        source,
        /closeContextPanel\(\)/,
        "the outside click does not close the panel",
    );
});

test("closing hands focus back to the toggle when focus was inside", () => {
    // Closing an overlay while the keyboard is inside it strands the user at
    // the top of the document. Handing focus back is the difference between a
    // dismissible panel and a trap.
    const source = extractFunction("closeContextPanel", appJs);

    assert.match(
        source,
        /contextPanel\.contains\(\s*document\.activeElement\s*\)/,
        "closeContextPanel() does not look at where focus is",
    );
    assert.match(
        source,
        /contextToggle\.focus\(\)/,
        "focus is not returned to the toggle, so a keyboard user who closed " +
            "the panel is dropped at the top of the page",
    );
});

test("opening the panel does not steal focus", () => {
    // The ARIA disclosure pattern leaves focus on the toggle: the panel is the
    // next thing in the tab order. Grabbing focus on open makes a two-click
    // round trip out of a control the user already activated.
    const source = extractFunction("toggleContextPanel", appJs);
    assert.doesNotMatch(
        source,
        /\.focus\(\)/,
        "the toggle moves focus on open; the disclosure pattern leaves focus on " +
            "the button and lets the panel follow in the tab order",
    );
});

// ─── 5. The toggle works at every breakpoint ───────────────────────────────

test("the desktop rail is a grid column, not an overlay", () => {
    // The brief's explicit non-goal: the 320px rail is 23% of a 1366px viewport
    // and it is the clearest expression of the instrumented-system identity, so
    // it must not become a drawer floating over the content.
    const rule = decls(desktop, "#context-panel");

    assert.doesNotMatch(
        rule,
        /position:\s*fixed/,
        "#context-panel is position:fixed above the mobile breakpoint, so the " +
            "desktop rail became an overlay",
    );
    assert.doesNotMatch(
        rule,
        /transform/,
        "#context-panel carries a transform above the mobile breakpoint, so the " +
            "desktop rail became a drawer",
    );
});

test("closing the rail gives its width back to the conversation", () => {
    // Hiding a grid item is not enough on its own: the three-column template
    // would leave an empty third track. Both halves have to move together, or
    // the toggle appears to do nothing at all -- which is the defect.
    assert.match(
        desktop,
        /body:not\(\.context-open\)\s+\.main-grid\s*\{[^}]*grid-template-columns:[^}]*\}/,
        "nothing gives the context column back when the panel is closed",
    );

    const track = decls(desktop, "body:not(.context-open) .main-grid");
    assert.doesNotMatch(
        track,
        /var\(--context-w\)/,
        "the closed state still reserves the context track, so closing the " +
            "panel leaves a 320px hole where the rail was",
    );

    assert.match(
        desktop,
        /body:not\(\.context-open\)\s+#context-panel\s*\{[^}]*display:\s*none/,
        "the panel is still rendered when closed, so the toggle only changed a " +
            "class and nothing on screen",
    );
});

test("the placeholder rule is gone", () => {
    // `.context-panel.open { /* PR #2 will implement */ }` was the second half
    // of the desktop no-op: an empty rule above the breakpoint.
    assert.doesNotMatch(
        css,
        /\.context-panel\.open\s*\{\s*\/\*[^*]*\*\/\s*\}/,
        "`.context-panel.open` is still an empty placeholder rule",
    );
});

test("the phone overlay still slides in", () => {
    // Regression guard: fixing the desktop side must not un-fix the phone side.
    const rule = decls(mobile, ".context-panel.open");
    assert.match(
        rule,
        /transform:\s*translateX\(-100%\)/,
        "the phone overlay no longer slides in when the panel opens",
    );
});

// ─── 6. The markup agrees with the state the app will compute ──────────────

test("the toggle declares what it controls and whether it is open", () => {
    const toggle = html.slice(html.indexOf('id="context-toggle"') - 200, html.indexOf('id="context-toggle"') + 300);

    assert.match(
        toggle,
        /aria-expanded="(true|false)"/,
        "#context-toggle has no aria-expanded, so its state is invisible to a " +
            "screen-reader user",
    );
    assert.match(
        toggle,
        /aria-controls="context-panel"/,
        "#context-toggle has no aria-controls, so nothing in the markup says " +
            "which region it governs",
    );
});

test("the shipped default shows the rail, with JS or without it", () => {
    // If JS never runs the desktop rail must still be there: it is the
    // credibility feature, and hiding it behind a script is a worse failure
    // than the one being fixed.
    assert.match(
        html.slice(html.indexOf("<body"), html.indexOf("<body") + 80),
        /^<body class="[^"]*context-open/,
        "the body does not ship the open state, so the rail is hidden until " +
            "app.js runs and disappears entirely if it never does",
    );
});

test("the dismissal is a labelled control, not a bare glyph", () => {
    // On a phone the panel is the whole screen. A 14px cross in the corner is
    // not something a first-time visitor identifies as "close".
    const close = html.slice(
        html.indexOf('id="context-close"'),
        html.indexOf('id="context-close"') + 500,
    );

    assert.match(close, /<\/svg>\s*<span[^>]*>\s*\S/, "the close control has no visible text");
    assert.match(
        decls(mobile, "#context-close"),
        /min-width:\s*44px/,
        "the close control is below the 44px touch target on a phone",
    );
});
