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
import { commandLayer } from "../src/commands";
import { missingFeatures } from "../src/features";
import { currentIntegrationID, currentProviderID, envConnectionActive, type SessionSources } from "../src/session";
import type { AccountState, ToastVariant } from "../src/state";
import plugin from "../src/tui";

function context(overrides: Record<string, unknown> = {}) {
  const fn = () => undefined;
  return {
    client: { credential: { list: fn, activate: fn } },
    data: {
      on: fn,
      session: { get: fn, status: fn, message: { list: fn } },
      location: { provider: { list: fn }, integration: { list: fn } }
    },
    keymap: { layer: fn },
    ui: { slot: fn, toast: { show: fn }, dialog: { select: fn, confirm: fn }, model: { current: fn }, router: { current: fn } },
    theme: { text: { feedback: {} } },
    ...overrides
  };
}

describe("missingFeatures", () => {
  test("is empty for a complete context", () => {
    expect(missingFeatures(context())).toEqual([]);
  });

  test("names what an older OpenCode lacks", () => {
    // OpenCode 2.0.17: client.credential has update, activate and remove, but no list (V14).
    const older = context({ client: { credential: { activate: () => undefined } } });
    expect(missingFeatures(older)).toEqual(["client.credential.list"]);
    expect(missingFeatures(context({ theme: {} }))).toEqual(["theme.text.feedback"]);
  });

  test("tolerates anything that is not a context", () => {
    expect(missingFeatures(undefined).length).toBeGreaterThan(10);
    expect(missingFeatures(null).length).toBeGreaterThan(10);
  });
});

function sources(overrides: Partial<SessionSources> = {}): SessionSources {
  return {
    session: () => undefined,
    messages: () => [],
    uiModel: () => undefined,
    providers: () => [{ id: "openai" }, { id: "opencode-go", integrationID: "opencode" }, { id: "zai-coding-plan" }],
    integrations: () => [],
    ...overrides
  };
}

describe("current provider (F4)", () => {
  test("prefers the session's committed model", () => {
    const chain = sources({
      session: () => ({ model: { providerID: "zai-coding-plan" } }),
      messages: () => [{ type: "assistant", model: { providerID: "openai" } }],
      uiModel: () => ({ providerID: "opencode-go" })
    });
    expect(currentProviderID("ses_1", chain)).toBe("zai-coding-plan");
  });

  test("then the last assistant or model-switched message", () => {
    const chain = sources({
      messages: () => [
        { type: "assistant", model: { providerID: "openai" } },
        { type: "model-switched", model: { providerID: "zai-coding-plan" } },
        { type: "user" }
      ],
      uiModel: () => ({ providerID: "opencode-go" })
    });
    expect(currentProviderID("ses_1", chain)).toBe("zai-coding-plan");
  });

  test("then the prompt's selection, which is all there is on the home route", () => {
    const chain = sources({ uiModel: () => ({ providerID: "opencode-go" }) });
    expect(currentProviderID("ses_1", chain)).toBe("opencode-go");
    expect(currentProviderID(undefined, sources({ session: () => ({ model: { providerID: "openai" } }), uiModel: () => ({ providerID: "zai-coding-plan" }) }))).toBe(
      "zai-coding-plan"
    );
  });

  test("maps the provider to its integration through the provider list", () => {
    expect(currentIntegrationID(undefined, sources({ uiModel: () => ({ providerID: "opencode-go" }) }))).toBe("opencode");
    expect(currentIntegrationID(undefined, sources({ uiModel: () => ({ providerID: "unlisted" }) }))).toBeUndefined();
    expect(currentIntegrationID(undefined, sources())).toBeUndefined();
  });
});

describe("envConnectionActive", () => {
  test("is true only when an env var is the integration's first connection", () => {
    const chain = sources({
      integrations: () => [
        { id: "deepseek", connections: [{ type: "credential" }, { type: "env" }] },
        { id: "moonshotai", connections: [{ type: "env" }] },
        { id: "openai", connections: [] }
      ]
    });
    expect(envConnectionActive("deepseek", chain)).toBe(false);
    expect(envConnectionActive("moonshotai", chain)).toBe(true);
    expect(envConnectionActive("openai", chain)).toBe(false);
    expect(envConnectionActive("missing", chain)).toBe(false);
  });
});

