/**
 * Style-token contract: a declaration that references `var(--x)` is only as
 * real as the token it names.
 *
 * The disclaimer card shipped with `background: var(--surface-container)`,
 * `font-family: var(--font-sora)` and `font-family: var(--font-inter)`. None of
 * those three was ever declared in `:root` — they are Tailwind theme *names*
 * (see the inline `tailwind.config` in index.html) written as if they were CSS
 * custom properties. An unresolved `var()` is invalid at computed-value time,
 * so the whole declaration falls through to `unset`: the app's entry overlay
 * had no background at all, and the card silently lost its typography.
 *
 * Nothing catches that class of bug at runtime — no console error, no test,
 * just a missing visual. So this file reads the stylesheet the same way the
 * cascade does and refuses to let a name escape without a definition.
 *
 *   node --test tests/frontend/
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const STYLE_CSS = join(here, "..", "..", "frontend", "style.css");

const css = readFileSync(STYLE_CSS, "utf8");

/**
 * Every custom property declared in *any* `:root` block (the base one plus the
 * two inside `@media`/`@supports` fallbacks). Values are not validated here —
 * only the existence of the name.
 */
function definedTokens() {
    const names = new Set();
    for (const block of css.matchAll(/:root\s*\{([^}]*)\}/g)) {
        for (const decl of block[1].matchAll(/(--[A-Za-z0-9_-]+)\s*:/g)) {
            names.add(decl[1]);
        }
    }
    return names;
}

