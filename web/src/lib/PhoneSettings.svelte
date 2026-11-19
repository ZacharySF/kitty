<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "./api";
  import Select from "./ui/Select.svelte";

  /**
   * Settings → Connected apps → Phone. The phone surface is a second listener that serves
   * only /phone/v1 (kitty.core.phone): off until a network is chosen here. Adding a phone
   * shows a short address to open on the phone once, for five minutes; the phone's token
   * is shown there, never in this window.
   */
  type Scope = "capture" | "rika" | "read";
  type Phone = { id: string; name: string; scopes: Scope[]; created_at: string; last_used_at: string | null };
  type State = {
    network: "off" | "wifi" | "tailscale"; port: number; listening: boolean; address: string | null; error: string | null;
    addresses: { wifi: string | null; tailscale: string | null }; phones: Phone[];
    setup?: { url: string; code: string; expires_in: number };
  };
  const ALLOWS: { value: string; label: string; scopes: Scope[] }[] = [
    { value: "inbox", label: "Add to the Inbox", scopes: ["capture"] },
    { value: "rika", label: "Also ask Rika", scopes: ["capture", "rika"] },
    { value: "today", label: "Also show today", scopes: ["capture", "rika", "read"] },
  ];
  let phone = $state<State | null>(null);
  let error = $state("");
  let busy = $state(false);
  let adding = $state(false);
  let name = $state("iPhone");
  let allow = $state("inbox");
  let setup = $state<State["setup"] | null>(null);

  async function run(path: string, body?: object) {
    if (busy) return;
    busy = true; error = "";
    try {
      const next = await api<State>(path, body);
      phone = next;
      if (next.setup) { setup = next.setup; adding = false; }
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  onMount(() => void run("/phone"));
  function remove(item: Phone) {
    if (!window.confirm(`Remove ${item.name}? Its shortcut stops working at once.`)) return;
    void run("/phone/devices/remove", { id: item.id });
  }
  const can = (scopes: Scope[]) => [...ALLOWS].reverse().find((item) => item.scopes.every((scope) => scopes.includes(scope)))?.label ?? "";
  const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "never");
</script>

<Panel title="Phone" aria-label="Phone">
  <div class="phone">
    <p class="muted">An iPhone can add to rika! with a Siri shortcut (“Hey Siri, add to Rika”) while it is on the same network as this computer. Nothing is installed on the phone.</p>
    {#if phone}
      <Select label="Where phones reach rika!" kind="segments" value={phone.network} disabled={busy}
        options={[{ value: "off", label: "Off" }, { value: "wifi", label: "This Wi-Fi", hint: phone.addresses.wifi ?? undefined }, { value: "tailscale", label: "Tailscale", hint: phone.addresses.tailscale ?? undefined }]}
        onchange={(network) => run("/phone/network", { network })} />
      {#if phone.error}<p role="alert">{phone.error}</p>{/if}
      {#if phone.listening}
        <p class="muted">Listening at <code>{phone.address}</code>. If phones cannot reach it, allow port {phone.port} in this computer's firewall.{#if phone.network === "wifi"}{" "}On Wi-Fi a phone's token travels unencrypted; Tailscale is the safer choice.{/if}</p>
      {/if}
      {#if phone.phones.length}
        <ul class="rows">
          {#each phone.phones as item (item.id)}
            <li class="row">
              <span class="who"><strong>{item.name}</strong><small>{can(item.scopes)} · last used {when(item.last_used_at)}</small></span>
              <Button size="sm" disabled={busy} onclick={() => remove(item)}>Remove</Button>
            </li>
          {/each}
        </ul>
      {/if}
      {#if setup}
        <div class="setup" role="status">
          <p>On the phone, open this address in Safari within five minutes:</p>
          <p class="address">{setup.url}</p>
          <p class="muted">It shows the phone's token once, to paste into the shortcut. The steps for the shortcut are in the guide under Phone.</p>
          <button type="button" class="text-button" onclick={() => (setup = null)}>Done</button>
        </div>
      {:else if adding}
        <form class="add" onsubmit={(event) => { event.preventDefault(); void run("/phone/devices", { name, scopes: ALLOWS.find((item) => item.value === allow)!.scopes }); }}>
          <label>Name <input type="text" bind:value={name} maxlength="80" required /></label>
          <Select label="What this phone may do" kind="segments" value={allow} options={ALLOWS.map(({ value, label }) => ({ value, label }))} onchange={(value) => (allow = value)} />
          <span class="actions"><Button size="sm" intent="primary" type="submit" disabled={busy}>Add phone</Button><button type="button" class="text-button" onclick={() => (adding = false)}>Cancel</button></span>
        </form>
      {:else if phone.listening}
        <span><Button size="sm" onclick={() => (adding = true)}>Add a phone</Button></span>
      {/if}
    {:else if !error}<p class="muted" role="status">Loading phone settings…</p>{/if}
    {#if error}<p role="alert">{error}</p>{/if}
  </div>
</Panel>

<style>
  .phone { display: grid; gap: .8rem; justify-items: start; }
  .phone p { margin: 0; }
  .muted { color: var(--muted); font-size: 14px; line-height: 1.5; }
  .rows { list-style: none; margin: 0; padding: 0; display: grid; gap: .5rem; width: 100%; }
  .row { display: flex; align-items: center; gap: .75rem; padding: .65rem 0; border-top: 1px solid var(--hairline); }
  .who { display: grid; gap: .15rem; flex: 1; min-width: 0; }
  .who small { color: var(--muted); }
  .setup, .add { display: grid; gap: .6rem; justify-items: start; padding-top: .6rem; border-top: 1px solid var(--hairline); width: 100%; }
  .address { font-size: 20px; overflow-wrap: anywhere; user-select: all; }
  .add label { display: grid; gap: .3rem; font-size: 14px; color: var(--muted); }
  .add input { font: inherit; font-size: 16px; padding: .45rem .6rem; min-width: 16rem; }
  .actions { display: flex; align-items: center; gap: .9rem; }
  code { overflow-wrap: anywhere; }
</style>
