<script lang="ts">
  import Popover from "./ui/Popover.svelte";
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import {
    NAME_MAX, cancelPairing, codeComplete, countdown, deviceName, discoveryLabel, enterPairingCode, formatCode,
    listenLabel, loadDevices, pairable, pendingLabel, renameDevice, secondsLeft, setPaused,
    setPeerAddress, showPairingCode, statusLabel, statusTone, syncNow, unpair,
    type DevicesState, type Peer,
  } from "./devices";

  let devices = $state<DevicesState | null>(null);
  let error = $state("");
  let notice = $state("");
  let busy = $state(false);
  let now = $state(Date.now());
  /** Bumped by every change so an older poll response never overwrites a newer state. */
  let generation = 0;

  let editingName = $state(false);
  let nameDraft = $state("");

  let pairAddress = $state("");
  let pairCode = $state("");
  let pairing = $state(false);
  let pairError = $state("");
  let pairNotice = $state("");
  let codeInput = $state<HTMLInputElement | null>(null);

  /** The paired device whose address field is open. */
  let editingAddress = $state<string | null>(null);
  let addressDraft = $state("");

  const nearby = $derived(devices ? pairable(devices.discovered) : []);
  const canPair = $derived(!pairing && codeComplete(pairCode) && !!pairAddress.trim());

  async function refresh() {
    const started = generation;
    try {
      const next = await loadDevices();
      if (started === generation) { devices = next; now = Date.now(); error = ""; }
    } catch (cause) { if (started === generation) error = (cause as Error).message; }
  }
  onMount(() => {
    void refresh();
    // Sync status, nearby devices and an incoming pairing change while Settings is open.
    const poll = setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 2000);
    const clock = setInterval(() => { now = Date.now(); }, 1000);
    window.addEventListener("focus", refresh);
    return () => {
      clearInterval(poll);
      clearInterval(clock);
      window.removeEventListener("focus", refresh);
    };
  });

  async function run(action: () => Promise<DevicesState>, done = ""): Promise<boolean> {
    if (busy) return false;
    busy = true; error = ""; notice = "";
    generation += 1;
    try { devices = await action(); now = Date.now(); notice = done; return true; }
    catch (cause) { error = (cause as Error).message; await refresh(); return false; }
    finally { busy = false; }
  }

  function openName() { if (devices) { nameDraft = devices.device.name; editingName = true; notice = ""; } }
  async function saveName() {
    const name = deviceName(nameDraft);
    if (!name) { error = `A device name has 1 to ${NAME_MAX} characters`; return; }
    if (await run(() => renameDevice(name), "Device name saved.")) editingName = false;
  }

  function choose(address: string) {
    pairAddress = address;
    pairError = "";
    codeInput?.focus();
  }
  async function pair() {
    if (!canPair) return;
    const before = new Set(devices?.peers.map((peer) => peer.device_id) ?? []);
    pairing = true; pairError = ""; pairNotice = "";
    generation += 1;
    try {
      const next = await enterPairingCode(pairCode, pairAddress);
      devices = next; now = Date.now();
      const added = next.peers.find((peer) => !before.has(peer.device_id));
      pairNotice = added ? `Paired with ${added.name}.` : "Paired.";
      pairCode = ""; pairAddress = "";
    } catch (cause) { pairError = (cause as Error).message; }
    finally { pairing = false; }
  }

  function openAddress(peer: Peer) { editingAddress = peer.device_id; addressDraft = peer.address ?? ""; notice = ""; }
  async function saveAddress(peer: Peer, address: string | null) {
    const done = address?.trim() ? `${peer.name} is reached at ${address.trim()}.` : `${peer.name} is found automatically again.`;
    if (await run(() => setPeerAddress(peer.device_id, address), done)) editingAddress = null;
  }
  function remove(peer: Peer) {
    if (!window.confirm(`Unpair ${peer.name}? The two devices stop syncing and have to pair again with a new code. Notes already on either device stay there.`)) return;
    void run(() => unpair(peer.device_id), `${peer.name} unpaired.`);
  }
  const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "Never");
</script>

