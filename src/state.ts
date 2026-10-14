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

// Credential, usage and switching orchestration, free of any UI framework so
// it can be driven by fakes in tests; the TUI entry bridges `subscribe` into
// signals.
//
// Secrets: a credential's value is read from a fresh `credential.list()` and
// handed straight to the one usage request that needs it. The exposed state
// holds only `AccountView`s and usage results, and the revision tracker keeps a
// SHA-256 digest per credential, never the value.
//
// Why revisions: OpenCode changes a stored value without any event (a token
// refresh, a provider plugin's startup rewrite, a `--standalone` client;
// docs/opencode-v2-integration.md, V7 and V12). Keying the usage cache and the
// 401/403 back-off on `credentialID#revision`, with the revision bumped
// whenever the digest changes, makes a replaced value a new key: its cached
// usage is not reused and its back-off does not apply.

import { nextAccount, projectAccounts, usageRequest, type AccountView, type CredentialEntry } from "./accounts";
import { usageDetails } from "./bands";
import { isRecord } from "./jwt";
import { fetchUsage, REQUEST_TIMEOUT_MS } from "./usage";
import type { FetchLike, UsageSnapshot, UsageWindow } from "./usage/types";

export const POLL_INTERVAL_MS = 60_000;
export const USAGE_TTL_MS = 60_000;
export const PICKER_DEADLINE_MS = 5_000;
export const PICKER_CONCURRENCY = 4;
export const LIST_TIMEOUT_MS = 5_000;

export type UsageView =
  | { readonly state: "pending" }
  | { readonly state: "ok", readonly planName?: string, readonly windows: readonly UsageWindow[] }
  | { readonly state: "unavailable", readonly message: string }

export type AccountState = {
  readonly loaded: boolean
  readonly accounts: readonly AccountView[]
  // Keyed by credential id.
  readonly usage: Readonly<Record<string, UsageView>>
  readonly error?: string
}

export type SelectOption = {
  readonly title: string
  readonly value: string
  readonly description?: string
  readonly footer?: string
  readonly category?: string
}

export type ToastVariant = "info" | "success" | "warning" | "error"

export type Timers = {
  setInterval(callback: () => void, ms: number): unknown
  clearInterval(handle: unknown): void
  setTimeout(callback: () => void, ms: number): unknown
  clearTimeout(handle: unknown): void
}

export type SessionStatus = {
  // The current session's integration (from its provider), when known.
  readonly integrationID?: string
  readonly status?: "idle" | "running"
}

export type StateDeps = {
  readonly client: {
    readonly credential: {
      list(): Promise<unknown>
      activate(input: { credentialID: string }): Promise<unknown>
    }
  }
  // `credential.switched` and `credential.updated`; the handler gets the event's data.
  readonly events: {
    on(type: "credential.switched" | "credential.updated", handler: (data: unknown) => void): () => void
  }
  readonly fetchImpl?: FetchLike
  readonly now: () => number
  readonly timers: Timers
  readonly select: (input: { title: string, options: SelectOption[] }) => Promise<string | undefined>
  readonly confirm: (input: { title: string, message: string }) => Promise<boolean | undefined>
  readonly toast: (input: { variant: ToastVariant, message: string }) => void
  readonly sessionStatus: () => SessionStatus
  // The `usage` plugin option: false means no usage request is ever sent.
  readonly usage: boolean
}

