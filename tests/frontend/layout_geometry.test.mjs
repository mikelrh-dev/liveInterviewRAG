/**
 * Layout geometry contract for the centre column: the avatar must fit the
 * column it lives in, and the ring that reports its state must be centred on
 * it.
 *
 * Both findings here are the same underlying mistake — laying an absolutely
 * positioned child out against a box that was never the one it meant.
 *
 *   #orbital-ring was a *sibling* of #avatar-wrapper, so its `top: 50%`
 *   resolved against #hero-center (599px) and not the avatar (380px).
 *   #avatar-wrapper was a fixed 380px inside a column that shrinks to
 *   1fr = 100vw - 640px, so below 1020px of viewport it was clipped.
 *
 * No browser here, so these assert the two things that are objectively true
 * of the source: the DOM nesting that decides the containing block, and the
 * absence of the fixed width that decides the overflow.
 *
 *   node --test tests/frontend/
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(here, "..", "..", "frontend");
const html = readFileSync(join(FRONTEND, "index.html"), "utf8");
const css = readFileSync(join(FRONTEND, "style.css"), "utf8");

const markup = html.replace(/<!--[\s\S]*?-->/g, "");

/** Attributes may contain `>`; quoted values are consumed as one token. */
const TAG = /<(\/?)([A-Za-z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)(\/?)>/g;
const VOID = new Set([
    "area", "base", "br", "col", "embed", "hr", "img",
    "input", "link", "meta", "source", "track", "wbr",
]);

/**
 * A stack walk over the tag stream — enough structure to answer "what is this
 * element's parent, and what is inside it" without a DOM library. Comments are
 * stripped first so a commented-out tag cannot join the tree.
 */
function parseTree(source) {
    const byId = new Map();
    const stack = [];
    let match;
    TAG.lastIndex = 0;

    while ((match = TAG.exec(source)) !== null) {
        const [, closing, tag, attrs, selfClose] = match;
        if (closing) {
            const open = stack.pop();
            if (open) open.node.innerEnd = match.index;
            continue;
        }
        const id = attrs.match(/\bid="([^"]+)"/)?.[1] ?? null;
        const node = {
            id,
            tag,
            parent: stack[stack.length - 1]?.node ?? null,
            openStart: match.index,
            innerStart: TAG.lastIndex,
            innerEnd: source.length,
        };
        if (id) byId.set(id, node);
        if (!selfClose && !VOID.has(tag.toLowerCase())) {
            stack.push({ tag, node });
        }
    }
    return byId;
}

const tree = parseTree(markup);

/** `{ tag, parentTag, openTag, inner }` for the element carrying this id. */
function elementById(id) {
    const node = tree.get(id);
    assert.ok(node, `#${id} not found in frontend/index.html`);
    return {
        tag: node.tag,
        parentTag: node.parent ? `${node.parent.tag}#${node.parent.id}` : null,
        openTag: markup.slice(node.openStart, node.innerStart),
        inner: markup.slice(node.innerStart, node.innerEnd),
    };
}


// ─── F2: the ring centres on the avatar, not on the hero ────────────────────

test("the orbital ring is a child of the box it is centred on", () => {
    // Containment, not ancestry-by-accident: the ring centres itself with
    // `top: 50%; left: 50%`, so its *nearest positioned ancestor* is the box
    // whose centre it must land on. #avatar-wrapper is that box.
    const wrapper = elementById("avatar-wrapper");
    assert.match(
        wrapper.inner,
        /\bid="orbital-ring"/,
        "#orbital-ring is not inside #avatar-wrapper, so `top: 50%` resolves " +
            "against #hero-center and the ring sits below the avatar's centre",
    );
});

test("the ring's containing block is the avatar, not the hero column", () => {
    // Belt and braces: whatever the markup does, the offset parent has to be
    // a box whose height is the avatar's, and not the hero column.
    const ring = elementById("orbital-ring");
    assert.equal(ring.parentTag, "div#avatar-wrapper");

    const hero = elementById("hero-center");
    assert.doesNotMatch(
        hero.inner.slice(0, hero.inner.indexOf('id="orbital-ring"')),
        /\bid="orbital-ring"/,
        "the ring still leads #hero-center's children",
    );
});

