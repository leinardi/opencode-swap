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

import type { UsageWindow } from "./usage/types";

export type Band = "green" | "yellow" | "orange" | "red"

export type UsageDetail = {
  readonly label?: string
  readonly percent: number
  readonly band: Band
  readonly reset?: string
}

// A short duration label ("5h", "7d", ...) derived from the window's own
// length, with no table of known windows, so an unfamiliar or changed length
// still gets a sensible label.
export function windowLabel(windowSeconds: number | undefined): string | undefined {
  if (typeof windowSeconds !== "number" || !Number.isFinite(windowSeconds) || windowSeconds <= 0) return undefined;
  const units: [number, string][] = [
    [86_400, "d"],
    [3600, "h"],
    [60, "m"],
    [1, "s"]
  ];
  const [unitSeconds, suffix] = units.find(([seconds]) => windowSeconds >= seconds) ?? [1, "s"];
  return `${Math.round(windowSeconds / unitSeconds)}${suffix}`;
}

// The band is pace-based when the window's length and reset time are known:
// usage compared with the share of the window already elapsed, so 60% used
// with 90% of the window gone is fine and 60% used after 10% is not. Otherwise
// it falls back to absolute thresholds. `now` is epoch milliseconds.
export function usageWindowDetail(window: UsageWindow, now: number): UsageDetail | undefined {
  if (!Number.isFinite(window.usedPercent)) return undefined;

  const percent = Math.round(window.usedPercent);
  const absoluteBand: Band = percent >= 90 ? "red" : percent >= 70 ? "orange" : percent >= 50 ? "yellow" : "green";
  const label = windowLabel(window.windowSeconds);
  const resetAt = window.resetAt;
  if (typeof resetAt !== "number" || !Number.isFinite(resetAt)) return { label, percent, band: absoluteBand };

  const windowSeconds = window.windowSeconds;
  let band = absoluteBand;
  if (typeof windowSeconds === "number" && Number.isFinite(windowSeconds) && windowSeconds > 0) {
    const windowMs = windowSeconds * 1000;
    const elapsedPercent = ((windowMs - (resetAt - now)) / windowMs) * 100;
    if (elapsedPercent < 5) band = "green";
    else if (elapsedPercent <= 100) {
      const ratio = (window.usedPercent / elapsedPercent) * 100;
      band = ratio < 85 ? "green" : ratio < 105 ? "orange" : "red";
    }
  }
  const date = new Date(resetAt);
  if (Number.isNaN(date.getTime())) return { label, percent, band };
  // A reset on today's local date renders as bare "HH:MM"; any other date
  // keeps the date, so tomorrow morning (under 24h away, but not today) never
  // reads as "today".
  const hhmm = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  const today = new Date(now);
  if (date.getFullYear() === today.getFullYear() && date.getMonth() === today.getMonth() && date.getDate() === today.getDate()) {
    return { label, percent, band, reset: hhmm };
  }
  const month = date.toLocaleString(undefined, { month: "short" });
  return { label, percent, band, reset: `${month} ${date.getDate()}, ${hhmm}` };
}

export function usageDetails(windows: readonly UsageWindow[], now: number): UsageDetail[] {
  return windows.map((window) => usageWindowDetail(window, now)).filter((detail): detail is UsageDetail => detail !== undefined);
}
