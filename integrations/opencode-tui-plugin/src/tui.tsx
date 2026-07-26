/** @jsxImportSource @opentui/solid */

import type { TuiDialogSelectOption, TuiPlugin, TuiPluginApi, TuiPluginModule } from "@opencode-ai/plugin/tui"
import { Show, createEffect, createMemo, createSignal, on, onCleanup, onMount, untrack } from "solid-js"

type Account = {
  name: string
  type: string
}

type Active = {
  state: "managed" | "unmanaged" | "none" | "incompatible"
  name?: string
  reason?: string
}

type Usage = {
  applicable: boolean
  available?: boolean
  used_percent?: number
  reset_at?: number
  window_seconds?: number
}

type ProviderStatus = {
  id: string
  accounts: Account[]
  active: Active
  usage?: Usage
}

type Status = {
  schema_version: number
  providers: ProviderStatus[]
}

type AccountChoice = {
  provider: string
  account: string
}

const POLL_INTERVAL_MS = 60_000
const COMMAND_TIMEOUT_MS = 10_000

function command(options: Record<string, unknown> | undefined): string {
  const value = options?.command
  return typeof value === "string" && value.trim() ? value : "opencode-swap"
}

// Usage lookups send the account's live OAuth access token to chatgpt.com
// (see README "Network access"). On by default; set `{ "usage": false }` to
// disable the extra request and its egress entirely. Any JS-falsy value (0,
// "", null), and the strings "false"/"no"/"off"/"0" (any case, untrimmed
// whitespace tolerated), are also treated as disabled -- OpenCode plugin
// options come from user-edited JSON/JSONC, and only recognizing the exact
// boolean `false` would silently keep the egress on for a plausible typo.
function usageEnabled(options: Record<string, unknown> | undefined): boolean {
  const value = options?.usage
  if (value === undefined) return true
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase()
    return !["", "false", "no", "off", "0"].includes(normalized)
  }
  return Boolean(value)
}

async function run(options: Record<string, unknown> | undefined, args: string[]) {
  const process = Bun.spawn([command(options), ...args], {
    stdin: "ignore",
    stdout: "pipe",
    stderr: "pipe",
    timeout: COMMAND_TIMEOUT_MS,
  })
  const [exitCode, stdout, stderr] = await Promise.all([
    process.exited,
    new Response(process.stdout).text(),
    new Response(process.stderr).text(),
  ])
  if (exitCode !== 0) {
    const reason = exitCode === null ? "timed out" : `exit ${exitCode}`
    const detail = stderr.trim()
    throw new Error(`opencode-swap command failed (${reason})${detail ? `: ${detail}` : ""}`)
  }
  return stdout
}

function status(value: unknown): Status {
  if (!value || typeof value !== "object") throw new Error("invalid status response")
  const result = value as Partial<Status>
  if (result.schema_version !== 1 || !Array.isArray(result.providers)) throw new Error("unsupported status response")
  return result as Status
}

async function readStatus(options: Record<string, unknown> | undefined, provider?: string, usage = false) {
  const args = ["status"]
  if (provider) args.push(provider)
  args.push("--json")
  if (usage) args.push("--usage")
  return status(JSON.parse(await run(options, args)))
}

function providerIDFromMessages(messages: ReturnType<TuiPluginApi["state"]["session"]["messages"]>) {
  let assistantProviderID: string | undefined
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role === "user" && message.model?.providerID) return message.model.providerID
    if (message.role === "assistant" && !assistantProviderID) assistantProviderID = message.providerID
  }
  return assistantProviderID
}

function latestProviderID(api: TuiPluginApi, sessionID: string) {
  return providerIDFromMessages(api.state.session.messages(sessionID)) ?? api.state.session.get(sessionID)?.model?.providerID
}

async function currentStatus(options: Record<string, unknown> | undefined, providerID: string | undefined) {
  if (!providerID) return

  const summary = await readStatus(options)
  const provider = summary.providers.find((item) => item.id === providerID)
  if (!provider || provider.accounts.length === 0) return
  if (provider.active.state !== "managed" || !usageEnabled(options)) return provider

  const detailed = (await readStatus(options, providerID, true)).providers.find((item) => item.id === providerID)
  return detailed ?? provider
}

function text(provider: ProviderStatus) {
  if (provider.active.state === "unmanaged") return "unmanaged account"
  if (provider.active.state === "none") return "no active account"
  if (provider.active.state === "incompatible") return "incompatible account"
  // Defends against a future CLI adding a new `active.state` value under the
  // same schema_version=1 (see cli.py's compatibility contract): an older
  // plugin build must degrade to this instead of rendering `undefined`.
  return provider.active.name ?? "unknown account state"
}

