/**
 * Retry-policy tests: backoff must absorb load, not amplify it.
 *
 * The defect, in two independent parts:
 *
 *  (a) Every failure was treated as transient. `fetchWithBackoff` did
 *      `if (!res.ok) throw new Error("HTTP " + res.status)` inside its try
 *      block, so a 413 (recording over the ceiling) or a 422 (bad audio)
 *      entered the same path as a dropped connection and re-ran the whole
 *      pipeline — upload, Whisper STT, RAG, LLM, TTS — up to six times. The
 *      413 case is the worst because nginx/ sets no client_max_body_size, so
 *      its 1 MB default rejects before the app's own 5 MB ceiling is ever
 *      consulted, and a legitimate long recording reliably produces one.
 *
 *  (b) The request was not idempotent. This helper carries the message POST,
 *      which persists a conversation turn. A network-level failure there is
 *      ambiguous: the request may have reached the server, been processed,
 *      and only the response been lost. The server's write is collision-safe,
 *      but on collision it re-derives a fresh turn number, so a duplicate
 *      POST is a duplicate turn, not an overwrite.
 *
 * Assertions are on attempt counts and on the delays handed to the timer, not
 * on wall-clock timing, so nothing here is flaky.
 *
 *   node --test tests/frontend/
 */

import test from "node:test";
import assert from "node:assert/strict";
import { loadWithGlobals, readAppJs } from "./harness.mjs";

// ─── doubles ───────────────────────────────────────────────────────────────

/** A Response stand-in. `headers.get` answers only Retry-After, which is all
 *  the policy reads. */
function response(status, opts = {}) {
    const headers = opts.headers || {};
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: {
            get(name) {
                if (name.toLowerCase() !== "retry-after") return null;
                return headers["Retry-After"] ?? null;
            },
        },
        json: async () => (opts.detail === undefined ? {} : { detail: opts.detail }),
    };
}

/**
 * A fetch that hands back scripted outcomes in order and records every call.
 * An unscripted extra call throws, so an unwanted retry cannot pass silently.
 */
function scriptedFetch(outcomes) {
    const queue = [...outcomes];
    const calls = [];
    const fn = async (url, options) => {
        calls.push({ url, options });
        if (queue.length === 0) {
            throw new Error("fetch called more times than the test scripted");
        }
        const next = queue.shift();
        if (next instanceof Error) throw next;
        return next;
    };
    fn.calls = calls;
    return fn;
}

/** Records every delay the policy asks for, and fires immediately: no real
 *  waiting, so the suite is deterministic. */
function recordingTimer() {
    const delays = [];
    return {
        delays,
        setTimer(fn, ms) {
            delays.push(ms);
            fn();
            return delays.length;
        },
    };
}

function noopStatus() {}

function policyFor(replayable) {
    return loadWithGlobals("createRetryPolicy", {})({ replayable });
}

function loadDriver(fetchFn, policy) {
    const timer = recordingTimer();
    const statuses = [];
    const drive = loadWithGlobals("fetchWithBackoff", {
        fetch: fetchFn,
        setStatus: (text, cls) => statuses.push({ text, cls }),
        setTimeout: timer.setTimer,
    });
    return {
        drive: (url, options) => drive(url, options, policy),
        timer,
        statuses,
    };
}

const POST = { method: "POST", body: "formdata" };

async function expectFailure(promise) {
    try {
        await promise;
    } catch (e) {
        return e;
    }
    assert.fail("the request was expected to fail but resolved");
}


// ─── 1. A rejected request is attempted exactly once ───────────────────────

test("every 4xx is attempted exactly once and surfaced with its status", async () => {
    // These are statements about *this* request: malformed, too large,
    // unauthorised, gone. No number of attempts changes the answer, and each
    // attempt re-runs upload + STT + RAG + LLM + TTS.
    for (const status of [400, 401, 403, 404, 413, 422]) {
        const fetchFn = scriptedFetch([response(status, { detail: "nope" })]);
        const { drive, statuses } = loadDriver(fetchFn, policyFor(false));

        const err = await expectFailure(drive("/api/x", POST));

        assert.equal(fetchFn.calls.length, 1, `HTTP ${status} was retried`);
        assert.equal(err.status, status, `HTTP ${status} lost its status`);
        assert.equal(
            statuses.filter((s) => /reintentando/i.test(s.text)).length,
            0,
            `HTTP ${status} was announced as a retry`,
        );
    }
});

