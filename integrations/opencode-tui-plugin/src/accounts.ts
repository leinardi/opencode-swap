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

// OpenCode 2's stored credentials, as `client.credential.list()` returns them
// (`{id, integrationID, label, active, value}`; see
// docs/opencode-v2-integration.md, V3 and V7), turned into what the plugin
// shows. Values carry live secrets, so the split is deliberate: `AccountView`
// is the only shape that reaches UI state and never holds a secret, and
// `usageRequest` hands a secret only to the one usage call that sends it.

import { decodeClaims, extractAccountId, extractEmail, isRecord } from "./jwt";

export type CredentialEntry = {
  readonly id: string
  readonly integrationID: string
  readonly label: string
  readonly active: boolean
  readonly value: unknown
}

// "unknown" is any value this plugin does not recognize (a new OpenCode
// credential type, or a known type missing a field it needs): still listed and
// switchable by label, but never given an email or a usage lookup (F7).
export type CredentialKind = "oauth" | "key" | "external" | "unknown"

export type UsageSource = "openai" | "zai"

export type AccountView = {
  readonly id: string
  readonly integrationID: string
  readonly label: string
  readonly active: boolean
  readonly kind: CredentialKind
  readonly email?: string
  readonly keyHint?: string
  readonly usage?: UsageSource
}

export type UsageRequest =
  | { readonly source: "openai", readonly accessToken: string, readonly accountId?: string }
  | { readonly source: "zai", readonly apiKey: string }

export type ProviderInfo = {
  readonly id: string
  readonly integrationID?: string
}

export const OPENAI_INTEGRATION = "openai";
export const ZAI_INTEGRATION = "zai-coding-plan";

// Below this, 4 trailing characters would reveal too much of a short,
// possibly hand-picked key; real API keys are all comfortably longer, and at
// least 16 characters always stay hidden.
const MIN_KEY_LENGTH_FOR_HINT = 20;

type OAuthValue = {
  type: "oauth"
  methodID: string
  access: string
  metadata?: Record<string, unknown>
}

type KeyValue = { type: "key", key: string }

function optionalRecord(value: unknown): boolean {
  return value === undefined || isRecord(value);
}

// The schema OpenCode itself enforces (packages/schema/src/credential.ts:32-70);
// anything short of it is "unknown" rather than read field by field.
function asOAuth(value: unknown): OAuthValue | undefined {
  if (!isRecord(value) || value.type !== "oauth") return undefined;
  const { methodID, refresh, access, expires, metadata } = value;
  if (typeof methodID !== "string" || typeof refresh !== "string" || typeof access !== "string") return undefined;
  if (typeof expires !== "number" || !Number.isSafeInteger(expires) || expires < 0 || !optionalRecord(metadata)) return undefined;
  return { type: "oauth", methodID, access, ...(isRecord(metadata) ? { metadata } : {}) };
}

function asKey(value: unknown): KeyValue | undefined {
  if (!isRecord(value) || value.type !== "key") return undefined;
  if (typeof value.key !== "string" || !optionalRecord(value.metadata)) return undefined;
  return { type: "key", key: value.key };
}

function isExternal(value: unknown): boolean {
  return isRecord(value) && value.type === "external" && typeof value.methodID === "string" && optionalRecord(value.metadata);
}

function kindOf(value: unknown): CredentialKind {
  if (asOAuth(value)) return "oauth";
  if (asKey(value)) return "key";
  if (isExternal(value)) return "external";
  return "unknown";
}

// F5: whether wham/usage accepts every OpenAI OAuth method (OpenCode 2's
// default login stores `chatgpt-token-sharing`) needs a real login to check,
// so every one is eligible and a refusal renders as n/a.
export function usageSource(entry: CredentialEntry): UsageSource | undefined {
  if (entry.integrationID === OPENAI_INTEGRATION && asOAuth(entry.value)) return "openai";
  if (entry.integrationID === ZAI_INTEGRATION && asKey(entry.value)) return "zai";
  return undefined;
}

function openAIEmail(entry: CredentialEntry): string | undefined {
  if (entry.integrationID !== OPENAI_INTEGRATION) return undefined;
  const oauth = asOAuth(entry.value);
  return oauth ? extractEmail(decodeClaims(oauth.access)) : undefined;
}

function keyHint(value: unknown): string | undefined {
  const key = asKey(value)?.key;
  return key !== undefined && key.length >= MIN_KEY_LENGTH_FOR_HINT ? `…${key.slice(-4)}` : undefined;
}

// Built field by field from non-secret facts: no spread of the entry or its
// value, so a field OpenCode adds later cannot ride along into UI state.
export function toAccountView(entry: CredentialEntry): AccountView {
  const email = openAIEmail(entry);
  const hint = keyHint(entry.value);
  const usage = usageSource(entry);
  return {
    id: entry.id,
    integrationID: entry.integrationID,
    label: entry.label,
    active: entry.active === true,
    kind: kindOf(entry.value),
    ...(email ? { email } : {}),
    ...(hint ? { keyHint: hint } : {}),
    ...(usage ? { usage } : {})
  };
}

function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

export function sortAccounts(accounts: readonly AccountView[]): AccountView[] {
  return [...accounts].sort(
    (a, b) => compare(a.integrationID, b.integrationID) || compare(a.label, b.label) || compare(a.id, b.id)
  );
}

// The `credential.list()` response, projected and sorted. An entry whose
// envelope is malformed can be neither shown nor activated, so it is left out
// rather than patched up.
export function projectAccounts(entries: readonly unknown[]): AccountView[] {
  const views: AccountView[] = [];
  for (const entry of entries) {
    if (!isRecord(entry)) continue;
    const { id, integrationID, label, active, value } = entry;
    if (typeof id !== "string" || typeof integrationID !== "string" || typeof label !== "string") continue;
    views.push(toAccountView({ id, integrationID, label, active: active === true, value }));
  }
  return sortAccounts(views);
}

// The account after the integration's active one, in sorted order, wrapping
// around. With no active account the first one; undefined when there is
// nothing to switch to.
export function nextAccount(accounts: readonly AccountView[], integrationID: string): AccountView | undefined {
  const candidates = sortAccounts(accounts.filter((account) => account.integrationID === integrationID));
  const current = candidates.findIndex((account) => account.active);
  if (current === -1) return candidates[0];
  if (candidates.length < 2) return undefined;
  return candidates[(current + 1) % candidates.length];
}

// A provider's integration is `integrationID ?? id` (OpenCode's
// model-resolver.ts:376-378). It must come from the provider list rather than
// be assumed equal: the Console's providers all map to "opencode" (V8). An
// unlisted provider resolves to nothing rather than to a guess.
export function resolveIntegration(providerID: string, providers: readonly ProviderInfo[]): string | undefined {
  const provider = providers.find((item) => item.id === providerID);
  if (!provider) return undefined;
  return provider.integrationID ?? provider.id;
}

// The secret a usage lookup needs, read from the live entry at request time.
// Never store the result: it is the credential itself.
export function usageRequest(entry: CredentialEntry): UsageRequest | undefined {
  switch (usageSource(entry)) {
    case "openai": {
      const oauth = asOAuth(entry.value);
      if (!oauth) return undefined;
      const stored = oauth.metadata?.accountID;
      const accountId = typeof stored === "string" && stored ? stored : extractAccountId(decodeClaims(oauth.access));
      return { source: "openai", accessToken: oauth.access, ...(accountId ? { accountId } : {}) };
    }
    case "zai": {
      const key = asKey(entry.value);
      return key ? { source: "zai", apiKey: key.key } : undefined;
    }
    default:
      return undefined;
  }
}
