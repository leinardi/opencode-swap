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

// Port of the z.ai half of the Python CLI's tests/test_usage.py.

import { describe, expect, test } from "bun:test";
import type { UsageSnapshot } from "../src/usage/types";
import { fetchZaiUsage, parseZaiUsage, ZAI_USAGE_URL } from "../src/usage/zai";
import { fakeFetch, header, jsonResponse, textResponse } from "./helpers";

const PAYLOAD = {
  code: 200,
  msg: "Operation successful",
  data: {
    limits: [
      { type: "CREDIT_LIMIT", unit: 3, number: 5, usage: 2000, currentValue: 167, percentage: 8, nextResetTime: 1787862798247 },
      { type: "CREDIT_LIMIT", unit: 6, number: 1, usage: 10000, currentValue: 1245, percentage: 12, nextResetTime: 1788357330998 }
    ],
    level: "lite"
  },
  success: true
};

function windows(snapshot: UsageSnapshot) {
  if (!snapshot.available) throw new Error(`unavailable: ${snapshot.message}`);
  return snapshot.windows;
}

const SHAPE: UsageSnapshot = { available: false, message: "unexpected response shape" };

describe("fetchZaiUsage", () => {
  test("derives windows from unit and number", async () => {
    const { impl, calls } = fakeFetch(() => jsonResponse(PAYLOAD));
    const snapshot = await fetchZaiUsage("zai-key", { fetchImpl: impl });
    expect(calls[0].url).toBe(ZAI_USAGE_URL);
    expect(header(calls[0], "authorization")).toBe("Bearer zai-key");
    expect(header(calls[0], "accept")).toBe("application/json");
    expect(snapshot).toEqual({
      available: true,
      planName: "GLM Coding Lite",
      windows: [
        { usedPercent: 8, resetAt: 1787862798247, windowSeconds: 18000 },
        { usedPercent: 12, resetAt: 1788357330998, windowSeconds: 604800 }
      ]
    });
  });

  test("an HTTP error is unavailable and keeps its status", async () => {
    const { impl } = fakeFetch(() => jsonResponse({}, 401));
    expect(await fetchZaiUsage("zai-key", { fetchImpl: impl })).toEqual({ available: false, message: "HTTP 401", status: 401 });
  });

  test("malformed JSON and a non-object body are unavailable", async () => {
    const notJson = await fetchZaiUsage("zai-key", { fetchImpl: fakeFetch(() => textResponse("not json")).impl });
    const list = await fetchZaiUsage("zai-key", { fetchImpl: fakeFetch(() => jsonResponse([1, 2])).impl });
    expect(notJson.available).toBe(false);
    expect(list).toEqual(SHAPE);
  });

  test("a truncated body is an HTTP protocol error", async () => {
    const truncated = () =>
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("{\"data\":"));
            controller.error(new Error("connection reset"));
          }
        })
      );
    const snapshot = await fetchZaiUsage("zai-key", { fetchImpl: fakeFetch(truncated).impl });
    expect(snapshot).toEqual({ available: false, message: "HTTP protocol error" });
  });
});

