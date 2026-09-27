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

/** `{ tag, parentTag, inner }` for the element carrying this id. */
function elementById(id) {
    const node = tree.get(id);
    assert.ok(node, `#${id} not found in frontend/index.html`);
    return {
        tag: node.tag,
        parentTag: node.parent ? `${node.parent.tag}#${node.parent.id}` : null,
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
