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
import { usageDetails, usageWindowDetail, windowLabel } from "../src/bands";

const HOUR = 3_600_000;
const WEEK_SECONDS = 604_800;
// Local-time construction keeps the reset formatting independent of the
// machine's time zone.
const NOW = new Date(2026, 9, 14, 10, 0).getTime();

describe("windowLabel", () => {
  test("derives the label from the window length", () => {
    expect(windowLabel(18_000)).toBe("5h");
    expect(windowLabel(WEEK_SECONDS)).toBe("7d");
    expect(windowLabel(90)).toBe("2m");
    expect(windowLabel(45)).toBe("45s");
    expect(windowLabel(0.5)).toBe("1s");
  });

  test("has no label for a missing or unusable length", () => {
    for (const value of [undefined, 0, -60, NaN, Infinity]) expect(windowLabel(value)).toBeUndefined();
  });
});

describe("usageWindowDetail", () => {
  test("uses absolute bands without a reset time", () => {
    const band = (usedPercent: number) => usageWindowDetail({ usedPercent, windowSeconds: WEEK_SECONDS }, NOW)?.band;
    expect([band(49), band(50), band(69), band(70), band(89), band(90)]).toEqual(["green", "yellow", "yellow", "orange", "orange", "red"]);
  });

  test("uses absolute bands without a window length", () => {
    expect(usageWindowDetail({ usedPercent: 95, resetAt: NOW + HOUR }, NOW)).toEqual({ percent: 95, band: "red", reset: "11:00" });
  });

  test("uses pace bands when the length and reset are known", () => {
    // Half of a 10-hour window has elapsed.
    const band = (usedPercent: number) =>
      usageWindowDetail({ usedPercent, resetAt: NOW + 5 * HOUR, windowSeconds: 36_000 }, NOW)?.band;
    expect([band(40), band(42.4), band(42.5), band(52), band(52.5), band(90)]).toEqual(["green", "green", "orange", "orange", "red", "red"]);
  });

  test("is green in the first 5% of a window, whatever the usage", () => {
    expect(usageWindowDetail({ usedPercent: 100, resetAt: NOW + 9.6 * HOUR, windowSeconds: 36_000 }, NOW)?.band).toBe("green");
  });

  test("falls back to absolute bands once the reset time has passed", () => {
    expect(usageWindowDetail({ usedPercent: 10, resetAt: NOW - HOUR, windowSeconds: 36_000 }, NOW)?.band).toBe("green");
    expect(usageWindowDetail({ usedPercent: 75, resetAt: NOW - HOUR, windowSeconds: 36_000 }, NOW)?.band).toBe("orange");
  });

  test("reads the injected clock, not the real one", () => {
    const window = { usedPercent: 60, resetAt: NOW + 5 * HOUR, windowSeconds: 36_000 };
    expect(usageWindowDetail(window, NOW)?.band).toBe("red");
    expect(usageWindowDetail(window, NOW + 4 * HOUR)?.band).toBe("green");
  });

  test("shows a reset today as HH:MM and any other day with its date", () => {
    const today = usageWindowDetail({ usedPercent: 1, resetAt: new Date(2026, 9, 14, 23, 5).getTime() }, NOW);
    const tomorrow = usageWindowDetail({ usedPercent: 1, resetAt: new Date(2026, 9, 15, 9, 5).getTime() }, NOW);
    expect(today?.reset).toBe("23:05");
    expect(tomorrow?.reset).toMatch(/^\S+ 15, 09:05$/);
  });

  test("rounds the percentage and labels the window", () => {
    expect(usageWindowDetail({ usedPercent: 41.6, windowSeconds: 18_000 }, NOW)).toEqual({ label: "5h", percent: 42, band: "green" });
  });

  test("drops a window with no usable percentage", () => {
    expect(usageWindowDetail({ usedPercent: NaN }, NOW)).toBeUndefined();
    expect(usageDetails([{ usedPercent: NaN }, { usedPercent: 3 }], NOW)).toEqual([{ percent: 3, band: "green" }]);
  });
});
