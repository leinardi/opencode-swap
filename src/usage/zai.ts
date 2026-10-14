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

// Z.AI GLM Coding Plan quota, from the undocumented endpoint z.ai's own
// subscription UI calls, with the account's API key as Bearer auth.
// `data.limits[]` holds the windows: each `CREDIT_LIMIT` entry (older
// responses: `TOKENS_LIMIT`, and the kind under `name` instead of `type`;
// openusage honors both) is a percentage window `number * unit` long, `unit`
// being z.ai's period enum (3 hour, 4 day, 5 month, 6 week; from z.ai's
// frontend source, cross-checked with openusage's ZAIUsageMapper.swift). An
// unknown unit keeps the window without a duration rather than guessing one.
// `TIME_LIMIT` entries count monthly web searches, not a rate limit, and are
// skipped. `data.level` is the plan tier.

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

export const ZAI_USAGE_URL = "https://api.z.ai/api/monitor/usage/quota/limit";

const UNIT_SECONDS: Record<number, number> = { 3: 3600, 4: 86_400, 5: 2_592_000, 6: 604_800 };
const PERCENTAGE_KINDS = new Set(["CREDIT_LIMIT", "TOKENS_LIMIT"]);
const PLAN_NAMES: Record<string, string> = {
  lite: "GLM Coding Lite",
  pro: "GLM Coding Pro",
  max: "GLM Coding Max"
};

class BrokenQuota extends Error {}

function planName(level: unknown): string | undefined {
  if (typeof level !== "string") return undefined;
  const key = level.trim().toLowerCase();
  return Object.hasOwn(PLAN_NAMES, key) ? PLAN_NAMES[key] : undefined;
}

// undefined for an entry that is not a percentage quota at all. A recognized
// quota entry whose percentage can't be read throws: a broken quota record
// makes the whole snapshot untrustworthy rather than silently short.
function parseLimit(entry: unknown): UsageWindow | undefined {
  if (!isRecord(entry)) return undefined;
  const kind = entry.type || entry.name;
  if (typeof kind !== "string" || !PERCENTAGE_KINDS.has(kind)) return undefined;
  const percent = usedPercent(entry.percentage);
  if (percent === undefined) throw new BrokenQuota();
  const { unit, number } = entry;
  const seconds =
    typeof unit === "number" && Number.isInteger(unit) && Object.hasOwn(UNIT_SECONDS, unit) && finiteNumber(number) && number > 0
      ? UNIT_SECONDS[unit] * number
      : undefined;
  return usageWindow(percent, resetAtMillis(entry.nextResetTime), seconds);
}

export function parseZaiUsage(body: unknown): UsageSnapshot {
  if (!isRecord(body)) return unavailable(UNEXPECTED_SHAPE);
  // z.ai answers 2xx with `success: false` for real errors too (observed:
  // "service unavailable"). The message is fixed text, never the server's
  // `msg`, which is not trusted to be credential-free.
  if (body.success === false) {
    const note = body.msg;
    const planGone = typeof note === "string" && note.toLowerCase().includes("coding plan");
    return unavailable(planGone ? "no active GLM coding plan" : "z.ai rejected the request");
  }
  const data = isRecord(body.data) ? body.data : undefined;
  // openusage also tolerates the limits array at the root.
  const limits = data?.limits ?? body.limits;
  if (!Array.isArray(limits)) return unavailable(UNEXPECTED_SHAPE);
  const windows: UsageWindow[] = [];
  try {
    for (const entry of limits) {
      const window = parseLimit(entry);
      if (window) windows.push(window);
    }
  } catch (error) {
    if (error instanceof BrokenQuota) return unavailable(UNEXPECTED_SHAPE);
    throw error;
  }
  // A stable sort, so windows of the same (or no) duration keep z.ai's order.
  const duration = (window: UsageWindow) => window.windowSeconds ?? Infinity;
  windows.sort((a, b) => (duration(a) === duration(b) ? 0 : duration(a) < duration(b) ? -1 : 1));
  const plan = planName(data?.level);
  return { available: true, ...(plan ? { planName: plan } : {}), windows };
}

export async function fetchZaiUsage(apiKey: string, options: RequestOptions = {}): Promise<UsageSnapshot> {
  const headers = { Authorization: `Bearer ${apiKey}`, Accept: "application/json", "User-Agent": USER_AGENT };
  const result = await fetchJson(ZAI_USAGE_URL, headers, options);
  return result.ok ? parseZaiUsage(result.body) : unavailable(result.message, result.status);
}