test("the ring paints above the videos and the orb canvas", () => {
    // Moving the ring inside #avatar-wrapper drops it into a stacking context
    // it did not share before: as a plain sibling of #portal-ring it sorted
    // *under* the absolutely positioned children it must be readable through.
    // Stacking order inside the wrapper has to stay: videos (1) < ring < canvas (2).
    const wrapper = elementById("avatar-wrapper");
    // Direct children only — the videos and the canvas live inside #portal-ring.
    const order = [...parseTree(wrapper.inner).values()]
        .filter((node) => node.parent === null)
        .map((node) => node.id);

    assert.deepEqual(
        order,
        ["portal-ring", "orbital-ring"],
        "the ring must come after #portal-ring in the wrapper to paint over it",
    );

    const zIndex = Number(
        css.match(/#orbital-ring\s*\{[^}]*z-index:\s*(\d+)/)?.[1],
    );
    assert.ok(
        Number.isFinite(zIndex),
        "#orbital-ring declares no z-index, so it falls behind the avatar videos",
    );

    const videoZ = Number(
        css.match(/#avatar-neutral-video,\s*#avatar-talking-video\s*\{[^}]*z-index:\s*(\d+)/)?.[1],
    );
    const canvasZ = Number(css.match(/#orb-canvas\s*\{[^}]*z-index:\s*(\d+)/)?.[1]);

    assert.ok(zIndex > videoZ, `ring (${zIndex}) must paint over the videos (${videoZ})`);
    assert.ok(zIndex > canvasZ, `ring (${zIndex}) must paint over the canvas (${canvasZ})`);
});

// ─── F3: the avatar fits the column it is given ─────────────────────────────

test("the avatar sizes to its container, not to a fixed pixel value", () => {
    // `.main-grid` is `320px | 1fr | 320px`, so the centre column is
    // 100vw - 640px and reaches 380px only at >= 1020px of viewport. A fixed
    // 380px avatar is therefore clipped on every width in (768px, 1020px).
    const rule = css.match(/#avatar-wrapper\s*\{([^}]*)\}/);
    assert.ok(rule, "#avatar-wrapper rule not found in style.css");

    const width = rule[1].match(/(?<!-)\bwidth:\s*([^;]+);/);
    assert.ok(width, "#avatar-wrapper declares no width");
    assert.match(
        width[1],
        /%/, // container-relative, so it can never exceed the column
        `#avatar-wrapper width is \`${width[1].trim()}\` — a fixed pixel size ` +
            "overflows the 1fr centre column below 1020px of viewport",
    );
});

test("the avatar stays square whatever the width resolves to", () => {
    // `width` alone would stretch the circle into an ellipse once the width is
    // container-driven; the box needs an aspect ratio (or a matching height).
    const rule = css.match(/#avatar-wrapper\s*\{([^}]*)\}/)[1];
    const square =
        /aspect-ratio:\s*1\s*\/\s*1/.test(rule) ||
        (rule.match(/(?<!-)\bheight:\s*([^;]+);/) || [])[1] === width_under_test(rule);

    assert.ok(
        square,
        "#avatar-wrapper has a container-relative width but no square aspect ratio",
    );
});

function width_under_test(rule) {
    return (rule.match(/(?<!-)\bwidth:\s*([^;]+);/) || [])[1];
}


// ─── A1: every header control is reachable on a phone ──────────────────────

/**
 * The body of the first `@media (max-width: 768px)` block, by brace count.
 *
 * There are two such blocks — the main one and the `100dvh` fallback nested in
 * `@supports` — and only the first carries layout rules, so this takes the
 * first occurrence rather than concatenating them.
 */
function mobileBlock() {
    const at = css.indexOf("@media (max-width: 768px)");
    assert.notEqual(at, -1, "no @media (max-width: 768px) block in style.css");

    const brace = css.indexOf("{", at);
    let depth = 0;
    for (let i = brace; i < css.length; i++) {
        if (css[i] === "{") depth++;
        else if (css[i] === "}") {
            depth--;
            if (depth === 0) return css.slice(brace + 1, i);
        }
    }
    throw new Error("unbalanced braces reading the mobile block");
}

const mobile = mobileBlock();

/**
 * The declaration body of one rule.
 *
 * Depth counting rather than `indexOf("}")`: declarations here carry
 * `var(--outline-variant)` and a naive scan stops inside the function call,
 * silently truncating the rule it was trying to read.
 */
function decls(block, selector) {
    const at = block.indexOf(selector + " {");
    assert.notEqual(at, -1, `rule \`${selector}\` not found`);

    let depth = 0;
    for (let i = block.indexOf("{", at); i < block.length; i++) {
        if (block[i] === "{") depth++;
        else if (block[i] === "}") {
            depth--;
            if (depth === 0) return block.slice(block.indexOf("{", at) + 1, i);
        }
    }
    throw new Error(`unbalanced braces reading \`${selector}\``);
}

test("the mobile header drops the status pill instead of a control", () => {
    // The brief's framing: END is the only way to end an interview on a phone
    // (the sidebar's button is display:none below the breakpoint), so END is
    // primary. `.status-pill` is the one header item that carries no action —
    // and the rail it was a fragment of (the SISTEMA section, #sidebar-status)
    // is itself display:none on mobile, so on a phone it reported a system
    // state nothing on screen could elaborate or contradict. Trading
    // decoration for reachability is the deliberate call.
    assert.match(
        decls(mobile, ".status-pill"),
        /display:\s*none/,
        "the ONLINE pill is still in the mobile header row, pushing END off " +
            "the edge; its information (the SISTEMA rail) is already hidden on " +
            "mobile, so it is decoration competing with a primary control",
    );
});

test("header controls are never the thing that shrinks", () => {
    // The root cause of the clip is not "the row is too wide" — it is that
    // every flex item defaults to `flex-shrink: 1`, so under overflow the
    // browser squeezes the buttons. END has a min-width floor and a
    // fixed-height header, so what it loses is its text, not its box.
    // Each rule is read from the block that actually declares it.
    const rules = [
        [".header-right", css],
        ["#context-toggle", css],
        ["#mobile-end-btn", mobile],
    ];

    for (const [selector, block] of rules) {
        assert.match(
            decls(block, selector),
            /flex-shrink:\s*0/,
            `\`${selector}\` can shrink, so a narrow viewport squeezes a ` +
                "control instead of the brand text",
        );
    }
});

test("the wordmark is the only elastic thing in the header", () => {
    // A truncated brand is a cosmetic loss; a truncated END button is a dead
    // primary flow. So the slack has exactly one place to go, and it goes
    // there without wrapping (the header is a fixed 64px in a
    // `body { overflow: hidden }` shell, so a wrapped title clips vertically).
    for (const block of [".header-left", ".header-title"]) {
        assert.match(
            decls(css, block),
            /min-width:\s*0/,
            `\`${block}\` has flex min-width:auto, so it cannot shrink below ` +
                "its min-content width and the overflow lands on a control",
        );
    }
    assert.match(
        decls(css, ".header-title"),
        /white-space:\s*nowrap/,
        "the wordmark can wrap, and a second line is clipped by the fixed " +
            "64px header",
    );
    assert.match(
        decls(css, ".header-title"),
        /text-overflow:\s*ellipsis/,
        "the wordmark shrinks with no truncation, so it is just cut off",
    );
});

test("the mobile header's boxes fit a 320px viewport", () => {
    // Arithmetic, not a rendered measurement — no browser runs here. What it
    // CAN prove is the part that is declared: the sum of the boxes the browser
    // cannot shrink (padding, gaps, icon sizes, borders, the END touch-target
    // floor) plus the widest label it is not allowed to truncate, against the
    // narrowest phone width the brief requires.
    const tokenPx = (name) => {
        const match = css.match(new RegExp(`${name}:\\s*(\\d+(?:\\.\\d+)?)px`));
        assert.ok(match, `:root declares no ${name} in px`);
        return Number(match[1]);
    };

    /** Resolves `12px` and `var(--token)` to a number, from one declaration. */
    const px = (selector, prop, block = mobile) => {
        const rule = decls(block, selector);
        const read = (name) =>
            rule.match(new RegExp(`(?:^|;)\\s*${name}\\s*:\\s*([^;]+)`))?.[1]?.trim();

        let value = read(prop);
        if (value === undefined) {
            // `padding: 6px 10px` answers for `padding-right`, not the reverse.
            const shorthand = { "padding-right": "padding", "padding-left": "padding" }[prop];
            const parts = shorthand && read(shorthand)?.split(/\s+/);
            if (parts) value = parts[parts.length === 2 ? 1 : 2];
        }
        assert.ok(value, `\`${selector}\` declares no ${prop}`);

        const token = value.match(/^var\(\s*(--[\w-]+)\s*\)$/);
        if (token) return tokenPx(token[1]);

        const literal = value.match(/^(\d+(?:\.\d+)?)px/);
        assert.ok(literal, `\`${selector} { ${prop}: ${value} } is not a px length`);
        return Number(literal[1]);
    };

    // --space-md is 16px and --space-sm is 8px, but read them from :root
    // rather than hardcoding, so a spacing-token change is visible here.
    const md = tokenPx("--space-md");
    const sm = tokenPx("--space-sm");

    /**
     * The two numbers in this budget that CSS cannot hand us: text advance.
     * JetBrains Mono's advance is 0.6em, plus any letter-spacing, and the only
     * labels measured are the two header strings that may not be truncated
     * ("Contexto" on the toggle, "ONLINE" on the pill). The wordmark is
     * excluded on purpose — it is the one element allowed to ellipsis, which
     * is what makes the rest of the row fit.
     */
    const labelWidth = (text, fontRem, trackingEm = 0) =>
        text.length * (fontRem * 16 * 0.6 + fontRem * 16 * trackingEm);

    // Is the ONLINE pill in the mobile row at all? Derived, not assumed — the
    // sum below has to be a consequence of the stylesheet or it proves nothing.
    const pillHidden = new RegExp(`\\.status-pill\\s*\\{[^}]*display:\\s*none`).test(mobile);
    const pillCost = pillHidden
        ? 0
        : px(".status-pill .dot", "width", css) +
          px(".status-pill", "gap", css) +
          labelWidth("ONLINE", 0.7, 0.08);

    const items = pillHidden ? 2 : 3; // toggle + END, or pill + toggle + END
    const gaps = items - 1;

    const fixed =
        2 * px("header", "padding-right", mobile) + // both gutters
        px(".header-left", "gap", css) + // logo -> wordmark
        px(".header-logo", "width", css) +
        gaps * px(".header-right", "gap", mobile) +
        2 * px("#context-toggle", "padding-right", mobile) + // both sides
        2 * px("#context-toggle", "border", css) + // both edges
        px("#context-toggle", "gap", css) + // icon -> label
        px("#context-toggle svg", "width", css) +
        px("#mobile-end-btn", "min-width", mobile) + // END's text fits inside it
        pillCost;

    // END's own text fits inside its 44px touch-target floor, so that floor is
    // its whole cost; "Contexto" is the widest label the layout may not cut.
    const labelAllowance = labelWidth("Contexto", 0.75);

    // 320px is the tightest width the brief requires; 375px is the one the
    // review measured. Both are asserted, because they fail differently: at
    // 375px the row fits on paper and still clips in practice (the pill and
    // the wordmark both have min-content floors, so the squeeze lands on the
    // controls — see the flex-shrink test), while at 320px it does not even fit.
    const needed = fixed + labelAllowance;
    const failures = [320, 375]
        .map((vw) => ({ vw, available: vw - 2 * md, needed }))
        .filter(({ available }) => needed > available)
        .map(({ vw, available }) => `${needed.toFixed(1)}px needed > ${available}px at ${vw}px`);

    assert.deepEqual(
        failures,
        [],
        `the mobile header needs ${fixed.toFixed(1)}px of boxes (including ` +
            `the ${pillHidden ? "absent" : "present"} ONLINE pill) plus ` +
            `${labelAllowance.toFixed(1)}px of "Contexto"`,
    );

    // Sanity: the sum above is not accidentally vacuous.
    assert.ok(fixed > 100, `fixed-box budget collapsed to ${fixed}px — parser bug?`);
    assert.equal(md, 16, "--space-md is not 16px; re-check the padding arithmetic");
    assert.equal(sm, 8, "--space-sm is not 8px; re-check the gap arithmetic");
});

test("the header does not solve overflow by wrapping", () => {
    // Records the rejected alternative. `flex-wrap: wrap` on a header with a
    // fixed `height: var(--header-h)` inside a `body { overflow: hidden }`
    // shell trades a horizontal clip for a vertical one: the second line is
    // simply not there, and END lands below the fold of the chrome. Rejected
    // deliberately; this test is here so nobody re-picks it by accident.
    assert.doesNotMatch(
        css,
        /header\s*\{[^}]*flex-wrap/,
        "the header wraps — with a fixed height that clips the wrapped line " +
            "rather than making room for it",
    );
});
