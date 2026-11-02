<script lang="ts" module>
  export type SelectOption = {
    value: string;
    label: string;
    /** A short muted note after the label ("Doesn't reschedule", an offset). */
    hint?: string;
    /** A CSS colour for the option's dot (a subject, deck or calendar colour). */
    color?: string;
    /** A count badge (cards due, items). */
    count?: number | string;
    disabled?: boolean;
  };
  export type SelectKind = "auto" | "segments" | "list" | "search" | "timezone";

  let zones: SelectOption[] | undefined;
  /** Every IANA time zone with its current UTC offset, built once on first use. */
  export function timeZoneOptions(): SelectOption[] {
    if (zones) return zones;
    const all = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") ?? ["UTC"];
    const now = new Date();
    zones = (all.includes("UTC") ? all : ["UTC", ...all]).map((zone) => ({
      value: zone,
      label: zone.replaceAll("_", " "),
      hint: new Intl.DateTimeFormat("en", { timeZone: zone, timeZoneName: "shortOffset" }).formatToParts(now).find((part) => part.type === "timeZoneName")?.value ?? "",
    }));
    return zones;
  }
</script>

<script lang="ts">
  import { ToggleGroup } from "@nunera/bracket";
  import Popover from "./Popover.svelte";

  /**
   * Q7: kitty!'s one dropdown. Four or fewer choices become segments (Bracket's
   * ToggleGroup); longer lists open the themed glass listbox; `search` adds a filter
   * field (and an optional create row) for lists that grow, such as decks and
   * notebooks; `timezone` is the searchable list of IANA zones. The browser's own
   * select menu is never shown.
   */
  let {
    value = $bindable(""),
    options = [],
    label,
    kind = "auto",
    placeholder = "Choose…",
    id,
    name,
    disabled = false,
    oncreate,
    createLabel = (query: string) => `Create “${query}”`,
    createAlways = false,
    onchange,
  }: {
    value?: string;
    options?: SelectOption[];
    /** Accessible name; also the search field's "Search …" name. */
    label: string;
    kind?: SelectKind;
    placeholder?: string;
    /** For an external `<label for>`. */
    id?: string;
    /** Submits the value with a surrounding form. */
    name?: string;
    disabled?: boolean;
    /** Offers "Create “query”" in a searchable list. */
    oncreate?: (query: string) => void;
    createLabel?: (query: string) => string;
    /** Keeps the create row (e.g. "New deck") even before anything is typed. */
    createAlways?: boolean;
    onchange?: (value: string) => void;
  } = $props();

  const uid = $props.id();
  const listId = `${uid}-list`;
  const mode = $derived(
    kind === "timezone" ? "search" : kind === "auto" ? (options.length <= 4 && !oncreate ? "segments" : "list") : kind,
  );
  let open = $state(false);
  let query = $state("");
  let active = $state(0);
  let typed = "";
  let typedAt = 0;
  let popover = $state<{ close: (refocus?: boolean) => void }>();
  let triggerElement = $state<HTMLButtonElement>();
  let listElement = $state<HTMLElement>();

  const all = $derived(kind === "timezone" && !options.length ? (open ? timeZoneOptions() : []) : options);
  const current = $derived(all.find((option) => option.value === value) ?? options.find((option) => option.value === value)
    ?? (kind === "timezone" && value ? { value, label: value.replaceAll("_", " ") } : undefined));
  const needle = $derived(query.trim().toLowerCase());
  const shown = $derived(mode === "search" && needle ? all.filter((option) => option.label.toLowerCase().includes(needle) || option.value.toLowerCase().includes(needle)) : all);
  const creating = $derived(Boolean(oncreate && (createAlways || query.trim()) && !all.some((option) => option.label.toLowerCase() === needle)));
  const rows = $derived(shown.length + (creating ? 1 : 0));
  const dotted = $derived(all.some((option) => option.color));

  // Segments: ToggleGroup clears its value when the pressed segment is clicked again; a select keeps it.
  let segment = $state("");
  $effect.pre(() => {
    segment = value;
  });
  function pickSegment(next: string | string[]) {
    if (typeof next !== "string" || !next) {
      segment = value;
      return;
    }
    value = next;
    onchange?.(next);
  }

  const optionId = (index: number) => `${uid}-option-${index}`;

  function choose(option: SelectOption) {
    if (option.disabled) return;
    popover?.close(true);
    if (option.value === value) return;
    value = option.value;
    onchange?.(option.value);
  }

  function activate(index: number) {
    if (index >= shown.length) {
      popover?.close(true);
      oncreate?.(query.trim());
      return;
    }
    const option = shown[index];
    if (option) choose(option);
  }

  /** Moves the active row by `step`, skipping disabled options and wrapping around. */
  function move(step: number) {
    for (let offset = 1; offset <= rows; offset += 1) {
      const index = (((active + step * offset) % rows) + rows) % rows;
      if (!shown[index]?.disabled) {
        active = index;
        return;
      }
    }
  }

  function opened(panel: HTMLElement) {
    if (triggerElement) panel.style.minWidth = `${Math.max(triggerElement.offsetWidth, 200)}px`;
    query = "";
    const selected = all.findIndex((option) => option.value === value);
    active = selected >= 0 ? selected : Math.max(0, all.findIndex((option) => !option.disabled));
    reveal();
  }

  function reveal() {
    queueMicrotask(() => listElement?.querySelector(`#${CSS.escape(optionId(active))}`)?.scrollIntoView({ block: "nearest" }));
  }

  function listKey(event: KeyboardEvent) {
    const key = event.key;
    if (key === "ArrowDown" || key === "ArrowUp") move(key === "ArrowDown" ? 1 : -1);
    else if (key === "Home" || key === "End") {
      active = key === "Home" ? rows - 1 : 0;
      move(key === "Home" ? 1 : -1);
    } else if (key === "Enter" || (key === " " && mode === "list")) activate(active);
    else if (key === "Tab") return popover?.close(false);
    else if (mode === "list" && key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now();
      typed = now - typedAt < 600 ? typed + key.toLowerCase() : key.toLowerCase();
      typedAt = now;
      const match = shown.findIndex((option) => !option.disabled && option.label.toLowerCase().startsWith(typed));
      if (match >= 0) active = match;
    } else return;
    // Handled here: a surrounding form or dialog must not also act on the key.
    event.preventDefault();
    event.stopPropagation();
    reveal();
  }

  function triggerKey(event: KeyboardEvent) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    open = true;
  }

  function search(event: Event & { currentTarget: HTMLInputElement }) {
    query = event.currentTarget.value;
    active = 0;
  }

  /** The label split around the first match of the query, for highlighting. */
  function parts(text: string): [string, string, string] {
    const at = needle ? text.toLowerCase().indexOf(needle) : -1;
    return at < 0 ? [text, "", ""] : [text.slice(0, at), text.slice(at, at + needle.length), text.slice(at + needle.length)];
  }
