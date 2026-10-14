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

import type { ResolvedTheme } from "@opencode/theme/tui";
import { createEffect, createMemo, For, on, Show, type Accessor } from "solid-js";
import { footerAccount } from "./accounts";
import { usageDetails, type Band } from "./bands";
import type { AccountState } from "./state";

export type FooterProps = {
  readonly state: Accessor<AccountState>
  readonly integrationID: Accessor<string | undefined>
  readonly envActive: Accessor<boolean>
  readonly theme: () => ResolvedTheme
  readonly debug: boolean
  // Called when the session's integration changes, so its usage is looked up
  // now rather than at the next poll.
  readonly onIntegration: (integrationID: string) => void
}

// `label · 5h 42% @14:30 | 7d 10%`, for the active stored credential of the
// session's integration. Hidden when there is none, or when an env var is
// the active connection (V8): the stored label would then be wrong.
export function Footer(props: FooterProps) {
  const account = createMemo(() => footerAccount(props.state().accounts, props.integrationID(), props.envActive()));
  const usage = createMemo(() => {
    const current = account();
    return current ? props.state().usage[current.id] : undefined;
  });
  const details = createMemo(() => {
    const current = usage();
    return current?.state === "ok" ? usageDetails(current.windows, Date.now()) : [];
  });
  const reason = createMemo(() => {
    const integrationID = props.integrationID();
    if (!integrationID) return "no provider";
    if (props.envActive()) return `${integrationID}: env connection`;
    return `${integrationID}: no stored credential`;
  });
  const color = (band: Band) => {
    const feedback = props.theme().text.feedback;
    if (band === "red") return feedback.error.base;
    if (band === "orange") return feedback.warning.base;
    if (band === "yellow") return feedback.info.base;
    return feedback.success.base;
  };

  // Not deferred: home and each session render their own prompt, so a footer
  // is mounted afresh on navigation, and its first integration needs a lookup
  // as much as a later change does.
  createEffect(
    on(
      () => props.integrationID(),
      (integrationID) => {
        if (integrationID) props.onIntegration(integrationID);
      }
    )
  );

  return (
    <Show
      when={account()}
      fallback={
        <Show when={props.debug}>
          <text fg={props.theme().text.muted} flexShrink={0}>
            swap · {reason()}
          </text>
        </Show>
      }
    >
      {(current) => (
        <text fg={props.theme().text.muted} flexShrink={0}>
          <span style={{ fg: props.theme().text.base }}>{current().label}</span>
          <Show when={usage()?.state === "pending"}>{" · …"}</Show>
          <Show when={usage()?.state === "unavailable"}>{" · n/a"}</Show>
          <Show when={details().length > 0}>
            {" · "}
            <For each={details()}>
              {(detail, index) => (
                <>
                  <Show when={index() > 0}>{" | "}</Show>
                  <Show when={detail.label}>{(label) => <>{label()} </>}</Show>
                  <span style={{ fg: color(detail.band) }}>{detail.percent}%</span>
                  <Show when={detail.reset}>{(reset) => <> @{reset()}</>}</Show>
                </>
              )}
            </For>
          </Show>
        </text>
      )}
    </Show>
  );
}