describe("parseZaiUsage", () => {
  test("excludes TIME_LIMIT entries", () => {
    const body = {
      data: {
        limits: [
          { type: "CREDIT_LIMIT", unit: 3, number: 5, percentage: 8, nextResetTime: 1787862798247 },
          { type: "TIME_LIMIT", unit: 5, number: 1, percentage: 45, nextResetTime: 1788357330998 }
        ]
      }
    };
    expect(windows(parseZaiUsage(body)).map((window) => window.usedPercent)).toEqual([8]);
  });

  test("accepts the legacy TOKENS_LIMIT type", () => {
    const body = { data: { limits: [{ type: "TOKENS_LIMIT", unit: 6, number: 1, percentage: 30, nextResetTime: 1788357330998 }] } };
    expect(windows(parseZaiUsage(body))).toEqual([{ usedPercent: 30, resetAt: 1788357330998, windowSeconds: 604800 }]);
  });

  test("accepts the kind under the legacy name key", () => {
    const body = { data: { limits: [{ name: "CREDIT_LIMIT", unit: 3, number: 5, percentage: 8, nextResetTime: 1787862798247 }] } };
    expect(windows(parseZaiUsage(body)).map((window) => window.usedPercent)).toEqual([8]);
  });

  test("a recognized limit with an unreadable percentage makes it unavailable", () => {
    const body = { data: { limits: [{ type: "CREDIT_LIMIT", unit: 3, number: 5, nextResetTime: 1787862798247 }] } };
    expect(parseZaiUsage(body)).toEqual(SHAPE);
  });

  test("an unrelated entry without a percentage does not poison the snapshot", () => {
    const body = {
      data: {
        limits: [
          { type: "CREDIT_LIMIT", unit: 3, number: 5, percentage: 8, nextResetTime: 1787862798247 },
          { type: "TIME_LIMIT", unit: 5, number: 1 }
        ]
      }
    };
    expect(windows(parseZaiUsage(body)).map((window) => window.usedPercent)).toEqual([8]);
  });

  test("an unknown unit keeps the window without a duration, sorted last", () => {
    const body = {
      data: {
        limits: [
          { type: "CREDIT_LIMIT", unit: 99, number: 1, percentage: 70, nextResetTime: 1788357330998 },
          { type: "CREDIT_LIMIT", unit: 3, number: 5, percentage: 8, nextResetTime: 1787862798247 }
        ]
      }
    };
    const parsed = windows(parseZaiUsage(body));
    expect(parsed.map((window) => window.windowSeconds)).toEqual([18000, undefined]);
    expect(parsed.map((window) => window.usedPercent)).toEqual([8, 70]);
  });

  test("an out-of-range percentage on a quota entry makes it unavailable", () => {
    // 150 is not a usable percentage; on a recognized CREDIT_LIMIT entry that
    // makes the whole response untrustworthy rather than a partial snapshot.
    const body = {
      data: {
        limits: [
          { type: "CREDIT_LIMIT", unit: 3, number: 5, percentage: 150, nextResetTime: 1787862798247 },
          { type: "CREDIT_LIMIT", unit: 6, number: 1, percentage: 12, nextResetTime: 1788357330998 }
        ]
      }
    };
    expect(parseZaiUsage(body)).toEqual(SHAPE);
  });

  test("tolerates the limits array at the root", () => {
    const body = { limits: [{ type: "CREDIT_LIMIT", unit: 3, number: 5, percentage: 8, nextResetTime: 1787862798247 }] };
    expect(windows(parseZaiUsage(body))).toHaveLength(1);
  });

  test("no coding plan reports its own reason", () => {
    const body = { success: false, code: 500, msg: "The account has no active coding plan" };
    expect(parseZaiUsage(body)).toEqual({ available: false, message: "no active GLM coding plan" });
  });

  test("another success:false response is unavailable, and its msg is not echoed", () => {
    const snapshot = parseZaiUsage({ success: false, code: 503, msg: "service unavailable" });
    expect(snapshot).toEqual({ available: false, message: "z.ai rejected the request" });
  });

  test("a successful response without a limits array is an unexpected shape", () => {
    expect(parseZaiUsage({ code: 200, data: { level: "lite" } })).toEqual(SHAPE);
    expect(parseZaiUsage({ data: { limits: {} } })).toEqual(SHAPE);
  });

  test("an empty limits array is a valid snapshot with no usage", () => {
    expect(parseZaiUsage({ data: { level: "lite", limits: [] } })).toEqual({ available: true, planName: "GLM Coding Lite", windows: [] });
  });

  test("an unknown level is not displayed", () => {
    const body = { data: { level: "galaxy", limits: [{ type: "CREDIT_LIMIT", unit: 3, number: 5, percentage: 8, nextResetTime: 1787862798247 }] } };
    const snapshot = parseZaiUsage(body);
    expect(snapshot.available && snapshot.planName).toBeUndefined();
  });

  test("a non-integer or non-positive window length is left without a duration", () => {
    const body = {
      data: {
        limits: [
          { type: "CREDIT_LIMIT", unit: 3, number: 0, percentage: 1 },
          { type: "CREDIT_LIMIT", unit: 3.5, number: 1, percentage: 2 },
          { type: "CREDIT_LIMIT", unit: "3", number: 1, percentage: 3 }
        ]
      }
    };
    expect(windows(parseZaiUsage(body))).toEqual([{ usedPercent: 1 }, { usedPercent: 2 }, { usedPercent: 3 }]);
  });
});
