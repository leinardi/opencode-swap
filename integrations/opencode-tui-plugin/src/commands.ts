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

import type { KeymapLayer } from "@opencode/plugin/tui/context";
import type { AccountStore, StateDeps } from "./state";

type Commands = Pick<AccountStore, "pick" | "next" | "forceRefresh" | "state">

// The layer must be `mode: "global"`: in the default mode the prompt's slash
// completion never offers its commands (docs/opencode-v2-integration.md, V6).
export function commandLayer(store: Commands, toast: StateDeps["toast"]): KeymapLayer {
  const guarded = (work: () => Promise<void>) => () =>
    work().catch(() => toast({ variant: "error", message: "opencode-swap could not complete the command." }));
  return {
    mode: "global",
    commands: [
      {
        id: "opencode-swap.swap",
        title: "Switch account",
        description: "Pick the stored credential a provider uses",
        group: "Accounts",
        palette: true,
        slash: { name: "swap" },
        run: guarded(() => store.pick())
      },
      {
        id: "opencode-swap.next",
        title: "Switch to the next account",
        description: "Move this session's provider to its next stored credential",
        group: "Accounts",
        palette: true,
        slash: { name: "swap-next" },
        run: guarded(() => store.next())
      },
      {
        id: "opencode-swap.refresh",
        title: "Refresh accounts and usage",
        description: "Re-read stored credentials and look usage up again",
        group: "Accounts",
        palette: true,
        slash: { name: "swap-refresh" },
        run: guarded(async () => {
          await store.forceRefresh();
          const error = store.state.error;
          toast(error ? { variant: "error", message: error } : { variant: "info", message: "Accounts and usage refreshed." });
        })
      }
    ]
  };
}
