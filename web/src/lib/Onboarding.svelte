<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { api, type Bootstrap } from "./api";
  import { DOCK_SELECTOR, tutorialSteps, type AccountState } from "./onboarding";
  import type { View } from "./workspace";
  import Icon from "./Icon.svelte";
  import ThemePicker from "./ThemePicker.svelte";

  let { workspace, onview }: { workspace: Bootstrap | null; onview: (view: View) => void } = $props();
  let account = $state<AccountState | null>(null);
  let mode = $state<"welcome" | "tour" | "finish" | null>(null);
  let welcomeStep = $state(0);
  let stepIndex = $state(0);
  let dockPresent = $state(false);
  const steps = $derived(tutorialSteps(workspace?.modules.enabled ?? [], dockPresent));
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
      dialog.querySelector<HTMLElement>(".tour-card .primary, .welcome-reading .primary")?.focus({ preventScroll: true });
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
      if (window.location.hash.slice(1) !== targetView) onview(targetView);
    });
    reveals = 0;
    void tick().then(() => requestAnimationFrame(positionHighlight));
  });
  function positionHighlight() {
    if (mode !== "tour") return;
    const target = document.querySelector(step.selector);
    if (!target) { rect = null; return; }
    // Keep the spotlight on the part of the target that is actually visible between the
    // workspace header and the floating dock, where the dock's card covers its column.
    let bounds = target.getBoundingClientRect();
    const dock = document.querySelector('[data-kitty-panel="dock"]');
    const card = dock && !dock.contains(target) ? dock.getBoundingClientRect() : null;
    const floor = card && card.left < bounds.right && card.right > bounds.left ? Math.min(window.innerHeight, card.top) : window.innerHeight;
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
      else if (!account.onboarding.tutorial_completed) startTour();
    } catch (cause) { error = `Could not load the welcome guide: ${(cause as Error).message}`; }
  }
  onMount(() => {
    mounted = true;
    void load();
    const reopen = (event: Event) => {
      error = "";
      const detail = (event as CustomEvent).detail;
      if (detail === "tutorial") startTour();
      else { welcomeStep = 0; mode = "welcome"; void api<AccountState>("/account").then(value => account = value).catch(cause => error = cause.message); }
    };
    window.addEventListener("kitty-onboarding", reopen);
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
    welcomeStep = 1; error = "";
  }
  function startTour() {
    originalView = (window.location.hash.slice(1) || "today") as View;
    originalScroll = window.scrollY;
    dockPresent = !!document.querySelector(DOCK_SELECTOR);
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

{#if error && !mode}<button class="guide-retry" onclick={() => { error = ""; void load(); }}>{error} · Retry welcome guide</button>{/if}
<dialog bind:this={dialog} class:tour={mode === "tour"} class="onboarding" aria-labelledby="onboarding-title"
  oncancel={(event) => { event.preventDefault(); void finish(); }}>
  {#if mode === "tour"}
    {#if rect}<div class="spotlight" aria-hidden="true" style={`left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px`}></div>{:else}<div class="tour-shade"></div>{/if}
    <section class="tour-card" class:raised={rect !== null && rect.top + rect.height / 2 > window.innerHeight / 2 && rect.width > window.innerWidth / 2} aria-live="polite">
      <div class="tour-top"><span class="eyebrow">THE KITTY FIELD GUIDE</span><button class="quiet" onclick={finish} disabled={busy} aria-label="Skip tutorial">Skip tour ↗</button></div>
      <div class="progress" aria-label={`Step ${stepIndex + 1} of ${steps.length}`}>
        {#each steps as item, index}<span class:done={index <= stepIndex}></span>{/each}
      </div>
      <p class="step-count">{String(stepIndex + 1).padStart(2, "0")} / {String(steps.length).padStart(2, "0")}{#if step.unavailable}<span>MODULE OFF</span>{/if}</p>
      <h2 id="onboarding-title">{step.title}</h2>
      <p class="description">{step.text}</p>
      <p class="tip"><Icon name="kitty" size={18} />{step.tip}</p>
      {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
      <footer><button class="quiet" disabled={stepIndex === 0} onclick={() => stepIndex -= 1}>← Back</button><button class="primary" onclick={advance}>{stepIndex === steps.length - 1 ? "Finish tour" : "Next"}<span>→</span></button></footer>
    </section>
  {:else if mode}
    <div class="welcome-shell">
      <header><span class="wordmark"><Icon name="kitty" size={27} />kitty<span class="brand-dot">!</span></span><ThemePicker /><button class="quiet" disabled={busy} onclick={finish} aria-label="Skip onboarding">Skip for now ↗</button></header>
      <div class="welcome-grid">
        <aside class="welcome-art" aria-hidden="true">
          <div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="orbit orbit-three"></div>
          <div class="art-center"><Icon name="kitty" size={72} /></div>
          <span class="art-node node-notes"><Icon name="notes" size={20} />A thought</span>
          <span class="art-node node-study"><Icon name="study" size={20} />A little focus</span>
          <span class="art-node node-atlas"><Icon name="atlas" size={20} />A place for it</span>
          <div class="art-caption"><span>LESS SCATTER. MORE SPACE.</span><p>Your next thought belongs here.</p></div>
        </aside>
        <section class="welcome-reading">
          <p class="eyebrow">{mode === "finish" ? "READY WHEN YOU ARE" : welcomeStep === 0 ? "WELCOME TO YOUR WORKSPACE" : "A QUICK LOOK AROUND"}</p>
          {#if mode === "finish"}
            <div class="completion-mark"><Icon name="kitty" size={35} /></div>
            <h1 id="onboarding-title">Make room for<br />your next idea.</h1>
            <p class="description">You're ready to explore. Start small: capture a thought, open your Atlas, or settle into a little focus.</p>
            <div class="privacy-note"><Icon name="shield" size={19} /><p>Everything you've seen is local. Reopen this tour anytime from Settings → Welcome & account.</p></div>
            <button class="primary wide" disabled={busy} onclick={finish}>{busy ? "Saving…" : "Let's make it yours"}<span>→</span></button>
          {:else if welcomeStep === 0}
            <h1 id="onboarding-title">A little less<br /><em>scattered.</em></h1>
            <p class="description">Your notes, learning and plans, in one calm workspace. Connect Google if you'd like—or keep things entirely local.</p>
            {#if account?.identity}
              <div class="connected"><span class="connected-dot"></span><div><strong>Google connected</strong><p>{account.identity.email}</p></div></div>
              <button class="primary wide" onclick={continueWelcome}>Continue<span>→</span></button>
            {:else if ticket}
              <div class="connection-wait" role="status"><strong>Finish in your browser</strong><p>Google sign-in opens outside kitty. Return here when you're finished.</p></div>
              <a class="primary wide" href={authorizationUrl} target="_blank" rel="noopener noreferrer">Open Google sign-in<span>↗</span></a>
              <button class="quiet wide" onclick={cancelConnection}>Cancel sign-in</button>
            {:else}
              <button class="google-button wide" disabled={!account?.configured || busy} onclick={connect}><svg class="google-g" viewBox="0 0 48 48" width="19" height="19" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>{busy ? "Preparing…" : "Continue with Google"}<span>↗</span></button>
              {#if account && !account.configured}<p class="setup-note">Google isn't configured yet. Local mode is ready now; connection setup is documented in Settings.</p>{/if}
              <button class="primary wide" onclick={continueWelcome}>Continue locally<span>→</span></button>
            {/if}
            <div class="privacy-note"><Icon name="shield" size={19} /><p>Optional means optional. Google connects an identity—not cloud sync, Gmail access, or a lock on your local workspace.</p></div>
          {:else}
            <h1 id="onboarding-title">Your space.<br /><em>Your pace.</em></h1>
            <p class="description">Take a short guided tour. We'll point out the real controls, without changing your setup or starting anything for you.</p>
            <div class="tour-preview">
              <div><span>01</span><p><strong>Find your way</strong>Workspace tabs, panels and your Atlas</p></div>
              <div><span>02</span><p><strong>Choose your tools</strong>Modules, quick notes and Study</p></div>
              <div><span>03</span><p><strong>Make yourself at home</strong>Keep it local. Come back whenever you want.</p></div>
            </div>
            <button class="primary wide" disabled={busy} onclick={beginTour}>Show me around<span>→</span></button>
            <button class="quiet wide" disabled={busy} onclick={finish}>I'll explore on my own</button>
          {/if}
          {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
          <p class="welcome-footnote">LOCAL FIRST. ALWAYS YOURS.</p>
        </section>
      </div>
    </div>
  {/if}
</dialog>

<style>
  :global(:root body) .onboarding { color: var(--ink); background: color-mix(in srgb, var(--surface) 92%, transparent); border: 1px solid var(--border); border-radius: 20px; padding: 0; width: min(1080px, calc(100vw - 48px)); max-width: none; max-height: calc(100dvh - 48px); overflow: auto; box-shadow: var(--overlay-shadow); backdrop-filter: blur(28px); }
  :global(:root body) .onboarding::backdrop { background: color-mix(in srgb, var(--canvas) 68%, transparent); backdrop-filter: blur(9px); }
  .welcome-shell > header { display: flex; align-items: center; gap: 20px; padding: 24px 30px; border-bottom: 1px solid var(--border); }
  .welcome-shell > header .wordmark { margin-right: auto; gap: 8px; }
  .welcome-grid { display: grid; grid-template-columns: 1fr 1fr; min-height: 610px; }
  .welcome-art { position: relative; min-width: 0; overflow: hidden; border-right: 1px solid var(--border); background: radial-gradient(ellipse at 50% 42%, color-mix(in srgb, var(--accent) 12%, transparent), transparent 65%); }
  .orbit { position: absolute; border: 1px solid color-mix(in srgb, var(--accent) 22%, var(--border)); border-radius: 50%; left: 50%; top: 43%; transform: translate(-50%, -50%) rotate(-25deg); }
  .orbit-one { width: 240px; height: 240px; }.orbit-two { width: 380px; height: 280px; }.orbit-three { width: 470px; height: 410px; border-style: dashed; opacity: .6; }
  .art-center { position: absolute; left: 50%; top: 43%; transform: translate(-50%, -50%); width: 126px; height: 126px; display: grid; place-items: center; border-radius: 36px; color: var(--accent); background: color-mix(in srgb, var(--surface-raised) 75%, transparent); border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--border)); box-shadow: 0 0 70px color-mix(in srgb, var(--accent) 12%, transparent); }
  .art-node { position: absolute; display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-radius: 12px; border: 1px solid var(--border); background: color-mix(in srgb, var(--surface-raised) 87%, transparent); font-size: 13px; color: var(--muted); }
  .node-notes { left: 9%; top: 20%; transform: rotate(-7deg); }.node-study { right: 5%; top: 52%; transform: rotate(7deg); }.node-atlas { left: 10%; top: 65%; transform: rotate(-4deg); }
  .art-caption { position: absolute; bottom: 32px; left: 34px; right: 24px; }.art-caption span, .eyebrow, .welcome-footnote { font: 10px var(--font-mono); letter-spacing: .14em; color: var(--muted); }.art-caption p { font: 23px var(--font-ui); max-width: 240px; margin: 12px 0 0; }
  .welcome-reading { padding: 38px 44px 24px; align-self: center; min-width: 0; }
  h1 { font: 500 clamp(34px, 4vw, 52px)/1.06 var(--font-ui); letter-spacing: -.045em; margin: 22px 0; }h1 em { font-style: normal; color: var(--accent); }
  .description { font-size: 14px; line-height: 1.75; color: var(--muted); margin: 0 0 25px; max-width: 43ch; }
  .primary, .google-button { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 46px; padding: 11px 16px; border-radius: 9px; font-weight: 600; text-decoration: none; font-size: 13px; }
  .primary { background: var(--accent); color: var(--on-accent); border: 1px solid var(--accent); }.primary:hover { filter: brightness(1.08); }.google-button { background: var(--surface-raised); border: 1px solid var(--border); color: var(--ink); }.google-g { flex: none; margin-right: auto; }.google-button:disabled .google-g { filter: grayscale(1); opacity: .6; }.google-button > span:last-child { margin-left: auto; }
  .wide { width: 100%; margin-top: 12px; }.quiet { background: transparent; color: var(--muted); font-size: 12px; padding: 8px; border-radius: 6px; }.quiet:hover { color: var(--ink); background: var(--wash); }.quiet.wide { min-height: 35px; }
  .privacy-note { display: flex; gap: 10px; margin-top: 23px; color: var(--muted); }.privacy-note :global(svg) { flex: none; margin-top: 3px; }.privacy-note p, .setup-note { font-size: 11px; line-height: 1.65; margin: 0; }.setup-note { color: var(--muted); margin: 10px 0 15px; }.welcome-footnote { margin: 30px 0 0; font-size: 9px; }
  .tour-preview { display: grid; gap: 17px; margin: 24px 0 28px; }.tour-preview > div { display: flex; align-items: start; gap: 13px; }.tour-preview span { font: 11px var(--font-mono); color: var(--accent); padding-top: 2px; }.tour-preview p { font-size: 12px; color: var(--muted); margin: 0; }.tour-preview strong { display: block; color: var(--ink); font-size: 13px; margin-bottom: 3px; }
  .connected { display: flex; gap: 12px; align-items: center; border: 1px solid var(--border); padding: 16px; border-radius: 10px; }.connected strong { font-size: 13px; }.connected p { margin: 4px 0 0; color: var(--muted); overflow-wrap: anywhere; font-size: 12px; }.connected-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success); }.connection-wait { padding: 14px; background: var(--wash); border-radius: 8px; font-size: 13px; }.connection-wait p { font-size: 12px; color: var(--muted); margin-bottom: 0; }.completion-mark { color: var(--accent); margin-top: 25px; }
  :global(:root body) .onboarding.tour { position: fixed; inset: 0; width: 100vw; height: 100dvh; max-height: none; margin: 0; padding: 0; border: 0; border-radius: 0; background: transparent; backdrop-filter: none; -webkit-backdrop-filter: none; box-shadow: none; overflow: hidden; }:global(:root body) .onboarding.tour::backdrop { background: transparent; backdrop-filter: none; }
  .spotlight { position: fixed; border: 2px solid var(--accent); border-radius: 12px; box-shadow: 0 0 0 9999px color-mix(in srgb, var(--canvas) 76%, transparent); pointer-events: none; }.tour-shade { position: absolute; inset: 0; background: color-mix(in srgb, var(--canvas) 76%, transparent); }
  .tour-card { position: fixed; bottom: 26px; right: 26px; width: min(420px, calc(100vw - 32px)); max-height: calc(100dvh - 120px); overflow: auto; padding: 24px; border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--border)); border-radius: 16px; background: color-mix(in srgb, var(--surface) 96%, transparent); backdrop-filter: blur(24px); box-shadow: var(--overlay-shadow); }.tour-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.tour-top .eyebrow { font-size: 9px; }.progress { display: flex; gap: 5px; margin: 17px 0 22px; }.progress span { flex: 1; height: 3px; background: var(--border); border-radius: 3px; }.progress .done { background: var(--accent); }.step-count { font: 10px var(--font-mono); color: var(--accent); display: flex; justify-content: space-between; }.step-count span { color: var(--muted); font-size: 9px; }h2 { font: 500 27px/1.18 var(--font-ui); letter-spacing: -.025em; margin: 12px 0 16px; }.tour-card .description { font-size: 13px; margin-bottom: 18px; }.tip { display: flex; gap: 10px; align-items: start; color: var(--muted); font-size: 11px; line-height: 1.6; padding: 13px 0; border-top: 1px solid var(--border); }.tip :global(svg) { flex: none; color: var(--accent); }footer { display: flex; justify-content: space-between; margin-top: 13px; }footer .primary { min-width: 125px; }.error-banner { margin-top: 12px; font-size: 12px; }.guide-retry { position: fixed; bottom: 20px; left: 20px; z-index: 100; padding: 12px; color: var(--ink); background: var(--surface); border: 1px solid var(--error); border-radius: 8px; }
  .tour-card.raised { bottom: auto; top: 76px; }
  @media (max-height: 560px) { .tour-card { padding: 16px 20px; max-height: calc(100dvh - 32px); bottom: 16px; }.tour-card.raised { top: 16px; }.tour-card h2 { font-size: 22px; margin: 4px 0 8px; }.progress { margin: 10px 0 12px; }.tour-card .description { margin-bottom: 10px; line-height: 1.55; }.tip { padding: 8px 0; } }
  @media (max-width: 760px) { :global(:root body) .onboarding:not(.tour) { width: calc(100vw - 24px); max-height: calc(100dvh - 24px); border-radius: 14px; }.welcome-grid { grid-template-columns: 1fr; min-height: 0; }.welcome-art { display: none; }.welcome-reading { padding: 28px 24px; }.welcome-shell > header { padding: 18px; gap: 8px; }h1 { font-size: 40px; }.tour-card { right: 16px; bottom: 16px; padding: 20px; } }
</style>
