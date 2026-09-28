/**
 * The session timer must be one writer, not one per interview.
 *
 * THE DEFECT
 * ----------
 * `startSessionTimer()` called `setInterval(...)` and threw the handle away.
 * Nothing could ever clear it, and it is called from three places: once on load
 * and once in each of the two END handlers, which both do
 * `sessionStartTime = null; startSessionTimer();` to reset the display.
 *
 * So every END click left another 1 Hz writer running for the life of the page.
 * After ten interviews the page was formatting `MM:SS` ten times a second,
 * forever, on a laptop that is on battery for the length of an interview. The
 * leak is invisible in the UI -- all the writers compute the same string and
 * write it to the same element -- which is exactly why it survived.
 *
 * WHY THE TEST COUNTS WRITERS
 * ---------------------------
 * The symptom is not a wrong value; it is the same value written N times. So
 * the assertion is on the count, made observable two ways that agree: how many
 * intervals are still registered, and how many times the tick body actually
 * runs when one second passes. A fake clock supplies both, and virtualises
 * `Date.now()` so the rendered elapsed time is a decision rather than a race.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom } from "./dom.mjs";

/** A window with the timer running on a clock the test controls. */
function timerEnv() {
    const env = createDom({ clock: true });
    const fn = env.loadApp(["startSessionTimer"]);
    return { env, fn, readout: env.document.getElementById("sidebar-timer") };
}

/** What the END handlers do to the display, verbatim in shape. */
function resetForNewInterview(fn) {
    fn.state.sessionStartTime = null;
    fn.startSessionTimer();
}

test("starting the timer twice leaves one writer, not two", () => {
    const { env, fn } = timerEnv();

    fn.startSessionTimer();
    assert.equal(env.clock.intervals, 1, "the timer did not start");

    fn.startSessionTimer();
    assert.equal(
        env.clock.intervals,
        1,
        `two calls left ${env.clock.intervals} writers registered. The handle is ` +
            "discarded, so nothing can ever stop the first one",
    );
    env.close();
});

test("ten interviews leave ten writers, and the clock proves it", () => {
    // The count is the symptom; the number of executions is the harm. Both are
    // asserted, because a test that only counts registrations could be satisfied
    // by a "fix" that clears the old interval and never starts a new one.
    const { env, fn, readout } = timerEnv();

    fn.startSessionTimer();
    for (let i = 0; i < 10; i++) resetForNewInterview(fn);

    assert.equal(
        env.clock.intervals,
        1,
        `ten END clicks left ${env.clock.intervals} writers registered`,
    );

    readout.textContent = "";
    env.clock.tick(1000);

    assert.equal(
        readout.textContent,
        "⏱ 00:01",
        `one second of wall time produced "${readout.textContent}": more than one ` +
            "writer is running, or the elapsed time is being computed twice",
    );
    env.close();
});

test("a restart that keeps the same start time does not move the clock", () => {
    // The other half of the singleton: re-arming must not restart the count
    // either. `startSessionTimer` only stamps `sessionStartTime` when it is
    // unset, so an END that clears it does start from zero -- but a second call
    // without clearing it must keep counting from where it was.
    const { env, fn, readout } = timerEnv();

    fn.startSessionTimer();
    env.clock.tick(5000);
    fn.startSessionTimer();
    env.clock.tick(1000);

    assert.equal(
        readout.textContent,
        "⏱ 00:06",
        `the second registration reset the elapsed time: it reads ` +
            `"${readout.textContent}" after 6 seconds`,
    );
    env.close();
});

test("a replaced writer does not keep writing", async () => {
    // The count of registrations and the count of WRITES are different claims,
    // and only the second is the harm. A "fix" that registered one interval and
    // left the earlier ones alive would satisfy the first and fail this.
    //
    // So the writes are observed directly, with a MutationObserver on the
    // readout: one second of virtual time, several re-arms, and the number of
    // times the element was actually mutated. Observer callbacks are delivered
    // as microtasks, so the count is read after the queue drains.
    const { env, fn, readout } = timerEnv();

    let writes = 0;
    // Count MUTATIONS, not deliveries: several writes in one tick arrive as one
    // callback carrying several records, so counting callbacks would report 1
    // for six writers and pass on exactly the defect this is here to catch.
    const observer = new env.window.MutationObserver((records) => {
        writes += records.length;
    });
    observer.observe(readout, { childList: true, characterData: true, subtree: true });

    fn.startSessionTimer();
    for (let i = 0; i < 5; i++) resetForNewInterview(fn);

    writes = 0;
    env.clock.tick(1000);
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(
        writes,
        1,
        `one second of virtual time produced ${writes} writes to the readout, ` +
            "not 1. Re-arming must replace the previous writer, not add to it",
    );
    observer.disconnect();
    env.close();
});
