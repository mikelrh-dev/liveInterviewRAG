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
 * tests/frontend/harness.mjs, and rendered into the REAL #context-content of
 * the shipped index.html by tests/frontend/dom.mjs. That is not tidiness. The
 * version of this file before it took a real DOM injected its OWN `escapeHtml`
 * and then asserted that `renderContext` escaped -- so replacing the shipped
 * `escapeHtml` with `return String(text)` left the whole suite green at
 * 186/186. The function under protection was never loaded. The escaping is
 * checked here against the real one now, and the DOM is what decides it: if an
 * `<img>` element exists in the rendered tree, the text was not escaped,
 * whatever the string looked like.
 *
 * The CSS that decides whether the ellipsis applies is asserted by reading the
 * declarations that decide it -- there is still no layout engine in this suite.
 *
 *   node --test tests/frontend/
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { extractFunction, readAppJs } from "./harness.mjs";
import { createDom } from "./dom.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(here, "..", "..", "frontend");
const appJs = readAppJs();
const css = readFileSync(join(FRONTEND, "style.css"), "utf8");

const CHUNKS = [
    { score: 0.8123, text: "El proyecto InterviewTTS usa FastAPI y SSE", source: "cv.md" },
    { score: 0.4, text: "Second", source: "skills/frontend.md" },
];

const env = createDom();
const { document } = env;
// The REAL escapeHtml. Injected here it was the source of the defect: the test
// asserted the renderer escaped while the code doing the escaping was a stub
// the test itself wrote.
const { renderContext, toggleChunk, escapeHtml } = env.loadApp(
    ["renderContext", "toggleChunk", "escapeHtml"],
    { contextContent: document.getElementById("context-content") },
);

test.after(() => env.close());

/** Render into the real panel and return the panel element. */
function render(chunks) {
    renderContext(chunks);
    return document.getElementById("context-content");
}

/** The serialised panel, for the assertions that are about markup shape. */
function renderHTML(chunks) {
    return render(chunks).innerHTML;
}


// ─── 1. The chip is a real control ─────────────────────────────────────────

test("each evidence chip is a native button", () => {
    const panel = render(CHUNKS);

    assert.equal(
        panel.querySelectorAll("button.chunk-pill").length,
        CHUNKS.length,
        "every chunk must render exactly one button",
    );
    assert.match(
        panel.innerHTML,
        /<button[^>]*type="button"/,
        "the chip is a <button> without type, so it defaults to submit and " +
            "inherits form semantics it has no business having",
    );
});

test("the chip is not a div with a role bolted on", () => {
    // The re-implementation that looks like the fix and is not: role="button"
    // only changes what is announced. It does not make the div focusable, does
    // not handle Enter or Space, and does not draw a focus ring.
    const panel = render(CHUNKS);

    assert.equal(panel.querySelectorAll("div").length, 0, "the chip is still a div");
    assert.equal(
        panel.querySelectorAll('[role="button"]').length,
        0,
        'the chip carries role="button", which announces a control it does not ' +
            "behave like",
    );
});

