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

// OpenCode 2 ignores `engines` (docs/opencode-v2-integration.md, V4/V5), so
// an older 2.0.x loads this plugin anyway; on 2.0.17, for one,
// `client.credential.list` is undefined at runtime (V14). Every context member
// the plugin calls is checked before anything is registered.

// F6: the oldest 2.0.x that installs and has every API used (V14).
export const MIN_OPENCODE_VERSION = "2.0.20";

const FUNCTIONS = [
  "client.credential.list",
  "client.credential.activate",
  "data.on",
  "data.session.get",
  "data.session.status",
  "data.session.message.list",
  "data.location.provider.list",
  "data.location.integration.list",
  "keymap.layer",
  "ui.slot",
  "ui.toast.show",
  "ui.dialog.select",
  "ui.dialog.confirm",
  "ui.model.current",
  "ui.router.current"
];

const OBJECTS = ["theme.text.feedback"];

function lookup(root: unknown, path: string): unknown {
  let value = root;
  for (const name of path.split(".")) {
    if ((typeof value !== "object" && typeof value !== "function") || value === null) return undefined;
    value = (value as Record<string, unknown>)[name];
  }
  return value;
}

// The context members this plugin needs that `context` lacks; empty when it
// can run.
export function missingFeatures(context: unknown): string[] {
  return [
    ...FUNCTIONS.filter((path) => typeof lookup(context, path) !== "function"),
    ...OBJECTS.filter((path) => {
      const value = lookup(context, path);
      return typeof value !== "object" || value === null;
    })
  ];
}
