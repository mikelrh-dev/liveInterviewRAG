/**
 * Shared helpers for the Node frontend suites.
 *
 * app.js is a browser script that touches `document` at module scope, so it
 * cannot be imported here. Instead a unit is lifted out of the real source by
 * name and evaluated in isolation with stand-ins for the globals it reads.
 * That keeps every assertion honest — the code under test is the shipped code,
 * not a copy that can drift.
 *
 * These helpers are NOT a test file (`harness.mjs`, not `*.test.mjs`), so
 * `node --test "tests/frontend/*.test.mjs"` never treats them as a suite.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export const FRONTEND_DIR = join(here, "..", "..", "frontend");
export const APP_JS = join(FRONTEND_DIR, "app.js");
export const INDEX_HTML = join(FRONTEND_DIR, "index.html");

export const readAppJs = () => readFileSync(APP_JS, "utf8");
export const readIndexHtml = () => readFileSync(INDEX_HTML, "utf8");

/**
 * Pull a top-level function declaration out of app.js by name.
 *
 * Brace counting is naive on purpose — it is only ever pointed at functions
 * whose string literals contain balanced braces (no `${}` interpolation inside
 * an extracted unit). Adding a template literal with an unbalanced brace to a
 * lifted function will silently truncate the extraction, so prefer `+`.
 */
export function extractFunction(name, source = readAppJs()) {
    const signature = `function ${name}(`;
    const start = source.indexOf(signature);
    assert.notEqual(start, -1, `${name}() not found in frontend/app.js`);

    const brace = source.indexOf("{", start);
    let depth = 0;
    for (let i = brace; i < source.length; i++) {
        if (source[i] === "{") depth++;
        else if (source[i] === "}") {
            depth--;
            if (depth === 0) return source.slice(start, i + 1);
        }
    }
    throw new Error(`unbalanced braces extracting ${name}()`);
}

/**
 * Evaluate an extracted function with stand-ins for the globals it reads.
 *
 * The parameter names ARE the global names, so the extracted source resolves
 * them to the injected values instead of reaching for the real ones. The
 * result is a *factory*: call it with the unit's own arguments.
 */
export function loadWithGlobals(name, globals) {
    const names = Object.keys(globals);
    const factory = new Function(
        ...names,
        `${extractFunction(name)}; return ${name};`,
    );
    return factory(...names.map((key) => globals[key]));
}