<Panel title="Devices" aria-label="Devices">
    <div class="devices">
      <p class="muted">Sync your notes between your own devices. Pair each device once with a short code; after that they find each other on the same network, or at an address you set (for example over Tailscale), and sync directly, end-to-end encrypted. Nothing goes through a server.</p>
      <p class="muted">In this version only notes sync. Jobs, applications and the calendar stay on each device.</p>
      {#if devices}
        {@const device = devices.device}
        <h3>This device</h3>
        <div class="row">
          <span class="who">
            {#if editingName}
              <form class="entry" onsubmit={(event) => { event.preventDefault(); void saveName(); }}>
                <label>Device name <input aria-label="Device name" autocomplete="off" maxlength={NAME_MAX} bind:value={nameDraft} /></label>
                <Button size="sm" intent="primary" type="submit" disabled={busy || !deviceName(nameDraft) || deviceName(nameDraft) === device.name}>Save</Button>
                <Button size="sm" variant="ghost" type="button" onclick={() => { editingName = false; }}>Cancel</Button>
              </form>
            {:else}
              <strong>{device.name}</strong>
            {/if}
            <small>Fingerprint <span class="mono">{device.fingerprint}</span></small>
            <small class={device.listening ? "" : "bad"}>{listenLabel(device)}</small>
            <small class={device.discovery === "error" ? "bad" : ""}>{discoveryLabel(device)}</small>
            {#if device.paused}<small class="warn">Sync is paused on this device.</small>{/if}
          </span>
          <span class="actions">
            {#if !editingName}<Button size="sm" disabled={busy} aria-label="Rename this device" onclick={openName}>Rename</Button>{/if}
            <Button size="sm" disabled={busy} aria-pressed={device.paused} onclick={() => run(() => setPaused(!device.paused), device.paused ? "Sync resumed." : "Sync paused.")}>{device.paused ? "Resume sync" : "Pause sync"}</Button>
          </span>
        </div>

        <h3>Pair a device</h3>
        <div class="pair">
          <div class="pair-side" role="group" aria-label="Show a code">
            <strong>Show a code</strong>
            {#if devices.pairing}
              {@const shown = devices.pairing}
              {#if shown.paired}
                <p role="status">Paired with {shown.paired.name}.</p>
                <span><Button size="sm" disabled={busy} onclick={() => run(cancelPairing)}>Done</Button></span>
              {:else}
                <span class="big-code" aria-label={`Pairing code ${shown.code}`}>{shown.code}</span>
                <small class="muted">{secondsLeft(shown.expires_at, now) > 0 ? `Expires in ${countdown(shown.expires_at, now)}` : "Expired"} · {shown.attempts_left} {shown.attempts_left === 1 ? "try" : "tries"} left</small>
                <small class="muted">Enter this code on the other device. Compare the fingerprints of both devices if you want to be sure.</small>
                <span class="actions">
                  {#if secondsLeft(shown.expires_at, now) === 0}<Button size="sm" disabled={busy} onclick={() => run(showPairingCode)}>New code</Button>{/if}
                  <Button size="sm" variant="ghost" disabled={busy} aria-label="Cancel pairing code" onclick={() => run(cancelPairing)}>Cancel</Button>
                </span>
              {/if}
            {:else}
              <small class="muted">Shows a code for five minutes that the other device enters.</small>
              <span><Button size="sm" disabled={busy} onclick={() => run(showPairingCode)}>Show a code</Button></span>
            {/if}
          </div>
          <form class="pair-side" aria-label="Enter a code" onsubmit={(event) => { event.preventDefault(); void pair(); }}>
            <strong>Enter a code</strong>
            {#if nearby.length}
              <ul class="nearby" aria-label="Devices showing a code">
                {#each nearby as found (found.device_id)}
                  <li><Button size="sm" variant={pairAddress === found.address ? "outline" : "ghost"} aria-pressed={pairAddress === found.address} aria-label={`Pair with ${found.name}`} onclick={() => choose(found.address)}>{found.name} <small>{found.address}</small></Button></li>
                {/each}
              </ul>
            {:else}
              <small class="muted">No device nearby is showing a code. Choose Show a code on the other device, or type its address.</small>
            {/if}
            <label>Address <input aria-label="Device address" autocomplete="off" spellcheck="false" placeholder="192.168.1.20 or laptop.tailnet.ts.net" disabled={pairing} bind:value={pairAddress} /></label>
            <label>Code <input bind:this={codeInput} aria-label="Code shown on the other device" class="mono" autocomplete="off" spellcheck="false" placeholder="ABC-DEF" disabled={pairing} value={pairCode} oninput={(event) => { pairCode = formatCode(event.currentTarget.value); event.currentTarget.value = pairCode; }} /></label>
            <span><Button size="sm" intent="primary" type="submit" disabled={!canPair}>{pairing ? "Pairing…" : "Pair"}</Button></span>
            {#if pairing}<small class="muted" role="status">Pairing… this can take a few seconds.</small>{/if}
            {#if pairNotice}<p role="status">{pairNotice}</p>{/if}
            {#if pairError}<p role="alert">{pairError}</p>{/if}
          </form>
        </div>

        <h3>Paired devices</h3>
        {#if devices.peers.length}
          <ul class="rows">
            {#each devices.peers as peer (peer.device_id)}
              <li class="row" data-device={peer.device_id}>
                <span class="who">
                  <span class="title"><strong>{peer.name}</strong> <span class={`badge ${statusTone(peer.status)}`} data-status={peer.status}>{statusLabel(peer, now)}</span></span>
                  <small>Last sync {when(peer.last_sync_at)} · paired {when(peer.paired_at)}</small>
                  {#if editingAddress === peer.device_id}
                    <form class="entry" onsubmit={(event) => { event.preventDefault(); void saveAddress(peer, addressDraft); }}>
                      <label>Address <input aria-label={`Address for ${peer.name}`} autocomplete="off" spellcheck="false" placeholder="192.168.1.20 or laptop.tailnet.ts.net" bind:value={addressDraft} /></label>
                      <Button size="sm" intent="primary" type="submit" disabled={busy || !addressDraft.trim()}>Save</Button>
                      {#if peer.address}<Button size="sm" variant="ghost" type="button" disabled={busy} onclick={() => saveAddress(peer, null)}>Find automatically</Button>{/if}
                      <Button size="sm" variant="ghost" type="button" onclick={() => { editingAddress = null; }}>Cancel</Button>
                    </form>
                  {:else if peer.address}
                    <small>Address {peer.address} (set by you)</small>
                  {:else if peer.last_address}
                    <small>Found at {peer.last_address}</small>
                  {:else}
                    <small>No address yet; it is found on this network when it is online.</small>
                  {/if}
                  {#if peer.pending > 0}<small>{pendingLabel(peer.pending)}</small>{/if}
                  {#if peer.holds_history}<small class="warn">Unsynced changes are keeping history from being compacted.</small>{/if}
                </span>
                <span class="actions">
                  <Button size="sm" disabled={busy || peer.status === "syncing" || device.paused} aria-label={`Sync ${peer.name} now`} onclick={() => run(() => syncNow(peer.device_id))}>Sync now</Button>
                  {#if editingAddress !== peer.device_id}<Button size="sm" disabled={busy} aria-label={`Edit address for ${peer.name}`} onclick={() => openAddress(peer)}>Edit address</Button>{/if}
                  <Button size="sm" variant="ghost" disabled={busy} aria-label={`Unpair ${peer.name}`} onclick={() => remove(peer)}>Unpair</Button>
                </span>
              </li>
            {/each}
          </ul>
          <span><Button size="sm" disabled={busy || device.paused} onclick={() => run(() => syncNow())}>Sync all now</Button></span>
        {:else}<p class="muted">No paired devices yet.</p>{/if}
      {:else if !error}<p class="muted" role="status">Loading devices…</p>{/if}
      <Popover label="How device sync works" placement="bottom-start" class="explain">
        {#snippet trigger(props)}<button type="button" class="text-button" {...props}>How device sync works</button>{/snippet}
        <p>Pairing happens once per device: the code proves both devices belong to you, and each remembers the other's key. Devices then connect to each other directly, on your local network or over Tailscale, and every connection is end-to-end encrypted. Both devices need kitty! running at the same time to sync; changes made offline sync the next time they meet.</p>
      </Popover>
      {#if notice}<p role="status">{notice}</p>{/if}
      {#if error}<p role="alert">{error}</p>{/if}
    </div>
</Panel>

<style>
  .devices { display: grid; gap: .8rem; }
  .devices p { margin: 0; }
  h3 { margin: .25rem 0 0; font-size: .9rem; }
  .muted { color: var(--muted); font-size: .8rem; line-height: 1.5; }
  .rows { list-style: none; margin: 0; padding: 0; display: grid; gap: .5rem; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; padding: .65rem .75rem; border: 1px solid var(--border-fine, var(--border)); border-radius: 8px; }
  .who { display: grid; gap: .15rem; flex: 1; min-width: 0; }
  .who small { color: var(--muted); line-height: 1.5; overflow-wrap: anywhere; }
  .who .bad { color: var(--danger, var(--warning)); }
  .who .warn { color: var(--warning); }
  .title { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; }
  .badge { font-size: .72rem; padding: .05rem .45rem; border: 1px solid var(--border-fine, var(--border)); border-radius: 999px; color: var(--muted); }
  .badge.ok { border-color: var(--success, var(--accent)); color: var(--success, var(--accent)); }
  .badge.busy { border-color: var(--accent); color: var(--accent); }
  .badge.bad { border-color: var(--danger, var(--warning)); color: var(--danger, var(--warning)); }
  .actions { display: flex; gap: .5rem; flex-wrap: wrap; }
  .entry { display: flex; flex-wrap: wrap; align-items: end; gap: .5rem; width: 100%; }
  .entry label { display: grid; gap: .2rem; font-size: .8rem; flex: 1; min-width: 12rem; }
  .pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: .75rem; }
  .pair-side { display: grid; align-content: start; gap: .5rem; padding: .65rem .75rem; border: 1px solid var(--border-fine, var(--border)); border-radius: 8px; }
  .pair-side label { display: grid; gap: .2rem; font-size: .8rem; }
  .big-code { font-family: var(--font-mono, monospace); font-size: 2rem; letter-spacing: .12em; font-variant-numeric: tabular-nums; }
  .mono { font-family: var(--font-mono, monospace); font-variant-numeric: tabular-nums; }
  .nearby { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: .4rem; }
  .nearby small { color: var(--muted); }
  :global(.explain) p { margin: 0; max-width: 36rem; font-size: 12px; line-height: 1.7; color: var(--muted); }
</style>