describe("commandLayer", () => {
  function store(overrides: { error?: string, fail?: boolean } = {}) {
    const calls: string[] = [];
    const state: AccountState = { loaded: true, accounts: [], usage: {}, ...(overrides.error ? { error: overrides.error } : {}) };
    const run = (name: string) => async () => {
      calls.push(name);
      if (overrides.fail) throw new Error("planted-secret-in-error");
    };
    return { calls, store: { state, pick: run("pick"), next: run("next"), forceRefresh: run("forceRefresh") } };
  }

  test("registers /swap, /swap-next and /swap-refresh globally, in the palette, under Accounts", () => {
    const layer = commandLayer(store().store, () => undefined);
    expect(layer.mode).toBe("global");
    expect(layer.commands?.map((command) => [command.id, command.slash?.name, command.palette, command.group])).toEqual([
      ["opencode-swap.swap", "swap", true, "Accounts"],
      ["opencode-swap.next", "swap-next", true, "Accounts"],
      ["opencode-swap.refresh", "swap-refresh", true, "Accounts"]
    ]);
  });

  test("runs the store's actions, and reports a refresh", async () => {
    const { calls, store: fake } = store();
    const toasts: { variant: ToastVariant, message: string }[] = [];
    const layer = commandLayer(fake, (toast) => toasts.push(toast));
    for (const command of layer.commands ?? []) await command.run();
    expect(calls).toEqual(["pick", "next", "forceRefresh"]);
    expect(toasts).toEqual([{ variant: "info", message: "Accounts and usage refreshed." }]);
  });

  test("reports a failed refresh with the store's fixed error text", async () => {
    const toasts: { variant: ToastVariant, message: string }[] = [];
    const layer = commandLayer(store({ error: "Could not read stored credentials." }).store, (toast) => toasts.push(toast));
    await layer.commands?.[2].run();
    expect(toasts).toEqual([{ variant: "error", message: "Could not read stored credentials." }]);
  });

  test("a failing command becomes a toast without the error's text", async () => {
    const toasts: { variant: ToastVariant, message: string }[] = [];
    const layer = commandLayer(store({ fail: true }).store, (toast) => toasts.push(toast));
    await layer.commands?.[0].run();
    expect(toasts).toEqual([{ variant: "error", message: "opencode-swap could not complete the command." }]);
  });
});

describe("setup", () => {
  function recordingContext(options: { list?: boolean, toastThrows?: boolean } = {}) {
    const calls = { toasts: [] as unknown[], slots: [] as string[], layers: 0, lists: 0, subscriptions: 0, unsubscriptions: 0, removed: 0 };
    const credential: Record<string, unknown> = { activate: async () => undefined };
    if (options.list !== false) {
      credential.list = async () => {
        calls.lists++;
        return [];
      };
    }
    const context = {
      options: {},
      client: { credential },
      data: {
        on: () => {
          calls.subscriptions++;
          return () => calls.unsubscriptions++;
        },
        session: { get: () => undefined, status: () => "idle", message: { list: () => [] } },
        location: { provider: { list: () => [] }, integration: { list: () => [] } }
      },
      keymap: { layer: () => calls.layers++ },
      ui: {
        slot: (claim: { append: string }) => {
          calls.slots.push(claim.append);
          return () => calls.removed++;
        },
        toast: {
          show: (toast: unknown) => {
            if (options.toastThrows) throw new Error("no toast");
            calls.toasts.push(toast);
          }
        },
        dialog: { select: async () => undefined, confirm: async () => undefined },
        model: { current: () => undefined },
        router: { current: () => ({ type: "home" }) }
      },
      theme: { text: { feedback: {} } }
    };
    return { calls, context: context as unknown as Parameters<typeof plugin.setup>[0] };
  }

  test("is the OpenCode 2 module shape", () => {
    expect(plugin.id).toBe("opencode-swap");
    expect(typeof plugin.setup).toBe("function");
  });

  test("an older OpenCode gets one toast and nothing registered", async () => {
    const { calls, context } = recordingContext({ list: false });
    expect(await plugin.setup(context)).toBeUndefined();
    expect(calls.toasts).toEqual([{ variant: "warning", message: "opencode-swap needs OpenCode 2.0.20 or later." }]);
    expect(calls.slots).toEqual([]);
    expect(calls.layers).toBe(0);
    expect(calls.subscriptions).toBe(0);
  });

  test("a failing toast does not make setup throw", async () => {
    const { calls, context } = recordingContext({ list: false, toastThrows: true });
    expect(await plugin.setup(context)).toBeUndefined();
    expect(calls.slots).toEqual([]);
  });

  test("registers the footer and the commands, starts polling, and cleans up", async () => {
    const { calls, context } = recordingContext();
    const cleanup = await plugin.setup(context);
    expect(calls.slots).toEqual(["prompt.footer", "app"]);
    expect(calls.subscriptions).toBe(2);
    await Bun.sleep(5);
    expect(calls.lists).toBe(1);
    expect(calls.toasts).toEqual([]);
    expect(typeof cleanup).toBe("function");
    await (cleanup as () => void)();
    expect(calls.removed).toBe(2);
    expect(calls.unsubscriptions).toBe(2);
  });
});
