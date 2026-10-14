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

// Port of the OpenAI half of the Python CLI's tests/test_usage.py.

import { describe, expect, test } from "bun:test";
import { CHATGPT_USAGE_URL, fetchOpenAIUsage, parseOpenAIUsage } from "../src/usage/openai";
import type { UsageSnapshot } from "../src/usage/types";
import { fakeFetch, header, jsonResponse, textResponse } from "./helpers";

function payload(overrides: Record<string, unknown> = {}) {
  return {
    plan_type: "plus",
    rate_limit: {
      primary_window: { used_percent: 42, reset_at: 1785500000, limit_window_seconds: 604800 },
      secondary_window: { used_percent: 49, reset_at: 1785400000, limit_window_seconds: 18000 }
    },
    ...overrides
  };
}

async function fetchWith(body: unknown, status = 200): Promise<UsageSnapshot> {
  const { impl } = fakeFetch(() => jsonResponse(body, status));
  return fetchOpenAIUsage("access-token", undefined, { fetchImpl: impl });
}

function windows(snapshot: UsageSnapshot) {
  if (!snapshot.available) throw new Error(`unavailable: ${snapshot.message}`);
  return snapshot.windows;
}

describe("fetchOpenAIUsage", () => {
  test("extracts both windows, shortest first, resets in milliseconds", async () => {
    const { impl, calls } = fakeFetch(() => jsonResponse(payload()));
    const snapshot = await fetchOpenAIUsage("access-token", "acct-1", { fetchImpl: impl });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(CHATGPT_USAGE_URL);
    expect(snapshot).toEqual({
      available: true,
      planName: "ChatGPT Plus",
      windows: [
        { usedPercent: 49, resetAt: 1785400000 * 1000, windowSeconds: 18000 },
        { usedPercent: 42, resetAt: 1785500000 * 1000, windowSeconds: 604800 }
      ]
    });
  });

  test("sends the bearer token, and the account id header only when known", async () => {
    const { impl, calls } = fakeFetch(() => jsonResponse(payload()));
    await fetchOpenAIUsage("access-token", "acct-1", { fetchImpl: impl });
    await fetchOpenAIUsage("access-token", undefined, { fetchImpl: impl });
    expect(header(calls[0], "authorization")).toBe("Bearer access-token");
    expect(header(calls[0], "chatgpt-account-id")).toBe("acct-1");
    expect(header(calls[0], "user-agent")).toBe("opencode-swap");
    expect(header(calls[1], "chatgpt-account-id")).toBeNull();
  });

  test("an HTTP error is unavailable and keeps its status", async () => {
    expect(await fetchWith({}, 401)).toEqual({ available: false, message: "HTTP 401", status: 401 });
  });

  test("a network error is unavailable", async () => {
    const { impl } = fakeFetch(() => {
      throw new TypeError("connection refused");
    });
    expect(await fetchOpenAIUsage("access-token", undefined, { fetchImpl: impl })).toEqual({
      available: false,
      message: "network error"
    });
  });

  test("malformed JSON, invalid UTF-8 and a non-object body are unavailable", async () => {
    const respond = (response: Response) => fakeFetch(() => response).impl;
    const notJson = await fetchOpenAIUsage("t", undefined, { fetchImpl: respond(textResponse("not json")) });
    const badUtf8 = await fetchOpenAIUsage("t", undefined, { fetchImpl: respond(new Response(new Uint8Array([0xff]))) });
    const list = await fetchOpenAIUsage("t", undefined, { fetchImpl: respond(jsonResponse([1, 2])) });
    expect(notJson).toEqual({ available: false, message: "invalid JSON in response" });
    expect(badUtf8).toEqual({ available: false, message: "invalid JSON in response" });
    expect(list).toEqual({ available: false, message: "unexpected response shape" });
  });
});

