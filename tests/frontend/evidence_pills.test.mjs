/**
 * The evidence chips: what the recruiter is actually there to read.
 *
 * The chunk pills were `<div class="chunk-pill" onclick="toggleChunk(this)">`.
 * That gives a mouse user a target and gives everyone else nothing: a div is
 * not focusable, is not reachable with Tab, cannot be activated with Enter or
 * Space, draws no focus ring, and exposes no role or state to assistive
 * technology. The pills are the credibility argument of the whole panel -- they
 * are the retrieved passages the answer was built from -- and they were
 * unusable without a mouse.
 *
 * The fix is a native `<button>`. Keyboard activation, the focus ring, the
 * button role and the expanded/collapsed state are not re-implemented here;
 * the browser owns all of it. `role="button"` on a div was the alternative and
 * it is the worse one: it re-declares semantics the platform already provides
 * and then still has to add the key handling, the focusability and the state
 * that the native element gets for free.
 *
 * The second half is the ellipsis. `.chunk-preview` carries
 * `text-overflow: ellipsis` -- a property defined for a *block container* --
 * on a `<span>`, which is inline. The declaration was inert: the 100-character
 * preview wrapped onto a second line instead of truncating. And promoting it to
 * a flex item is not sufficient on its own, because a flex item's default
 * `min-width: auto` refuses to shrink below its min-content width, so it would
 * overflow rather than truncate until `min-width: 0` says it may.
 *
 * The unit under test is lifted verbatim out of frontend/app.js by
 * tests/frontend/harness.mjs, so the shipped source is what runs. The CSS that
 * decides whether the ellipsis applies is asserted by reading the declarations
 * that decide it -- there is no browser in this suite.
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
const css = readFileSync(join(FRONTEND, "style.css"), "utf8");

const CHUNKS = [
    { score: 0.8123, text: "El proyecto InterviewTTS usa FastAPI y SSE", source: "cv.md" },
    { score: 0.4, text: "Second", source: "skills/frontend.md" },
];

/**
 * A stand-in for the panel body. `renderContext` writes to `innerHTML` and
 * reads nothing else, so this is the whole of the DOM it touches.
 */
function render(chunks) {
    const host = { innerHTML: "" };
    loadWithGlobals("renderContext", {
        contextContent: host,
        escapeHtml: (text) =>
            String(text)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;"),
    })(chunks);
    return host.innerHTML;
}

/** A stand-in for one rendered chip: records classes and attributes. */
function fakePill() {
    const classes = new Set();
    const attrs = {};
    return {
        attrs,
        classList: {
            toggle(name, force) {
                const on = force === undefined ? !classes.has(name) : Boolean(force);
                if (on) classes.add(name);
                else classes.delete(name);
                return on;
            },
            contains: (name) => classes.has(name),
        },
        setAttribute: (name, value) => (attrs[name] = value),
        getAttribute: (name) => attrs[name],
    };
}

const toggleChunk = loadWithGlobals("toggleChunk", {});

// ─── 1. The chip is a real control ─────────────────────────────────────────

test("each evidence chip is a native button", () => {
    const html = render(CHUNKS);

    assert.equal(
        (html.match(/<button/g) || []).length,
        CHUNKS.length,
        "every chunk must render exactly one button",
    );
    assert.match(
        html,
        /<button[^>]*type="button"/,
        "the chip is a <button> without type, so it defaults to submit and " +
            "inherits form semantics it has no business having",
    );
});

test("the chip is not a div with a role bolted on", () => {
    // The re-implementation that looks like the fix and is not: role="button"
    // only changes what is announced. It does not make the div focusable, does
    // not handle Enter or Space, and does not draw a focus ring.
    const html = render(CHUNKS);

    assert.doesNotMatch(html, /<div/, "the chip is still a div");
    assert.doesNotMatch(
        html,
        /role="button"/,
        'the chip carries role="button", which announces a control it does not ' +
            "behave like",
    );
});

test("the chip has no inline onclick", () => {
    // An inline handler can only reach a function published on `window`, which
    // is why `window.toggleChunk = toggleChunk` existed. It also cannot be
    // activated from the keyboard even when the element is a button.
    const html = render(CHUNKS);

    assert.doesNotMatch(
        html,
        /onclick=/,
        "the chip still carries an inline onclick, so it needs a global to work",
    );
    assert.doesNotMatch(
        appJs,
        /window\.toggleChunk/,
        "app.js still publishes toggleChunk on window purely for the inline " +
            "handler; with a native button and a delegated listener it is dead",
    );
});

