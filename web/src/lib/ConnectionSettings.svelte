<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "./api";

  /**
   * Settings → Account: Microsoft (Outlook calendar and inbox) and Slack (a reaction
   * becomes a task). Both are read-only public OAuth clients of the owner's own app:
   * a client ID, never a secret (kitty.web_connect). Connect opens the provider's
   * sign-in in the browser; this page polls with the ticket only it holds.
   */
  type Provider = "microsoft" | "slack";
  type Account = { client_id: string; connected: boolean; account: string | null; last_sync: string | null; error: string | null };
  type State = { demo: boolean; microsoft: Account & { redirect: string }; slack: Account & { reaction: string; manifest: string; redirect: string } };
  const LABEL: Record<Provider, string> = { microsoft: "Microsoft", slack: "Slack" };
  const READS: Record<Provider, string> = {
    microsoft: "Reads your Outlook calendar and inbox. Nothing is sent or changed.",
    slack: "Reads your reactions. A message you react to becomes a task. Nothing is posted.",
  };
  // Bracket's Button types only button attributes; with `href` it renders an <a> that takes these too.
  const newTab = { target: "_blank", rel: "noopener noreferrer" };

  let connections = $state<State | null>(null);
  let error = $state<Record<Provider, string>>({ microsoft: "", slack: "" });
  let note = $state<Record<Provider, string>>({ microsoft: "", slack: "" });
  let busy = $state("");
  let clientId = $state<Record<Provider, string>>({ microsoft: "", slack: "" });
  let reaction = $state("");
  let pending = $state<{ provider: Provider; ticket: string; url: string } | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let mounted = true;

  function show(next: State) {
    connections = next;
    clientId = { microsoft: next.microsoft.client_id, slack: next.slack.client_id };
    reaction = next.slack.reaction;
  }
  async function run(provider: Provider, action: string, body: object = {}, done = "") {
    if (busy) return;
    busy = `${provider}:${action}`; error[provider] = ""; note[provider] = "";
    try { show(await api<State>(`/connect/${provider}/${action}`, body)); note[provider] = done; }
    catch (cause) { error[provider] = (cause as Error).message; await refresh(); }
    finally { busy = ""; }
  }
  async function refresh() {
    try { show(await api<State>("/connect")); }
    catch (cause) { error.microsoft = (cause as Error).message; }
  }
  onMount(() => void refresh());
  onDestroy(() => { mounted = false; clearTimeout(timer); });

  async function connect(provider: Provider) {
    if (busy) return;
    busy = `${provider}:start`; error[provider] = ""; note[provider] = "";
    try {
      const started = await api<{ ticket: string; url: string }>(`/connect/${provider}/start`, {});
      pending = { provider, ...started };
      timer = setTimeout(poll, 800);
    } catch (cause) { error[provider] = (cause as Error).message; }
    finally { busy = ""; }
  }
  async function poll() {
    const current = pending;
    if (!current || !mounted) return;
    try {
      const result = await api<{ status: string; error?: string }>(`/connect/${current.provider}/poll`, { ticket: current.ticket });
      if (current !== pending || !mounted) return;
      if (result.status === "pending") { timer = setTimeout(poll, 1000); return; }
      pending = null;
      if (result.status !== "connected") error[current.provider] = result.error ?? `${LABEL[current.provider]} sign-in could not complete.`;
      await refresh();
    } catch (cause) { if (current === pending && mounted) { pending = null; error[current.provider] = (cause as Error).message; } }
  }
  async function cancel() {
    clearTimeout(timer);
    const current = pending;
    pending = null;
    if (current) await api(`/connect/${current.provider}/cancel`, { ticket: current.ticket }).catch(() => {});
  }
  function disconnect(provider: Provider) {
    const keeps = provider === "microsoft" ? "Its calendars leave rika!; mail Rika already read stays." : "Tasks already made stay.";
    if (!window.confirm(`Disconnect ${LABEL[provider]}? ${keeps}`)) return;
    void run(provider, "disconnect");
  }
  async function copyManifest() {
    if (!connections) return;
    try { await navigator.clipboard.writeText(connections.slack.manifest); note.slack = "Manifest copied."; error.slack = ""; }
    catch { error.slack = "Clipboard unavailable; the manifest is in the guide under Slack."; }
  }
  const when = (value: string | null) => (value ? `Synced ${new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}` : "Not synced yet");
</script>