test("the chip has no inline onclick", () => {
    // An inline handler can only reach a function published on `window`, which
    // is why `window.toggleChunk = toggleChunk` existed. It also cannot be
    // activated from the keyboard even when the element is a button.
    const panel = render(CHUNKS);

    assert.equal(
        panel.querySelectorAll("[onclick]").length,
        0,
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
    const panel = render(CHUNKS);

    const pills = panel.querySelectorAll("button.chunk-pill");
    assert.ok(pills.length > 0, "no chip rendered at all");

    const ids = new Set([...panel.querySelectorAll("[id]")].map((el) => el.id));

    for (const pill of pills) {
        assert.equal(
            pill.getAttribute("aria-expanded"),
            "false",
            "a collapsed chip does not say it is collapsed, so a screen-reader " +
                "user hears a control with no state",
        );

        const controls = pill.getAttribute("aria-controls");
        assert.ok(controls, "no chip names the region it expands");
        assert.ok(
            ids.has(controls),
            `aria-controls="${controls}" points at an element that does not ` +
                `exist in the rendered markup; ids present are ${JSON.stringify([...ids])}`,
        );
    }
});

test("the chip contains only phrasing content", () => {
    // A <button> may not contain flow content. The full text this wrapped in a
    // <div><p> is flow content, so it has to be a <span> that the stylesheet
    // lays out as a block -- otherwise the "fix" ships invalid HTML.
    const pill = render(CHUNKS).querySelector("button.chunk-pill");

    for (const flow of pill.querySelectorAll("div,p,section,article,ul,ol,li,table,h1,h2,h3,h4,h5,h6")) {
        assert.fail(
            `the chip contains <${flow.tagName.toLowerCase()}>, which its content ` +
                "model forbids",
        );
    }
});

// ─── 1b. The escaping, checked against the function that actually escapes ──

test("chunk text cannot become an element", () => {
    // The control. `renderContext` writes retrieved markdown into innerHTML, so
    // the escaping is the only thing between a wiki page and script execution.
    //
    // The assertion is a query, not a substring: if the text had been
    // interpolated raw the parser would have built an <img> element, and that
    // element is right here. This is the test the stubbed version could not be.
    const panel = render([
        { score: 0.5, text: '<img src=x onerror="boom">', source: "cv.md" },
    ]);

    assert.equal(
        panel.querySelectorAll("img").length,
        0,
        "the chunk text was interpolated unescaped: the parser built a real " +
            "<img> element out of it",
    );
    assert.equal(
        panel.querySelectorAll("script").length,
        0,
        "the chunk text produced a script element",
    );
    assert.match(
        panel.textContent,
        /<img src=x onerror="boom">/,
        "the text is not even rendered as literal characters, so the chip has " +
            "silently dropped the passage it exists to show",
    );
});

test("the source attribution cannot become an element either", () => {
    const panel = render([
        { score: 0.5, text: "safe", source: '<svg onload="boom">' },
    ]);

    assert.equal(
        panel.querySelectorAll("svg").length,
        0,
        "chunk.source was interpolated unescaped: the parser built an <svg>",
    );
    assert.match(
        panel.textContent,
        /Fuente: <svg onload="boom">/,
        "the source is no longer rendered, so the chip has lost half its claim",
    );
});

test("the preview and the full text are both escaped", () => {
    // Both positions, or the chip is half a control: a preview that renders raw
    // executes the payload even though the collapsed body below it is escaped.
    const panel = render([
        { score: 0.5, text: '<img src=x onerror="boom">', source: "cv.md" },
    ]);
    const pill = panel.querySelector("button.chunk-pill");

    const preview = pill.querySelector(".chunk-preview");
    const full = pill.querySelector(".chunk-full span");
    assert.ok(preview && full, "the chip lost its preview or its full text");

    for (const [name, node] of [["preview", preview], ["full text", full]]) {
        assert.equal(
            node.querySelectorAll("img").length,
            0,
            `the ${name} was interpolated unescaped`,
        );
        assert.equal(node.textContent, '<img src=x onerror="boom">');
    }
});

test("a payload that closes its own span cannot escape the chip", () => {
    // The template is a single concatenated string, so the payload does not have
    // to be well-formed HTML to break out of a position -- it only has to close
    // the tag that is open. This is the case a per-node query is the only honest
    // judge of: the string still contains "<span>", the tree does not.
    const panel = render([
        { score: 0.5, text: '</span><img src=x onerror="boom"><span>', source: "cv.md" },
    ]);

    assert.equal(
        panel.querySelectorAll("img").length,
        0,
        "a payload that closes the surrounding span was interpolated unescaped",
    );
    assert.equal(
        panel.querySelectorAll("button.chunk-pill").length,
        1,
        "the payload broke out of the chip's own markup",
    );
});

test("the escape helper is the one the renderer calls", () => {
    // Guard against the specific shape of the old defect coming back: a local
    // escaper shadowing the shipped one. The renderer resolves `escapeHtml`
    // from its own scope, so if that name is ever bound to anything but the
    // real function the XSS tests above would be testing the substitute.
    const body = extractFunction("renderContext", appJs);

    assert.match(
        body,
        /escapeHtml\(chunk\.text\)/,
        "renderContext no longer routes the passage through escapeHtml",
    );
    assert.match(
        body,
        /escapeHtml\(chunk\.source\)/,
        "renderContext no longer routes the source through escapeHtml",
    );
    assert.doesNotMatch(
        body,
        /function\s+escapeHtml/,
        "renderContext declares its own escapeHtml, so the real one is not what " +
            "escapes the text the tests inspect",
    );
});

test("the empty case still says so", () => {
    // Regression guard: the rewrite must not lose the "nothing retrieved" state,
    // which is a claim the panel has to be able to make honestly.
    assert.ok(render([]).querySelector(".context-empty"), "the empty case lost its message");
    assert.ok(render(null).querySelector(".context-empty"), "the null case lost its message");
});

// ─── 2. The state a chip is in is reported, not just drawn ────────────────

test("activating a collapsed chip expands it and says so", () => {
    const pill = render(CHUNKS).querySelector("button.chunk-pill");

    toggleChunk(pill);

    assert.equal(pill.classList.contains("expanded"), true, "the chip did not expand");
    assert.equal(pill.getAttribute("aria-expanded"), "true", "aria-expanded did not follow");
});

test("activating an expanded chip collapses it and says so", () => {
    const pill = render(CHUNKS).querySelector("button.chunk-pill");
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
    const pill = render(CHUNKS).querySelector("button.chunk-pill");
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

test("a chip is actually clickable in the rendered panel", () => {
    // The delegated listener is bound in init(), which this suite does not run.
    // It is bound here, by hand, exactly as app.js binds it -- the assertion
    // under test is that a click on a real chip in a real panel reaches the
    // real toggle, which is the wiring the old string tests could not see.
    const panel = render(CHUNKS);
    panel.addEventListener("click", (e) => {
        const pill = e.target.closest(".chunk-pill");
        if (pill) toggleChunk(pill);
    });

    const pill = panel.querySelector("button.chunk-pill");
    pill.dispatchEvent(new env.window.MouseEvent("click", { bubbles: true }));

    assert.equal(
        pill.classList.contains("expanded"),
        true,
        "a click on the chip did not expand it, so the panel's chips are dead",
    );
});