test("a 413 does not run the pipeline five more times", async () => {
    // The concrete regression: nginx's 1 MB default preempts the app's 5 MB
    // ceiling, so a legitimate long recording reliably draws a 413.
    const fetchFn = scriptedFetch([response(413, { detail: "too large" })]);
    const { drive, timer } = loadDriver(fetchFn, policyFor(false));

    await expectFailure(drive("/api/x", POST));

    assert.equal(fetchFn.calls.length, 1, "the pipeline was re-run for a 413");
    assert.deepEqual(timer.delays, [], "a 413 scheduled backoff waits");
});

test("a 413 tells the user their recording is the problem", async () => {
    const fetchFn = scriptedFetch([response(413, { detail: "Request body too large" })]);
    const { drive } = loadDriver(fetchFn, policyFor(false));

    const err = await expectFailure(drive("/api/x", POST));

    assert.match(err.message, /413/, "the status is not in the message");
    assert.match(err.message, /grabaci|audio|demasiad|tama/i, "the message does not name the size problem");
});

test("a 404 and a 503 are not reported the same way", async () => {
    // 404 is refused outright; 503 is retried until the budget runs out. The
    // user must be able to tell a dead conversation from a busy server.
    const f404 = scriptedFetch([response(404)]);
    const f503 = scriptedFetch(new Array(8).fill(response(503)));
    const gone = await expectFailure(loadDriver(f404, policyFor(false)).drive("/api/x", POST));
    const busy = await expectFailure(loadDriver(f503, policyFor(false)).drive("/api/x", POST));

    assert.notEqual(gone.message, busy.message, "two different causes share one message");
    assert.match(gone.message, /404/);
    assert.match(busy.message, /503/);
});

// ─── 2. Genuinely transient failures are retried ───────────────────────────

test("a 503 is retried", async () => {
    const fetchFn = scriptedFetch([response(503), response(200)]);
    const { drive } = loadDriver(fetchFn, policyFor(false));

    const res = await drive("/api/x", POST);

    assert.equal(res.status, 200);
    assert.equal(fetchFn.calls.length, 2, "a 503 was not retried");
});

test("500, 502, 504 and 408 are retried", async () => {
    for (const status of [408, 500, 502, 504]) {
        const fetchFn = scriptedFetch([response(status), response(200)]);
        const { drive } = loadDriver(fetchFn, policyFor(false));

        await drive("/api/x", POST);

        assert.equal(fetchFn.calls.length, 2, `HTTP ${status} was not retried`);
    }
});

test("a 429 is retried", async () => {
    const fetchFn = scriptedFetch([response(429), response(200)]);
    const { drive } = loadDriver(fetchFn, policyFor(false));

    await drive("/api/x", POST);
    assert.equal(fetchFn.calls.length, 2);
});

test("a retried request is announced as a retry, with its status", async () => {
    const fetchFn = scriptedFetch([response(503), response(200)]);
    const { drive, statuses } = loadDriver(fetchFn, policyFor(false));

    await drive("/api/x", POST);

    const retry = statuses.find((s) => /reintent/i.test(s.text));
    assert.ok(retry, "a retry happened but the user was never told");
    assert.match(retry.text, /503/, "the retry notice does not carry the status");
});

// ─── 3. Retry-After is honoured ────────────────────────────────────────────

test("a 429 with Retry-After waits at least that long", async () => {
    const fetchFn = scriptedFetch([
        response(429, { headers: { "Retry-After": "3" } }),
        response(200),
    ]);
    const { drive, timer } = loadDriver(fetchFn, policyFor(false));

    await drive("/api/x", POST);

    assert.equal(timer.delays.length, 1, "the 429 scheduled no wait");
    assert.ok(
        timer.delays[0] >= 3000,
        `Retry-After: 3 was ignored in favour of the local schedule (waited ${timer.delays[0]}ms)`,
    );
});

