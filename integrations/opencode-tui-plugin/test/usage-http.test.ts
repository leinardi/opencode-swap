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

// The request layer's failure modes, and the leak tests: a planted credential
// must never reach a snapshot, whichever way the request fails.

import { describe, expect, test } from "bun:test";
import { fetchUsage } from "../src/usage";
import { fetchJson } from "../src/usage/http";
import { fetchOpenAIUsage } from "../src/usage/openai";
import type { FetchLike, UsageSnapshot } from "../src/usage/types";
import { fetchZaiUsage } from "../src/usage/zai";
import { fakeFetch, hangingFetch, jsonResponse, textResponse } from "./helpers";

const ACCESS = "planted-access-token-0123456789";
const KEY = "planted-zai-key-0123456789abcdef";

// Every way a lookup can go wrong, each one quoting the credential it was
// sent with where a careless implementation would pass it on.
function failures(secret: string): [string, FetchLike][] {
  const echo = `Bearer ${secret}`;
  return [
    ["thrown header error", fakeFetch(() => {
      throw new TypeError(`Header 'authorization' has invalid value: '${echo}'`);
    }).impl],
    ["rejected fetch", fakeFetch(() => Promise.reject(new Error(`request failed for ${echo}`))).impl],
    ["thrown non-Error", fakeFetch(() => {
      throw echo;
    }).impl],
    ["HTTP error echoing the token", fakeFetch(() => textResponse(`invalid token ${echo}`, 401)).impl],
    ["JSON error echoing the token", fakeFetch(() => jsonResponse({ error: { message: echo } }, 403)).impl],
    ["non-JSON body echoing the token", fakeFetch(() => textResponse(`<html>${echo}</html>`)).impl],
    ["redirect to a URL holding the token", fakeFetch(() => new Response(null, { status: 302, headers: { Location: `https://example.invalid/?t=${secret}` } })).impl],
    ["body stream error echoing the token", fakeFetch(() => new Response(new ReadableStream({
      start(controller) {
        controller.error(new Error(`reset while sending ${echo}`));
      }
    }))).impl],
    ["plan name and msg echoing the token", fakeFetch(() => jsonResponse({ plan_type: secret, success: false, msg: echo })).impl]
  ];
}

function expectNoLeak(snapshot: UsageSnapshot, secret: string) {
  const text = JSON.stringify(snapshot);
  expect(text).not.toContain(secret);
  expect(text).not.toContain("Bearer");
}

describe("credential leaks", () => {
  for (const [name, impl] of failures(ACCESS)) {
    test(`OpenAI: ${name}`, async () => {
      expectNoLeak(await fetchOpenAIUsage(ACCESS, "acct-1", { fetchImpl: impl }), ACCESS);
    });
  }

  for (const [name, impl] of failures(KEY)) {
    test(`z.ai: ${name}`, async () => {
      expectNoLeak(await fetchZaiUsage(KEY, { fetchImpl: impl }), KEY);
    });
  }

  test("a credential with CR/LF is refused before any request, without echoing it", async () => {
    const injected = `${ACCESS}\r\nX-Injected: 1`;
    const { impl, calls } = fakeFetch(() => jsonResponse({}));
    const openai = await fetchOpenAIUsage(injected, undefined, { fetchImpl: impl });
    const zai = await fetchZaiUsage(injected, { fetchImpl: impl });
    expect(calls).toHaveLength(0);
    for (const snapshot of [openai, zai]) {
      expect(snapshot).toEqual({ available: false, message: "malformed request" });
      expectNoLeak(snapshot, ACCESS);
    }
  });

  test("a non-ASCII account id is refused as well", async () => {
    const { impl, calls } = fakeFetch(() => jsonResponse({}));
    const snapshot = await fetchOpenAIUsage(ACCESS, "acct-é", { fetchImpl: impl });
    expect(calls).toHaveLength(0);
    expect(snapshot).toEqual({ available: false, message: "malformed request" });
  });
});

describe("fetchJson", () => {
  test("refuses to follow redirects and reports them as their status", async () => {
    const { impl, calls } = fakeFetch(() => new Response(null, { status: 302, headers: { Location: "https://example.invalid/" } }));
    expect(await fetchJson("https://example.test/", {}, { fetchImpl: impl })).toEqual({ ok: false, message: "HTTP 302", status: 302 });
    expect(calls[0].init.redirect).toBe("manual");
    expect(calls[0].init.method).toBe("GET");
  });

  test("times out with a fixed message", async () => {
    expect(await fetchJson("https://example.test/", {}, { fetchImpl: hangingFetch, timeoutMs: 5 })).toEqual({
      ok: false,
      message: "request timed out"
    });
  });

  test("a caller abort is reported as aborted, not as a timeout", async () => {
    const controller = new AbortController();
    const pending = fetchJson("https://example.test/", {}, { fetchImpl: hangingFetch, signal: controller.signal, timeoutMs: 60_000 });
    controller.abort();
    expect(await pending).toEqual({ ok: false, message: "request aborted" });
  });

  test("an already aborted signal sends nothing", async () => {
    const controller = new AbortController();
    controller.abort();
    const { impl, calls } = fakeFetch(() => jsonResponse({}));
    expect(await fetchJson("https://example.test/", {}, { fetchImpl: impl, signal: controller.signal })).toEqual({
      ok: false,
      message: "request aborted"
    });
    expect(calls).toHaveLength(0);
  });

  test("passes a combined signal to the request", async () => {
    const controller = new AbortController();
    const { impl, calls } = fakeFetch(() => jsonResponse({ ok: 1 }));
    expect(await fetchJson("https://example.test/", {}, { fetchImpl: impl, signal: controller.signal })).toEqual({ ok: true, body: { ok: 1 } });
    const signal = calls[0].init.signal;
    expect(signal).toBeInstanceOf(AbortSignal);
    controller.abort();
    expect(signal?.aborted).toBe(true);
  });
});

describe("fetchUsage", () => {
  test("sends each request to its own endpoint with its own credential", async () => {
    const { impl, calls } = fakeFetch(() => jsonResponse({ data: { limits: [] }, rate_limit: {} }));
    await fetchUsage({ source: "openai", accessToken: "access", accountId: "acct-1" }, { fetchImpl: impl });
    await fetchUsage({ source: "zai", apiKey: "zai-key" }, { fetchImpl: impl });
    expect(calls.map((call) => [call.url, new Headers(call.init.headers).get("authorization")])).toEqual([
      ["https://chatgpt.com/backend-api/wham/usage", "Bearer access"],
      ["https://api.z.ai/api/monitor/usage/quota/limit", "Bearer zai-key"]
    ]);
  });
});
