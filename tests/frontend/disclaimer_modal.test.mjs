/**
 * A dialog that promises modality has to deliver it.
 *
 * THE DEFECT
 * ----------
 * `#disclaimer-overlay` contains a card with `role="dialog"
 * aria-modal="true"`, and neither claim was true:
 *
 *   - Nothing took focus. A keyboard user's first Tab after page load landed
 *     wherever the document happened to be, not in the dialog.
 *   - Nothing trapped focus. Tab walked straight out of the dialog into the
 *     page behind it.
 *   - Nothing was inert, so END, Contexto, the transcript and the close button
 *     all stayed in the tab order behind a dialog that had promised they were
 *     not.
 *
 * `aria-modal` is a promise about behaviour, not a description of markup.
 * Assistive technology is entitled to suppress the rest of the page on the
 * strength of it, and then a user with a keyboard is in a context they cannot
 * leave. The interview cannot be started until the gate is acknowledged, so the
 * page is not merely cluttered behind the dialog -- it is unusable without it.
 *
 * WHAT IS TESTED AND HOW
 * ---------------------
 * Focus movement and the focus trap are behavioural: a real keydown on the real
 * document, asserting where `document.activeElement` ended up.
 *
 * `inert` is asserted as the attribute it is. jsdom 26 has no inert semantics
 * -- an element with the attribute is still focusable -- so "focus is blocked"
 * cannot be observed here and is not claimed. The trap and the focus move are
 * what actually keep a keyboard user in the dialog; `inert` is the mechanism
 * that also removes the rest of the page from the accessibility tree and from
 * pointer interaction, and it is the part a browser enforces for free.
 *
 *   node --test "tests/frontend/*.test.mjs"
 */

import test from "node:test";
import assert from "node:assert/strict";
import { createDom } from "./dom.mjs";

/** A window with the disclaimer gate loaded, not yet acknowledged. */
function gateEnv({ acknowledged = false } = {}) {
    const env = createDom();
    env.window.localStorage.setItem("interviewtts.disclaimerAccepted", acknowledged ? "1" : "0");
    const fn = env.loadApp(["initDisclaimer"]);
    return { env, fn };
}

const TAB = (shift = false) => ({ key: "Tab", shiftKey: shift });

function pressTab(env, shift = false) {
    const event = new env.window.KeyboardEvent("keydown", {
        ...TAB(shift),
        bubbles: true,
        cancelable: true,
    });
    env.document.dispatchEvent(event);
    return event;
}

test("the gate takes focus when it opens", () => {
    const { env, fn } = gateEnv();
    fn.initDisclaimer();

    assert.equal(
        env.document.activeElement,
        env.document.getElementById("disclaimer-accept"),
        `focus is on ${env.document.activeElement && env.document.activeElement.id || "nothing"}. ` +
            "A dialog that does not take focus leaves a keyboard user's next Tab " +
            "to land wherever the document happens to be",
    );
    env.close();
});

test("Tab cannot leave the dialog", () => {
    const { env, fn } = gateEnv();
    fn.initDisclaimer();

    const event = pressTab(env);
    const accept = env.document.getElementById("disclaimer-accept");

    assert.equal(
        env.document.activeElement,
        accept,
        `Tab moved focus to ${env.document.activeElement && env.document.activeElement.id || "nothing"}, ` +
            "which is outside a dialog marked aria-modal",
    );
    assert.equal(
        event.defaultPrevented,
        true,
        "the Tab was not cancelled, so the browser will move focus itself and " +
            "the handler is only racing it",
    );
    env.close();
});

test("Shift+Tab cannot leave the dialog either", () => {
    const { env, fn } = gateEnv();
    fn.initDisclaimer();

    pressTab(env, true);

    assert.equal(
        env.document.activeElement,
        env.document.getElementById("disclaimer-accept"),
        "Shift+Tab escaped a dialog marked aria-modal",
    );
    env.close();
});