test("the chip declares the state it is in and the region it controls", () => {
    const html = render(CHUNKS);

    assert.match(
        html,
        /aria-expanded="false"/,
        "a collapsed chip does not say it is collapsed, so a screen-reader " +
            "user hears a control with no state",
    );

    const controls = [...html.matchAll(/aria-controls="([^"]+)"/g)].map((m) => m[1]);
    const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]);

    assert.ok(controls.length > 0, "no chip names the region it expands");
    for (const id of controls) {
        assert.ok(
            ids.includes(id),
            `aria-controls="${id}" points at an element that does not exist in ` +
                `the rendered markup; the ids present are ${JSON.stringify(ids)}`,
        );
    }
});

test("the chip contains only phrasing content", () => {
    // A <button> may not contain flow content. The full text this wrapped in a
    // <div><p> is flow content, so it has to be a <span> that the stylesheet
    // lays out as a block -- otherwise the "fix" ships invalid HTML.
    const button = /<button[\s\S]*?<\/button>/.exec(render(CHUNKS))[0];

    assert.doesNotMatch(
        button,
        /<(div|p|section|article|ul|ol|li|table|h[1-6])\b/,
        "the button contains flow content, which its content model forbids",
    );
});

test("both the preview and the full text are escaped", () => {
    // renderContext writes retrieved wiki text into innerHTML. The escaping is
    // the only thing between a chunk of a markdown page and script execution,
    // and rewriting the template is exactly when it goes missing.
    const html = render([
        { score: 0.5, text: '<img src=x onerror="boom">', source: "cv.md" },
    ]);

    assert.doesNotMatch(html, /<img/, "the chunk text was interpolated unescaped");
    // escapeHtml() is `textContent` -> `innerHTML`, which escapes `&`, `<` and
    // `>` and deliberately leaves quotes alone: the value lands in element
    // CONTENT, where a quote is a character, not an attribute delimiter.
    assert.match(
        html,
        /&lt;img src=x onerror="boom"&gt;/,
        "the chunk text was not escaped for a content position",
    );
    assert.match(
        html,
        /Fuente: cv\.md/,
        "the source is no longer rendered, so the chip has lost half its claim",
    );
});

test("the empty case still says so", () => {
    // Regression guard: the rewrite must not lose the "nothing retrieved" state,
    // which is a claim the panel has to be able to make honestly.
    assert.match(render([]), /context-empty/);
    assert.match(render(null), /context-empty/);
});

// ─── 2. The state a chip is in is reported, not just drawn ────────────────

test("activating a collapsed chip expands it and says so", () => {
    const pill = fakePill();
    pill.setAttribute("aria-expanded", "false");

    toggleChunk(pill);

    assert.equal(pill.classList.contains("expanded"), true, "the chip did not expand");
    assert.equal(pill.getAttribute("aria-expanded"), "true", "aria-expanded did not follow");
});

test("activating an expanded chip collapses it and says so", () => {
    const pill = fakePill();
    pill.classList.toggle("expanded", true);
    pill.setAttribute("aria-expanded", "true");

    toggleChunk(pill);

    assert.equal(pill.classList.contains("expanded"), false, "the chip did not collapse");
    assert.equal(
        pill.getAttribute("aria-expanded"),
        "false",
        "aria-expanded did not follow the collapse",
    );
});

test("the class and the attribute never disagree", () => {
    // The class drives the visual and the attribute drives the announcement.
    // A toggle that only flipped one of them is a control that lies to half its
    // users, so the two are written from the same decision on every call.
    const pill = fakePill();
    let expanded = null;

    for (let i = 0; i < 4; i++) {
        toggleChunk(pill);
        expanded = pill.classList.contains("expanded");
        assert.equal(
            pill.getAttribute("aria-expanded"),
            String(expanded),
            `call ${i + 1}: the class says ${expanded} and aria-expanded says ` +
                `${pill.getAttribute("aria-expanded")}`,
        );
    }
});

