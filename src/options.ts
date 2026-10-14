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

export type PluginOptions = {
  readonly usage: boolean
  readonly debug: boolean
}

// Usage lookups send an account's own OpenAI access token to chatgpt.com, or
// its z.ai API key to api.z.ai. On by default; `{ "usage": false }` disables
// the requests entirely. Any JS-falsy value (0, "", null) and the strings
// "false"/"no"/"off"/"0" (any case, surrounding whitespace tolerated) also
// disable them: plugin options come from user-edited JSON/JSONC, and honouring
// only the exact boolean `false` would keep the egress on for a plausible typo.
export function usageEnabled(options: Record<string, unknown> | undefined): boolean {
  const value = options?.usage;
  if (value === undefined) return true;
  if (typeof value === "string") {
    return !["", "false", "no", "off", "0"].includes(value.trim().toLowerCase());
  }
  return Boolean(value);
}

// Off unless explicitly `true`: the opposite default of `usage`, since turning
// diagnostics on by a typo is the surprise here.
export function debugEnabled(options: Record<string, unknown> | undefined): boolean {
  return options?.debug === true;
}

export function parseOptions(options: Record<string, unknown> | undefined): PluginOptions {
  return { usage: usageEnabled(options), debug: debugEnabled(options) };
}
