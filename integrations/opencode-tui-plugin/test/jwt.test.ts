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
import { decodeClaims, extractAccountId, extractEmail } from "../src/jwt";
import { makeJwt } from "./helpers";

describe("decodeClaims", () => {
  test("round-trips a JWT payload", () => {
    const claims = decodeClaims(makeJwt({ chatgpt_account_id: "acct-1", email: "a@example.com" }));
    expect(claims.chatgpt_account_id).toBe("acct-1");
    expect(claims.email).toBe("a@example.com");
  });

  test("returns {} for anything that is not a JWT", () => {
    expect(decodeClaims("not-a-jwt")).toEqual({});
    expect(decodeClaims("a.b")).toEqual({});
    expect(decodeClaims("a.!!!notb64.c")).toEqual({});
    expect(decodeClaims("a.b.c.d")).toEqual({});
    expect(decodeClaims("")).toEqual({});
  });

  test("returns {} when the payload is not a JSON object", () => {
    const encode = (text: string) => btoa(text).replace(/=+$/, "");
    expect(decodeClaims(`h.${encode("[1,2]")}.s`)).toEqual({});
    expect(decodeClaims(`h.${encode("\"text\"")}.s`)).toEqual({});
    expect(decodeClaims(`h.${encode("not json")}.s`)).toEqual({});
  });

  test("rejects a payload that is not valid UTF-8", () => {
    expect(decodeClaims(`h.${btoa("\xff\xfe").replace(/=+$/, "")}.s`)).toEqual({});
  });

  test("decodes URL-safe characters and UTF-8, padded or not", () => {
    const token = makeJwt({ email: "rené@example.com", note: "??>>" });
    expect(decodeClaims(token).email).toBe("rené@example.com");
    const [head, payload, signature] = token.split(".");
    const padded = payload + "=".repeat((4 - (payload.length % 4)) % 4);
    expect(decodeClaims(`${head}.${padded}.${signature}`).email).toBe("rené@example.com");
  });
});

describe("extractAccountId", () => {
  test("reads the direct claim", () => {
    expect(extractAccountId({ chatgpt_account_id: "acct-1" })).toBe("acct-1");
  });

  test("reads the nested OpenAI auth claim", () => {
    expect(extractAccountId({ "https://api.openai.com/auth": { chatgpt_account_id: "acct-2" } })).toBe("acct-2");
  });

  test("falls back to the first organization", () => {
    expect(extractAccountId({ organizations: [{ id: "org-1" }] })).toBe("org-1");
  });

  test("is undefined when absent or not a string", () => {
    expect(extractAccountId({})).toBeUndefined();
    expect(extractAccountId({ chatgpt_account_id: 7, organizations: [] })).toBeUndefined();
    expect(extractAccountId({ organizations: ["org-1"] })).toBeUndefined();
  });
});

describe("extractEmail", () => {
  test("is undefined when absent, like on real OpenAI access tokens", () => {
    expect(extractEmail({ chatgpt_account_id: "acct-1" })).toBeUndefined();
  });

  test("reads the direct claim", () => {
    expect(extractEmail({ email: "a@example.com" })).toBe("a@example.com");
  });

  test("reads the nested OpenAI auth claim", () => {
    expect(extractEmail({ "https://api.openai.com/auth": { email: "b@example.com" } })).toBe("b@example.com");
  });
});