/** `var(--x)` reads, keyed by token name, each with the lines that use it. */
function referencedTokens() {
    const uses = new Map();
    for (const match of css.matchAll(/var\(\s*(--[A-Za-z0-9_-]+)/g)) {
        const line = css.slice(0, match.index).split("\n").length;
        if (!uses.has(match[1])) uses.set(match[1], []);
        uses.get(match[1]).push(line);
    }
    return uses;
}

test("the stylesheet actually declares tokens in :root", () => {
    // Guards the guard: if the :root parser ever stopped matching, the
    // undefined-token test below would pass vacuously.
    assert.ok(
        definedTokens().size > 20,
        "no :root tokens parsed — the extraction is broken, not the stylesheet",
    );
});

test("every var(--x) in the stylesheet resolves to a declared token", () => {
    const defined = definedTokens();
    const uses = referencedTokens();

    assert.ok(uses.size > 0, "no var() reads found — the extraction is broken");

    const undefinedTokens = [...uses.keys()]
        .filter((name) => !defined.has(name))
        .sort()
        .map((name) => `${name} (style.css:${uses.get(name).join(",")})`);

    assert.deepEqual(
        undefinedTokens,
        [],
        `stylesheet references custom properties that :root never declares; an ` +
            `unresolved var() drops the entire declaration: ${undefinedTokens.join("; ")}`,
    );
});

test("the disclaimer card renders the container surface, not nothing", () => {
    // The specific regression: `background: var(--surface-container)` with no
    // such token, so the entry overlay shipped with a transparent card.
    const card = css.match(/\.disclaimer-card\s*\{([^}]*)\}/);
    assert.ok(card, ".disclaimer-card rule not found in style.css");

    const background = card[1].match(/background:\s*([^;]+);/);
    assert.ok(background, ".disclaimer-card declares no background");

    const token = background[1].match(/var\(\s*(--[A-Za-z0-9_-]+)/)?.[1];
    assert.ok(token, "the card background is not a token reference");
    assert.ok(
        definedTokens().has(token),
        `disclaimer-card background uses undefined ${token}`,
    );
});

// ─── WCAG 1.4.11 Non-text Contrast (AA) ────────────────────────────────────

/** WCAG 2.x relative luminance of an sRGB triple. */
function luminance([r, g, b]) {
    const channel = (c) => {
        const s = c / 255;
        return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a, b) {
    const [hi, lo] = [luminance(a) + 0.05, luminance(b) + 0.05];
    return Math.max(hi, lo) / Math.min(hi, lo);
}

const toRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

/** Source alpha compositing, so a translucent fill is judged on what it paints. */
function over(foreground, alpha, background) {
    return foreground.map((c, i) => Math.round(c * alpha + background[i] * (1 - alpha)));
}

/** Literal value of a `:root` custom property (the surfaces are tokens). */
function rootValue(name) {
    const block = css.slice(css.indexOf(":root"), css.indexOf("}", css.indexOf(":root")));
    const value = block.match(new RegExp(`${name}\\s*:\\s*([^;]+);`))?.[1]?.trim();
    assert.ok(value, `:root does not declare ${name}`);
    assert.match(value, /^#[0-9a-f]{6}$/i, `${name} is not a plain hex colour: ${value}`);
    return toRgb(value);
}

/** The declarations of one rule, looked up by its exact selector. */
function rule(selector, property) {
    const start = css.indexOf(selector + " {");
    assert.notEqual(start, -1, `rule \`${selector}\` not found in style.css`);

    const open = css.indexOf("{", start);
    let depth = 0;
    for (let i = open; i < css.length; i++) {
        if (css[i] === "{") depth++;
        else if (css[i] === "}") {
            depth--;
            if (depth === 0) {
                const body = css.slice(open + 1, i);
                const value = body.match(new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`))?.[1];
                assert.ok(value, `\`${selector}\` declares no ${property}`);
                return value.trim();
            }
        }
    }
    throw new Error(`unbalanced braces reading \`${selector}\``);
}

/** `rgba(r, g, b, a)` from a rule, resolved against a surface underneath it. */
function fillOver(selector, surface) {
    const value = rule(selector, "background");
    const rgba = value.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)$/);
    assert.ok(rgba, `${selector} background is not a plain rgba(): ${value}`);
    return over([+rgba[1], +rgba[2], +rgba[3]], Number(rgba[4]), surface);
}

/**
 * Every colour `--outline-variant` is drawn against.
 *
 * WCAG 1.4.11 is per-surface: a token that clears 3:1 on the sidebar and
 * fails it on a black conversation column is still a failure, and the token
 * carries the *only* border on `#context-toggle`, `.chunk-pill` and
 * `.disclaimer-card`. The translucent fills are resolved from the stylesheet
 * itself so a fill change is re-measured instead of silently going stale.
 */
const BORDER_TOKEN = "--outline-variant";
const MIN_RATIO = 3.0;

const SURFACES = () => {
    const deep = rootValue("--bg-deep");
    const stardust = rootValue("--bg-stardust");
    const voidBlack = rootValue("--bg-void");
    const container = rootValue("--bg-container");

    return [
        // `header` bottom border and `#context-toggle` rest on --bg-deep.
        { where: "--bg-deep (header, #context-toggle)", rgb: deep },
        // `#sidebar`, `#context-panel`, its header rule and `#context-close`.
        { where: "--bg-stardust (sidebar, context panel, #context-close)", rgb: stardust },
        // `.bubble`, `.typing-bubble` and the conversation scrollbar.
        { where: "--bg-void (main: bubbles, scrollbar thumb)", rgb: voidBlack },
        // The card's border has a colour on each side: overlay outside, fill in.
        {
            where: "disclaimer card, outside — .overlay--disclaimer over --bg-deep",
            rgb: fillOver(".overlay--disclaimer", deep),
        },
        { where: "disclaimer card + orbit, inside — --bg-container", rgb: container },
        { where: ".chunk-pill fill", rgb: fillOver(".chunk-pill", stardust) },
        { where: ".chunk-pill.expanded fill", rgb: fillOver(".chunk-pill.expanded", stardust) },
        { where: ".bubble / .typing-bubble fill", rgb: fillOver(".message.candidate .bubble", voidBlack) },
    ];
};

test("the outline-variant token is actually painted as a border somewhere", () => {
    // Guards the surface table below: if nobody drew this token, the contrast
    // test would pass on a list that no longer describes the stylesheet.
    const borders = [...css.matchAll(/border(?:-[a-z]+)?:\s*[^;]*var\(\s*--outline-variant/g)];
    assert.ok(borders.length >= 8, `only ${borders.length} borders use ${BORDER_TOKEN}`);
});

test(`${BORDER_TOKEN} clears WCAG 1.4.11 (3:1) on every surface it borders`, () => {
    const token = rootValue(BORDER_TOKEN);
    const failures = [];

    for (const surface of SURFACES()) {
        const ratio = contrast(token, surface.rgb);
        if (ratio < MIN_RATIO) {
            const rgb = `rgb(${surface.rgb.join(", ")})`;
            failures.push(
                `${BORDER_TOKEN} on ${surface.where} (${rgb}): ` +
                    `${ratio.toFixed(2)}:1 < ${MIN_RATIO}:1`,
            );
        }
    }

    assert.deepEqual(failures, [], `non-text contrast below 3:1:\n  ${failures.join("\n  ")}`);
});
