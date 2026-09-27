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
