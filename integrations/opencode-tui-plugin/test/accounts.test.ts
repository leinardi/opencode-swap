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
import {
  footerAccount,
  nextAccount,
  projectAccounts,
  resolveIntegration,
  toAccountView,
  usageRequest,
  usageSource,
  type AccountView,
  type CredentialEntry
} from "../src/accounts";
import { makeJwt } from "./helpers";

const REFRESH = "planted-refresh-token-0123456789";
const KEY = "planted-zai-key-0123456789abcdef";

function oauth(claims: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  return { type: "oauth", methodID: "chatgpt-browser", refresh: REFRESH, access: makeJwt(claims), expires: 1_900_000_000_000, ...extra };
}

function entry(id: string, integrationID: string, label: string, value: unknown, active = false): CredentialEntry {
  return { id, integrationID, label, active, value };
}

const ACCESS_CLAIMS = { email: "work@example.com", "https://api.openai.com/auth": { chatgpt_account_id: "acct-from-jwt" } };

describe("toAccountView", () => {
  test("projects an OpenAI OAuth credential without any secret", () => {
    const value = oauth(ACCESS_CLAIMS, { metadata: { accountID: "acct-meta" } });
    const view = toAccountView(entry("cred_1", "openai", "work", value, true));
    expect(view).toEqual({ id: "cred_1", integrationID: "openai", label: "work", active: true, kind: "oauth", email: "work@example.com", usage: "openai" });
    const text = JSON.stringify(view);
    for (const secret of [value.access, REFRESH, "acct-meta", "acct-from-jwt", "chatgpt-browser"]) expect(text).not.toContain(secret);
  });

  test("shows only the last 4 characters of a long key", () => {
    const view = toAccountView(entry("cred_2", "zai-coding-plan", "team", { type: "key", key: KEY }));
    expect(view).toEqual({ id: "cred_2", integrationID: "zai-coding-plan", label: "team", active: false, kind: "key", keyHint: "…cdef", usage: "zai" });
    expect(JSON.stringify(view)).not.toContain(KEY.slice(0, -4));
  });

  test("gives a short key no hint", () => {
    expect(toAccountView(entry("cred_3", "deepseek", "short", { type: "key", key: "0123456789abcdefghi" })).keyHint).toBeUndefined();
    expect(toAccountView(entry("cred_3", "deepseek", "long", { type: "key", key: "0123456789abcdefghij" })).keyHint).toBe("…ghij");
  });

  test("an external credential is listed with no email, hint or usage", () => {
    expect(toAccountView(entry("cred_4", "openai", "ext", { type: "external", methodID: "sso" }))).toEqual({
      id: "cred_4",
      integrationID: "openai",
      label: "ext",
      active: false,
      kind: "external"
    });
  });

  test("an email is read only from OpenAI access tokens", () => {
    expect(toAccountView(entry("cred_5", "github-copilot", "gh", oauth({ email: "gh@example.com" }))).email).toBeUndefined();
  });

  test("a token without an email claim, or not a JWT, has no email", () => {
    expect(toAccountView(entry("cred_6", "openai", "a", oauth({ chatgpt_account_id: "x" }))).email).toBeUndefined();
    expect(toAccountView(entry("cred_7", "openai", "b", { ...oauth({}), access: "opaque-token" })).email).toBeUndefined();
  });

  test("unknown shapes are listed by label with no email or usage (F7)", () => {
    const shapes: unknown[] = [
      null,
      "text",
      { type: "mystery", access: makeJwt(ACCESS_CLAIMS) },
      { ...oauth(ACCESS_CLAIMS), access: 5 },
      { ...oauth(ACCESS_CLAIMS), refresh: undefined },
      { ...oauth(ACCESS_CLAIMS), expires: -1 },
      { ...oauth(ACCESS_CLAIMS), expires: 1.5 },
      { ...oauth(ACCESS_CLAIMS), metadata: "x" },
      { type: "key", key: 42 },
      { type: "key", key: KEY, metadata: [] },
      { type: "external" }
    ];
    for (const value of shapes) {
      const view = toAccountView(entry("cred_x", "openai", "odd", value));
      expect(view).toEqual({ id: "cred_x", integrationID: "openai", label: "odd", active: false, kind: "unknown" });
      expect(usageSource(entry("cred_x", "zai-coding-plan", "odd", value))).toBeUndefined();
    }
  });
});

describe("projectAccounts", () => {
  test("sorts by integration, label and id, and drops malformed envelopes", () => {
    const accounts = projectAccounts([
      entry("cred_b", "zai-coding-plan", "work", { type: "key", key: KEY }),
      entry("cred_2", "openai", "work", oauth({})),
      entry("cred_1", "openai", "work", oauth({})),
      entry("cred_3", "openai", "home", oauth({}), true),
      { id: 7, integrationID: "openai", label: "bad id", value: {} },
      { id: "cred_9", integrationID: "openai", value: {} },
      null,
      "cred_10"
    ]);
    expect(accounts.map((account) => account.id)).toEqual(["cred_3", "cred_1", "cred_2", "cred_b"]);
  });

  test("only a literal true marks an account active", () => {
    const [view] = projectAccounts([{ id: "cred_1", integrationID: "openai", label: "a", active: "true", value: {} }]);
    expect(view.active).toBe(false);
  });

  test("the projected list serializes without any secret", () => {
    const access = makeJwt(ACCESS_CLAIMS);
    const text = JSON.stringify(
      projectAccounts([
        entry("cred_1", "openai", "work", { ...oauth(ACCESS_CLAIMS), access }),
        entry("cred_2", "zai-coding-plan", "team", { type: "key", key: KEY, metadata: { note: "x" } }),
        entry("cred_3", "anthropic", "odd", { type: "future", secret: "planted-unknown-secret" })
      ])
    );
    for (const secret of [access, REFRESH, KEY.slice(0, -4), "planted-unknown-secret"]) expect(text).not.toContain(secret);
  });
});