test("Retry-After is honoured over a longer local backoff", async () => {
    const fetchFn = scriptedFetch([
        response(429, { headers: { "Retry-After": "5" } }),
        response(200),
    ]);
    const { drive, timer } = loadDriver(fetchFn, policyFor(false));

    await drive("/api/x", POST);

    assert.ok(
        timer.delays[0] >= 5000,
        `Retry-After: 5 was shortened to ${timer.delays[0]}ms by the local schedule`,
    );
});

test("a Retry-After longer than the whole budget is refused, not waited out", async () => {
    // Waiting an hour with the mic held would be worse than failing.
    const fetchFn = scriptedFetch([
        response(429, { headers: { "Retry-After": "3600" } }),
        response(200),
    ]);
    const { drive, timer } = loadDriver(fetchFn, policyFor(false));

    const err = await expectFailure(drive("/api/x", POST));

    assert.equal(fetchFn.calls.length, 1, "an hour-long Retry-After was waited on");
    assert.deepEqual(timer.delays, []);
    assert.equal(err.status, 429);
});

test("a Retry-After HTTP-date is understood", async () => {
    const when = new Date(Date.now() + 4000).toUTCString();
    const fetchFn = scriptedFetch([
        response(429, { headers: { "Retry-After": when } }),
        response(200),
    ]);
    const { drive, timer } = loadDriver(fetchFn, policyFor(false));

    await drive("/api/x", POST);

    assert.ok(
        timer.delays[0] >= 2000,
        `an HTTP-date Retry-After was not honoured (waited ${timer.delays[0]}ms)`,
    );
});

// ─── 4. Total elapsed time is bounded, not just the attempt count ──────────

test("the total wait never exceeds the documented bound", async () => {
    const policy = policyFor(false);
    const bound = policy.totalBudgetMs();

    assert.equal(bound, 15000, "the documented bound moved; the report must follow it");

    // Drive the worst case: a 503 forever. Every delay the policy grants must
    // still fit inside the bound.
    const fetchFn = scriptedFetch(new Array(12).fill(response(503)));
    const { drive, timer } = loadDriver(fetchFn, policy);

    await expectFailure(drive("/api/x", POST));

    const total = timer.delays.reduce((a, b) => a + b, 0);
    assert.ok(total <= bound, `total wait ${total}ms exceeded the ${bound}ms bound`);
    assert.ok(fetchFn.calls.length <= 5, `${fetchFn.calls.length} attempts is more than the bound allows`);
});

test("the schedule itself sums to at most the bound", () => {
    const policy = policyFor(false);
    const sum = policy.schedule().reduce((a, b) => a + b, 0);
    assert.ok(sum <= policy.totalBudgetMs(), `schedule sums to ${sum}ms`);
});

test("an old 30s-per-delay schedule is gone", () => {
    // 1s, 2s, 4s, 8s, 16s, 30s reached 63s of holding. The old code doubled
    // into a 30s ceiling with no total bound at all.
    const policy = policyFor(false);
    for (const d of policy.schedule()) {
        assert.ok(d <= 8000, `a ${d}ms delay is back in the schedule`);
    }
});

// ─── 5. The ambiguity of a non-idempotent POST ─────────────────────────────

test("a network failure on the message POST is not silently replayed", async () => {
    // fetch() rejects with a bare TypeError and, by design, will not say
    // whether the request ever left the client. Here it may have been
    // received, the turn committed, and only the response lost — so a blind
    // replay can produce a duplicate turn.
    const fetchFn = scriptedFetch([new TypeError("Failed to fetch")]);
    const { drive } = loadDriver(fetchFn, policyFor(false));

    const err = await expectFailure(drive("/api/x", POST));

    assert.equal(fetchFn.calls.length, 1, "an ambiguous failure was replayed automatically");
    assert.equal(err.ambiguous, true, "the failure is not flagged as ambiguous");
    assert.match(err.message, /reintent|pulsa|micro/i, "the user is not told what to do");
});

test("the ambiguity is explained, not buried", async () => {
    const fetchFn = scriptedFetch([new TypeError("Failed to fetch")]);
    const { drive } = loadDriver(fetchFn, policyFor(false));

    const err = await expectFailure(drive("/api/x", POST));

    assert.match(
        err.message,
        /no se sabe|puede que|unknown/i,
        "the message does not say the outcome is unknown",
    );
});

