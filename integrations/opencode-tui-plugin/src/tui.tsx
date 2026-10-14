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

// The OpenCode 2 TUI entry (`./tui`): a `{id, setup}` module
// (docs/opencode-v2-integration.md, V4/V5). Everything from @opencode/* is a
// type: the host refuses a plugin-local copy of its runtime packages.

import type { Plugin } from "@opencode/plugin/tui";
import { createSignal } from "solid-js";
import { commandLayer } from "./commands";
import { MIN_OPENCODE_VERSION, missingFeatures } from "./features";
import { Footer } from "./footer";
import { parseOptions } from "./options";
import { currentIntegrationID, envConnectionActive, type SessionSources } from "./session";
import { AccountStore, type StateDeps } from "./state";

function routeSessionID(context: Plugin.Context): string | undefined {
  const route = context.ui.router.current();
  return route.type === "session" ? route.sessionID : undefined;
}

function sessionSources(context: Plugin.Context): SessionSources {
  return {
    session: (sessionID) => context.data.session.get(sessionID),
    messages: (sessionID) => context.data.session.message.list(sessionID) ?? [],
    uiModel: () => context.ui.model.current(),
    providers: () => context.data.location.provider.list() ?? [],
    integrations: () => context.data.location.integration.list() ?? []
  };
}

function setup(context: Plugin.Context): (() => void) | undefined {
  if (missingFeatures(context).length > 0) {
    try {
      context.ui.toast.show({ variant: "warning", message: `opencode-swap needs OpenCode ${MIN_OPENCODE_VERSION} or later.` });
    } catch {
      // Without a working toast there is nothing left to tell the user with.
    }
    return undefined;
  }

  const options = parseOptions(context.options);
  const sources = sessionSources(context);
  const toast: StateDeps["toast"] = (input) => context.ui.toast.show(input);
  const store = new AccountStore({
    client: {
      credential: {
        list: () => context.client.credential.list(),
        activate: (input) => context.client.credential.activate(input)
      }
    },
    events: {
      on: (type, handler) => context.data.on(type, (event) => handler(event.data))
    },
    now: () => Date.now(),
    timers: {
      setInterval: (callback, ms) => setInterval(callback, ms),
      clearInterval: (handle) => clearInterval(handle as ReturnType<typeof setInterval>),
      setTimeout: (callback, ms) => setTimeout(callback, ms),
      clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>)
    },
    select: (input) => context.ui.dialog.select<string>(input),
    confirm: (input) => context.ui.dialog.confirm(input),
    toast,
    sessionStatus: () => {
      const sessionID = routeSessionID(context);
      return {
        integrationID: currentIntegrationID(sessionID, sources),
        ...(sessionID ? { status: context.data.session.status(sessionID) } : {})
      };
    },
    usage: options.usage
  });

  const [state, setState] = createSignal(store.state);
  const unsubscribe = store.subscribe(setState);
  const refresh = () => void store.refresh().catch(() => undefined);

  const removeFooter = context.ui.slot({
    append: "prompt.footer",
    render: (input) => {
      const integrationID = () => currentIntegrationID(input.sessionID, sources);
      return (
        <Footer
          state={state}
          integrationID={integrationID}
          envActive={() => {
            const id = integrationID();
            return id !== undefined && envConnectionActive(id, sources);
          }}
          theme={() => context.theme}
          debug={options.debug}
          onIntegration={refresh}
        />
      );
    }
  });
  // On 2.0.17, `keymap.layer` called from setup throws "Keymap.Provider is
  // missing"; inside an `app` slot render it works, as for the built-ins (V6).
  const removeCommands = context.ui.slot({
    append: "app",
    render: () => {
      context.keymap.layer(() => commandLayer(store, toast));
      return null;
    }
  });

  store.start();
  return () => {
    unsubscribe();
    removeFooter();
    removeCommands();
    store.dispose();
  };
}

const plugin: Plugin.Definition = { id: "opencode-swap", setup };

export default plugin;
