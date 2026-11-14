<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api, type Bootstrap, type ModulesState } from "./api";
  import ModulePicker from "./ModulePicker.svelte";
  import { moduleStore } from "./modulePicker.svelte";
  import { tutorialSteps, type AccountState } from "./onboarding";
  import type { View } from "./workspace";
  import { currentTheme, THEME_EVENT } from "./themes";
  import { getTheme } from "./themes/catalog";

  const GUIDE = "https://github.com/ZacharySF/kitty/blob/main/docs/GOOGLE_ONBOARDING.md";
  // Bracket's Button types only button attributes; with `href` it renders an <a> that takes these too.
  const newTab = { target: "_blank", rel: "noopener noreferrer" };

  let { workspace, onview }: { workspace: Bootstrap | null; onview: (view: View) => void } = $props();
  let account = $state<AccountState | null>(null);
  let mode = $state<"welcome" | "tour" | "finish" | null>(null);
  // The welcome card's steps: account, the module picker, its restart, then the tour offer.
  let welcomeStep = $state<"account" | "modules" | "restart" | "tour">("account");
  /** The desktop app could not be asked to restart (a plain browser): the card says to. */
  let restartByHand = $state(false);
  let stepIndex = $state(0);
  const steps = $derived(tutorialSteps(workspace?.modules.enabled ?? []));
  const step = $derived(steps[Math.min(stepIndex, steps.length - 1)]);
  let dialog = $state<HTMLDialogElement>();
  let error = $state("");
  let busy = $state(false);
  let ticket = $state("");
  let authorizationUrl = $state("");
  let timer: ReturnType<typeof setTimeout> | undefined;
  let mounted = false;
  let rect = $state<{ left: number; top: number; width: number; height: number } | null>(null);
  let originalView: View = "today";
  let originalScroll = 0;
  let reveals = 0;
  // The welcome still is rendered once per scheme (web/public/art/brain-*.webp).
  let scheme = $state(getTheme(currentTheme()).scheme);

  $effect(() => {
    if (!dialog) return;
    if (mode && !dialog.open) dialog.showModal();
    else if (!mode && dialog.open) dialog.close();
  });
  // Views opened behind the tour may autofocus their own fields (WebKit honours that even
  // behind a modal dialog), and swapped-out buttons drop focus to the page; keep keyboard
  // focus on the guide while it is open.
  function keepFocus() {
    setTimeout(() => {
      if (!mode || !dialog?.open || dialog.contains(document.activeElement)) return;
      dialog.querySelector<HTMLElement>('.tour-card [data-variant="solid"], .welcome [data-variant="solid"]')?.focus({ preventScroll: true });
    });
  }
  $effect(() => {
    void [mode, welcomeStep, stepIndex];
    void tick().then(keepFocus);
  });
  $effect(() => {
    if (mode !== "tour") { rect = null; return; }
    const targetView = step.view;
    const selector = step.selector;
    untrack(() => {
      if (window.location.hash.slice(1).split("/")[0] !== targetView) onview(targetView);
    });
    reveals = 0;
    void tick().then(() => requestAnimationFrame(positionHighlight));
  });
  function positionHighlight() {
    if (mode !== "tour") return;
    const target = document.querySelector(step.selector);
    if (!target) { rect = null; return; }
    // Keep the spotlight on the part of the target that is actually visible below the workspace header.
    let bounds = target.getBoundingClientRect();
    const floor = window.innerHeight;
    // Views render after the step changes, so retry a few times until the target is in sight.
    if (reveals < 3) {
      const header = document.querySelector(".workbench-top");
      const ceiling = header && !header.contains(target) && !target.contains(header) ? header.getBoundingClientRect().bottom : 0;
      const hidden = bounds.top < ceiling || (bounds.bottom > floor && floor - bounds.top < Math.min(bounds.height, 200));
      const scroller = hidden ? scrollParent(target) : null;
      if (scroller) {
        reveals += 1;
        scroller.scrollBy({ top: bounds.top - ceiling - 16, behavior: "instant" });
        bounds = target.getBoundingClientRect();
      }
    }
    const top = Math.max(8, bounds.top - 6);
    const next = {
      left: Math.max(8, bounds.left - 6), top,
      width: Math.max(0, Math.min(window.innerWidth - 16, bounds.width + 12)),
      height: Math.max(0, Math.min(floor - top - 8, bounds.bottom + 6 - top)),
    };
    if (JSON.stringify(rect) !== JSON.stringify(next)) rect = next;
  }
  function scrollParent(element: Element): Element | null {
    for (let node = element.parentElement; node; node = node.parentElement) {
      const { overflowY } = getComputedStyle(node);
      if (/(auto|scroll)/.test(overflowY) && node.scrollHeight > node.clientHeight) return node;
    }
    return document.scrollingElement;
  }
  async function load() {
    try {
      account = await api<AccountState>("/account");
      if (!account.onboarding.welcome_completed) mode = "welcome";
      // The first run's restart lands here: the welcome is done, the tour is still on offer.
      else if (!account.onboarding.tutorial_completed) { welcomeStep = "tour"; mode = "welcome"; }
    } catch (cause) { error = `Could not load the welcome guide: ${(cause as Error).message}`; }
  }
  onMount(() => {
    mounted = true;
    void load();
    const reopen = (event: Event) => {
      error = "";
      const detail = (event as CustomEvent).detail;
      if (detail === "tutorial") startTour();
      else { welcomeStep = "account"; mode = "welcome"; void api<AccountState>("/account").then(value => account = value).catch(cause => error = cause.message); }
    };
    window.addEventListener("kitty-onboarding", reopen);
    const syncScheme = () => { scheme = getTheme(currentTheme()).scheme; };
    window.addEventListener(THEME_EVENT, syncScheme);
    window.addEventListener("resize", positionHighlight);
    window.addEventListener("scroll", positionHighlight, true);
    const observer = new MutationObserver(positionHighlight);
    observer.observe(document.getElementById("app")!, { childList: true, subtree: true });
    document.addEventListener("focusin", keepFocus);
    document.addEventListener("focusout", keepFocus);
    return () => {
      mounted = false;
      clearTimeout(timer);
      if (ticket) void api("/account/google/cancel", { ticket }).catch(() => {});
      observer.disconnect();
      window.removeEventListener("kitty-onboarding", reopen);
      window.removeEventListener(THEME_EVENT, syncScheme);
      window.removeEventListener("resize", positionHighlight);
      window.removeEventListener("scroll", positionHighlight, true);
      document.removeEventListener("focusin", keepFocus);
      document.removeEventListener("focusout", keepFocus);
    };
  });
  async function save(body: Record<string, boolean>) {
    account = await api<AccountState>("/account/onboarding", body);
  }
  async function connect() {
    busy = true; error = "";
    try {
      const started = await api<{ ticket: string; url: string }>("/account/google/start", {});
      ticket = started.ticket;
      authorizationUrl = started.url;
      timer = setTimeout(poll, 800);
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  async function poll() {
    const current = ticket;
    if (!current || !mounted) return;
    try {
      const result = await api<{ status: string; error?: string }>("/account/google/poll", { ticket: current });
      if (current !== ticket || !mounted) return;
      if (result.status === "pending") { timer = setTimeout(poll, 1000); return; }
      ticket = ""; authorizationUrl = "";
      if (result.status === "connected") account = await api<AccountState>("/account");
      else error = result.error ?? "Google sign-in could not complete. You can continue locally.";
    } catch (cause) { if (current === ticket && mounted) { ticket = ""; authorizationUrl = ""; error = (cause as Error).message; } }
  }
  async function cancelConnection() {
    clearTimeout(timer);
    const current = ticket;
    ticket = ""; authorizationUrl = "";
    if (current) await api("/account/google/cancel", { ticket: current }).catch(() => {});
  }
  async function continueWelcome() {
    await cancelConnection();
    welcomeStep = "modules"; error = "";
  }
  /** The picker saved: a changed selection restarts rika! by itself, else on to the tour offer. */
  async function afterModules(state: ModulesState) {
    error = "";
    if (!state.restart_required) { welcomeStep = "tour"; return; }
    try {
      await save({ welcome_completed: true });
      restartByHand = false;
      welcomeStep = "restart";
      restartByHand = !(await moduleStore.restart());
    } catch (cause) { error = (cause as Error).message; }
  }
  function startTour() {
    originalView = (window.location.hash.slice(1).split("/")[0] || "today") as View;
    originalScroll = window.scrollY;
    stepIndex = 0; error = ""; mode = "tour";
  }
  async function beginTour() {
    busy = true; error = "";
    try { await save({ welcome_completed: true }); startTour(); }
    catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  async function finish() {
    busy = true; error = "";
    try {
      await cancelConnection();
      await save({ welcome_completed: true, tutorial_completed: true });
      mode = null;
      onview(originalView);
      await tick();
      window.scrollTo(0, originalScroll);
      window.dispatchEvent(new Event("kitty-account-changed"));
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  function advance() { if (stepIndex < steps.length - 1) stepIndex += 1; else mode = "finish"; }
</script>

{#if error && !mode}<button class="guide-retry" onclick={() => { error = ""; void load(); }}>{error} Retry the welcome guide.</button>{/if}
<dialog bind:this={dialog} class:tour={mode === "tour"} class:wide={mode === "welcome" && welcomeStep === "modules"} class="onboarding" aria-labelledby="onboarding-title"
  oncancel={(event) => { event.preventDefault(); if (!moduleStore.busy) void finish(); }}>
  {#if mode === "tour"}
    {#if rect}<div class="spotlight" aria-hidden="true" style={`left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px`}></div>{:else}<div class="tour-shade"></div>{/if}
    <section class="tour-card" class:raised={rect !== null && rect.top + rect.height / 2 > window.innerHeight / 2 && rect.width > window.innerWidth / 2} aria-live="polite">
      <div class="tour-top">
        <div class="progress" role="progressbar" aria-label="Tour progress" aria-valuemin={1} aria-valuemax={steps.length} aria-valuenow={stepIndex + 1} aria-valuetext={`Step ${stepIndex + 1} of ${steps.length}`}>
          {#each steps as item, index (item.id)}<span class:done={index <= stepIndex}></span>{/each}
        </div>
        <Button size="sm" variant="ghost" onclick={finish} disabled={busy} aria-label="Skip tutorial">Skip</Button>
      </div>
      <h2 id="onboarding-title">{step.title}</h2>
      <p class="description">{step.text}</p>
      <p class="tip">{step.tip}</p>
      {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
      <footer>
        <Button variant="ghost" disabled={stepIndex === 0} onclick={() => stepIndex -= 1}>Back</Button>
        <Button variant="solid" intent="primary" onclick={advance}>{stepIndex === steps.length - 1 ? "Done" : "Next"}</Button>
      </footer>
    </section>
  {:else if mode}
    <div class="welcome">
      <div class="welcome-tools"><Button size="sm" variant="ghost" disabled={busy || moduleStore.busy} onclick={finish} aria-label="Skip onboarding">Skip</Button></div>
      {#if mode === "finish" || welcomeStep !== "modules"}<img class="brain" src={`/art/brain-${scheme}.webp`} alt="" width="760" height="652" />{/if}
      <div class="welcome-body">
        {#if mode === "finish"}
          <h1 id="onboarding-title">You're set</h1>
          <p class="note">The tour is in Settings, Account.</p>
          <Button variant="solid" intent="primary" size="lg" full disabled={busy} onclick={finish}>{busy ? "Saving…" : "Done"}</Button>
        {:else if welcomeStep === "account"}
          <h1 id="onboarding-title">Welcome to rika!</h1>
          {#if account?.identity}
            <p class="note">Signed in as {account.identity.email}</p>
            <Button variant="solid" intent="primary" size="lg" full onclick={continueWelcome}>Continue</Button>
          {:else if ticket}
            <p class="note" role="status">Finish signing in in your browser.</p>
            <Button variant="solid" intent="primary" size="lg" full href={authorizationUrl} {...newTab}>Open Google sign-in</Button>
            <Button variant="ghost" size="lg" full onclick={cancelConnection}>Cancel</Button>
          {:else}
            <Button size="lg" full disabled={!account?.configured || busy} onclick={connect}>
              {#snippet leading()}<svg class="google-g" viewBox="0 0 48 48" width="18" height="18" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>{/snippet}
              {busy ? "Opening…" : "Continue with Google"}
            </Button>
            <Button variant="solid" intent="primary" size="lg" full onclick={continueWelcome}>Start locally</Button>
            {#if account && !account.configured}<p class="note">Google sign-in needs <a href={GUIDE} target="_blank" rel="noopener noreferrer">setting up</a> first.</p>{/if}
          {/if}
        {:else if welcomeStep === "modules"}
          <h1 id="onboarding-title">What will you use rika! for?</h1>
          <p class="note">Pick any. You can change this later.</p>
          <ModulePicker question fresh={!account?.onboarding.welcome_completed} onsaved={afterModules} />
        {:else if welcomeStep === "restart"}
          {#if restartByHand}
            <h1 id="onboarding-title">Restart rika! to finish</h1>
            <Button variant="solid" intent="primary" size="lg" full onclick={() => (welcomeStep = "tour")}>Continue</Button>
          {:else}
            <h1 id="onboarding-title" role="status">Restarting rika!…</h1>
          {/if}
        {:else}
          <h1 id="onboarding-title">Take a quick tour?</h1>
          <Button variant="solid" intent="primary" size="lg" full disabled={busy} onclick={beginTour}>Show me around</Button>
          <Button variant="ghost" size="lg" full disabled={busy} onclick={finish}>Not now</Button>
        {/if}
        {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
      </div>
    </div>
  {/if}
</dialog>

<style>
  :global(:root body) .onboarding { color: var(--ink); background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 0; width: min(420px, calc(100vw - 24px)); max-width: none; max-height: calc(100dvh - 24px); overflow: auto; box-shadow: var(--overlay-shadow); }
  :global(:root body) .onboarding.wide { width: min(560px, calc(100vw - 24px)); }
  .wide .welcome-body { padding-top: 8px; }
  .wide h1, .wide .note { text-align: left; }
  .wide .note { margin-bottom: 6px; }
  :global(:root body) .onboarding::backdrop { background: color-mix(in srgb, var(--canvas) 82%, transparent); }
  .welcome-tools { display: flex; justify-content: flex-end; gap: 4px; padding: 10px 10px 0; }
  .brain { display: block; width: calc(100% - 40px); height: auto; aspect-ratio: 760 / 652; margin: 4px auto 20px; }
  .welcome-body { display: grid; gap: 10px; padding: 0 28px 28px; }
  h1 { font: 400 var(--fs-title)/1.15 var(--font-ui); margin: 0 0 8px; text-align: center; min-width: 0; overflow-wrap: break-word; }
  .note { margin: 0; color: var(--muted); font-size: var(--fs-label); line-height: 1.5; text-align: center; overflow-wrap: anywhere; }
  .google-g { flex: none; }
  :global(.br-btn:disabled) .google-g { filter: grayscale(1); opacity: .6; }
  .error-banner { margin: 0; font-size: var(--fs-label); }
  :global(:root body) .onboarding.tour { position: fixed; inset: 0; width: 100vw; height: 100dvh; max-height: none; margin: 0; padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; overflow: hidden; }:global(:root body) .onboarding.tour::backdrop { background: transparent; }
  .spotlight { position: fixed; border: 2px solid var(--accent); border-radius: 12px; box-shadow: 0 0 0 9999px color-mix(in srgb, var(--canvas) 76%, transparent); pointer-events: none; }.tour-shade { position: absolute; inset: 0; background: color-mix(in srgb, var(--canvas) 76%, transparent); }
  .tour-card { position: fixed; bottom: 24px; right: 24px; width: min(380px, calc(100vw - 32px)); max-height: calc(100dvh - 120px); overflow: auto; padding: 18px 20px 20px; border: 1px solid var(--border); border-radius: 16px; background: var(--surface); box-shadow: var(--overlay-shadow); }
  .tour-card.raised { bottom: auto; top: 76px; }
  .tour-top { display: flex; align-items: center; gap: 12px; }
  .progress { flex: 1; display: flex; gap: 4px; }.progress span { flex: 1; height: 3px; background: var(--border); border-radius: 3px; }.progress .done { background: var(--accent); }
  h2 { font: 600 20px/1.25 var(--font-ui); margin: 14px 0 8px; min-width: 0; overflow-wrap: break-word; }
  .description { font-size: 13.5px; line-height: 1.55; margin: 0 0 8px; }
  .tip { color: var(--muted); font-size: var(--fs-label); line-height: 1.5; margin: 0 0 16px; }
  footer { display: flex; justify-content: space-between; }
  .guide-retry { position: fixed; bottom: 20px; left: 20px; z-index: 100; padding: 12px; color: var(--ink); background: var(--surface); border: 1px solid var(--error); border-radius: 8px; }
  @media (max-height: 560px) { .tour-card { padding: 14px 18px; max-height: calc(100dvh - 32px); bottom: 16px; }.tour-card.raised { top: 16px; }h2 { margin: 8px 0 6px; } }
  @media (max-width: 760px) { .tour-card { right: 16px; bottom: 16px; } }
</style>
