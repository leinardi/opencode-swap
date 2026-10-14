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
import { debugEnabled, parseOptions, usageEnabled } from "../src/options";

describe("usageEnabled", () => {
  test("is on by default", () => {
    expect(usageEnabled(undefined)).toBe(true);
    expect(usageEnabled({})).toBe(true);
  });

  test("is off for false, falsy values and the disabling strings", () => {
    for (const usage of [false, 0, "", null, "false", "FALSE", " no ", "Off", "0"]) expect(usageEnabled({ usage })).toBe(false);
  });

  test("stays on for truthy values and other strings", () => {
    for (const usage of [true, 1, "true", "yes", "on", "nope", {}]) expect(usageEnabled({ usage })).toBe(true);
  });
});

describe("debugEnabled", () => {
  test("is on only for the boolean true", () => {
    expect(debugEnabled({ debug: true })).toBe(true);
    for (const debug of [undefined, false, "true", 1]) expect(debugEnabled({ debug })).toBe(false);
    expect(debugEnabled(undefined)).toBe(false);
  });
});

test("parseOptions combines both", () => {
  expect(parseOptions({ usage: "off", debug: true })).toEqual({ usage: false, debug: true });
  expect(parseOptions(undefined)).toEqual({ usage: true, debug: false });
});
