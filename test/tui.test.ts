/*
 * MIT License
 *
 * Copyright (c) 2026 Roberto Leinardi
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

import { describe, expect, test } from "bun:test";
import type { Plugin } from "@opencode/plugin/tui";
import plugin, { DISCONTINUED_MESSAGE, PLUGIN_ID } from "../src/tui";

type Toast = { variant: string, message: string }

// A context that records toasts and throws on anything else it is asked for,
// so a test fails if the notice ever reaches past `ui.toast`.
function recordingContext(show: (toast: Toast) => void): { context: Plugin.Context, touched: string[] } {
  const touched: string[] = [];
  const guard = (path: string, value: object): object =>
    new Proxy(value, {
      get(target, key) {
        const name = `${path}.${String(key)}`;
        touched.push(name);
        if (key in target) return (target as Record<PropertyKey, unknown>)[key];
        throw new Error(`the notice must not read ${name}`);
      }
    });
  const context = guard("context", { ui: guard("context.ui", { toast: guard("context.ui.toast", { show }) }) });
  return { context: context as unknown as Plugin.Context, touched };
}

describe("the discontinuation notice", () => {
  test("keeps the 0.4.x plugin's id, so a migrated bare entry enables it instead of installing from npm", () => {
    expect(plugin.id).toBe("opencode-swap");
    expect(PLUGIN_ID).toBe("opencode-swap");
  });

  test("shows one warning naming both entries to remove", () => {
    const toasts: Toast[] = [];
    const { context, touched } = recordingContext((toast) => toasts.push(toast));

    expect(plugin.setup(context)).toBeUndefined();

    expect(toasts).toEqual([{ variant: "warning", message: DISCONTINUED_MESSAGE }]);
    expect(DISCONTINUED_MESSAGE).toContain("\"@leinardi/opencode-swap\"");
    expect(DISCONTINUED_MESSAGE).toContain("\"opencode-swap\"");
    expect(touched).toEqual(["context.ui", "context.ui.toast", "context.ui.toast.show"]);
  });

  test("still loads when the toast fails", () => {
    const { context } = recordingContext(() => {
      throw new Error("no toast here");
    });

    expect(() => plugin.setup(context)).not.toThrow();
  });
});
