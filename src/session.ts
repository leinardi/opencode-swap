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

// Which provider, and so which integration, the current session talks to.
// See docs/opencode-v2-integration.md, V8 and V9.

import { resolveIntegration, type ProviderInfo } from "./accounts";

type ModelRef = { readonly providerID: string }

export type SessionSources = {
  // `data.session.get(id)`: its `model` is the committed one, set on the first prompt.
  session(sessionID: string): { readonly model?: ModelRef } | undefined
  // `data.session.message.list(id)`.
  messages(sessionID: string): readonly { readonly type: string, readonly model?: ModelRef }[]
  // `ui.model.current()`: the prompt's selection, or the new-session model on the home route.
  uiModel(): ModelRef | undefined
  // `data.location.provider.list()`.
  providers(): readonly ProviderInfo[]
  // `data.location.integration.list()`.
  integrations(): readonly IntegrationInfo[]
}

export type IntegrationInfo = {
  readonly id: string
  readonly connections: readonly { readonly type: string }[]
}

// F4's chain: the session's committed model, then the last assistant or
// model-switched message, then the prompt's selection.
export function currentProviderID(sessionID: string | undefined, sources: SessionSources): string | undefined {
  if (sessionID) {
    const committed = sources.session(sessionID)?.model?.providerID;
    if (committed) return committed;
    const messages = sources.messages(sessionID);
    for (let index = messages.length - 1; index >= 0; index--) {
      const message = messages[index];
      if ((message.type === "assistant" || message.type === "model-switched") && message.model?.providerID) {
        return message.model.providerID;
      }
    }
  }
  return sources.uiModel()?.providerID;
}

export function currentIntegrationID(sessionID: string | undefined, sources: SessionSources): string | undefined {
  const providerID = currentProviderID(sessionID, sources);
  return providerID ? resolveIntegration(providerID, sources.providers()) : undefined;
}

// An env connection is active exactly when it is the integration's first
// connection: stored credentials are listed first, so any stored credential
// beats an env var (packages/core/src/integration.ts:396-410,719-722).
export function envConnectionActive(integrationID: string, sources: SessionSources): boolean {
  const integration = sources.integrations().find((item) => item.id === integrationID);
  return integration?.connections[0]?.type === "env";
}