function usageDetails(provider: ProviderStatus) {
  const usage = provider.usage
  if (!usage?.applicable || !usage.available || typeof usage.used_percent !== "number" || !Number.isFinite(usage.used_percent)) return

  const percent = Math.round(usage.used_percent)
  const absoluteBand = percent >= 90 ? "red" : percent >= 70 ? "orange" : percent >= 50 ? "yellow" : "green"
  const resetAt = usage.reset_at
  if (typeof resetAt !== "number" || !Number.isFinite(resetAt)) return { percent, band: absoluteBand }

  const windowSeconds = usage.window_seconds
  let band: "green" | "yellow" | "orange" | "red" = absoluteBand
  if (typeof windowSeconds === "number" && Number.isFinite(windowSeconds) && windowSeconds > 0) {
    const windowMs = windowSeconds * 1000
    const elapsedPercent = ((windowMs - (resetAt - Date.now())) / windowMs) * 100
    if (elapsedPercent < 5) band = "green"
    else if (elapsedPercent <= 100) {
      const ratio = (usage.used_percent / elapsedPercent) * 100
      band = ratio < 85 ? "green" : ratio < 105 ? "orange" : "red"
    }
  }
  const date = new Date(resetAt)
  if (Number.isNaN(date.getTime())) return { percent, band }
  const month = date.toLocaleString(undefined, { month: "short" })
  const reset = `${month} ${date.getDate()}, ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`
  return { percent, band, reset }
}

function StatusView(props: {
  api: TuiPluginApi
  options: Record<string, unknown> | undefined
  sessionID: string
  refreshers: Set<() => void>
}) {
  const [provider, setProvider] = createSignal<ProviderStatus>()
  const [debug, setDebug] = createSignal("provider=unknown · show=pending")
  const debugging = createMemo(() => props.options?.debug === true)
  const providerID = createMemo(() => latestProviderID(props.api, props.sessionID))
  let generation = 0
  let refreshing = false
  let refreshQueued = false
  let disposed = false

  const refresh = () => {
    if (disposed) return
    if (refreshing) {
      refreshQueued = true
      return
    }
    refreshing = true
    refreshQueued = false
    const request = ++generation
    const requestedProviderID = providerID()
    if (debugging() && provider() === undefined) setDebug(`provider=${requestedProviderID ?? "none"} · show=pending`)
    void currentStatus(props.options, requestedProviderID)
      .then((result) => {
        if (request !== generation) return
        setProvider(result)
        if (untrack(debugging)) {
          setDebug(
            `provider=${result?.id ?? requestedProviderID ?? "none"} · show=${result !== undefined} · accounts=${result?.accounts.length ?? 0} · active=${result?.active.state ?? "n/a"}`,
          )
        }
      })
      .catch((error) => {
        if (request !== generation) return
        setProvider(undefined)
        if (untrack(debugging)) {
          const message = error instanceof Error ? error.message : String(error)
          setDebug(`provider=${requestedProviderID ?? "none"} · show=false · error=${message}`)
        }
      })
      .finally(() => {
        refreshing = false
        if (refreshQueued) refresh()
      })
  }

  onMount(() => {
    refresh()
    const interval = setInterval(refresh, POLL_INTERVAL_MS)
    props.refreshers.add(refresh)
    onCleanup(() => {
      disposed = true
      refreshQueued = false
      generation += 1
      clearInterval(interval)
      props.refreshers.delete(refresh)
    })
  })

  // Re-fetch when the active provider changes; `on` reads providerID() untracked
  // inside the callback so refresh()'s own store reads never retrigger this effect.
  createEffect(on(providerID, () => refresh(), { defer: true }))

  return (
    // OpenTUI drops a slot renderer whose first result is null before async status resolves.
    <text fg={props.api.theme.current.textMuted} flexShrink={0}>
      <Show
        when={debugging()}
        fallback={
          <Show when={provider()}>
            {(value) => {
              const usage = createMemo(() => usageDetails(value()))
              const usageColor = createMemo(() => {
                const theme = props.api.theme.current
                switch (usage()?.band) {
                  case "red":
                    return theme.error
                  case "orange":
                    return theme.warning
                  case "yellow":
                    return theme.info
                  default:
                    return theme.success
                }
              })
              return (
                <>
                  <span style={{ fg: props.api.theme.current.text }}>{text(value())}</span>
                  <Show when={usage()}>
                    {(details) => (
                      <>
                        {" · "}
                        <span style={{ fg: usageColor() }}>{details().percent}%</span>
                        <Show when={details().reset}>{(reset) => <> @{reset()}</>}</Show>
                      </>
                    )}
                  </Show>
                </>
              )
            }}
          </Show>
        }
      >
        <span style={{ fg: props.api.theme.current.warning }}>
          swap debug · {debug()}
        </span>
      </Show>
    </text>
  )
}

function currentSessionID(api: TuiPluginApi): string | undefined {
  const route = api.route.current
  if (route.name !== "session" || !("params" in route) || !route.params || typeof route.params.sessionID !== "string") return
  return route.params.sessionID
}

function currentSessionIsBusy(api: TuiPluginApi) {
  const sessionID = currentSessionID(api)
  if (!sessionID) return false
  const status = api.state.session.status(sessionID)
  return status !== undefined && status.type !== "idle"
}

