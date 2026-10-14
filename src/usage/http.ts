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

import type { RequestOptions } from "./types";

export const REQUEST_TIMEOUT_MS = 5_000;
export const USER_AGENT = "opencode-swap";

export type JsonResult =
  | { readonly ok: true, readonly body: unknown }
  | { readonly ok: false, readonly message: string, readonly status?: number }

// Printable ASCII, space and tab. Anything else (CR/LF above all) would be
// rejected by the runtime's Headers, and Bun's TypeError for that quotes the
// value verbatim -- here the Bearer credential.
const HEADER_VALUE = /^[\t\x20-\x7e]*$/;

function failure(message: string, status?: number): JsonResult {
  return status === undefined ? { ok: false, message } : { ok: false, message, status };
}

// GET `url` and parse JSON. Never throws, and every failure message is fixed
// text: no exception message, response body or header value is ever echoed,
// since any of them can carry the credential the request was sent with.
export async function fetchJson(url: string, headers: Record<string, string>, options: RequestOptions = {}): Promise<JsonResult> {
  if (!Object.values(headers).every((value) => HEADER_VALUE.test(value))) return failure("malformed request");
  const caller = options.signal;
  if (caller?.aborted) return failure("request aborted");

  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), options.timeoutMs ?? REQUEST_TIMEOUT_MS);
  const signal = caller ? AbortSignal.any([caller, timeout.signal]) : timeout.signal;
  const interrupted = (fallback: string) => {
    if (caller?.aborted) return failure("request aborted");
    if (timeout.signal.aborted) return failure("request timed out");
    return failure(fallback);
  };

  try {
    let response: Response;
    try {
      const fetchImpl = options.fetchImpl ?? fetch;
      // A redirect is reported as its HTTP status rather than followed, so the
      // credential is only ever sent to the endpoint named here.
      response = await fetchImpl(url, { method: "GET", headers: new Headers(headers), redirect: "manual", signal });
    } catch {
      return interrupted("network error");
    }

    if (!response.ok) {
      response.body?.cancel().catch(() => undefined);
      return failure(`HTTP ${response.status}`, response.status);
    }

    let bytes: ArrayBuffer;
    try {
      bytes = await response.arrayBuffer();
    } catch {
      // A truncated or reset body; the Python CLI reported IncompleteRead the same way.
      return interrupted("HTTP protocol error");
    }

    try {
      return { ok: true, body: JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) };
    } catch {
      return failure("invalid JSON in response");
    }
  } finally {
    clearTimeout(timer);
  }
}
