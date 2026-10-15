<script lang="ts">
  import { untrack } from "svelte";
  import { Panel, Button, Badge } from "@nunera/bracket";
  import { api, safeUrl } from "./api";

  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  type Source = { kind: string; id: number; title: string; url: string };
  type Checklist = { title: string; done: boolean };
  type Column = { id: number; title: string; position: number };
  type Card = {
    id: number;
    column_id: number;
    position: number;
    title: string;
    notes: string;
    priority: string;
    labels: string[];
    due_date: string | null;
    checklist: Checklist[];
    archived: boolean;
    source: Source | null;
    source_url: string;
  };
  type Summary = {
    id: number;
    title: string;
    archived: boolean;
    revision: number;
    cards: number;
  };
  type Board = {
    id: number;
    title: string;
    archived: boolean;
    revision: number;
    columns: Column[];
    cards: Card[];
    metrics: {
      cards: number;
      archived: number;
      due: number;
      overdue: number;
      checklist_done: number;
      checklist_total: number;
    };
  };
  type Draft = {
    id: number | null;
    column_id: number;
    title: string;
    notes: string;
    priority: string;
    labels: string;
    due_date: string;
    checklist: Checklist[];
    source: string;
    source_url: string;
    archived: boolean;
    revision: number;
  };
  let boards = $state<Summary[]>([]),
    sources = $state<Source[]>([]),
    board = $state<Board | null>(null);
  let draft = $state<Draft | null>(null),
    checklistTitle = $state("");
  let search = $state(""),
    priority = $state(""),
    archiveFilter = $state("active"),
    dueFilter = $state("all"),
    showArchivedBoards = $state(false);
  let boardTitle = $state(""),
    columnTitle = $state(""),
    error = $state(""),
    notice = $state(""),
    busy = $state(false),
    blocked = $state(false),
    dragging = $state<number | null>(null);
  const today = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };
  const cardsIn = (column: number) =>
    (board?.cards || [])
      .filter((c) => c.column_id === column)
      .sort((a, b) => a.position - b.position);
  const visible = (c: Card) =>
    (archiveFilter === "all" ||
      c.archived === (archiveFilter === "archived")) &&
    (!priority || c.priority === priority) &&
    (dueFilter === "all" ||
      (dueFilter === "due"
        ? !!c.due_date
        : !!c.due_date && c.due_date < today())) &&
    (!search.trim() ||
      [c.title, c.notes, ...c.labels, c.source?.title || ""]
        .join(" ")
        .toLowerCase()
        .includes(search.trim().toLowerCase()));
  const sourceKey = (s: Source) => `${s.kind}:${s.id}`;

  function discard(): boolean {
    if (draft && !window.confirm("Discard this unsaved card draft?"))
      return false;
    draft = null;
    blocked = false;
    return true;
  }
  async function refreshLists() {
    const [b, s] = await Promise.all([
      api<{ boards: Summary[] }>("/kanban"),
      api<{ sources: Source[] }>("/kanban/sources"),
    ]);
    boards = b.boards;
    sources = s.sources;
  }
  async function load(id?: number, preserve = false) {
    if (busy) return;
    if (!preserve && !discard()) return;
    busy = true;
    error = "";
    try {
      await refreshLists();
      const selected =
        id || board?.id || boards.find((b) => !b.archived)?.id || boards[0]?.id;
      board =
        selected && boards.some((b) => b.id === selected)
          ? await api<Board>(`/kanban/boards/${selected}`)
          : null;
      if (preserve && draft) {
        blocked = true;
        notice =
          "Latest board loaded. Your draft is preserved; review current card data before explicitly using it.";
      }
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }
  $effect(() => {
    revision;
    untrack(() => {
      if (draft)
        notice =
          "Workspace changed. Reload to review the latest board before saving your draft.";
      else void load();
    });
  });

  async function mutate(
    path: string,
    body: Record<string, unknown>,
    expected = board?.revision,
  ): Promise<boolean> {
    if (!board || busy) return false;
    busy = true;
    error = "";
    notice = "";
    try {
      const updated = await api<Board>(`/kanban/boards/${board.id}${path}`, {
        ...body,
        revision: expected,
      });
      board = updated;
      await refreshLists();
      notice = "Saved locally.";
      return true;
    } catch (e) {
      error = (e as Error).message;
      if (error.includes("Board changed")) blocked = true;
      return false;
    } finally {
      busy = false;
    }
  }
  async function createBoard() {
    if (busy || !boardTitle.trim() || !discard()) return;
    busy = true;
    error = "";
    try {
      board = await api<Board>("/kanban/boards", { title: boardTitle });
      boardTitle = "";
      await refreshLists();
      notice = "Board created locally.";
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }
  async function renameBoard() {
    if (!board) return;
    const title = window.prompt("Board title", board.title);
    if (title !== null) await mutate("", { title });
  }
  async function archiveBoard() {
    if (
      board &&
      window.confirm(
        `${board.archived ? "Restore" : "Archive"} this board? Cards are retained.`,
      )
    )
      await mutate("", { archived: !board.archived });
  }
  async function deleteBoard() {
    if (
      !board ||
      busy ||
      !window.confirm(
        `Permanently delete “${board.title}” and ALL its cards? Calendar exports are retained.`,
      ) ||
      !discard()
    )
      return;
    busy = true;
    error = "";
    try {
      await api(`/kanban/boards/${board.id}/delete`, {
        revision: board.revision,
        confirmed: true,
      });
      board = null;
      await refreshLists();
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }
  async function createColumn() {
    if (
      columnTitle.trim() &&
      (await mutate("/columns", { title: columnTitle }))
    )
      columnTitle = "";
  }
  async function renameColumn(c: Column) {
    const title = window.prompt("Column title", c.title);
    if (title !== null) await mutate(`/columns/${c.id}`, { title });
  }
  async function deleteColumn(c: Column) {
    if (!board) return;
    const choices = board.columns.filter((x) => x.id !== c.id);
    if (!choices.length) {
      error = "A board must keep at least one column.";
      return;
    }
    let destination_id: number | undefined;
    if (cardsIn(c.id).length) {
      const answer = window.prompt(
        `Move ALL cards (including archived) into which column? Enter its ID:\n${choices.map((x) => `${x.id}: ${x.title}`).join("\n")}`,
      );
      if (answer === null) return;
      destination_id = Number(answer);
      if (!choices.some((x) => x.id === destination_id)) {
        error = "Choose a listed destination column ID.";
        return;
      }
    }
    if (
      window.confirm(
        `Delete column “${c.title}”${destination_id ? " and move its cards to the selected column" : ""}?`,
      )
    )
      await mutate(`/columns/${c.id}/delete`, {
        confirmed: true,
        ...(destination_id ? { destination_id } : {}),
      });
  }
  function openCard(c: Card) {
    if (!board || !discard()) return;
    draft = {
      id: c.id,
      column_id: c.column_id,
      title: c.title,
      notes: c.notes,
      priority: c.priority,
      labels: c.labels.join(", "),
      due_date: c.due_date || "",
      checklist: c.checklist.map((i) => ({ ...i })),
      source: c.source ? sourceKey(c.source) : "",
      source_url: c.source_url,
      archived: c.archived,
      revision: board.revision,
    };
    checklistTitle = "";
    notice = "";
    blocked = false;
  }
  function newCard(column: Column) {
    if (!board || !discard()) return;
    draft = {
      id: null,
      column_id: column.id,
      title: "",
      notes: "",
      priority: "normal",
      labels: "",
      due_date: "",
      checklist: [],
      source: "",
      source_url: "",
      archived: false,
      revision: board.revision,
    };
    checklistTitle = "";
    blocked = false;
  }
  async function saveCard() {
    if (!draft || blocked) return;
    const d = draft;
    const selected = sources.find((s) => sourceKey(s) === d.source);
    if (d.source && !selected) {
      error = "Linked source no longer exists. Choose another or clear it.";
      return;
    }
    const body = {
      title: d.title,
      notes: d.notes,
      priority: d.priority,
      labels: d.labels
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean),
      due_date: d.due_date || null,
      checklist: d.checklist,
      source: selected ? { kind: selected.kind, id: selected.id } : null,
      source_url: d.source_url,
      archived: d.archived,
      ...(d.id === null ? { column_id: d.column_id } : {}),
    };
    if (
      await mutate(
        d.id === null ? "/cards" : `/cards/${d.id}`,
        body,
        d.revision,
      )
    )
      draft = null;
  }
  async function deleteCard(c: Card) {
    if (
      window.confirm(
        `Permanently delete “${c.title}”? Calendar exports are retained.`,
      ) &&
      (await mutate(`/cards/${c.id}/delete`, { confirmed: true }))
    ) {
      if (draft?.id === c.id) draft = null;
    }
  }
  async function move(c: Card, column: number, position?: number) {
    const target = cardsIn(column).filter((x) => x.id !== c.id);
    await mutate(`/cards/${c.id}/move`, {
      column_id: column,
      position: position === undefined ? target.length : position,
    });
  }
  async function drop(event: DragEvent, column: number, before?: Card) {
    event.preventDefault();
    event.stopPropagation();
    const c = board?.cards.find((x) => x.id === dragging);
    dragging = null;
    if (!c || c.id === before?.id) return;
    const target = cardsIn(column).filter((x) => x.id !== c.id);
    await move(
      c,
      column,
      before ? target.findIndex((x) => x.id === before.id) : target.length,
    );
  }
  async function exportDate(c: Card) {
    if (
      !board ||
      busy ||
      !window.confirm(
        `Export “${c.title}” on ${c.due_date} as an all-day local Calendar deadline? Repeat exports preserve the existing event, even if edited or deleted there.`,
      )
    )
      return;
    busy = true;
    error = "";
    notice = "";
    try {
      const result = await api<{ event: { id: number; deleted?: boolean } }>(
        `/kanban/boards/${board.id}/cards/${c.id}/calendar`,
        { revision: board.revision },
      );
      notice = `Calendar event #${result.event.id} linked. Existing calendar edits are preserved; manage date changes in Calendar.`;
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<div class="kanban">
  <Panel
    title="~/kanban / boards"
    icon="▦"
    readout={demo ? "DEMO / LOCAL DATA" : "LOCAL SQLITE"}
  >
    <div class="toolbar">
      <label
        >Board <select
          value={board?.id || ""}
          disabled={busy}
          onchange={(e) => void load(Number(e.currentTarget.value))}
        >
          <option value="" disabled>Select a board</option>
          {#each boards.filter((b) => showArchivedBoards || !b.archived || b.id === board?.id) as b (b.id)}<option
              value={b.id}
              >{b.title}{b.archived ? " [archived]" : ""} · {b.cards} cards</option
            >{/each}
        </select></label
      >
      <label class="check"
        ><input type="checkbox" bind:checked={showArchivedBoards} /> Archived boards</label
      >
      <Button disabled={busy} onclick={() => void load(board?.id, true)}
        >Reload</Button
      >
      {#if board}<Button disabled={busy} onclick={renameBoard}>Rename</Button
        ><Button disabled={busy} onclick={archiveBoard}
          >{board.archived ? "Restore board" : "Archive board"}</Button
        ><Button disabled={busy} onclick={deleteBoard}>Delete board</Button
        >{/if}
    </div>
    <form
      class="toolbar"
      onsubmit={(e) => {
        e.preventDefault();
        void createBoard();
      }}
    >
      <label
        >New board <input
          bind:value={boardTitle}
          maxlength="500"
          placeholder="A project, a week, a job search…"
          required
        /></label
      >
      <Button type="submit" disabled={busy || !boardTitle.trim()}
        >Create board</Button
      >
    </form>
    <p class="help">
      Independent task boards. Source links never change job, opportunity,
      project, or study status. Drag cards onto a card to insert before it, or
      into a column to append. Move selectors and ↑/↓ work with keyboard, touch,
      and pen.
    </p>
  </Panel>
  {#if error}<div class="error" role="alert">
      {error}
      <Button disabled={busy} onclick={() => void load(board?.id, true)}
        >Reload latest without losing draft</Button
      >
    </div>{/if}
  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  {#if board}
    <div class="metrics" aria-label="Board metrics">
      <Badge label={`${board.metrics.cards} ACTIVE`} /><Badge
        label={`${board.metrics.archived} ARCHIVED`}
      /><Badge label={`${board.metrics.due} DATED`} /><Badge
        label={`${board.metrics.overdue} OVERDUE`}
      /><Badge
        label={`${board.metrics.checklist_done}/${board.metrics.checklist_total} CHECKLIST`}
      /><Badge label={`REV ${board.revision}`} />
    </div>
    <Panel
      title={board.title + (board.archived ? " / archived" : " / working set")}
      icon="▤"
    >
      <div class="toolbar filters">
        <label
          >Search <input
            type="search"
            bind:value={search}
            placeholder="Title, notes, label, source"
          /></label
        >
        <label
          >Priority <select bind:value={priority}
            ><option value="">All</option
            >{#each ["low", "normal", "high", "urgent"] as p}<option value={p}
                >{p}</option
              >{/each}</select
          ></label
        >
        <label
          >Cards <select bind:value={archiveFilter}
            ><option value="active">Active</option><option value="archived"
              >Archived</option
            ><option value="all">All</option></select
          ></label
        >
        <label
          >Deadlines <select bind:value={dueFilter}
            ><option value="all">All</option><option value="due"
              >Has due date</option
            ><option value="overdue">Overdue</option></select
          ></label
        >
      </div>
      <form
        class="toolbar"
        onsubmit={(e) => {
          e.preventDefault();
          void createColumn();
        }}
      >
        <label
          >New column <input
            bind:value={columnTitle}
            maxlength="500"
            required
          /></label
        ><Button type="submit" disabled={busy || !columnTitle.trim()}
          >Add column</Button
        >
      </form>
    </Panel>
    {#if draft}
      <Panel
        title={draft.id === null ? "New card" : `Card #${draft.id} / details`}
        icon="✎"
        readout={`DRAFT FROM REV ${draft.revision}`}
      >
        <form
          class="details"
          onsubmit={(e) => {
            e.preventDefault();
            void saveCard();
          }}
        >
          <label
            >Title <input
              bind:value={draft.title}
              maxlength="500"
              required
            /></label
          >
          <label
            >Notes <textarea bind:value={draft.notes} rows="4" maxlength="20000"
            ></textarea></label
          >
          <div class="toolbar">
            <label
              >Priority <select bind:value={draft.priority}
                >{#each ["low", "normal", "high", "urgent"] as p}<option
                    value={p}>{p}</option
                  >{/each}</select
              ></label
            >
            <label
              >Due date <input type="date" bind:value={draft.due_date} /></label
            >
            <label
              >Labels (comma separated) <input
                bind:value={draft.labels}
              /></label
            >
            <label class="check"
              ><input type="checkbox" bind:checked={draft.archived} /> Archived</label
            >
          </div>
          <label
            >Local source <select bind:value={draft.source}
              ><option value="">No source link</option
              >{#each sources as s (sourceKey(s))}<option value={sourceKey(s)}
                  >{s.kind.replaceAll("_", " ")} #{s.id} · {s.title}</option
                >{/each}</select
            ></label
          >
          <label
            >Explicit source URL <input
              type="url"
              bind:value={draft.source_url}
              placeholder="https://…"
              maxlength="4000"
            /></label
          >
          <p class="help">
            Source records are references, not synchronized status. Calendar
            exports use your exact due date; later exports do not overwrite
            calendar edits.
          </p>
          <fieldset>
            <legend
              >Checklist · {draft.checklist.filter((i) => i.done).length}/{draft
                .checklist.length}</legend
            >
            {#each draft.checklist as item, index}<div
                class="toolbar checklist"
              >
                <label class="check"
                  ><input
                    type="checkbox"
                    bind:checked={item.done}
                    aria-label={`Complete checklist item ${index + 1}`}
                  /><input
                    bind:value={item.title}
                    maxlength="500"
                    required
                    aria-label={`Checklist item ${index + 1}`}
                  /></label
                ><Button
                  type="button"
                  onclick={() => {
                    if (draft)
                      draft.checklist = draft.checklist.filter(
                        (_, i) => i !== index,
                      );
                  }}>Remove</Button
                >
              </div>{/each}
            <div class="toolbar">
              <label
                >New item <input
                  bind:value={checklistTitle}
                  maxlength="500"
                /></label
              ><Button
                type="button"
                disabled={!checklistTitle.trim()}
                onclick={() => {
                  if (draft && checklistTitle.trim()) {
                    draft.checklist = [
                      ...draft.checklist,
                      { title: checklistTitle.trim(), done: false },
                    ];
                    checklistTitle = "";
                  }
                }}>Add checklist item</Button
              >
            </div>
          </fieldset>
          {#if blocked}<p class="error">
              Saving is paused because the board changed. Reload, compare the
              latest card below, then explicitly choose to use your draft.
            </p>
            <Button
              type="button"
              disabled={busy}
              onclick={() => void load(board?.id, true)}>Reload latest</Button
            ><Button
              type="button"
              disabled={busy}
              onclick={() => {
                if (
                  draft &&
                  board &&
                  window.confirm(
                    "Use this preserved draft against the latest board revision? Review current card contents first; saving will replace this card's editable fields.",
                  )
                ) {
                  draft.revision = board.revision;
                  blocked = false;
                }
              }}>Use my draft after review</Button
            >{/if}
          <div class="toolbar">
            <Button
              type="submit"
              intent="primary"
              disabled={busy || blocked || !draft.title.trim()}
              >Save card</Button
            ><Button type="button" disabled={busy} onclick={discard}
              >Close / discard draft</Button
            >
          </div>
        </form>
      </Panel>
    {/if}
    <div class="columns">
      {#each board.columns as column (column.id)}
        <section
          class="column"
          aria-label={column.title}
          ondragover={(e) => e.preventDefault()}
          ondrop={(e) => void drop(e, column.id)}
        >
          <Panel
            title={column.title}
            readout={`${cardsIn(column.id).filter(visible).length}/${cardsIn(column.id).length}`}
            icon="▥"
          >
            <div class="column-actions">
              <Button disabled={busy} onclick={() => newCard(column)}
                >+ Card</Button
              >
              <Button disabled={busy} onclick={() => void renameColumn(column)}
                >Rename</Button
              >
              <Button
                disabled={busy || column.position === 0}
                aria-label={`Move ${column.title} left`}
                onclick={() =>
                  void mutate(`/columns/${column.id}/move`, {
                    position: column.position - 1,
                  })}>←</Button
              >
              <Button
                disabled={busy || column.position === board.columns.length - 1}
                aria-label={`Move ${column.title} right`}
                onclick={() =>
                  void mutate(`/columns/${column.id}/move`, {
                    position: column.position + 1,
                  })}>→</Button
              >
              <Button
                disabled={busy || board.columns.length === 1}
                onclick={() => void deleteColumn(column)}>Delete</Button
              >
            </div>
            <div class="cards">
              {#each cardsIn(column.id).filter(visible) as card (card.id)}
                <article
                  class:archived={card.archived}
                  class="card"
                  ondragover={(e) => e.preventDefault()}
                  ondrop={(e) => void drop(e, column.id, card)}
                >
                  <button
                    class="card-title"
                    disabled={busy}
                    draggable={!busy}
                    ondragstart={(e) => {
                      dragging = card.id;
                      e.dataTransfer?.setData("text/plain", String(card.id));
                      if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
                    }}
                    ondragend={() => (dragging = null)}
                    onclick={() => openCard(card)}>{card.title}</button
                  >
                  <div class="card-meta">
                    <Badge
                      label={card.priority.toUpperCase()}
                      size="sm"
                    />{#if card.archived}<Badge
                        label="ARCHIVED"
                        size="sm"
                      />{/if}{#if card.due_date}<span
                        class:overdue={card.due_date < today()}
                        >Due {card.due_date}</span
                      >{/if}
                  </div>
                  {#if card.notes}<p class="card-notes">{card.notes}</p>{/if}
                  {#if card.labels.length}<div class="labels">
                      {#each card.labels as label}<Badge
                          {label}
                          size="sm"
                        />{/each}
                    </div>{/if}
                  {#if card.checklist.length}<p class="help">
                      Checklist {card.checklist.filter((i) => i.done)
                        .length}/{card.checklist.length} · open details to complete
                    </p>{/if}
                  {#if card.source}<p class="source">
                      {card.source.kind.replaceAll("_", " ")} #{card.source.id}: {card
                        .source.title}{#if safeUrl(card.source.url)}
                        · <a
                          href={safeUrl(card.source.url)}
                          target="_blank"
                          rel="noopener noreferrer">Open source ↗</a
                        >{/if}
                    </p>{/if}
                  {#if safeUrl(card.source_url)}<a
                      href={safeUrl(card.source_url)}
                      target="_blank"
                      rel="noopener noreferrer">Explicit source ↗</a
                    >{/if}
                  <label class="move"
                    >Move to <select
                      value={card.column_id}
                      disabled={busy}
                      onchange={(e) =>
                        void move(card, Number(e.currentTarget.value))}
                      >{#each board.columns as target (target.id)}<option
                          value={target.id}>{target.title}</option
                        >{/each}</select
                    ></label
                  >
                  <div class="card-actions">
                    <Button
                      disabled={busy || card.position === 0}
                      aria-label={`Move ${card.title} up`}
                      onclick={() =>
                        void move(card, column.id, card.position - 1)}>↑</Button
                    ><Button
                      disabled={busy ||
                        card.position === cardsIn(column.id).length - 1}
                      aria-label={`Move ${card.title} down`}
                      onclick={() =>
                        void move(card, column.id, card.position + 1)}>↓</Button
                    ><Button disabled={busy} onclick={() => openCard(card)}
                      >Details</Button
                    ><Button
                      disabled={busy}
                      onclick={() =>
                        void mutate(`/cards/${card.id}`, {
                          archived: !card.archived,
                        })}>{card.archived ? "Restore" : "Archive"}</Button
                    ><Button
                      disabled={busy || !card.due_date}
                      onclick={() => void exportDate(card)}>Calendar</Button
                    ><Button
                      disabled={busy}
                      onclick={() => void deleteCard(card)}>Delete</Button
                    >
                  </div>
                </article>
              {/each}
              {#if !cardsIn(column.id).filter(visible).length}<p class="empty">
                  No matching cards. Add a card or move one here.
                </p>{/if}
            </div>
          </Panel>
        </section>
      {/each}
    </div>
  {:else}<Panel title="No board selected" icon="▦"
      ><p>
        Create a board to start. Every new board begins with To do / In progress
        / Done; rename or reorder them to match your workflow.
      </p></Panel
    >{/if}
</div>

<style>
  .kanban {
    display: grid;
    gap: 14px;
    color: var(--ink);
    min-width: 0;
  }
  .toolbar,
  .metrics,
  .column-actions,
  .card-actions,
  .card-meta,
  .labels {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .toolbar {
    margin-bottom: 10px;
  }
  label {
    display: grid;
    gap: 5px;
    color: var(--muted);
    font: 12px var(--br-font-ui, monospace);
  }
  .check {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  input,
  select,
  textarea {
    border: 1px solid var(--border);
    border-radius: 0;
    padding: 8px;
    color: var(--ink);
    background: var(--surface);
    max-width: 100%;
    min-width: 0;
    font: 13px var(--br-font-ui, monospace);
  }
  input[type="checkbox"] {
    accent-color: var(--violet);
  }
  .toolbar input:not([type="checkbox"]) {
    width: min(320px, 70vw);
  }
  .help,
  .source,
  .empty {
    font-size: 12px;
    color: var(--muted);
    line-height: 1.6;
  }
  .notice {
    border-left: 3px solid var(--accent);
    padding: 9px 12px;
    margin: 0;
  }
  .error {
    border: 1px solid var(--violet);
    padding: 12px;
    color: var(--ink);
    background: var(--surface);
  }
  .columns {
    display: grid;
    grid-auto-flow: column;
    grid-auto-columns: minmax(280px, 1fr);
    gap: 12px;
    overflow-x: auto;
    align-items: start;
    padding-bottom: 12px;
  }
  .column {
    min-width: 0;
    border-top: 2px solid var(--violet);
  }
  .column-actions {
    gap: 4px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
  }
  .cards {
    display: grid;
    gap: 10px;
    padding-top: 10px;
    min-height: 100px;
  }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 11px;
    display: grid;
    gap: 9px;
    min-width: 0;
  }
  .card:focus-within {
    border-color: var(--accent);
  }
  .card.archived {
    border-style: dashed;
  }
  .card-title {
    text-align: left;
    background: none;
    color: var(--ink);
    border: 0;
    border-left: 2px solid var(--accent);
    padding: 4px 8px;
    cursor: grab;
    font: 14px var(--br-font-ui, monospace);
    overflow-wrap: anywhere;
  }
  .card-title:active {
    cursor: grabbing;
  }
  .card-meta {
    font-size: 11px;
  }
  .card-notes {
    white-space: pre-wrap;
    max-height: 100px;
    overflow: auto;
    font-size: 12px;
    line-height: 1.6;
    margin: 0;
    overflow-wrap: anywhere;
  }
  .source {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .overdue {
    color: var(--violet);
    font-weight: bold;
  }
  a {
    color: var(--accent);
    font-size: 12px;
  }
  .move {
    grid-template-columns: auto 1fr;
    align-items: center;
  }
  .card-actions {
    gap: 4px;
  }
  .details {
    display: grid;
    gap: 12px;
  }
  .details > label input,
  .details > label select,
  .details > label textarea {
    width: 100%;
  }
  fieldset {
    border: 1px solid var(--border);
    padding: 12px;
    min-width: 0;
  }
  legend {
    color: var(--violet);
    font: 12px var(--br-font-ui, monospace);
  }
  .checklist label {
    flex: 1;
  }
  .checklist input:not([type="checkbox"]) {
    flex: 1;
    width: 100%;
  }
  @media (max-width: 700px) {
    .columns {
      grid-auto-flow: row;
      grid-auto-columns: auto;
    }
    .toolbar {
      align-items: stretch;
    }
    .toolbar > label {
      flex: 1;
    }
  }
</style>
