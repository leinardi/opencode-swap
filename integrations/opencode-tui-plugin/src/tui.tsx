/** @jsxImportSource @opentui/solid */

import type { TuiDialogSelectOption, TuiPlugin, TuiPluginApi, TuiPluginModule } from "@opencode-ai/plugin/tui"
import { Show, createEffect, createSignal, onCleanup } from "solid-js"

type Account = {
  name: string
  type: string
}

type Active = {
  state: "managed" | "unmanaged" | "none"
  name?: string
}

type Usage = {
  applicable: boolean
  available?: boolean
  used_percent?: number
  reset_at?: number
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

function command(options: Record<string, unknown> | undefined): string {
  const value = options?.command
  return typeof value === "string" && value.trim() ? value : "opencode-swap"
}

async function run(options: Record<string, unknown> | undefined, args: string[]) {
  const process = Bun.spawn([command(options), ...args], {
    stdin: "ignore",
    stdout: "pipe",
    stderr: "pipe",
  })
  const [exitCode, stdout] = await Promise.all([process.exited, new Response(process.stdout).text()])
  if (exitCode !== 0) throw new Error("opencode-swap command failed")
  return stdout
}

function status(value: unknown): Status {
  if (!value || typeof value !== "object") throw new Error("invalid status response")
  const result = value as Partial<Status>
  if (result.schema_version !== 1 || !Array.isArray(result.providers)) throw new Error("unsupported status response")
  return result as Status
}

async function readStatus(api: TuiPluginApi, options: Record<string, unknown> | undefined, provider?: string, usage = false) {
  const args = ["status"]
  if (provider) args.push(provider)
  args.push("--json")
  if (usage) args.push("--usage")
  return status(JSON.parse(await run(options, args)))
}

function latestProviderID(api: TuiPluginApi, sessionID: string) {
  const messages = api.state.session.messages(sessionID)
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role === "user" && message.model?.providerID) return message.model.providerID
  }
}

async function currentStatus(api: TuiPluginApi, options: Record<string, unknown> | undefined, sessionID: string) {
  const providerID = latestProviderID(api, sessionID)
  if (!providerID) return

  const summary = await readStatus(api, options)
  const provider = summary.providers.find((item) => item.id === providerID)
  if (!provider || provider.accounts.length === 0) return
  if (provider.active.state !== "managed") return provider

  return (await readStatus(api, options, providerID, true)).providers[0]
}

function text(provider: ProviderStatus) {
  if (provider.active.state === "unmanaged") return "unmanaged account"
  if (provider.active.state === "none") return "no active account"
  const usage = provider.usage
  if (usage?.applicable && usage.available && typeof usage.used_percent === "number") {
    return `${provider.active.name} · ${Math.round(usage.used_percent)}%`
  }
  return provider.active.name
}

function StatusView(props: {
  api: TuiPluginApi
  options: Record<string, unknown> | undefined
  sessionID: string
  refreshers: Set<() => void>
}) {
  const [provider, setProvider] = createSignal<ProviderStatus>()
  let generation = 0

  const refresh = () => {
    const request = ++generation
    void currentStatus(props.api, props.options, props.sessionID)
      .then((result) => {
        if (request === generation) setProvider(result)
      })
      .catch(() => {
        if (request === generation) setProvider(undefined)
      })
  }

  createEffect(() => {
    refresh()
    const interval = setInterval(refresh, POLL_INTERVAL_MS)
    const unlistenMessage = props.api.event.on("message.updated", refresh)
    const unlistenSession = props.api.event.on("session.updated", refresh)
    props.refreshers.add(refresh)
    onCleanup(() => {
      generation += 1
      clearInterval(interval)
      unlistenMessage()
      unlistenSession()
      props.refreshers.delete(refresh)
    })
  })

  return (
    <Show when={provider()}>
      {(value) => (
        <text fg={props.api.theme.current.textMuted} flexShrink={0}>
          {text(value())}
        </text>
      )}
    </Show>
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
    summary = await readStatus(api, options)
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
    provider = (await readStatus(api, options)).providers.find((item) => item.id === providerID)
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
