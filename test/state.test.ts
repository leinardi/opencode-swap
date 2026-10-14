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
  AccountStore,
  digestValue,
  LIST_TIMEOUT_MS,
  PICKER_DEADLINE_MS,
  POLL_INTERVAL_MS,
  RevisionTracker,
  type SelectOption,
  type SessionStatus,
  type StateDeps,
  type Timers,
  type ToastVariant
} from "../src/state";
import { REQUEST_TIMEOUT_MS } from "../src/usage/http";
import { makeJwt } from "./helpers";

const BASE_TIME = 1_790_000_000_000;
const REFRESH = "planted-refresh-token-0123456789";
const K0 = "planted-zai-key-K0-0123456789abcdef";
const K1 = "planted-zai-key-K1-0123456789abcdef";
const A0 = makeJwt({ email: "work@example.com", chatgpt_account_id: "acct-1", nonce: "A0" });
const A1 = makeJwt({ email: "work@example.com", chatgpt_account_id: "acct-1", nonce: "A1" });

const OPENAI_BODY = { plan_type: "plus", rate_limit: { primary_window: { used_percent: 42, limit_window_seconds: 18000 } } };
const ZAI_BODY = { data: { level: "lite", limits: [{ type: "CREDIT_LIMIT", unit: 3, number: 5, percentage: 8 }] } };

type Entry = { id: string, integrationID: string, label: string, active: boolean, value: Record<string, unknown> }

function openai(id: string, label: string, active: boolean, access = A0): Entry {
  return {
    id,
    integrationID: "openai",
    label,
    active,
    value: { type: "oauth", methodID: "chatgpt-browser", refresh: REFRESH, access, expires: 1_900_000_000_000, metadata: { accountID: "acct-1" } }
  };
}

function zai(id: string, label: string, active: boolean, key = K0): Entry {
  return { id, integrationID: "zai-coding-plan", label, active, value: { type: "key", key } };
}

class ManualTimers implements Timers {
  now = 0;
  #next = 1;
  readonly #tasks = new Map<number, { at: number, callback: () => void, every?: number }>();

  get pending(): number {
    return this.#tasks.size;
  }

  setInterval(callback: () => void, ms: number): unknown {
    const id = this.#next++;
    this.#tasks.set(id, { at: this.now + ms, callback, every: ms });
    return id;
  }

  clearInterval(handle: unknown): void {
    this.#tasks.delete(handle as number);
  }

  setTimeout(callback: () => void, ms: number): unknown {
    const id = this.#next++;
    this.#tasks.set(id, { at: this.now + ms, callback });
    return id;
  }

  clearTimeout(handle: unknown): void {
    this.#tasks.delete(handle as number);
  }