describe("parseOpenAIUsage", () => {
  test("sorts by window length even when primary is the longer window", () => {
    expect(windows(parseOpenAIUsage(payload())).map((window) => window.windowSeconds)).toEqual([18000, 604800]);
  });

  test("discovers a third window under an unknown key", () => {
    const body = {
      rate_limit: {
        primary_window: { used_percent: 42, reset_at: 1785500000, limit_window_seconds: 604800 },
        tertiary_window: { used_percent: 5, reset_at: 1785300000, limit_window_seconds: 3600 }
      }
    };
    expect(windows(parseOpenAIUsage(body)).map((window) => window.windowSeconds)).toEqual([3600, 604800]);
  });

  test("a single window still works", () => {
    const body = { rate_limit: { primary_window: { used_percent: 42, reset_at: 1785500000, limit_window_seconds: 604800 } } };
    expect(windows(parseOpenAIUsage(body))).toEqual([{ usedPercent: 42, resetAt: 1785500000000, windowSeconds: 604800 }]);
  });

  test("skips rate_limit entries that are not windows", () => {
    const body = {
      rate_limit: {
        primary_window: { used_percent: 42, reset_at: 1785500000, limit_window_seconds: 604800 },
        some_flag: true,
        some_note: "not a window",
        empty_dict: {},
        a_list: [{ used_percent: 5 }]
      }
    };
    expect(windows(parseOpenAIUsage(body))).toHaveLength(1);
  });

  test("ignores the sibling code_review_rate_limit", () => {
    const body = {
      rate_limit: { primary_window: { used_percent: 42, reset_at: 1785500000, limit_window_seconds: 604800 } },
      code_review_rate_limit: { primary_window: { used_percent: 99, reset_at: 1785500000, limit_window_seconds: 604800 } }
    };
    expect(windows(parseOpenAIUsage(body)).map((window) => window.usedPercent)).toEqual([42]);
  });

  test("passes a reset already in milliseconds through", () => {
    const body = { rate_limit: { primary_window: { used_percent: 10, reset_at: 1785500000123 } } };
    expect(windows(parseOpenAIUsage(body))).toEqual([{ usedPercent: 10, resetAt: 1785500000123 }]);
  });

  test("windows without a duration sort last, ties by key", () => {
    const body = {
      rate_limit: {
        zeta: { used_percent: 3 },
        alpha: { used_percent: 2 },
        short: { used_percent: 1, limit_window_seconds: 60 }
      }
    };
    expect(windows(parseOpenAIUsage(body)).map((window) => window.usedPercent)).toEqual([1, 2, 3]);
  });

  test("an unknown plan type is not displayed", () => {
    const secret = "access-token-leaked-by-server";
    const snapshot = parseOpenAIUsage(payload({ plan_type: secret }));
    expect(snapshot.available && snapshot.planName).toBeUndefined();
    expect(JSON.stringify(snapshot)).not.toContain(secret);
    const inherited = parseOpenAIUsage(payload({ plan_type: "constructor" }));
    expect(inherited.available && inherited.planName).toBeUndefined();
  });

  test("plan names are matched case-insensitively", () => {
    const snapshot = parseOpenAIUsage(payload({ plan_type: " PRO " }));
    expect(snapshot.available && snapshot.planName).toBe("ChatGPT Pro");
  });

  test("a missing rate_limit is available with no windows", () => {
    expect(parseOpenAIUsage({})).toEqual({ available: true, windows: [] });
  });

  test("rejects non-finite, out-of-range and huge values", () => {
    const body = {
      rate_limit: {
        nan: { used_percent: NaN, reset_at: 1e300, limit_window_seconds: Infinity },
        negative: { used_percent: -1 },
        over: { used_percent: 101 },
        text: { used_percent: "42" },
        bool: { used_percent: true }
      }
    };
    expect(windows(parseOpenAIUsage(body))).toEqual([]);
    const huge = { rate_limit: { w: { used_percent: 1, reset_at: 1e300, limit_window_seconds: Infinity } } };
    expect(windows(parseOpenAIUsage(huge))).toEqual([{ usedPercent: 1 }]);
  });

  test("a poisoned second window does not affect the first", () => {
    const body = {
      rate_limit: {
        primary_window: { used_percent: 42, reset_at: 1785500000, limit_window_seconds: 604800 },
        secondary_window: { used_percent: NaN, reset_at: 1785400000, limit_window_seconds: 18000 }
      }
    };
    expect(windows(parseOpenAIUsage(body)).map((window) => window.usedPercent)).toEqual([42]);
  });

  test("a non-object body is an unexpected shape", () => {
    expect(parseOpenAIUsage([1, 2])).toEqual({ available: false, message: "unexpected response shape" });
    expect(parseOpenAIUsage(null)).toEqual({ available: false, message: "unexpected response shape" });
  });
});
