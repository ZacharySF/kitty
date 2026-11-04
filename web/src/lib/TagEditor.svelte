<!-- One tag's name, keywords, aliases, mode and short-term opt-in (Settings → Tags and tag pages). -->
<script lang="ts">
  import { MODE_LABELS, parseTerms, shortTerms, termsText, updateTag, type TagMode, type TagRecord } from "./tags";

  let { tag, globalMode, onsaved }: { tag: TagRecord; globalMode: TagMode; onsaved: (tag: TagRecord) => void } = $props();
  let name = $state("");
  let keywords = $state("");
  let aliases = $state("");
  let mode = $state<"" | TagMode>("");
  let short = $state(false);
  let error = $state("");
  let saving = $state(false);
  let loaded = "";

  $effect(() => {
    // Reload the fields when another tag (or a newer save of this one) arrives.
    const key = `${tag.id}:${tag.revision}:${tag.name}:${tag.keywords.join("\u0000")}:${tag.aliases.join("\u0000")}:${tag.mode}:${tag.short}`;
    if (key === loaded) return;
    loaded = key;
    name = tag.name;
    keywords = termsText(tag.keywords);
    aliases = termsText(tag.aliases);
    mode = tag.mode ?? "";
    short = tag.short;
  });

  const ignored = $derived(shortTerms([...parseTerms(keywords), ...parseTerms(aliases), name], short));

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (saving) return;
    saving = true;
    error = "";
    try {
      onsaved(await updateTag(tag.id, { name, keywords: parseTerms(keywords), aliases: parseTerms(aliases), mode: mode || null, short }));
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      saving = false;
    }
  }
</script>

<form class="tag-editor" aria-label={`Edit tag ${tag.name}`} onsubmit={save}>
  <label>Name <input bind:value={name} maxlength="100" required /></label>
  <label>Keywords <input bind:value={keywords} placeholder="comma-separated, e.g. pandas, numpy" /></label>
  <label>Aliases <input bind:value={aliases} placeholder="other names; #alias also tags" /></label>
  <label>Mode
    <select bind:value={mode}>
      <option value="">Follow the Tags setting ({MODE_LABELS[globalMode]})</option>
      <option value="suggest">Suggest</option>
      <option value="always">Always attach</option>
    </select>
  </label>
  <label class="check"><input type="checkbox" bind:checked={short} /> Also match terms shorter than 3 characters</label>
  {#if ignored.length}<p class="muted">Not matched (shorter than 3 characters): {ignored.join(", ")}</p>{/if}
  {#if error}<p role="alert">{error}</p>{/if}
  <div class="actions"><button type="submit" disabled={saving}>{saving ? "Saving…" : "Save tag"}</button></div>
</form>

<style>
  .tag-editor { display: grid; gap: .5rem; font-size: 13px; }
  label { display: grid; gap: .2rem; color: var(--muted); font-size: 12px; }
  label.check { display: flex; align-items: center; gap: .4rem; color: var(--ink); }
  input:not([type=checkbox]), select { font: inherit; font-size: 13px; color: var(--ink); background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; }
  .muted { margin: 0; color: var(--muted); font-size: 12px; }
  .actions { display: flex; gap: .5rem; }
  button { font: inherit; font-size: 12px; cursor: pointer; color: var(--on-accent, #fff); background: var(--accent); border: 0; border-radius: 6px; padding: 5px 10px; }
  button:disabled { opacity: .6; cursor: default; }
  [role=alert] { margin: 0; color: var(--error); }
</style>
