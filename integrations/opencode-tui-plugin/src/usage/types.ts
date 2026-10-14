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

export type UsageWindow = {
  readonly usedPercent: number
  // Epoch milliseconds.
  readonly resetAt?: number
  readonly windowSeconds?: number
}

// `message` is always one of this module family's fixed strings (or "HTTP
// <status>"), never text from an exception, a response body or a header, so
// it can be shown as is without leaking the credential the request carried.
export type UsageSnapshot =
  | { readonly available: true, readonly planName?: string, readonly windows: readonly UsageWindow[] }
  | { readonly available: false, readonly message: string, readonly status?: number }

export type FetchLike = (url: string, init: RequestInit) => Promise<Response>

export type RequestOptions = {
  readonly fetchImpl?: FetchLike
  readonly signal?: AbortSignal
  readonly timeoutMs?: number
}

export const UNEXPECTED_SHAPE = "unexpected response shape";

// 9999-12-31: past this a reset time is not a date worth rendering.
const MAX_RESET_AT_MILLIS = 253_402_300_000_000;

export function unavailable(message: string, status?: number): UsageSnapshot {
  return status === undefined ? { available: false, message } : { available: false, message, status };
}

export function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

// A `used_percent`/`percentage` value in 0-100, or undefined: the signal both
// providers' parsers use to tell a quota window from an unrelated sibling entry.
export function usedPercent(value: unknown): number | undefined {
  return finiteNumber(value) && value >= 0 && value <= 100 ? value : undefined;
}

export function resetAtMillis(value: unknown): number | undefined {
  if (!finiteNumber(value) || value < 0 || value > MAX_RESET_AT_MILLIS) return undefined;
  // opencode-balancer's heuristic: values already in the millisecond range
  // pass through, smaller ones are seconds.
  const millis = value > 1_000_000_000_000 ? value : value * 1000;
  return millis <= MAX_RESET_AT_MILLIS ? millis : undefined;
}

export function usageWindow(percent: number, resetAt: number | undefined, windowSeconds: number | undefined): UsageWindow {
  return {
    usedPercent: percent,
    ...(resetAt === undefined ? {} : { resetAt }),
    ...(windowSeconds === undefined ? {} : { windowSeconds })
  };
}
