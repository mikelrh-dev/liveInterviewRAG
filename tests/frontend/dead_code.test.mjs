/**
 * No dead code in the frontend.
 *
 * `initParticles()` was 38 lines of tsparticles configuration with zero call
 * sites, and the library it guarded against was never loaded — index.html has
 * no tsparticles script tag, so the function could only ever have logged
 * "tsparticles not loaded — skipping particles" if it had been called at all.
 * The particles were designed and never shipped; the CSS grid background
 * replaced them.
 *
 * The fix is deletion, not wiring. A guard clause that returns early forever
 * is still 38 lines someone has to read, reason about and keep in sync with a
 * library that is not in the build.
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
const appJs = readFileSync(join(FRONTEND, "app.js"), "utf8");
const html = readFileSync(join(FRONTEND, "index.html"), "utf8");

test("the particles loader is gone", () => {
    assert.doesNotMatch(
        appJs,
        /initParticles/,
        "initParticles() is dead code: it has no call sites and its library " +
            "is never loaded",
    );
    assert.doesNotMatch(
        appJs,
        /\btsParticles\b/,
        "app.js still references the tsparticles global, which nothing loads",
    );
});

test("no particles library creeps back into the page", () => {
    // The two halves of the same finding: the code was dead *because* the
    // script was absent. Asserting only the code would let someone re-add the
    // tag and make the function dead in a new way.
    assert.doesNotMatch(
        html,
        /tsparticles/i,
        "index.html loads a particles library that no code configures",
    );
});