function showConfirm(
  api: TuiPluginApi,
  options: Record<string, unknown> | undefined,
  choice: AccountChoice,
  args: string[],
  refreshers: Set<() => void>,
) {
  api.ui.dialog.replace(() => (
    <api.ui.DialogConfirm
      title="Switch OpenCode account"
      message={`Use ${choice.provider}:${choice.account}? The current session must be idle. Other OpenCode sessions or processes can still race token refresh.`}
      onCancel={() => api.ui.dialog.clear()}
      onConfirm={() => {
        if (currentSessionIsBusy(api)) {
          api.ui.dialog.clear()
          api.ui.toast({
            variant: "warning",
            message: "Wait for the current request to finish before switching accounts.",
          })
          return
        }
        api.ui.dialog.clear()
        void run(options, args)
          .then(() => {
            for (const refresh of refreshers) refresh()
            api.ui.toast({ variant: "success", message: `Using ${choice.provider}:${choice.account}` })
          })
          .catch(() => api.ui.toast({ variant: "error", message: "Account switch failed. Check opencode-swap output." }))
      }}
    />
  ))
}

async function showAccounts(api: TuiPluginApi, options: Record<string, unknown> | undefined, refreshers: Set<() => void>) {
  let summary: Status
  try {
    summary = await readStatus(options)
  } catch {
    api.ui.toast({ variant: "error", message: "Unable to read opencode-swap status." })
    return
  }

  const choices: TuiDialogSelectOption<AccountChoice>[] = summary.providers.flatMap((provider) =>
    provider.accounts.map((account) => ({
      title: account.name,
      value: { provider: provider.id, account: account.name },
      category: provider.id,
      description: account.type,
      footer: provider.active.state === "managed" && provider.active.name === account.name ? "active" : undefined,
    })),
  )
  if (choices.length === 0) {
    api.ui.toast({ variant: "info", message: "No saved opencode-swap accounts." })
    return
  }

  api.ui.dialog.replace(() => (
    <api.ui.DialogSelect
      title="Switch OpenCode account"
      placeholder="Find account"
      options={choices}
      onSelect={(selected) =>
        showConfirm(api, options, selected.value, ["use", selected.value.provider, selected.value.account, "--yes"], refreshers)
      }
    />
  ))
}

async function showNextAccount(api: TuiPluginApi, options: Record<string, unknown> | undefined, refreshers: Set<() => void>) {
  const sessionID = currentSessionID(api)
  const providerID = sessionID ? latestProviderID(api, sessionID) : undefined
  if (!providerID) {
    api.ui.toast({ variant: "info", message: "Send a request with a managed provider before switching to its next account." })
    return
  }

  let provider: ProviderStatus | undefined
  try {
    provider = (await readStatus(options)).providers.find((item) => item.id === providerID)
  } catch {
    api.ui.toast({ variant: "error", message: "Unable to read opencode-swap status." })
    return
  }
  if (!provider || provider.active.state !== "managed" || !provider.active.name) {
    api.ui.toast({ variant: "info", message: "Current provider has no managed active account to switch from." })
    return
  }

  const names = provider.accounts.map((account) => account.name).sort()
  const current = names.indexOf(provider.active.name)
  if (current === -1 || names.length === 0) {
    api.ui.toast({ variant: "error", message: "Saved account status is inconsistent. Run opencode-swap doctor." })
    return
  }
  const next = names[(current + 1) % names.length]
  if (!next) return
  showConfirm(api, options, { provider: provider.id, account: next }, ["use", provider.id, next, "--yes"], refreshers)
}

const tui: TuiPlugin = async (api, options) => {
  const config = options as Record<string, unknown> | undefined
  const refreshers = new Set<() => void>()

  api.slots.register({
    order: 100,
    slots: {
      session_prompt_right(_context, props) {
        return <StatusView api={api} options={config} sessionID={props.session_id} refreshers={refreshers} />
      },
    },
  })

  api.keymap.registerLayer({
    commands: [
      {
        name: "opencode-swap.accounts",
        title: "Switch OpenCode account",
        category: "Accounts",
        namespace: "palette",
        slashName: "swap",
        run() {
          void showAccounts(api, config, refreshers)
        },
      },
      {
        name: "opencode-swap.refresh",
        title: "Refresh OpenCode account status",
        category: "Accounts",
        namespace: "palette",
        slashName: "swap-refresh",
        run() {
          for (const refresh of refreshers) refresh()
        },
      },
      {
        name: "opencode-swap.next",
        title: "Switch to next OpenCode account",
        category: "Accounts",
        namespace: "palette",
        slashName: "swap-next",
        run() {
          void showNextAccount(api, config, refreshers)
        },
      },
    ],
  })
}

const plugin: TuiPluginModule & { id: string } = {
  id: "opencode-swap",
  tui,
}

export default plugin