</script>

{#if mode === "segments"}
  <div class="select-segments" {id}>
    <ToggleGroup items={options.map((option) => ({ id: option.value, label: option.label, disabled: option.disabled }))}
      bind:value={segment} {label} {disabled} onchange={pickSegment} />
  </div>
{:else}
  <Popover bind:this={popover} bind:open {label} role="none" focus="first" class="select-popover" onopen={opened}>
    {#snippet trigger(props)}
      <button bind:this={triggerElement} type="button" {id} class="select-trigger" role="combobox" aria-label={label} {disabled}
        {...props} aria-haspopup="listbox" aria-controls={open ? listId : undefined} onkeydown={triggerKey}
        >{#if current?.color}<span class="dot" style:background={current.color}></span>{/if}<span class="value" class:placeholder={!current}>{current?.label ?? placeholder}</span>{#if current?.count != null}<span class="count">{current.count}</span>{/if}<span class="chev" aria-hidden="true"></span></button
      >
    {/snippet}
    {#if mode === "search"}
      <div class="search">
        <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"><path d="m21 21-4.4-4.4M19 10a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
        <input class="find" type="search" data-autofocus aria-label={`Search ${label}`} aria-controls={listId} autocomplete="off" spellcheck="false"
          aria-activedescendant={rows ? optionId(active) : undefined} placeholder="Search" value={query} oninput={search} onkeydown={listKey} />
        <kbd>Esc</kbd>
      </div>
    {/if}
    <div bind:this={listElement} id={listId} class="options" role="listbox" aria-label={label} tabindex={mode === "list" ? 0 : -1}
      data-autofocus={mode === "list" ? "" : undefined} aria-activedescendant={mode === "list" && rows ? optionId(active) : undefined}
      onkeydown={mode === "list" ? listKey : undefined}>
      {#each shown as option, index (option.value)}
        {@const [before, match, after] = parts(option.label)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <div id={optionId(index)} role="option" tabindex="-1" class="option" class:active={index === active} aria-selected={option.value === value}
          aria-disabled={option.disabled ? "true" : undefined} onpointermove={() => !option.disabled && (active = index)}
          onclick={() => choose(option)}
          >{#if option.color}<span class="dot" style:background={option.color}></span>{:else if dotted}<span class="dot none"></span>{/if}<span class="label">{before}{#if match}<mark>{match}</mark>{/if}{after}</span>{#if option.hint}<span class="hint">{option.hint}</span>{/if}{#if option.count != null}<span class="count">{option.count}</span>{/if}<span class="check" aria-hidden="true">{option.value === value ? "✓" : ""}</span></div
        >
      {/each}
      {#if creating}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <div id={optionId(shown.length)} role="option" tabindex="-1" aria-selected="false" class="option create" class:active={active === shown.length}
          onpointermove={() => (active = shown.length)} onclick={() => activate(shown.length)}
          ><span class="plus" aria-hidden="true">+</span><span class="label">{createLabel(query.trim())}</span></div
        >
      {/if}
      {#if !rows}<div class="empty">No matches</div>{/if}
    </div>
    {#if mode === "search" && rows}
      <div class="foot" aria-hidden="true"><kbd>↑↓</kbd> move <kbd>↵</kbd> pick</div>
    {/if}
  </Popover>
{/if}
{#if name}<input type="hidden" {name} {value} />{/if}

<style>
  /* Closed: a 28px field with the theme's inset fill, the current dot and count, a chevron. */
  .select-trigger {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    min-width: 120px;
    max-width: 100%;
    height: var(--control-h);
    padding: 0 10px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-field);
    background: color-mix(in srgb, var(--solid-inset, #111) 45%, transparent);
    color: var(--ink);
    font: 400 var(--fs-control) / 1 var(--font-ui);
    text-align: left;
    cursor: pointer;
  }
  .select-trigger:hover:not(:disabled) {
    border-color: var(--br-lineHot);
  }
  .select-trigger[aria-expanded="true"] {
    border-color: var(--accent);
  }
  .select-trigger:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .value {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .value.placeholder {
    color: var(--subtle);
  }
  .chev {
    width: 6px;
    height: 6px;
    margin: -3px 2px 0 2px;
    border-right: 1.5px solid var(--subtle);
    border-bottom: 1.5px solid var(--subtle);
    rotate: 45deg;
    flex-shrink: 0;
    transition: rotate var(--motion-ms, 140ms) ease, margin var(--motion-ms, 140ms) ease;
  }
  .select-trigger[aria-expanded="true"] .chev {
    rotate: 225deg;
    margin-top: 3px;
  }
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 999px;
    flex-shrink: 0;
  }
  .dot.none {
    background: transparent;
  }
  .count {
    display: inline-grid;
    place-items: center;
    min-width: 18px;
    height: 16px;
    padding: 0 5px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 10%, transparent);
    color: var(--muted);
    font: 500 9.5px/1 var(--font-mono);
  }

  /* Open: the app's overlay glass, a check on the current item, a wash on the active row. */
  :global(.kitty-popover.select-popover) {
    display: grid;
    gap: 1px;
  }
  /* The popover is the glass; its listbox must not draw the shared overlay box again. */
  :global(:root body .kitty-popover) .options[role="listbox"] {
    display: grid;
    gap: 1px;
    max-height: min(320px, 60dvh);
    padding: 0;
    overflow: auto;
    border: 0;
    border-radius: 0;
    background: none;
    box-shadow: none;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
    animation: none;
    outline: none;
  }
  .option {
    display: flex;
    align-items: center;
    gap: 9px;
    min-height: var(--control-h);
    padding: 0 9px;
    border-radius: var(--r-item);
    color: var(--ink);
    cursor: pointer;
  }
  .option[aria-selected="true"],
  .option.active {
    background: var(--wash);
  }
  .option.active {
    box-shadow: inset 0 0 0 1px var(--hairline);
  }
  .option[aria-disabled="true"] {
    color: var(--subtle);
    cursor: default;
  }
  .label {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  mark {
    background: none;
    color: var(--accent);
    font-weight: 600;
  }
  .hint {
    color: var(--subtle);
    font-size: 11.5px;
    white-space: nowrap;
  }
  .check {
    width: 12px;
    color: var(--accent);
    text-align: right;
  }
  .create {
    color: var(--muted);
  }
  .plus {
    width: 6px;
    text-align: center;
  }
  .empty {
    padding: 8px 9px;
    color: var(--subtle);
  }
  .search {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 30px;
    padding: 0 9px;
    border-bottom: 1px solid var(--hairline);
    margin-bottom: 4px;
    color: var(--subtle);
  }
  /* Outranks the global field fill: the search sits flush in the menu. */
  :global(:root body) .search input.find {
    flex: 1;
    min-width: 0;
    height: 100%;
    padding: 0;
    border: 0;
    background: none;
    color: var(--ink);
    font: inherit;
    outline: none;
  }
  .search input.find::-webkit-search-cancel-button {
    display: none;
  }
  kbd {
    padding: 2px 5px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-chip);
    color: var(--subtle);
    font-size: 9.5px;
    letter-spacing: 0.06em;
  }
  .foot {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 6px 9px 3px;
    border-top: 1px solid var(--hairline);
    margin-top: 4px;
    color: var(--subtle);
    font-size: 11px;
  }

  /* Segments: one pill, the chosen segment carries the theme's selection pair. */
  .select-segments {
    display: inline-flex;
  }
  .select-segments :global(.toggle-group) {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    height: var(--control-h);
    padding: 2px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-control);
    background: color-mix(in srgb, var(--solid-inset, #111) 40%, transparent);
  }
  :global(:root body) .select-segments :global(.toggle-group .br-btn) {
    height: 100%;
    min-height: 0;
    margin: 0;
    padding: 0 10px;
    border: 0;
    border-radius: var(--r-control);
    background: transparent;
    color: var(--muted);
    font: 500 12px/1 var(--font-ui);
  }
  :global(:root body) .select-segments :global(.toggle-group .br-btn:hover:not(:disabled)) {
    color: var(--ink);
  }
  :global(:root body) .select-segments :global(.toggle-group .br-btn[aria-pressed="true"]) {
    background: var(--select-fill, color-mix(in srgb, var(--ink) 90%, var(--accent)));
    color: var(--select-ink, var(--solid-void, #141414));
    box-shadow: inset 0 0 0 1px var(--select-edge, transparent);
  }
</style>
