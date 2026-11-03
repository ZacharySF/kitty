<script lang="ts">
  import AgentSkillsSwitch from "./AgentSkillsSwitch.svelte";
  import { api } from "./api";
  import { launchPreview, type AgentKind } from "./agentSkills";
  import { agentSkills } from "./agentSkills.svelte";
  import Select from "./ui/Select.svelte";

  type AgentInfo = { binary: string | null; session: string; cwd: string; default: string; kinds: string[] };
  let info = $state<AgentInfo | null>(null);
  let choosing = $state(false);
  let external = $state(false);
  let busy = $state(false);
  let kind = $state("omp");
  let error = $state("");
  let notice = $state("");
  /** Agent skills switched off for the next launch, per kind (docs/agent-skills-design.md Q5 C). */
  let off = $state<Record<string, string[]>>({});
  try { kind = localStorage.getItem("kitty.coding-agent") || "omp"; } catch { /* Storage is optional. */ }

  const quote = (text: string) => `'${text.replaceAll("'", "'\\''")}'`;
  const command = $derived(info?.binary ? `${quote(info.binary)} --session ${quote(info.session)}` : "");
  const entry = $derived(agentSkills.kind(kind));
  const dropped = $derived(new Set(off[kind] ?? []));
  const names = $derived(entry?.skills.map((skill) => skill.name) ?? []);

  async function load() {
    try {
      info = await api<AgentInfo>("/terminal/agents");
      if (!info.kinds.includes(kind)) kind = info.default;
      error = info.binary ? "" : "The bundled herdr runtime is missing; reinstall rika!";
    } catch (cause) { error = (cause as Error).message; }
  }
  async function toggleChooser() {
    choosing = !choosing;
    if (choosing) await Promise.all([load(), agentSkills.load(true)]);
  }
  async function toggleExternal() {
    external = !external;
    if (external) await load();
  }
  function check(name: string, on: boolean) {
    const rest = (off[kind] ?? []).filter((item) => item !== name);
    off = { ...off, [kind]: on ? rest : [...rest, name] };
  }
  async function launch() {
    if (busy) return;
    busy = true;
    error = notice = "";
    try {
      try { localStorage.setItem("kitty.coding-agent", kind); } catch { /* Storage is optional. */ }
      const skillsOff = entry?.enabled ? names.filter((name) => dropped.has(name)) : [];
      const result = await api<{ tab: string; pane: string; notice?: string }>("/terminal/agents", skillsOff.length ? { kind, skills_off: skillsOff } : { kind });
      notice = `Agent tab opened (${result.tab}, ${result.pane}).`;
      // Codex's hook trust: said once, then the session log covers it quietly.
      if (result.notice) {
        let told = false;
        try { told = localStorage.getItem("kitty.codex-hooks-told") === "1"; localStorage.setItem("kitty.codex-hooks-told", "1"); } catch { /* Storage is optional. */ }
        if (!told) notice += ` ${result.notice}`;
      }
      choosing = false;
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
</script>

<div class="agent-tools">
  <div class="agent-actions">
    <button class="text-button" aria-expanded={choosing} onclick={toggleChooser} disabled={busy}>New Agent tab</button>
    <button class="text-button" aria-expanded={external} onclick={toggleExternal}>Attach external agent</button>
    {#if notice}<span role="status">{notice}</span>{/if}
  </div>
  {#if choosing && info}
    <form onsubmit={(event) => { event.preventDefault(); void launch(); }}>
      <div class="row">
        <div class="field"><span>Coding agent</span>
          <Select label="Coding agent" bind:value={kind} disabled={busy} options={info.kinds.map((option) => ({ value: option, label: option }))} />
        </div>
        <button class="text-button" disabled={busy || !info.binary}>{busy ? "Starting agent…" : "Start agent"}</button>
        <span>Own Agent tab · herdr skill · workspace directory · installed agent required</span>
      </div>
      {#if entry?.enabled}
        <fieldset class="checklist" aria-label={`Agent skills for this ${entry.label} launch`}>
          <legend>Skills for this launch <small>{names.length - names.filter((name) => dropped.has(name)).length} of {names.length}</small></legend>
          {#each entry.skills as skill (skill.name)}
            <label title={skill.description}><input type="checkbox" checked={!dropped.has(skill.name)} disabled={busy}
              onchange={(event) => check(skill.name, event.currentTarget.checked)} /> {skill.name}</label>
          {:else}<span>No agent skills found for {entry.label}.</span>{/each}
        </fieldset>
        <pre class="args" aria-label="Launch arguments">{launchPreview(kind as AgentKind, names, dropped)}</pre>
        <span>This tab only · your ~/.omp, ~/.claude and ~/.codex stay untouched</span>
      {:else if entry}
        <AgentSkillsSwitch {entry} />
      {/if}
    </form>
  {/if}
  {#if error}<p role="alert">{error}</p>{/if}
  {#if external && info && command}
    <section class="external" aria-label="External agent attachment">
      <p>Use rika!'s named session, never herdr's default. Open the terminal first. Each external agent creates its own labelled tab; use the returned workspace, tab and root_pane IDs, not another client's focused pane.</p>
      <pre>{command} workspace list
{command} tab create --workspace &lt;workspace-id&gt; --cwd {quote(info.cwd)} --label 'My agent' --no-focus
{command} --skill</pre>
      <p>Read herdr's skill before controlling panes. To view this same session from a terminal outside herdr:</p>
      <pre>env -u HERDR_ENV {command}</pre>
      <p>Tabs and agent states appear live here and on the Rika bar. CLI access grants full shell access as your user.</p>
    </section>
  {/if}
</div>

<style>
  .agent-tools { flex: none; padding: 3px 8px; border-bottom: 1px solid var(--hairline); font: 10.5px var(--font-mono); color: var(--muted); }
  .agent-actions, .row, .field { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
  form { display: grid; gap: 4px; padding: 4px 0; }
  p { margin: 4px 0; }
  [role="alert"] { color: var(--br-caution, #e8c872); }
  .external { max-height: 180px; overflow: auto; padding: 6px 0; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; color: var(--ink); margin: 6px 0; font: inherit; user-select: text; }
  .checklist { display: flex; flex-wrap: wrap; gap: 4px 12px; max-height: 120px; overflow: auto; margin: 0; padding: 4px 0; border: 0; }
  legend { float: left; width: 100%; padding: 0; color: var(--ink); }
  legend small { margin-left: 6px; color: var(--subtle); }
  .checklist label { display: inline-flex; align-items: center; gap: 5px; color: var(--ink); cursor: pointer; }
  .args { margin: 0; }
</style>