const PENDING: UsageView = { state: "pending" };
const LIST_FAILED = "Could not read stored credentials.";

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (isRecord(value)) {
    const fields = Object.keys(value)
      .filter((key) => value[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`);
    return `{${fields.join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

// SHA-256 of the value's canonical JSON, so key order does not count as a change.
export async function digestValue(value: unknown): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(value)));
  return Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

// credentialID -> {digest, revision}. Revisions come from one counter shared
// by every credential, so a key is never reused, not even by a credential that
// is removed and re-created with the same id (opencode-swap's migration
// generates deterministic ids).
export class RevisionTracker {
  #records = new Map<string, { digest: string, revision: number }>();
  #counter = 0;

  // Returns the ids whose value changed or that disappeared.
  apply(digests: ReadonlyMap<string, string>): Set<string> {
    const changed = new Set<string>();
    for (const id of this.#records.keys()) {
      if (!digests.has(id)) {
        this.#records.delete(id);
        changed.add(id);
      }
    }
    for (const [id, digest] of digests) {
      const record = this.#records.get(id);
      if (record?.digest === digest) continue;
      this.#records.set(id, { digest, revision: ++this.#counter });
      if (record) changed.add(id);
    }
    return changed;
  }

  key(credentialID: string): string | undefined {
    const record = this.#records.get(credentialID);
    return record ? `${credentialID}#${record.revision}` : undefined;
  }
}

type Listed = { readonly entry: CredentialEntry, readonly key: string }

type InFlight = {
  readonly key: string
  readonly controller: AbortController
  readonly promise: Promise<void>
}

function toEntries(raw: unknown): CredentialEntry[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const entries: CredentialEntry[] = [];
  for (const item of raw) {
    if (!isRecord(item)) continue;
    const { id, integrationID, label, active, value } = item;
    if (typeof id !== "string" || typeof integrationID !== "string" || typeof label !== "string") continue;
    entries.push({ id, integrationID, label, active: active === true, value });
  }
  return entries;
}

function parseSwitched(data: unknown): { integrationID: string, credentialID: string | null } | undefined {
  if (!isRecord(data) || typeof data.integrationID !== "string") return undefined;
  const { credentialID } = data;
  if (credentialID !== null && typeof credentialID !== "string") return undefined;
  return { integrationID: data.integrationID, credentialID };
}

function toUsageView(snapshot: UsageSnapshot): UsageView {
  if (!snapshot.available) return { state: "unavailable", message: snapshot.message };
  return { state: "ok", ...(snapshot.planName ? { planName: snapshot.planName } : {}), windows: snapshot.windows };
}

// The picker footer for an account's usage: its windows, "…" while a lookup
// is pending, "n/a" when it failed.
export function usageSummary(view: UsageView | undefined, now: number): string | undefined {
  if (!view) return undefined;
  if (view.state === "pending") return "…";
  if (view.state === "unavailable") return "n/a";
  const details = usageDetails(view.windows, now);
  if (details.length === 0) return view.planName;
  return details.map((detail) => (detail.label ? `${detail.label} ${detail.percent}%` : `${detail.percent}%`)).join(" · ");
}

export class AccountStore {
  readonly #deps: StateDeps;
  readonly #tracker = new RevisionTracker();
  readonly #listeners = new Set<(state: AccountState) => void>();
  readonly #unsubscribe: (() => void)[] = [];
  // Per credential; bumped by a switch or update event for it, a revision
  // change, /swap-refresh and dispose. A usage result is applied only if its
  // credential's generation and revision key are both unchanged.
  readonly #generations = new Map<string, number>();
  readonly #inFlight = new Map<string, InFlight>();
  readonly #cache = new Map<string, { readonly at: number, readonly view: UsageView }>();
  readonly #backoff = new Set<string>();
  readonly #timeouts = new Set<unknown>();
  #state: AccountState = { loaded: false, accounts: [], usage: {} };
  #interval: unknown;
  #listQueue: Promise<unknown> = Promise.resolve();
  #disposed = false;
  // One entry per unfinished #race, removed as soon as it settles; dispose
  // settles the rest, so no promise is left waiting on a cleared timer or on
  // a request that ignores its abort signal.
  readonly #races = new Set<() => void>();

  constructor(deps: StateDeps) {
    this.#deps = deps;
  }

  // For tests: the number of unfinished timed operations.
  get pendingRaces(): number {
    return this.#races.size;
  }

  get state(): AccountState {
    return this.#state;
  }

  subscribe(listener: (state: AccountState) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  start(): void {
    if (this.#disposed || this.#interval !== undefined) return;
    this.#unsubscribe.push(
      this.#deps.events.on("credential.switched", (data) => this.#onSwitched(data)),
      this.#deps.events.on("credential.updated", () => this.#onUpdated())
    );
    this.#interval = this.#deps.timers.setInterval(() => this.#background(this.refresh()), POLL_INTERVAL_MS);
    this.#background(this.refresh());
  }

  dispose(): void {
    if (this.#disposed) return;
    this.#disposed = true;
    for (const settle of [...this.#races]) settle();
    this.#races.clear();
    if (this.#interval !== undefined) this.#deps.timers.clearInterval(this.#interval);
    for (const handle of this.#timeouts) this.#deps.timers.clearTimeout(handle);
    this.#timeouts.clear();
    for (const unsubscribe of this.#unsubscribe.splice(0)) unsubscribe();
    for (const flight of this.#inFlight.values()) flight.controller.abort();
    this.#inFlight.clear();
    this.#cache.clear();
    this.#backoff.clear();
    this.#listeners.clear();
  }

  // The 60 s poll: a fresh list, then the usage of the active credential of
  // the current session's integration (cached for 60 s per revision).
  async refresh(): Promise<void> {
    const listed = await this.#list();
    if (!listed) return;
    const integrationID = this.#deps.sessionStatus().integrationID;
    const active = listed.find(({ entry }) => entry.integrationID === integrationID && entry.active);
    if (active) await this.#ensureUsage(active);
  }

  // /swap-refresh: forget every cached result and back-off, then poll.
  async forceRefresh(): Promise<void> {
    if (this.#disposed) return;
    this.#invalidate(() => true);
    await this.refresh();
  }

  // /swap: every account, grouped by integration, with usage looked up first
  // (at most PICKER_CONCURRENCY at once, for at most PICKER_DEADLINE_MS).
  async pick(): Promise<void> {
    const listed = await this.#list({ toastOnError: true });
    if (!listed) return;
    if (listed.length === 0) {
      this.#deps.toast({ variant: "info", message: "No stored credentials. Connect a provider first." });
      return;
    }
    await this.#race(this.#fetchAll(listed), PICKER_DEADLINE_MS, () => undefined);
    if (this.#disposed) return;

    const now = this.#deps.now();
    const options = this.#state.accounts.map<SelectOption>((account) => {
      const description = account.email ?? account.keyHint;
      const footer = account.active ? "active" : usageSummary(this.#state.usage[account.id], now);
      return {
        title: account.label,
        value: account.id,
        category: account.integrationID,
        ...(description ? { description } : {}),
        ...(footer ? { footer } : {})
      };
    });
    const chosen = await this.#deps.select({ title: "Switch account", options });
    if (this.#disposed || chosen === undefined) return;
    const account = this.#state.accounts.find((item) => item.id === chosen);
    if (!account) return;
    if (account.active) {
      this.#deps.toast({ variant: "info", message: `${account.integrationID} already uses ${account.label}.` });
      return;
    }
    await this.#activate(account);
  }

  // /swap-next: the next account of the current session's integration.
  async next(): Promise<void> {
    const integrationID = this.#deps.sessionStatus().integrationID;
    if (!integrationID) {
      this.#deps.toast({ variant: "info", message: "This session has no provider yet; pick one with /swap." });
      return;
    }
    const listed = await this.#list({ toastOnError: true });
    if (!listed) return;
    const account = nextAccount(this.#state.accounts, integrationID);
    if (!account) {
      this.#deps.toast({ variant: "info", message: `${integrationID} has no other stored credential.` });
      return;
    }
    await this.#activate(account);
  }

  async #activate(account: AccountView): Promise<void> {
    const session = this.#deps.sessionStatus();
    const sameIntegration = session.integrationID === account.integrationID;
    const running = sameIntegration && session.status === "running" ? " A request is in progress in this session." : "";
    const confirmed = await this.#deps.confirm({
      title: "Switch account",
      message: `Use ${account.label} for ${account.integrationID}?${running}`
    });
    if (this.#disposed || confirmed !== true) return;
    try {
      await this.#deps.client.credential.activate({ credentialID: account.id });
    } catch {
      // The displayed active account stays as it was: it only changes from a
      // fresh list or the `credential.switched` event.
      if (!this.#disposed) this.#deps.toast({ variant: "error", message: `Could not switch ${account.integrationID} to ${account.label}.` });
      return;
    }
    if (this.#disposed) return;
    const unaffected =
      session.integrationID && !sameIntegration ? ` This session uses ${session.integrationID} and is unaffected.` : "";
    this.#deps.toast({ variant: "success", message: `${account.integrationID} now uses ${account.label}.${unaffected}` });
    this.#background(this.refresh());
  }

  #onSwitched(data: unknown): void {
    if (this.#disposed) return;
    const event = parseSwitched(data);
    if (!event) {
      this.#onUpdated();
      return;
    }
    const inIntegration = (id: string) => this.#state.accounts.some((account) => account.id === id && account.integrationID === event.integrationID);
    this.#invalidate(inIntegration);
    this.#setState({
      accounts: this.#state.accounts.map((account) =>
        account.integrationID === event.integrationID ? { ...account, active: account.id === event.credentialID } : account
      )
    });
    this.#background(this.refresh());
  }

  // `credential.updated` carries no payload (V7), so it cannot be scoped to
  // the integration that changed: everything is invalidated.
  #onUpdated(): void {
    if (this.#disposed) return;
    this.#invalidate(() => true);
    this.#background(this.refresh());
  }

  // Fire-and-forget work (the poll, event and post-switch refreshes) must not
  // surface as an unhandled rejection in the host process.
  #background(work: Promise<void>): void {
    work.catch(() => undefined);
  }

  // Bumps the generation of every matching credential, aborts its request and
  // drops its cached usage, back-off and displayed usage.
  #invalidate(matches: (credentialID: string) => boolean): void {
    const ids = new Set([...this.#generations.keys(), ...this.#inFlight.keys(), ...Object.keys(this.#state.usage)]);
    for (const id of ids) {
      if (matches(id)) this.#forget(id);
    }
    for (const key of [...this.#cache.keys(), ...this.#backoff]) {
      if (matches(key.slice(0, key.lastIndexOf("#")))) {
        this.#cache.delete(key);
        this.#backoff.delete(key);
      }
    }
  }

  #forget(credentialID: string): void {
    this.#generations.set(credentialID, (this.#generations.get(credentialID) ?? 0) + 1);
    this.#inFlight.get(credentialID)?.controller.abort();
    this.#inFlight.delete(credentialID);
    if (credentialID in this.#state.usage) {
      const usage = { ...this.#state.usage };
      delete usage[credentialID];
      this.#setState({ usage });
    }
  }

  // Lists are serialized, so each one is reconciled against the one before it
  // and a slow list can never overwrite a newer one.
  #list(options: { toastOnError?: boolean } = {}): Promise<Listed[] | undefined> {
    const run = this.#listQueue.then(() => this.#listNow(options));
    this.#listQueue = run.catch(() => undefined);
    return run;
  }

  async #listNow(options: { toastOnError?: boolean }): Promise<Listed[] | undefined> {
    if (this.#disposed) return undefined;
    // Bounded, because lists are serialized: one call that never settles (a
    // stale client after a reconnect, V7) would otherwise block every later
    // poll, command and event for the rest of the session.
    const list = Promise.resolve()
      .then(() => this.#deps.client.credential.list())
      .catch(() => undefined);
    const raw = await this.#race(list, LIST_TIMEOUT_MS, () => undefined);
    if (this.#disposed) return undefined;
    const entries = toEntries(raw);
    if (!entries) {
      this.#setState({ error: LIST_FAILED });
      if (options.toastOnError) this.#deps.toast({ variant: "error", message: LIST_FAILED });
      return undefined;
    }
    const digests = new Map<string, string>();
    for (const entry of entries) digests.set(entry.id, await digestValue(entry.value));
    if (this.#disposed) return undefined;

    for (const id of this.#tracker.apply(digests)) this.#forget(id);
    const keys = new Set(entries.map((entry) => this.#tracker.key(entry.id)));
    for (const key of [...this.#cache.keys(), ...this.#backoff]) {
      if (!keys.has(key)) {
        this.#cache.delete(key);
        this.#backoff.delete(key);
      }
    }
    this.#setState({ loaded: true, accounts: projectAccounts(entries), error: undefined });
    return entries.flatMap((entry) => {
      const key = this.#tracker.key(entry.id);
      return key ? [{ entry, key }] : [];
    });
  }

  async #fetchAll(listed: readonly Listed[]): Promise<void> {
    const queue = [...listed];
    // Marked before the workers start, so an entry still queued when the
    // picker's deadline opens the dialog shows "…" rather than no usage.
    const usage = { ...this.#state.usage };
    for (const item of queue) if (this.#needsFetch(item)) usage[item.entry.id] ??= PENDING;
    this.#setState({ usage });
    const worker = async () => {
      for (let item = queue.shift(); item && !this.#disposed; item = queue.shift()) await this.#ensureUsage(item);
    };
    await Promise.all(Array.from({ length: Math.min(PICKER_CONCURRENCY, queue.length) }, worker));
  }

  // `work`, or `onTimeout()` once `ms` pass on the injected timers or the
  // store is disposed, whichever comes first.
  async #race<T>(work: Promise<T>, ms: number, onTimeout: () => T): Promise<T> {
    let handle: unknown;
    let settle!: () => void;
    const timeout = new Promise<T>((resolve) => {
      settle = () => resolve(onTimeout());
      handle = this.#deps.timers.setTimeout(settle, ms);
    });
    this.#timeouts.add(handle);
    this.#races.add(settle);
    try {
      return await Promise.race([work, timeout]);
    } finally {
      this.#races.delete(settle);
      this.#deps.timers.clearTimeout(handle);
      this.#timeouts.delete(handle);
    }
  }

  // The cache entry is stamped when its request starts, and counts as fresh
  // for USAGE_TTL_MS less the longest a list can take. Without that margin a
  // poll whose list happens to be faster than the previous one would find the
  // previous result still fresh, and the footer would refresh only every
  // other poll.
  #fresh(key: string): UsageView | undefined {
    const cached = this.#cache.get(key);
    return cached && this.#deps.now() - cached.at < USAGE_TTL_MS - LIST_TIMEOUT_MS ? cached.view : undefined;
  }

  // Whether a lookup would be sent for this entry right now.
  #needsFetch({ entry, key }: Listed): boolean {
    // A list that was overtaken by a newer one: its value is not the current
    // revision, so it is neither sent nor cached.
    if (this.#disposed || !this.#deps.usage || this.#tracker.key(entry.id) !== key) return false;
    if (!usageRequest(entry) || this.#backoff.has(key)) return false;
    return this.#fresh(key) === undefined;
  }

  #ensureUsage({ entry, key }: Listed): Promise<void> {
    if (this.#disposed || !this.#deps.usage) return Promise.resolve();
    if (this.#tracker.key(entry.id) !== key) return Promise.resolve();
    const request = usageRequest(entry);
    if (!request) return Promise.resolve();

    const cached = this.#fresh(key);
    if (cached) {
      this.#setUsage(entry.id, cached);
      return Promise.resolve();
    }
    if (this.#backoff.has(key)) return Promise.resolve();
    const current = this.#inFlight.get(entry.id);
    if (current?.key === key) return current.promise;
    current?.controller.abort();

    const generation = this.#generations.get(entry.id) ?? 0;
    const controller = new AbortController();
    const owned = () =>
      !this.#disposed && (this.#generations.get(entry.id) ?? 0) === generation && this.#tracker.key(entry.id) === key;
    // A known view stays on screen while it is revalidated; only an account
    // with nothing to show yet reads as pending.
    if (!(entry.id in this.#state.usage)) this.#setUsage(entry.id, PENDING);
    const startedAt = this.#deps.now();
    const lookup = fetchUsage(request, { fetchImpl: this.#deps.fetchImpl, signal: controller.signal, timeoutMs: REQUEST_TIMEOUT_MS })
      .catch((): UsageSnapshot => ({ available: false, message: "usage lookup failed" }));
    // The 5 s per-request limit runs on the injected timers as well, so it
    // holds even for a fetch implementation that ignores its abort signal.
    const timedOut = (): UsageSnapshot => {
      controller.abort();
      return { available: false, message: "request timed out" };
    };
    const promise = this.#race(lookup, REQUEST_TIMEOUT_MS, timedOut)
      .then((snapshot) => {
        if (this.#inFlight.get(entry.id)?.controller === controller) this.#inFlight.delete(entry.id);
        if (!owned()) return;
        const view = toUsageView(snapshot);
        // Only a refusal is backed off. A network error or a 5xx is neither
        // cached nor backed off, so the next poll simply tries again.
        if (snapshot.available) this.#cache.set(key, { at: startedAt, view });
        else if (snapshot.status === 401 || snapshot.status === 403) this.#backoff.add(key);
        this.#setUsage(entry.id, view);
      });
    this.#inFlight.set(entry.id, { key, controller, promise });
    return promise;
  }

  #setUsage(credentialID: string, view: UsageView): void {
    if (this.#state.usage[credentialID] === view) return;
    this.#setState({ usage: { ...this.#state.usage, [credentialID]: view } });
  }

  #setState(patch: Partial<AccountState>): void {
    if (this.#disposed) return;
    const next = { ...this.#state, ...patch };
    if (next.error === undefined) delete (next as { error?: string }).error;
    this.#state = next;
    for (const listener of this.#listeners) {
      // One failing subscriber must not break the store or the others.
      try {
        listener(next);
      } catch {
        // Ignored on purpose.
      }
    }
  }
}
