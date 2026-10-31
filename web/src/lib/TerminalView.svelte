<script lang="ts">
  import { onMount } from "svelte";
  import { Terminal } from "@xterm/xterm";
  import { FitAddon } from "@xterm/addon-fit";
  import "@xterm/xterm/css/xterm.css";
  import { api } from "./api";
  import { getTheme } from "./themes";
  import { terminalSocketUrl, terminalTheme } from "./terminal";
  import TerminalAgents from "./TerminalAgents.svelte";

  /**
   * One herdr client for kitty!'s session (Q1, Q12), drawn transparent in the
   * Harness theme. data-kitty-terminal covers xterm's hidden textarea, so bare
   * letters reach the shell; Ctrl+Shift+F still fullscreens this panel (Q4).
   */
  let { label }: { label: string } = $props();

  let host = $state<HTMLDivElement>();
  let notice = $state("Connecting to herdr…");
  let generation = 0;
  let terminal: Terminal | undefined;
  let socket: WebSocket | undefined;
  const encoder = new TextEncoder();

  function send(data: string | Uint8Array) {
    if (socket?.readyState === WebSocket.OPEN) socket.send(data);
  }

  async function connect() {
    const attempt = ++generation;
    socket?.close();
    notice = "Connecting to herdr…";
    try {
      const { token, socket: path } = await api<{ token: string; socket: string }>("/terminal/session", {});
      if (attempt !== generation || !terminal) return;
      const next = new WebSocket(terminalSocketUrl(window.location, path, token, terminal.cols, terminal.rows));
      next.binaryType = "arraybuffer";
      next.onopen = () => { if (attempt === generation) notice = ""; };
      next.onmessage = (event) => {
        terminal?.write(typeof event.data === "string" ? event.data : new Uint8Array(event.data as ArrayBuffer));
      };
      next.onclose = () => {
        if (attempt === generation) notice = "herdr detached. Its session keeps running.";
      };
      socket = next;
    } catch (cause) {
      if (attempt === generation) notice = (cause as Error).message;
    }
  }

  onMount(() => {
    const term = new Terminal({
      allowTransparency: true,
      theme: terminalTheme(getTheme("harness").roles),
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--font-mono").trim() || "monospace",
      fontSize: 13,
      cursorBlink: true,
      scrollback: 0,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host!);
    terminal = term;
    const refit = () => { if (host?.isConnected && host.clientHeight > 0) fit.fit(); };
    refit();
    term.onData((data) => send(encoder.encode(data)));
    term.onBinary((data) => send(Uint8Array.from(data, (char) => char.charCodeAt(0))));
    term.onResize(({ cols, rows }) => send(JSON.stringify({ type: "resize", cols, rows })));
    const resize = new ResizeObserver(refit);
    resize.observe(host!);
    void connect();
    return () => {
      generation += 1;
      resize.disconnect();
      socket?.close();
      term.dispose();
      terminal = undefined;
    };
  });
</script>

<section class="terminal-view" data-kitty-terminal data-kitty-panel="terminal" tabindex="-1" aria-label={label}>
  <TerminalAgents />
  <div class="terminal-host" bind:this={host}></div>
  {#if notice}<p class="terminal-notice" role="status">
      {notice}
      {#if !notice.startsWith("Connecting")}<button class="text-button" onclick={connect}>Reconnect</button>{/if}
    </p>{/if}
</section>

<style>
  .terminal-view {
    position: relative;
    display: flex;
    flex-direction: column;
    min-height: 0;
    height: 100%;
    background: transparent;
  }
  .terminal-host {
    flex: 1;
    min-width: 0;
    min-height: 0;
    padding: 4px 6px;
  }
  .terminal-host :global(.xterm),
  .terminal-host :global(.xterm-viewport),
  .terminal-host :global(.xterm-screen) {
    background: transparent !important;
  }
  .terminal-host :global(.xterm) { height: 100%; }
  .terminal-notice {
    position: absolute;
    right: 10px;
    bottom: 8px;
    margin: 0;
    padding: 4px 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-void, #141414) 80%, transparent);
    color: var(--muted);
    font: 10.5px var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
</style>
