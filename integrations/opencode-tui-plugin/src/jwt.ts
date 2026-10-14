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

export type Claims = Record<string, unknown>

const OPENAI_AUTH_CLAIM = "https://api.openai.com/auth";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// atob + TextDecoder rather than Buffer: the plugin runs inside OpenCode's TUI,
// and nothing guarantees Node's Buffer global there.
function decodeBase64Url(segment: string): string | undefined {
  if (!/^[A-Za-z0-9_-]*={0,2}$/.test(segment)) return undefined;
  const base64 = segment.replace(/=+$/, "").replace(/-/g, "+").replace(/_/g, "/");
  try {
    const binary = atob(base64 + "=".repeat((4 - (base64.length % 4)) % 4));
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return undefined;
  }
}

// A JWT's payload claims, or {} for anything that is not a JWT with a JSON
// object payload. The signature is not verified: the claims only label an
// account (email) or address a request with that same token (account id).
export function decodeClaims(token: string): Claims {
  const parts = token.split(".");
  if (parts.length !== 3) return {};
  const payload = decodeBase64Url(parts[1]);
  if (payload === undefined) return {};
  try {
    const claims: unknown = JSON.parse(payload);
    return isRecord(claims) ? claims : {};
  } catch {
    return {};
  }
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

// Same fallback order as OpenCode 1's codex.ts: the direct claim, the nested
// OpenAI auth claim, then the first organization.
export function extractAccountId(claims: Claims): string | undefined {
  const direct = nonEmptyString(claims.chatgpt_account_id);
  if (direct) return direct;
  const nested = claims[OPENAI_AUTH_CLAIM];
  if (isRecord(nested)) {
    const accountId = nonEmptyString(nested.chatgpt_account_id);
    if (accountId) return accountId;
  }
  const organizations = claims.organizations;
  if (Array.isArray(organizations) && isRecord(organizations[0])) return nonEmptyString(organizations[0].id);
  return undefined;
}

// Real OpenAI access tokens were observed without an email claim (the Python
// CLI's M0 spike), so callers must treat it as optional.
export function extractEmail(claims: Claims): string | undefined {
  const direct = nonEmptyString(claims.email);
  if (direct) return direct;
  const nested = claims[OPENAI_AUTH_CLAIM];
  return isRecord(nested) ? nonEmptyString(nested.email) : undefined;
}
