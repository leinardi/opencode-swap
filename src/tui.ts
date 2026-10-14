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

// opencode-swap is discontinued; this release only says so. It exists because
// OpenCode 2 copies OpenCode 1's tui.json plugins into cli.json on its first
// start, turning `"plugin_enabled": {"opencode-swap": true}` into a bare
// "opencode-swap" entry (packages/cli/src/config/migrate.ts:160-166 in
// 2.0.29). A bare entry only enables a loaded plugin whose id matches;
// otherwise OpenCode installs it from npm (packages/tui/src/plugin/context.tsx:318-350),
// where "opencode-swap" is an unrelated package. The 0.4.x plugin never loads
// on OpenCode 2, so this one must: same id, nothing else, and no access to
// credentials.

import type { Plugin } from "@opencode/plugin/tui";

export const PLUGIN_ID = "opencode-swap";

export const DISCONTINUED_MESSAGE =
  "opencode-swap is discontinued. Remove both \"@leinardi/opencode-swap\" and \"opencode-swap\" from " +
  "\"plugins\" in ~/.config/opencode/cli.json. OpenCode 2 switches accounts itself (/connect); " +
  "for quota, see @slkiser/opencode-quota.";

function setup(context: Plugin.Context): undefined {
  try {
    context.ui.toast.show({ variant: "warning", message: DISCONTINUED_MESSAGE });
  } catch {
    // Without a working toast there is nothing left to tell the user with.
  }
  return undefined;
}

const plugin: Plugin.Definition = { id: PLUGIN_ID, setup };

export default plugin;