test("focus that lands outside is pulled back into the dialog", () => {
    // The other direction, and the one that matters after any change to the
    // markup: if focus ends up behind the dialog, the next Tab has to bring it
    // in rather than leave it there.
    const { env, fn } = gateEnv();
    fn.initDisclaimer();

    const end = env.document.getElementById("mobile-end-btn");
    end.focus();
    assert.equal(env.document.activeElement, end, "precondition: focus is behind the dialog");

    pressTab(env);

    assert.equal(
        env.document.activeElement,
        env.document.getElementById("disclaimer-accept"),
        "focus was left behind a dialog marked aria-modal",
    );
    env.close();
});

test("the page behind the dialog is inert while it is open", () => {
    // Asserted as the attribute, because jsdom has no inert semantics. The
    // focus trap above is what actually holds a keyboard user in the dialog;
    // this is the part that also removes the rest of the page from the
    // accessibility tree, which is what aria-modal is asking for.
    const { env, fn } = gateEnv();

    assert.equal(
        env.document.querySelectorAll("[inert]").length,
        0,
        "precondition: nothing is inert before the gate opens",
    );

    fn.initDisclaimer();

    const stillReachable = [...env.document.body.children].filter(
        (el) =>
            el !== env.document.getElementById("disclaimer-overlay") &&
            !el.hasAttribute("inert"),
    );
    assert.deepEqual(
        stillReachable.map((el) => el.id || el.className),
        [],
        `these regions are still in the tab order behind an aria-modal dialog: ` +
            stillReachable.map((el) => el.id || el.className).join(", "),
    );
    assert.equal(
        env.document.getElementById("disclaimer-overlay").hasAttribute("inert"),
        false,
        "the dialog itself was made inert, so it cannot be reached at all",
    );
    env.close();
});

test("acknowledging the gate gives the page back", () => {
    const { env, fn } = gateEnv();
    fn.initDisclaimer();

    env.document
        .getElementById("disclaimer-accept")
        .dispatchEvent(new env.window.MouseEvent("click", { bubbles: true }));

    assert.equal(
        env.document.getElementById("disclaimer-overlay").classList.contains("hidden"),
        true,
        "the gate is still on screen after being acknowledged",
    );
    const stillInert = [...env.document.querySelectorAll("[inert]")];
    assert.deepEqual(
        stillInert.map((el) => el.id || el.className),
        [],
        "the page is still inert after the gate was dismissed: the interview " +
            "can be started but nothing on the page can be reached",
    );
    assert.equal(
        env.document.getElementById("btn-mic").disabled,
        false,
        "the mic is still locked after the gate was acknowledged, so the " +
            "interview cannot be started at all",
    );
    env.close();
});

test("the trap stops trapping once the gate is dismissed", () => {
    // The other direction of the fix: a trap that keeps running after the
    // dialog closes is a page nobody can tab through.
    const { env, fn } = gateEnv();
    fn.initDisclaimer();
    env.document
        .getElementById("disclaimer-accept")
        .dispatchEvent(new env.window.MouseEvent("click", { bubbles: true }));

    const end = env.document.getElementById("mobile-end-btn");
    end.focus();
    const event = pressTab(env);

    assert.equal(
        event.defaultPrevented,
        false,
        "Tab is still being cancelled after the dialog is gone: the trap is " +
            "holding focus on a control the user can no longer see",
    );
    env.close();
});

test("a visitor who already acknowledged is not gated again", () => {
    // Precondition of the fix: the gate must not fire for a returning visitor,
    // or the trap would strand a user who never sees the dialog.
    const { env, fn } = gateEnv({ acknowledged: true });

    fn.initDisclaimer();

    assert.equal(
        env.document.getElementById("disclaimer-overlay").classList.contains("hidden"),
        true,
        "a returning visitor is shown the gate again",
    );
    assert.equal(
        env.document.querySelectorAll("[inert]").length,
        0,
        "the page was made inert for a visitor who never sees the dialog",
    );
    env.close();
});