{#each ["microsoft", "slack"] as const as provider (provider)}
  {@const item = connections?.[provider]}
  <Panel title={LABEL[provider]} aria-label={LABEL[provider]}>
    {#if connections && item}
      <div class="rows">
        <div class="set-row">
          <span class="set-label">Account</span>
          {#if connections.demo}
            <span class="set-desc">Off in the demo workspace.</span>
          {:else if item.connected}
            <span class="set-desc"><span class="dot" aria-hidden="true"></span>Connected as <strong>{item.account}</strong> · {when(item.last_sync)}</span>
          {:else if pending?.provider === provider}
            <span class="set-desc" role="status">Finish signing in in your browser.</span>
          {:else}
            <span class="set-desc">{READS[provider]}</span>
          {/if}
          <span class="set-end">
            {#if pending?.provider === provider}
              <Button size="sm" intent="primary" href={pending.url} {...newTab}>Open {LABEL[provider]} sign-in</Button>
              <Button size="sm" variant="ghost" onclick={cancel}>Cancel</Button>
            {:else if item.connected}
              <Button size="sm" disabled={!!busy} onclick={() => run(provider, "sync", {}, "Synced.")}>{busy === `${provider}:sync` ? "Syncing…" : "Sync now"}</Button>
              <Button size="sm" variant="ghost" disabled={!!busy} onclick={() => disconnect(provider)}>Disconnect</Button>
            {:else}
              <Button size="sm" intent="primary" disabled={connections.demo || !!busy || !!pending || !item.client_id} onclick={() => connect(provider)}
                title={item.client_id ? undefined : "Save your app's client ID first"}>Connect</Button>
            {/if}
          </span>
        </div>
        {#if provider === "slack"}
          <div class="set-row">
            <span class="set-label">App</span>
            <span class="set-desc">Create your own Slack app: api.slack.com/apps → Create New App → From a manifest, then paste this.</span>
            <span class="set-end"><Button size="sm" onclick={copyManifest}>Copy manifest</Button></span>
          </div>
        {/if}
        <form class="set-row" onsubmit={(event) => { event.preventDefault(); void run(provider, "config", { client_id: clientId[provider] }, "Saved."); }}>
          <label class="set-label" for={`connect-${provider}-client`}>Client ID</label>
          <input id={`connect-${provider}-client`} type="text" autocomplete="off" spellcheck="false" bind:value={clientId[provider]} disabled={item.connected || connections.demo}
            placeholder={provider === "microsoft" ? "00000000-0000-0000-0000-000000000000" : "1234567890.1234567890"} />
          <span class="set-end"><Button size="sm" type="submit" disabled={item.connected || connections.demo || !!busy || clientId[provider].trim() === item.client_id}>Save</Button></span>
        </form>
        {#if provider === "slack"}
          <form class="set-row" onsubmit={(event) => { event.preventDefault(); void run("slack", "config", { reaction }, "Saved."); }}>
            <label class="set-label" for="connect-slack-reaction">Reaction</label>
            <input id="connect-slack-reaction" class="short" type="text" autocomplete="off" spellcheck="false" bind:value={reaction} disabled={connections.demo} />
            <span class="set-desc">The emoji name that makes a task.</span>
            <span class="set-end"><Button size="sm" type="submit" disabled={connections.demo || !!busy || reaction.trim() === connections.slack.reaction}>Save</Button></span>
          </form>
        {/if}
      </div>
      {#if provider === "microsoft"}
        <p class="set-note">Create the app at entra.microsoft.com → App registrations → New registration, with the redirect <code>{connections.microsoft.redirect}</code> under “Mobile and desktop applications”. The steps are in the guide under Microsoft.</p>
      {:else}
        <p class="set-note">The app's client ID is on its Basic Information page. rika! listens on <code>{connections.slack.redirect}</code> while you sign in.</p>
      {/if}
      {#if !item.connected && item.error && !error[provider]}<p class="set-note" role="status">{item.error}</p>{/if}
      {#if item.connected && item.error && !error[provider]}<p class="set-note" role="status">Last sync: {item.error}</p>{/if}
    {:else if !error[provider]}
      <p class="set-note" role="status">Loading…</p>
    {/if}
    {#if note[provider]}<p class="set-note" role="status">{note[provider]}</p>{/if}
    {#if error[provider]}<p class="set-note" role="alert">{error[provider]}</p>{/if}
  </Panel>
{/each}

<style>
  .dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    margin-right: 7px;
    border-radius: 999px;
    background: var(--success);
    vertical-align: 1px;
  }
  .set-desc {
    overflow-wrap: anywhere;
  }
  strong {
    color: var(--ink);
    font-weight: 500;
  }
  input {
    flex: 1;
    min-width: 0;
    max-width: 26rem;
    font: inherit;
  }
  input.short {
    flex: 0 1 10rem;
  }
  code {
    overflow-wrap: anywhere;
  }
</style>