  advance(ms: number): void {
    const end = this.now + ms;
    for (;;) {
      const due = [...this.#tasks.entries()].filter(([, task]) => task.at <= end).sort(([, a], [, b]) => a.at - b.at)[0];
      if (!due) break;
      const [id, task] = due;
      this.now = task.at;
      if (task.every === undefined) this.#tasks.delete(id);
      else task.at += task.every;
      task.callback();
    }
    this.now = end;
  }
}

type FetchCall = {
  url: string
  authorization: string | null
  signal: AbortSignal
  resolve(body: unknown, status?: number): void
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// Requests stay pending until the test resolves them, and ignore their abort
// signal on purpose: a late result must be dropped by the store itself.
function harness(options: { entries: Entry[], session?: SessionStatus, usage?: boolean }) {
  const timers = new ManualTimers();
  const calls: FetchCall[] = [];
  const handlers = new Map<string, Set<(data: unknown) => void>>();
  const toasts: { variant: ToastVariant, message: string }[] = [];
  const selects: SelectOption[][] = [];
  const confirms: string[] = [];
  const activations: string[] = [];
  const state = {
    entries: options.entries,
    session: options.session ?? {},
    listCalls: 0,
    listGate: undefined as Promise<void> | undefined,
    listError: false,
    activateError: false,
    selectAnswer: undefined as string | undefined,
    confirmAnswer: true as boolean | undefined
  };
  const deps: StateDeps = {
    client: {
      credential: {
        async list() {
          state.listCalls++;
          if (state.listGate) await state.listGate;
          if (state.listError) throw new Error(`list failed ${K0}`);
          return structuredClone(state.entries);
        },
        async activate({ credentialID }) {
          activations.push(credentialID);
          if (state.activateError) throw new Error(`activate failed ${K0}`);
          return undefined;
        }
      }
    },
    events: {
      on(type, handler) {
        const set = handlers.get(type) ?? new Set();
        set.add(handler);
        handlers.set(type, set);
        return () => set.delete(handler);
      }
    },
    fetchImpl: (url, init) => {
      const { promise, resolve } = deferred<Response>();
      calls.push({
        url,
        authorization: new Headers(init.headers).get("authorization"),
        signal: init.signal as AbortSignal,
        resolve: (body, status = 200) => resolve(new Response(JSON.stringify(body), { status }))
      });
      return promise;
    },
    now: () => BASE_TIME + timers.now,
    timers,
    select: async ({ options }) => {
      selects.push(options);
      return state.selectAnswer;
    },
    confirm: async ({ message }) => {
      confirms.push(message);
      return state.confirmAnswer;
    },
    toast: (toast) => toasts.push(toast),
    sessionStatus: () => state.session,
    usage: options.usage ?? true
  };
  const store = new AccountStore(deps);
  const emit = (type: string, data: unknown) => {
    for (const handler of handlers.get(type) ?? []) handler(data);
  };
  const subscriptions = () => [...handlers.values()].reduce((count, set) => count + set.size, 0);
  return { store, timers, calls, toasts, selects, confirms, activations, state, emit, subscriptions };
}

async function waitFor(condition: () => boolean, what = "condition"): Promise<void> {
  for (let attempt = 0; attempt < 500; attempt++) {
    if (condition()) return;
    await Bun.sleep(1);
  }
  throw new Error(`timed out waiting for ${what}`);
}

// Gives every pending promise chain time to run, for asserting that nothing happens.
async function settle(): Promise<void> {
  for (let attempt = 0; attempt < 20; attempt++) await Bun.sleep(1);
}

describe("RevisionTracker", () => {
  test("bumps a revision only when the digest changes, and never reuses a key", () => {
    const tracker = new RevisionTracker();
    expect([...tracker.apply(new Map([["a", "d0"], ["b", "e0"]]))]).toEqual([]);
    const first = tracker.key("a");
    expect([...tracker.apply(new Map([["a", "d0"], ["b", "e0"]]))]).toEqual([]);
    expect(tracker.key("a")).toBe(first);
    expect([...tracker.apply(new Map([["a", "d1"], ["b", "e0"]]))]).toEqual(["a"]);
    const second = tracker.key("a");
    expect(second).not.toBe(first);
    expect([...tracker.apply(new Map([["b", "e0"]]))]).toEqual(["a"]);
    expect(tracker.key("a")).toBeUndefined();
    tracker.apply(new Map([["a", "d0"], ["b", "e0"]]));
    expect([first, second]).not.toContain(tracker.key("a"));
  });

  test("the digest ignores key order", async () => {
    expect(await digestValue({ a: 1, b: { c: 2, d: [3] } })).toBe(await digestValue({ b: { d: [3], c: 2 }, a: 1 }));
    expect(await digestValue({ a: 1 })).not.toBe(await digestValue({ a: 2 }));
  });
});

describe("polling and the usage cache", () => {
  test("polls the session integration's active credential and caches the result for 60 s", async () => {
    const h = harness({ entries: [openai("cred_oa1", "work", true), zai("cred_z1", "team", true)], session: { integrationID: "openai" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1, "first fetch");
    expect(h.calls[0].url).toBe("https://chatgpt.com/backend-api/wham/usage");
    expect(h.store.state.usage.cred_oa1).toEqual({ state: "pending" });
    h.calls[0].resolve(OPENAI_BODY);
    await waitFor(() => h.store.state.usage.cred_oa1?.state === "ok", "usage");

    // An unchanged value, even with its keys reordered, hits the cache.
    h.state.entries[0].value = Object.fromEntries(Object.entries(h.state.entries[0].value).reverse());
    await h.store.refresh();
    await settle();
    expect(h.calls).toHaveLength(1);

    h.timers.advance(POLL_INTERVAL_MS);
    await waitFor(() => h.calls.length === 2, "fetch after the TTL");
    // The known usage stays on screen while it is revalidated.
    expect(h.store.state.usage.cred_oa1?.state).toBe("ok");
    h.store.dispose();
  });

  test("every poll refetches, however slow the previous lookup was", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    h.store.start();
    for (let poll = 1; poll <= 3; poll++) {
      await waitFor(() => h.calls.length === poll, `lookup of poll ${poll}`);
      h.timers.advance(3000);
      h.calls[poll - 1].resolve(ZAI_BODY);
      await waitFor(() => h.store.state.usage.cred_z1?.state === "ok");
      await settle();
      h.timers.advance(POLL_INTERVAL_MS - 3000);
    }
    await waitFor(() => h.calls.length === 4, "lookup of poll 4");
    h.store.dispose();
  });

  test("a list that never settles times out and does not block later lists", async () => {
    const h = harness({ entries: [zai("cred_z1", "a", true), zai("cred_z2", "b", false)], usage: false });
    h.state.listGate = new Promise<void>(() => undefined);
    const stuck = h.store.pick();
    await waitFor(() => h.state.listCalls === 1);
    h.timers.advance(LIST_TIMEOUT_MS);
    await stuck;
    expect(h.toasts).toEqual([{ variant: "error", message: "Could not read stored credentials." }]);
    h.state.listGate = undefined;
    await h.store.pick();
    expect(h.selects).toHaveLength(1);
  });

  test("a throwing subscriber or session lookup breaks neither the store nor the host", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    const seen: string[] = [];
    h.store.subscribe(() => {
      throw new Error("subscriber failed");
    });
    h.store.subscribe((state) => seen.push(state.usage.cred_z1?.state ?? "none"));
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    h.calls[0].resolve(ZAI_BODY);
    await waitFor(() => seen.includes("ok"));
    const throwing = harness({ entries: [zai("cred_z1", "team", true)] });
    const failures: unknown[] = [];
    const onRejection = (reason: unknown) => failures.push(reason);
    process.on("unhandledRejection", onRejection);
    try {
      (throwing.state as { session: unknown }).session = undefined;
      throwing.store.start();
      throwing.timers.advance(POLL_INTERVAL_MS);
      throwing.emit("credential.updated", {});
      await settle();
    } finally {
      process.off("unhandledRejection", onRejection);
    }
    expect(failures).toEqual([]);
    h.store.dispose();
    throwing.store.dispose();
  });

  test("a switch event clears that integration's cache, and only that one", async () => {
    const h = harness({ entries: [openai("cred_oa1", "work", true), zai("cred_z1", "team", true)], session: { integrationID: "openai" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    h.calls[0].resolve(OPENAI_BODY);
    await waitFor(() => h.store.state.usage.cred_oa1?.state === "ok");

    h.emit("credential.switched", { integrationID: "zai-coding-plan", credentialID: "cred_z1" });
    await settle();
    expect(h.calls).toHaveLength(1);
    expect(h.store.state.usage.cred_oa1?.state).toBe("ok");

    h.emit("credential.switched", { integrationID: "openai", credentialID: "cred_oa1" });
    await waitFor(() => h.calls.length === 2, "refetch after the switch");
    h.store.dispose();
  });

  test("an update event clears every cache", async () => {
    const h = harness({ entries: [openai("cred_oa1", "work", true)], session: { integrationID: "openai" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    h.calls[0].resolve(OPENAI_BODY);
    await waitFor(() => h.store.state.usage.cred_oa1?.state === "ok");
    h.emit("credential.updated", {});
    await waitFor(() => h.calls.length === 2, "refetch after the update");
    h.store.dispose();
  });

  test("sends nothing when usage is disabled", async () => {
    const h = harness({ entries: [openai("cred_oa1", "work", true)], session: { integrationID: "openai" }, usage: false });
    h.store.start();
    await waitFor(() => h.store.state.loaded);
    await h.store.refresh();
    await settle();
    expect(h.calls).toHaveLength(0);
    h.store.dispose();
  });

  test("a failed poll records an error without a toast", async () => {
    const h = harness({ entries: [], session: { integrationID: "openai" } });
    h.state.listError = true;
    await h.store.refresh();
    expect(h.store.state.error).toBe("Could not read stored credentials.");
    expect(h.toasts).toHaveLength(0);
    expect(JSON.stringify(h.store.state)).not.toContain(K0);
  });
});

describe("late results", () => {
  test("a result that arrives after a switch is never applied", async () => {
    const h = harness({ entries: [openai("cred_oa1", "work", true), openai("cred_oa2", "home", false)], session: { integrationID: "openai" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    const late = h.calls[0];
    h.state.entries[0].active = false;
    h.state.entries[1].active = true;
    h.emit("credential.switched", { integrationID: "openai", credentialID: "cred_oa2" });
    expect(late.signal.aborted).toBe(true);
    expect(h.store.state.accounts.map((account) => [account.id, account.active])).toEqual([["cred_oa2", true], ["cred_oa1", false]]);
    late.resolve(OPENAI_BODY);
    await waitFor(() => h.calls.length === 2, "fetch for the new account");
    await settle();
    expect(h.store.state.usage.cred_oa1).toBeUndefined();
    h.store.dispose();
  });

  test("a result that arrives after an update event is never applied", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    const late = h.calls[0];
    h.emit("credential.updated", {});
    expect(late.signal.aborted).toBe(true);
    late.resolve(ZAI_BODY);
    await waitFor(() => h.calls.length === 2);
    await settle();
    expect(h.store.state.usage.cred_z1).toEqual({ state: "pending" });
    h.calls[1].resolve(ZAI_BODY);
    await waitFor(() => h.store.state.usage.cred_z1?.state === "ok");
    h.store.dispose();
  });

  test("a result for a value replaced without an event is never applied", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true, K0)], session: { integrationID: "zai-coding-plan" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    const late = h.calls[0];
    h.state.entries[0].value = { type: "key", key: K1 };
    h.timers.advance(POLL_INTERVAL_MS);
    await waitFor(() => h.calls.length === 2, "fetch with the new key");
    expect(late.signal.aborted).toBe(true);
    late.resolve({}, 401);
    await settle();
    expect(h.store.state.usage.cred_z1).toEqual({ state: "pending" });
    h.calls[1].resolve(ZAI_BODY);
    await waitFor(() => h.store.state.usage.cred_z1?.state === "ok");
    h.store.dispose();
  });

  test("nothing is applied after dispose", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    const seen: unknown[] = [];
    h.store.subscribe((state) => seen.push(state));
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    const before = h.store.state;
    const updates = seen.length;
    h.store.dispose();
    expect(h.calls[0].signal.aborted).toBe(true);
    h.calls[0].resolve(ZAI_BODY);
    await settle();
    expect(h.store.state).toBe(before);
    expect(seen).toHaveLength(updates);
  });

  test("a list that completes after dispose is not applied", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    const gate = deferred<void>();
    h.state.listGate = gate.promise;
    h.store.start();
    await waitFor(() => h.state.listCalls === 1);
    h.store.dispose();
    gate.resolve();
    await settle();
    expect(h.store.state.loaded).toBe(false);
    expect(h.calls).toHaveLength(0);
  });
});

describe("401/403 back-off", () => {
  test("stops polling a refused credential until /swap-refresh", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    h.calls[0].resolve({}, 401);
    await waitFor(() => h.store.state.usage.cred_z1?.state === "unavailable");
    expect(h.store.state.usage.cred_z1).toEqual({ state: "unavailable", message: "HTTP 401" });

    h.timers.advance(POLL_INTERVAL_MS);
    await h.store.refresh();
    await settle();
    expect(h.calls).toHaveLength(1);

    await waitFor(() => h.state.listCalls >= 3);
    void h.store.forceRefresh();
    await waitFor(() => h.calls.length === 2, "fetch after /swap-refresh");
    h.store.dispose();
  });

  test("a z.ai key replaced K0 to K1 with no event is retried with K1 on the next poll", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true, K0)], session: { integrationID: "zai-coding-plan" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    expect(h.calls[0].authorization).toBe(`Bearer ${K0}`);
    h.calls[0].resolve({}, 403);
    await waitFor(() => h.store.state.usage.cred_z1?.state === "unavailable");

    h.state.entries[0].value = { type: "key", key: K1 };
    h.timers.advance(POLL_INTERVAL_MS);
    await waitFor(() => h.calls.length === 2, "fetch with the new key");
    expect(h.calls[1].authorization).toBe(`Bearer ${K1}`);
    h.calls[1].resolve(ZAI_BODY);
    await waitFor(() => h.store.state.usage.cred_z1?.state === "ok");
    h.store.dispose();
  });

  test("an OAuth access token replaced with the same expiry and method is retried too", async () => {
    const h = harness({ entries: [openai("cred_oa1", "work", true, A0)], session: { integrationID: "openai" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    expect(h.calls[0].authorization).toBe(`Bearer ${A0}`);
    h.calls[0].resolve({}, 401);
    await waitFor(() => h.store.state.usage.cred_oa1?.state === "unavailable");

    h.state.entries[0].value = { ...h.state.entries[0].value, access: A1 };
    h.timers.advance(POLL_INTERVAL_MS);
    await waitFor(() => h.calls.length === 2, "fetch with the new token");
    expect(h.calls[1].authorization).toBe(`Bearer ${A1}`);
    h.store.dispose();
  });

  test("a switch event lifts the back-off", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    h.calls[0].resolve({}, 401);
    await waitFor(() => h.store.state.usage.cred_z1?.state === "unavailable");
    h.emit("credential.switched", { integrationID: "zai-coding-plan", credentialID: "cred_z1" });
    await waitFor(() => h.calls.length === 2, "fetch after the switch");
    h.store.dispose();
  });

  test("other failures are not backed off", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    h.calls[0].resolve({}, 500);
    await waitFor(() => h.store.state.usage.cred_z1?.state === "unavailable");
    void h.store.refresh();
    await waitFor(() => h.calls.length === 2);
    h.store.dispose();
  });
});

describe("exposed state", () => {
  test("never holds a credential value or a digest", async () => {
    const entries = [openai("cred_oa1", "work", true), zai("cred_z1", "team", true)];
    const h = harness({ entries, session: { integrationID: "zai-coding-plan" } });
    h.state.selectAnswer = undefined;
    const pick = h.store.pick();
    await waitFor(() => h.calls.length === 2);
    for (const call of h.calls) call.resolve(call.url.includes("chatgpt") ? OPENAI_BODY : ZAI_BODY);
    await pick;
    const text = JSON.stringify(h.store.state);
    expect(h.store.state.usage.cred_oa1?.state).toBe("ok");
    expect(h.store.state.usage.cred_z1?.state).toBe("ok");
    for (const secret of [A0, REFRESH, K0, "acct-1", await digestValue(entries[0].value), await digestValue(entries[1].value)]) {
      expect(text).not.toContain(secret);
    }
    expect(text).not.toMatch(/#\d/);
    expect(JSON.stringify(h.selects)).not.toContain(K0);
  });
});

describe("/swap picker", () => {
  test("runs at most 4 lookups at once", async () => {
    const entries = Array.from({ length: 10 }, (_, index) => zai(`cred_z${index}`, `key ${index}`, index === 0, `${K0}-${index}`));
    const h = harness({ entries });
    let inFlight = 0;
    let peak = 0;
    const pick = h.store.pick();
    for (let done = 0; done < 10; done++) {
      await waitFor(() => h.calls.length > done, `lookup ${done}`);
      await settle();
      inFlight = h.calls.length - done;
      peak = Math.max(peak, inFlight);
      h.calls[done].resolve(ZAI_BODY);
    }
    await pick;
    expect(peak).toBe(4);
    expect(h.selects).toHaveLength(1);
    expect(h.selects[0].map((option) => option.footer)).toEqual(["active", ...Array(9).fill("5h 8%")]);
  });

  test("opens the dialog at the 5 s deadline, with … for pending entries", async () => {
    const entries = ["a", "b", "c", "d", "e", "f"].map((label, index) => zai(`cred_z${index}`, label, index === 0, `${K0}-${index}`));
    const h = harness({ entries });
    const pick = h.store.pick();
    await waitFor(() => h.calls.length === 4);
    h.timers.advance(2000);
    for (const call of h.calls.slice(0, 4)) call.resolve(ZAI_BODY);
    await waitFor(() => h.calls.length === 6, "the queued lookups");
    h.timers.advance(PICKER_DEADLINE_MS - 2001);
    await settle();
    expect(h.selects).toHaveLength(0);
    h.timers.advance(1);
    await pick;
    expect(h.selects[0].map((option) => [option.title, option.footer, option.category])).toEqual([
      ["a", "active", "zai-coding-plan"],
      ["b", "5h 8%", "zai-coding-plan"],
      ["c", "5h 8%", "zai-coding-plan"],
      ["d", "5h 8%", "zai-coding-plan"],
      ["e", "…", "zai-coding-plan"],
      ["f", "…", "zai-coding-plan"]
    ]);
    expect(h.calls[4].signal.aborted).toBe(false);
    h.store.dispose();
  });

  test("entries still queued at the deadline read as pending, not as having no usage", async () => {
    const entries = ["a", "b", "c", "d", "e", "f"].map((label, index) => zai(`cred_z${index}`, label, index === 0, `${K0}-${index}`));
    const h = harness({ entries });
    const pick = h.store.pick();
    await waitFor(() => h.calls.length === 4);
    await settle();
    expect(h.calls).toHaveLength(4);
    expect(h.store.state.usage.cred_z4).toEqual({ state: "pending" });
    expect(h.store.state.usage.cred_z5).toEqual({ state: "pending" });
    h.timers.advance(PICKER_DEADLINE_MS);
    await pick;
    const footers = new Map(h.selects[0].map((option) => [option.title, option.footer]));
    expect([footers.get("e"), footers.get("f")]).toEqual(["…", "…"]);
    h.store.dispose();
  });

  test("a lookup that outlives its 5 s limit is aborted and shown as n/a", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    h.timers.advance(REQUEST_TIMEOUT_MS - 1);
    await settle();
    expect(h.store.state.usage.cred_z1).toEqual({ state: "pending" });
    h.timers.advance(1);
    await waitFor(() => h.store.state.usage.cred_z1?.state === "unavailable");
    expect(h.store.state.usage.cred_z1).toEqual({ state: "unavailable", message: "request timed out" });
    expect(h.calls[0].signal.aborted).toBe(true);
    h.timers.advance(POLL_INTERVAL_MS);
    await waitFor(() => h.calls.length === 2, "retry on the next poll");
    h.store.dispose();
  });

  test("each lookup has its own abort signal", async () => {
    const h = harness({ entries: [openai("cred_oa1", "work", true), zai("cred_z1", "team", true)] });
    h.store.start();
    void h.store.pick();
    await waitFor(() => h.calls.length === 2);
    const [openaiCall, zaiCall] = h.calls[0].url.includes("chatgpt") ? h.calls : [h.calls[1], h.calls[0]];
    h.emit("credential.switched", { integrationID: "openai", credentialID: "cred_oa1" });
    expect(openaiCall.signal.aborted).toBe(true);
    expect(zaiCall.signal.aborted).toBe(false);
    h.store.dispose();
    expect(zaiCall.signal.aborted).toBe(true);
  });

  test("shows the email or key hint, and lists unknown shapes without usage", async () => {
    const odd = { id: "cred_x", integrationID: "openai", label: "odd", active: false, value: { type: "future", secret: "planted-unknown" } };
    const h = harness({ entries: [openai("cred_oa1", "work", true), zai("cred_z1", "team", false), odd] });
    const pick = h.store.pick();
    await waitFor(() => h.calls.length === 2);
    for (const call of h.calls) call.resolve(call.url.includes("chatgpt") ? OPENAI_BODY : ZAI_BODY);
    await pick;
    expect(h.selects[0]).toEqual([
      { title: "odd", value: "cred_x", category: "openai" },
      { title: "work", value: "cred_oa1", category: "openai", description: "work@example.com", footer: "active" },
      { title: "team", value: "cred_z1", category: "zai-coding-plan", description: "…cdef", footer: "5h 8%" }
    ]);
  });

  test("a cancelled select never activates", async () => {
    const h = harness({ entries: [zai("cred_z1", "a", true), zai("cred_z2", "b", false)], usage: false });
    h.state.selectAnswer = undefined;
    await h.store.pick();
    expect(h.selects).toHaveLength(1);
    expect(h.confirms).toHaveLength(0);
    expect(h.activations).toHaveLength(0);
  });

  test("a declined or dismissed confirm never activates", async () => {
    for (const answer of [false, undefined]) {
      const h = harness({ entries: [zai("cred_z1", "a", true), zai("cred_z2", "b", false)], usage: false });
      h.state.selectAnswer = "cred_z2";
      h.state.confirmAnswer = answer;
      await h.store.pick();
      expect(h.confirms).toHaveLength(1);
      expect(h.activations).toHaveLength(0);
    }
  });

  test("activates the chosen credential and says which integration changed", async () => {
    const h = harness({ entries: [zai("cred_z1", "a", true), zai("cred_z2", "b", false)], session: { integrationID: "openai" }, usage: false });
    h.state.selectAnswer = "cred_z2";
    await h.store.pick();
    expect(h.confirms).toEqual(["Use b for zai-coding-plan?"]);
    expect(h.activations).toEqual(["cred_z2"]);
    expect(h.toasts).toEqual([
      { variant: "success", message: "zai-coding-plan now uses b. This session uses openai and is unaffected." }
    ]);
  });

  test("an activate rejection shows an error and keeps the previous active account", async () => {
    const h = harness({ entries: [zai("cred_z1", "a", true), zai("cred_z2", "b", false)], usage: false });
    h.state.selectAnswer = "cred_z2";
    h.state.activateError = true;
    const listsBefore = h.state.listCalls;
    await h.store.pick();
    expect(h.activations).toEqual(["cred_z2"]);
    expect(h.toasts).toEqual([{ variant: "error", message: "Could not switch zai-coding-plan to b." }]);
    expect(h.store.state.accounts.map((account) => [account.id, account.active])).toEqual([["cred_z1", true], ["cred_z2", false]]);
    expect(h.state.listCalls).toBe(listsBefore + 1);
  });

  test("a list failure is reported without detail", async () => {
    const h = harness({ entries: [] });
    h.state.listError = true;
    await h.store.pick();
    expect(h.toasts).toEqual([{ variant: "error", message: "Could not read stored credentials." }]);
    expect(h.selects).toHaveLength(0);
  });
});

describe("/swap-next", () => {
  test("activates the next account of the session's integration after a confirm", async () => {
    const h = harness({
      entries: [zai("cred_z1", "a", true), zai("cred_z2", "b", false), openai("cred_oa1", "work", true)],
      session: { integrationID: "zai-coding-plan", status: "running" },
      usage: false
    });
    await h.store.next();
    expect(h.confirms).toEqual(["Use b for zai-coding-plan? A request is in progress in this session."]);
    expect(h.activations).toEqual(["cred_z2"]);
    expect(h.toasts).toEqual([{ variant: "success", message: "zai-coding-plan now uses b." }]);
  });

  test("needs a session integration", async () => {
    const h = harness({ entries: [zai("cred_z1", "a", true)] });
    await h.store.next();
    expect(h.state.listCalls).toBe(0);
    expect(h.toasts.map((toast) => toast.variant)).toEqual(["info"]);
  });

  test("does nothing with a single account", async () => {
    const h = harness({ entries: [zai("cred_z1", "a", true)], session: { integrationID: "zai-coding-plan" }, usage: false });
    await h.store.next();
    expect(h.confirms).toHaveLength(0);
    expect(h.toasts).toEqual([{ variant: "info", message: "zai-coding-plan has no other stored credential." }]);
  });
});

describe("dispose", () => {
  test("clears the interval, the subscriptions and in-flight requests", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    h.store.start();
    expect(h.subscriptions()).toBe(2);
    await waitFor(() => h.calls.length === 1);
    h.store.dispose();
    expect(h.subscriptions()).toBe(0);
    expect(h.timers.pending).toBe(0);
    expect(h.calls[0].signal.aborted).toBe(true);
    const lists = h.state.listCalls;
    h.timers.advance(POLL_INTERVAL_MS * 3);
    await h.store.refresh();
    await h.store.pick();
    await h.store.next();
    await settle();
    expect(h.state.listCalls).toBe(lists);
    expect(h.toasts).toHaveLength(0);
  });

  test("finished operations leave nothing behind, and dispose settles the rest", async () => {
    const h = harness({ entries: [zai("cred_z1", "team", true)], session: { integrationID: "zai-coding-plan" } });
    h.store.start();
    await waitFor(() => h.calls.length === 1);
    expect(h.store.pendingRaces).toBe(1);
    h.calls[0].resolve(ZAI_BODY);
    await waitFor(() => h.store.state.usage.cred_z1?.state === "ok");
    await settle();
    expect(h.store.pendingRaces).toBe(0);
    expect(h.timers.pending).toBe(1);

    // Lookups that never settle on their own: the picker still returns once disposed.
    const hung = harness({ entries: [zai("cred_z1", "a", true), zai("cred_z2", "b", false)] });
    const pick = hung.store.pick();
    await waitFor(() => hung.calls.length === 2);
    expect(hung.store.pendingRaces).toBe(3);
    hung.store.dispose();
    await pick;
    expect(hung.store.pendingRaces).toBe(0);
    expect(hung.selects).toHaveLength(0);
    h.store.dispose();
  });

  test("a picker waiting for its deadline neither opens nor activates after dispose", async () => {
    const h = harness({ entries: [zai("cred_z1", "a", true), zai("cred_z2", "b", false)] });
    h.state.selectAnswer = "cred_z2";
    const pick = h.store.pick();
    await waitFor(() => h.calls.length === 2);
    h.store.dispose();
    for (const call of h.calls) call.resolve(ZAI_BODY);
    await pick;
    expect(h.selects).toHaveLength(0);
    expect(h.activations).toHaveLength(0);
  });
});