test("a network failure on a read-only request is still retried", async () => {
    // Backoff exists for a reason. A GET that only reads cannot duplicate
    // anything, so it gets the full schedule.
    const fetchFn = scriptedFetch([new TypeError("Failed to fetch"), response(200)]);
    const { drive } = loadDriver(fetchFn, policyFor(true));

    const res = await drive("/api/x", { method: "GET" });

    assert.equal(res.status, 200);
    assert.equal(fetchFn.calls.length, 2, "a safe request was not retried");
});

test("a network failure is announced as a connection problem", async () => {
    const fetchFn = scriptedFetch([new TypeError("Failed to fetch"), response(200)]);
    const { drive, statuses } = loadDriver(fetchFn, policyFor(true));

    await drive("/api/x", { method: "GET" });

    const notice = statuses.find((s) => /reintent/i.test(s.text));
    assert.ok(notice, "no retry notice");
    assert.match(notice.text, /conexi|conexión|red/i, "a network failure is not named as one");
});

test("a write that the server explicitly declined is still retried", async () => {
    // A 503 means the request reached the server and the server chose to
    // reject it. For /message/stream that is safe: the handler returns
    // StreamingResponse, so the status is committed before the pipeline runs
    // and no turn was written.
    const fetchFn = scriptedFetch([response(503), response(200)]);
    const { drive } = loadDriver(fetchFn, policyFor(false));

    await drive("/api/x", POST);
    assert.equal(fetchFn.calls.length, 2, "an explicit 503 was not retried");
});

test("exhausting the retries surfaces the last real status", async () => {
    const fetchFn = scriptedFetch(new Array(8).fill(response(503)));
    const { drive } = loadDriver(fetchFn, policyFor(false));

    const err = await expectFailure(drive("/api/x", POST));

    assert.equal(err.status, 503, "the real status was replaced by a generic error");
});

// ─── 6. The policy itself is inspectable ───────────────────────────────────

test("the retryable set is exactly the transient statuses", () => {
    const policy = policyFor(false);
    const retryable = [...policy.retryableStatuses()].sort((a, b) => a - b);
    assert.deepEqual(retryable, [408, 429, 500, 502, 503, 504]);
});

test("no 4xx other than 408 and 429 is retryable", () => {
    const policy = policyFor(false);
    for (let status = 400; status < 500; status++) {
        const expected = status === 408 || status === 429;
        assert.equal(
            policy.isRetryableStatus(status),
            expected,
            `HTTP ${status} classified as retryable=${!expected}`,
        );
    }
});

test("a successful response is returned untouched", async () => {
    const ok = response(200);
    const fetchFn = scriptedFetch([ok]);
    const { drive } = loadDriver(fetchFn, policyFor(false));

    assert.equal(await drive("/api/x", POST), ok);
    assert.equal(fetchFn.calls.length, 1);
});

// ─── 7. The wiring, verified structurally ──────────────────────────────────
//
// The driver tests above inject their own policy, so nothing behavioural
// would notice the production call site being handed the wrong one. That is
// the one substitution that would silently reintroduce duplicate turns, so it
// is pinned here.

test("the message POST is wired to the write-once policy", () => {
    const source = readAppJs();
    const at = source.indexOf("await fetchWithBackoff(");
    assert.notEqual(at, -1, "the message POST no longer goes through fetchWithBackoff");
    const call = source.slice(at, at + 400);

    assert.match(
        call,
        /writeOncePolicy/,
        "the message POST persists a turn; it must not use the replay-safe policy",
    );
});

test("no call site relies on an implicit default policy", () => {
    // fetchWithBackoff takes `policy` as a required argument on purpose: a
    // default would let a new call site pick a replay policy by accident.
    const source = readAppJs();
    const calls = source.match(/fetchWithBackoff\(/g) || [];
    const declarations = (source.match(/function fetchWithBackoff\(/g) || []).length;

    assert.equal(calls.length - declarations, 1, "unexpected number of call sites");
    assert.doesNotMatch(
        source,
        /function fetchWithBackoff\([^)]*=\s*\{/,
        "fetchWithBackoff grew a default policy",
    );
});