describe("nextAccount", () => {
  const view = (id: string, label: string, active = false, integrationID = "openai"): AccountView => ({
    id,
    integrationID,
    label,
    active,
    kind: "oauth"
  });

  test("moves to the next account in sorted order", () => {
    expect(nextAccount([view("c", "c"), view("a", "a", true), view("b", "b")], "openai")?.id).toBe("b");
  });

  test("wraps around after the last account", () => {
    expect(nextAccount([view("a", "a"), view("b", "b"), view("c", "c", true)], "openai")?.id).toBe("a");
  });

  test("ignores other integrations", () => {
    const accounts = [view("a", "a", true), view("z", "b", false, "zai-coding-plan"), view("c", "c")];
    expect(nextAccount(accounts, "openai")?.id).toBe("c");
  });

  test("has nothing to switch to with a single active account or none at all", () => {
    expect(nextAccount([view("a", "a", true)], "openai")).toBeUndefined();
    expect(nextAccount([], "openai")).toBeUndefined();
  });

  test("starts from the first account when none is active", () => {
    expect(nextAccount([view("b", "b"), view("a", "a")], "openai")?.id).toBe("a");
  });
});

describe("resolveIntegration", () => {
  const providers = [{ id: "openai" }, { id: "opencode-go", integrationID: "opencode" }, { id: "zai-coding-plan", integrationID: "zai-coding-plan" }];

  test("maps a provider to integrationID ?? id", () => {
    expect(resolveIntegration("openai", providers)).toBe("openai");
    expect(resolveIntegration("opencode-go", providers)).toBe("opencode");
    expect(resolveIntegration("zai-coding-plan", providers)).toBe("zai-coding-plan");
  });

  test("does not guess for an unlisted provider", () => {
    expect(resolveIntegration("deepseek", providers)).toBeUndefined();
  });
});

describe("usage eligibility", () => {
  test("every OpenAI OAuth method is eligible (F5)", () => {
    for (const methodID of ["chatgpt-browser", "chatgpt-headless", "chatgpt-token-sharing", "something-new"]) {
      expect(usageSource(entry("c", "openai", "a", oauth({}, { methodID })))).toBe("openai");
    }
  });

  test("only z.ai keys and OpenAI OAuth are eligible", () => {
    expect(usageSource(entry("c", "zai-coding-plan", "a", { type: "key", key: KEY }))).toBe("zai");
    expect(usageSource(entry("c", "openai", "a", { type: "key", key: KEY }))).toBeUndefined();
    expect(usageSource(entry("c", "zai-coding-plan", "a", oauth({})))).toBeUndefined();
    expect(usageSource(entry("c", "zai", "a", { type: "key", key: KEY }))).toBeUndefined();
    expect(usageSource(entry("c", "anthropic", "a", oauth({})))).toBeUndefined();
    expect(usageSource(entry("c", "openai", "a", { type: "external", methodID: "chatgpt-browser" }))).toBeUndefined();
  });
});

describe("usageRequest", () => {
  test("prefers the stored account id, then the token's", () => {
    const stored = oauth(ACCESS_CLAIMS, { metadata: { accountID: "acct-meta" } });
    expect(usageRequest(entry("c", "openai", "a", stored))).toEqual({ source: "openai", accessToken: stored.access, accountId: "acct-meta" });
    const fromJwt = oauth(ACCESS_CLAIMS, { metadata: { clientID: "client" } });
    expect(usageRequest(entry("c", "openai", "a", fromJwt))).toEqual({ source: "openai", accessToken: fromJwt.access, accountId: "acct-from-jwt" });
    const none = { ...oauth({}), access: "opaque-token" };
    expect(usageRequest(entry("c", "openai", "a", none))).toEqual({ source: "openai", accessToken: "opaque-token" });
  });

  test("hands a z.ai key to its lookup", () => {
    expect(usageRequest(entry("c", "zai-coding-plan", "a", { type: "key", key: KEY }))).toEqual({ source: "zai", apiKey: KEY });
  });

  test("has no request for an ineligible or unknown credential", () => {
    expect(usageRequest(entry("c", "deepseek", "a", { type: "key", key: KEY }))).toBeUndefined();
    expect(usageRequest(entry("c", "openai", "a", { type: "future" }))).toBeUndefined();
  });
});

describe("footerAccount", () => {
  const accounts: AccountView[] = [
    { id: "a", integrationID: "openai", label: "work", active: true, kind: "oauth" },
    { id: "b", integrationID: "openai", label: "home", active: false, kind: "oauth" },
    { id: "c", integrationID: "zai-coding-plan", label: "team", active: false, kind: "key" }
  ];

  test("is the integration's active stored credential", () => {
    expect(footerAccount(accounts, "openai", false)?.id).toBe("a");
  });

  test("is hidden with no active stored credential, no integration, or an active env connection", () => {
    expect(footerAccount(accounts, "zai-coding-plan", false)).toBeUndefined();
    expect(footerAccount(accounts, "deepseek", false)).toBeUndefined();
    expect(footerAccount(accounts, undefined, false)).toBeUndefined();
    expect(footerAccount(accounts, "openai", true)).toBeUndefined();
  });
});
