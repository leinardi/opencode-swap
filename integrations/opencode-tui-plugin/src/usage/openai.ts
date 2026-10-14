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

// ChatGPT usage, from the endpoint OpenAI's own Codex client calls (verified
// against opencode-balancer's src/core/usage/providers/openai.ts): GET with
// the account's access token as Bearer auth, plus ChatGPT-Account-Id when the
// account id is known. `rate_limit` holds one or more windows, each
// `{used_percent, reset_at, limit_window_seconds}` (a 5-hour window joined the
// 7-day one in 2026-08).
//
// Third-party clients disagree on which key holds which window
// (`primary_window`/`secondary_window` is seen both ways round), so this never
// keys into `rate_limit` by name: it keeps every value that parses as a window
// and sorts by `limit_window_seconds`. That survives one window, a third one or
// renamed keys, and skips siblings such as `code_review_rate_limit`, which sit
// outside `rate_limit`.

import { isRecord } from "../jwt";
import { fetchJson, USER_AGENT } from "./http";
import {
  finiteNumber,
  resetAtMillis,
  UNEXPECTED_SHAPE,
  unavailable,
  usageWindow,
  usedPercent,
  type RequestOptions,
  type UsageSnapshot,
  type UsageWindow
} from "./types";

export const CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage";

const PLAN_NAMES: Record<string, string> = {
  enterprise: "ChatGPT Enterprise",
  plus: "ChatGPT Plus",
  pro: "ChatGPT Pro",
  team: "ChatGPT Team"
};

function planName(planType: unknown): string | undefined {
  if (typeof planType !== "string") return undefined;
  const key = planType.trim().toLowerCase();
  return Object.hasOwn(PLAN_NAMES, key) ? PLAN_NAMES[key] : undefined;
}

function parseWindow(value: unknown): UsageWindow | undefined {
  if (!isRecord(value)) return undefined;
  const percent = usedPercent(value.used_percent);
  if (percent === undefined) return undefined;
  const seconds = value.limit_window_seconds;
  return usageWindow(percent, resetAtMillis(value.reset_at), finiteNumber(seconds) && seconds > 0 ? seconds : undefined);
}

export function parseOpenAIUsage(body: unknown): UsageSnapshot {
  if (!isRecord(body)) return unavailable(UNEXPECTED_SHAPE);
  const rateLimit = body.rate_limit;
  const parsed: [string, UsageWindow][] = [];
  if (isRecord(rateLimit)) {
    for (const [key, value] of Object.entries(rateLimit)) {
      const window = parseWindow(value);
      if (window) parsed.push([key, window]);
    }
  }
  // Windows with no usable duration sort last; ties break on the key name so
  // the order is deterministic.
  parsed.sort(([keyA, a], [keyB, b]) => {
    const durationA = a.windowSeconds ?? Infinity;
    const durationB = b.windowSeconds ?? Infinity;
    if (durationA !== durationB) return durationA < durationB ? -1 : 1;
    return keyA < keyB ? -1 : keyA > keyB ? 1 : 0;
  });
  const plan = planName(body.plan_type);
  return { available: true, ...(plan ? { planName: plan } : {}), windows: parsed.map(([, window]) => window) };
}

export async function fetchOpenAIUsage(
  accessToken: string,
  accountId: string | undefined,
  options: RequestOptions = {}
): Promise<UsageSnapshot> {
  const headers: Record<string, string> = { Authorization: `Bearer ${accessToken}`, "User-Agent": USER_AGENT };
  if (accountId) headers["ChatGPT-Account-Id"] = accountId;
  const result = await fetchJson(CHATGPT_USAGE_URL, headers, options);
  return result.ok ? parseOpenAIUsage(result.body) : unavailable(result.message, result.status);
}