test("a re-render does not leave the handler behind on a discarded node", () => {
    // renderContext replaces #context-content's innerHTML on every turn, so a
    // listener bound per pill dies with the node it was bound to. The listener
    // belongs on the container, which survives.
    assert.match(
        appJs,
        /contextContent\.addEventListener\(\s*"click"/,
        "no delegated listener on #context-content, so a chip stops responding " +
            "after the next turn re-renders the panel",
    );
});

// ─── 3. The ellipsis actually applies ──────────────────────────────────────

/**
 * The stylesheet with comments removed.
 *
 * Not cosmetic: style.css's own header comment names `.chunk-pill` while
 * explaining the contrast floors, so a plain `indexOf` finds that sentence and
 * then pairs it with whatever brace comes next. A selector mentioned in prose
 * has to be indistinguishable from a rule only if the prose is gone.
 */
const rules = css.replace(/\/\*[\s\S]*?\*\//g, "");

/** The declarations of `selector`. */
function decls(selector) {
    const at = rules.indexOf(selector);
    assert.notEqual(at, -1, `\`${selector}\` has no rule in style.css`);
    const brace = rules.indexOf("{", at + selector.length - 1);
    return rules.slice(brace + 1, rules.indexOf("}", brace));
}

test("the chip is a flex container, so its children are flex items", () => {
    // A flex item is blockified, which is what makes `text-overflow` apply to
    // it at all. An inline <span> next to an inline <span> never truncates.
    assert.match(
        decls(".chunk-pill"),
        /display:\s*flex/,
        ".chunk-pill is not a flex container, so .chunk-preview stays inline " +
            "and its text-overflow stays inert",
    );
});

test("the preview is allowed to shrink", () => {
    // The half of the ellipsis fix that is easy to miss: promoting the element
    // to a flex item is not enough, because a flex item's `min-width: auto`
    // resolves to its min-content width. Without `min-width: 0` the preview
    // refuses to get narrower than its longest word and overflows the pill
    // instead of truncating.
    assert.match(
        decls(".chunk-preview"),
        /min-width:\s*0/,
        ".chunk-preview has flex min-width:auto, so it will not shrink below " +
            "its min-content width and will overflow rather than truncate",
    );
});

test("the preview truncates on one line", () => {
    const rule = decls(".chunk-preview");

    for (const [property, why] of [
        ["white-space", "nowrap"],
        ["overflow", "hidden"],
        ["text-overflow", "ellipsis"],
    ]) {
        assert.match(
            rule,
            new RegExp(`${property}:\\s*${why}`),
            `.chunk-preview is missing \`${property}: ${why}\` (${why} is what ` +
                "the next one depends on)",
        );
    }
});

test("the preview is not laid out as an inline box", () => {
    // The declaration under test: `text-overflow: ellipsis` on an inline
    // element does nothing at all, which is why the 100-character preview used
    // to wrap onto a second line.
    assert.doesNotMatch(
        decls(".chunk-preview"),
        /display:\s*inline\b/,
        ".chunk-preview is display:inline, and text-overflow is defined for a " +
            "block container -- the ellipsis does nothing",
    );
});

test("the score does not squeeze when the preview shrinks", () => {
    assert.match(
        decls(".chunk-score"),
        /flex:\s*(none|0 0 auto)/,
        ".chunk-score can shrink, so a long preview squeezes the relevance " +
            "score out of the chip",
    );
});

test("the expanded text takes a row of its own", () => {
    assert.match(
        decls(".chunk-full"),
        /flex:\s*1 0 100%/,
        ".chunk-full does not claim a full row, so the revealed passage runs on " +
            "beside the score instead of under the chip",
    );
    assert.match(
        decls(".chunk-full > span"),
        /display:\s*block/,
        "the revealed passage is a <span> and stays inline, so the source line " +
            "and the text share a line",
    );
});

// ─── 4. A <button> still has to look like the chip, not like a button ──────

test("the chip inherits the typography the div had", () => {
    // Buttons do not inherit font or colour from the page: the UA stylesheet
    // gives them `font: 400 13.333px Arial` and `color: ButtonText`. On a dark
    // panel that is black-on-dark, and the pill reads as a different component
    // from the one the stylesheet describes.
    const rule = decls(".chunk-pill");

    assert.match(rule, /font:\s*inherit/, "the chip does not inherit the page's font");
    assert.match(rule, /color:\s*inherit/, "the chip does not inherit the page's colour");
    assert.match(rule, /text-align:\s*left/, "the chip keeps the UA's centred text");
});

test("the chip drops the UA button chrome", () => {
    const rule = decls(".chunk-pill");

    assert.match(
        rule,
        /appearance:\s*none/,
        "the chip keeps the platform's button chrome, so it renders with a " +
            "border-radius and padding the design never asked for",
    );
    assert.match(
        rule,
        /width:\s*100%/,
        "the chip does not fill the panel, so it is now a shrink-wrapped button",
    );
});

test("a keyboard user can see which chip they are on", () => {
    // Native focusability is only half of it: without a visible ring the chips
    // are reachable and still unusable.
    assert.match(
        decls(".chunk-pill:focus-visible"),
        /outline:\s*(?!none)/,
        ".chunk-pill:focus-visible sets no outline, so the chip is focusable " +
            "and invisible",
    );
});

test("the toggle is still reachable and still the only expander", () => {
    // The handler moved onto the container; the function itself must still be
    // there, because a delegated listener with no target is a dead control.
    assert.match(appJs, /function toggleChunk\(/, "toggleChunk() is gone");
    assert.match(
        extractFunction("toggleChunk", appJs),
        /aria-expanded/,
        "toggleChunk() no longer touches aria-expanded",
    );
});
