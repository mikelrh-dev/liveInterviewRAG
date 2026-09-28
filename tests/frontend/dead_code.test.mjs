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

test("the typing-interval registry is gone", () => {
    // `const typingIntervals = []` was declared and never read or written. The
    // typing animation it looks like it tracks is driven by the live region:
    // the answer is appended to as tokens arrive, and `finalizeAnswer` re-commits
    // the text and removes the cursor. There is no interval to cancel, so the
    // array was a container for nothing -- 38 lines of nothing is still
    // something to read, reason about and keep in sync.
    //
    // This is the one assertion in the file that is about a name rather than
    // about behaviour, and it stays that way deliberately: "this variable does
    // not exist" is not a behaviour. What guards the behaviour is
    // tests/frontend/announcements.test.mjs, which drives showTyping and
    // finalizeAnswer in a real document.
    assert.doesNotMatch(
        appJs,
        /typingIntervals/,
        "app.js declares a typing-interval registry that nothing reads or writes. " +
            "Either the typing animation needs cancelling -- in which case there " +
            "is a leak -- or the declaration is dead and should be deleted",
    );
});

test("there is no audio-blocked flag shadowing the overlay", () => {
    // `let audioBlocked` was written in three places and read in none. It could
    // not be "used" without inventing a reader: whether the audio is blocked is
    // already exactly whether `#audio-blocked-overlay` is showing, and a second
    // representation of that fact is a second thing to keep in step with the
    // first. So it was deleted rather than wired up, and this is what stops it
    // being re-added as a note to self.
    assert.doesNotMatch(
        stripComments(appJs),
        /\baudioBlocked\b/,
        "app.js has an audioBlocked flag again. It is written in several places " +
            "and read in none, and the overlay's own class is the state it would " +
            "duplicate",
    );
});

/**
 * The source with its comments removed.
 *
 * Needed because a comment that explains why a name was deleted still contains
 * the name, and a test for its absence has to be about the code. Same reason
 * evidence_pills.test.mjs strips comments before reading the stylesheet.
 */
function stripComments(source) {
    return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}
