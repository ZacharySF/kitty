<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "./api";
  import AIReview from "./AIReview.svelte";
  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  type Settings = {
    new_limit: number;
    review_limit: number;
    retention: number;
    learning_steps: number[];
    relearning_steps: number[];
    maximum_interval: number;
    bury_siblings: boolean;
    leech_threshold: number;
    timezone: string;
  };
  type Deck = {
    id: number;
    name: string;
    settings: Settings;
    archived: number;
    revision: number;
    due: number;
    new: number;
  };
  type Template = { name: string; front: string; back: string };
  type NoteType = {
    id: number;
    name: string;
    kind: string;
    fields: string[];
    templates: Template[];
    revision: number;
  };
  type Card = {
    id: number;
    ordinal: number;
    state: string;
    due: string;
    suspended: number;
    buried_until: string | null;
    leech: number;
    lapses: number;
    reviews: number;
    revision: number;
  };
  type Note = {
    id: number;
    deck_id: number;
    type_id: number;
    fields: Record<string, string>;
    tags: string[];
    archived: number;
    revision: number;
    cards: Card[];
  };
  type Review = {
    id: number;
    token: string;
    front: string;
    state: string;
    mode: string;
    typed: boolean;
  };
  type Overview = {
    decks: Deck[];
    types: NoteType[];
    media: { name: string; mime: string; size: number }[];
    stats: {
      total: number;
      due: number;
      new: number;
      learning: number;
      review: number;
      streak: number;
      suspended: number;
      leeches: number;
      days: { day: string; reviews: number; again: number; seconds: number }[];
    };
  };
  let overview = $state<Overview | null>(null),
    notes = $state<Note[]>([]),
    total = $state(0);
  let deckId = $state(0),
    typeId = $state(0),
    view = $state("decks"),
    search = $state(""),
    offset = $state(0);
  let error = $state(""),
    message = $state(""),
    busy = $state(false);
  let editing = $state<Note | null>(null),
    editorFields = $state<Record<string, string>>({}),
    editorTags = $state("");
  let deckEditing = $state<Deck | null>(null),
    deckName = $state(""),
    cfg = $state<Settings | null>(null),
    learningText = $state("60,600"),
    relearningText = $state("600");
  let typeEditing = $state<NoteType | null>(null),
    typeName = $state(""),
    typeKind = $state("template"),
    fieldNames = $state("Front, Back"),
    templateJson = $state(
      '[{"name":"Card 1","front":"{{Front}}","back":"{{Back}}"}]',
    );
  let review = $state<Review | null>(null),
    back = $state(""),
    intervals = $state<{ rating: number; due: string; seconds: number }[]>([]),
    remaining = $state(0),
    mode = $state("regular"),
    started = $state(0);
  let revealed = $state(false);
  let studyId = $state<string | null>(null),
    practiceResume = $state<{
      study_id: string;
      deck_id: number;
      mode: string;
      search: string;
    } | null>(null);
  let typedEnabled = $state(false),
    typedAnswer = $state("");
  let historyRows = $state<
    {
      id: number;
      card_id: number;
      rating: number;
      reviewed_at: string;
      mode: string;
      undone: number;
    }[]
  >([]);
  let format = $state("apkg"),
    sourceText = $state(""),
    sourcePage = $state("");
  type Draft = {
    front: string;
    back: string;
    tags: string[];
    source_quote: string;
    accepted: boolean;
  };
  let drafts = $state<Draft[]>([]),
    rawAI = $state("");
  let selectedType = $derived(overview?.types.find((t) => t.id === typeId));
  const labels = ["Again", "Hard", "Good", "Easy"];
  const defaultSettings: Settings = {
    new_limit: 20,
    review_limit: 200,
    retention: 0.9,
    learning_steps: [60, 600],
    relearning_steps: [600],
    maximum_interval: 36500,
    bury_siblings: true,
    leech_threshold: 8,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  };
  function failure(e: unknown) {
    error = e instanceof Error ? e.message : String(e);
  }
  async function load() {
    try {
      overview = await api<Overview>("/flashcards");
      if (!deckId) deckId = overview.decks[0]?.id || 0;
      if (!typeId) typeId = overview.types[0]?.id || 0;
    } catch (e) {
      failure(e);
    }
  }
  $effect(() => {
    revision;
    void load();
  });
  async function browse() {
    try {
      const r = await api<{ notes: Note[]; total: number }>(
        `/flashcards/browser?deck_id=${deckId}&search=${encodeURIComponent(search)}&offset=${offset}&limit=50`,
      );
      notes = r.notes;
      total = r.total;
    } catch (e) {
      failure(e);
    }
  }
  async function show(next: string) {
    view = next;
    error = "";
    if (next === "browser") await browse();
    if (next === "history") await loadHistory();
  }
  async function operate(
    path: string,
    body: unknown,
    after?: () => Promise<void>,
  ) {
    busy = true;
    error = "";
    message = "";
    try {
      await api(path, body);
      await load();
      if (after) await after();
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  function newNote(note: Note | null = null) {
    editing = note;
    if (note) {
      deckId = note.deck_id;
      typeId = note.type_id;
      editorFields = { ...note.fields };
      editorTags = note.tags.join(" ");
    } else {
      editorFields = Object.fromEntries(
        (selectedType?.fields || []).map((f) => [f, ""]),
      );
      editorTags = "";
    }
    view = "editor";
  }
  function switchType() {
    editorFields = Object.fromEntries(
      (selectedType?.fields || []).map((f) => [f, editorFields[f] || ""]),
    );
  }
  async function saveNote() {
    await operate(
      "/flashcards/note",
      {
        id: editing?.id,
        revision: editing?.revision,
        deck_id: deckId,
        type_id: typeId,
        fields: editorFields,
        tags: editorTags.split(/\s+/).filter(Boolean),
        archived: editing?.archived || false,
      },
      async () => {
        view = "browser";
        await browse();
      },
    );
  }
  function editDeck(deck: Deck | null = null) {
    deckEditing = deck;
    deckName = deck?.name || "";
    cfg = structuredClone(deck?.settings || defaultSettings);
    learningText = cfg.learning_steps.join(",");
    relearningText = cfg.relearning_steps.join(",");
    view = "deck-editor";
  }
  async function saveDeck() {
    if (!cfg) return;
    const value = {
      ...cfg,
      learning_steps: learningText
        .split(",")
        .filter((x) => x.trim())
        .map(Number),
      relearning_steps: relearningText
        .split(",")
        .filter((x) => x.trim())
        .map(Number),
    };
    await operate(
      "/flashcards/deck",
      {
        id: deckEditing?.id,
        revision: deckEditing?.revision,
        name: deckName,
        settings: value,
        archived: deckEditing?.archived || false,
      },
      async () => {
        view = "decks";
      },
    );
  }
  function editType(typ: NoteType | null = null) {
    typeEditing = typ;
    typeName = typ?.name || "";
    typeKind = typ?.kind || "template";
    fieldNames = typ?.fields.join(", ") || "Front, Back";
    templateJson = JSON.stringify(
      typ?.templates || [
        { name: "Card 1", front: "{{Front}}", back: "{{Back}}" },
      ],
      null,
      2,
    );
    view = "type-editor";
  }
  async function saveType() {
    try {
      await operate(
        "/flashcards/type",
        {
          id: typeEditing?.id,
          revision: typeEditing?.revision,
          name: typeName,
          kind: typeKind,
          fields: fieldNames.split(",").map((x) => x.trim()),
          templates: JSON.parse(templateJson),
        },
        async () => {
          view = "decks";
        },
      );
    } catch (e) {
      failure(e);
    }
  }
  function resetReview() {
    review = null;
    back = "";
    revealed = false;
    intervals = [];
    remaining = 0;
    studyId = null;
  }
  async function startReview(resume: boolean | Event = false) {
    busy = true;
    error = "";
    message = "";
    if (resume !== true) studyId = null;
    try {
      const r = await api<{
        card: Review | null;
        remaining: number;
        study_id: string | null;
      }>("/flashcards/review", {
        deck_id: deckId,
        search,
        mode,
        study_id: studyId,
      });
      review = r.card;
      remaining = r.remaining;
      if (review?.typed) typedEnabled = true;
      studyId = r.study_id;
      back = "";
      revealed = false;
      intervals = [];
      typedAnswer = "";
      started = Date.now();
      view = "review";
      if (studyId) {
        practiceResume = { study_id: studyId, deck_id: deckId, mode, search };
        try {
          sessionStorage.setItem(
            "kitty-flashcards-practice",
            JSON.stringify(practiceResume),
          );
        } catch {
          /* Browser storage may be disabled; SQLite still retains the queue. */
        }
      }
      if (!review)
        message =
          mode === "regular"
            ? "No eligible cards. Learning cards appear at their scheduled time; daily limits reset at local midnight."
            : "Practice queue complete. Start again for a new practice session.";
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  function resumePractice() {
    if (!practiceResume) return;
    deckId = practiceResume.deck_id;
    mode = practiceResume.mode;
    search = practiceResume.search;
    studyId = practiceResume.study_id;
    void startReview(true);
  }
  async function reveal() {
    if (!review || revealed || busy) return;
    busy = true;
    try {
      const r = await api<{ back: string; intervals: typeof intervals }>(
        "/flashcards/reveal",
        { token: review.token },
      );
      back = r.back;
      intervals = r.intervals;
      revealed = true;
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function grade(rating: number) {
    if (!review || !revealed || busy) return;
    busy = true;
    try {
      await api("/flashcards/grade", {
        token: review.token,
        rating,
        duration_ms: Math.min(3600000, Date.now() - started),
      });
      await load();
      await startReview(true);
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function undo() {
    await operate("/flashcards/undo", {}, async () => {
      if (view === "review") await startReview(true);
      else await loadHistory();
    });
  }
  function interval(seconds: number) {
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
  }
  async function loadHistory() {
    try {
      historyRows = (
        await api<{ reviews: typeof historyRows }>("/flashcards/history")
      ).reviews;
    } catch (e) {
      failure(e);
    }
  }
  async function noteAction(note: Note, action: string, value = true) {
    if (
      action === "delete" &&
      !window.confirm("Permanently delete this note and its cards?")
    )
      return;
    await operate(
      "/flashcards/action",
      { id: note.id, revision: note.revision, action, value },
      browse,
    );
  }
  async function cardAction(card: Card, action: string, value = true) {
    await operate(
      "/flashcards/action",
      { id: card.id, revision: card.revision, action, value },
      browse,
    );
  }
  async function archiveDeck(deck: Deck) {
    await operate("/flashcards/deck", {
      id: deck.id,
      revision: deck.revision,
      name: deck.name,
      settings: deck.settings,
      archived: !deck.archived,
    });
  }
  async function fileBase64(file: File) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192)
      binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return btoa(binary);
  }
  async function importFile(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    if (file.size > 11 * 1024 * 1024) {
      error =
        "This JSON upload supports files up to 11 MiB. Split a larger collection into smaller exports.";
      return;
    }
    busy = true;
    error = "";
    try {
      const result = await api<{
        imported: number;
        duplicates?: number;
        skipped_unsafe_media?: string[];
        warning?: string;
      }>("/flashcards/import", {
        format,
        deck_id: deckId,
        type_id: typeId,
        ...(format === "apkg"
          ? { data: await fileBase64(file) }
          : { text: await file.text() }),
      });
      message = `Imported ${result.imported} notes. ${result.duplicates ? `${result.duplicates} existing notes skipped. ` : ""}${result.warning || ""}${result.skipped_unsafe_media?.length ? ` Unsafe media skipped: ${result.skipped_unsafe_media.join(", ")}` : ""}`;
      await load();
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
      input.value = "";
    }
  }
  async function exportFile() {
    busy = true;
    error = "";
    try {
      const r = await api<{ data: string; mime: string; filename: string }>(
        "/flashcards/export",
        { format, deck_id: deckId },
      );
      const binary = atob(r.data),
        bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: r.mime }));
      const link = document.createElement("a");
      link.href = url;
      link.download = r.filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function upload(event: Event) {
    const input = event.currentTarget as HTMLInputElement,
      file = input.files?.[0];
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) {
      error = "Media exceeds 8 MiB";
      return;
    }
    await operate(
      "/flashcards/media",
      { name: file.name, data: await fileBase64(file) },
      async () => {
        message = `Attached ${file.name}. Reference as <img src="${file.name}"> or [sound:${file.name}].`;
      },
    );
    input.value = "";
  }
  function selectedText(event: Event) {
    const detail = (
      event as CustomEvent<{ purpose: string; text: string; page_id?: string }>
    ).detail;
    if (detail?.purpose !== "flashcards") return;
    sourceText = detail.text;
    sourcePage = detail.page_id || "";
    drafts = [];
    rawAI = "";
    view = "generate";
  }
  function quoteIsGrounded(quote: string) {
    const normalize = (value: string) => value.replace(/\s+/g, " ").trim();
    const evidence = normalize(quote);
    return evidence.length > 0 && normalize(sourceText).includes(evidence);
  }
  function receiveAI(text: string) {
    rawAI = text;
    drafts = [];
    error = "";
    try {
      const parsed = JSON.parse(
        text.replace(/^\s*```(?:json)?\s*/, "").replace(/\s*```\s*$/, ""),
      );
      if (!Array.isArray(parsed.cards) || parsed.cards.length > 100)
        throw new Error(
          "AI returned no bounded cards array. Review raw output; nothing was saved.",
        );
      drafts = parsed.cards.map((c: Record<string, unknown>) => {
        if (
          !c ||
          typeof c.front !== "string" ||
          typeof c.back !== "string" ||
          typeof c.source_quote !== "string" ||
          c.front.length > 10000 ||
          c.back.length > 20000
        )
          throw new Error("AI returned invalid cards. Nothing was saved.");
        if (!quoteIsGrounded(c.source_quote))
          throw new Error(
            "AI returned a source quote that is not in your notes. Nothing was saved; review the raw output.",
          );
        return {
          front: c.front,
          back: c.back,
          source_quote: c.source_quote,
          tags: Array.isArray(c.tags)
            ? c.tags.filter(
                (t): t is string =>
                  typeof t === "string" && /^\S{1,80}$/.test(t),
              )
            : [],
          accepted: false,
        };
      });
    } catch (e) {
      failure(e);
    }
  }
  async function acceptDraft(index: number) {
    const draft = drafts[index];
    if (draft.accepted) return;
    if (!quoteIsGrounded(draft.source_quote)) {
      error =
        "The source quote must match your notes before accepting this card.";
      return;
    }
    const basic = overview?.types.find(
      (t) =>
        t.kind === "basic" &&
        t.fields.includes("Front") &&
        t.fields.includes("Back"),
    );
    if (!basic) {
      error =
        "Create a Basic type with Front and Back fields before accepting drafts.";
      return;
    }
    if (
      !draft.source_quote.trim() ||
      !window.confirm(
        "I checked this card and its source quote for accuracy. Add it to this deck?",
      )
    )
      return;
    busy = true;
    error = "";
    try {
      await api("/flashcards/note", {
        deck_id: deckId,
        type_id: basic.id,
        fields: Object.fromEntries(
          basic.fields.map((f) => [
            f,
            f === "Front"
              ? draft.front
              : f === "Back"
                ? draft.back + "\n\nSource: " + draft.source_quote
                : "",
          ]),
        ),
        tags: [
          ...draft.tags,
          ...(sourcePage ? ["source-page:" + sourcePage] : []),
        ],
      });
      draft.accepted = true;
      await load();
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  onMount(() => {
    window.addEventListener("kitty-study-text", selectedText);
    try {
      const saved = JSON.parse(
        sessionStorage.getItem("kitty-flashcards-practice") || "null",
      );
      if (
        saved &&
        typeof saved.study_id === "string" &&
        typeof saved.deck_id === "number" &&
        ["cram", "filtered"].includes(saved.mode) &&
        typeof saved.search === "string"
      )
        practiceResume = saved;
    } catch {
      /* No resumable browser state. */
    }
    return () => window.removeEventListener("kitty-study-text", selectedText);
  });
  function keydown(event: KeyboardEvent) {
    if (
      view !== "review" ||
      busy ||
      event.ctrlKey ||
      event.altKey ||
      event.metaKey
    )
      return;
    const target = event.target as HTMLElement;
    if (
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
      target.isContentEditable
    )
      return;
    if (event.code === "Space") {
      event.preventDefault();
      if (!revealed) void reveal();
      else void grade(3);
    } else if (revealed && /^[1-4]$/.test(event.key)) {
      event.preventDefault();
      void grade(Number(event.key));
    }
  }
</script>

<svelte:window onkeydown={keydown} />
<section class="flashcards" aria-label="Flashcards workspace">
  <header>
    <div>
      <h1>Flashcards</h1>
      <p>
        Local notes, safe media, and FSRS spaced repetition.{demo
          ? " Demo actions remain local."
          : ""}
      </p>
    </div>
    <button onclick={() => newNote()} disabled={!overview || busy}
      >Add note</button
    >
  </header>
  <nav aria-label="Flashcard views">
    {#each [["decks", "Decks"], ["browser", "Browser"], ["review", "Study"], ["history", "History"], ["transfer", "Import / export"], ["generate", "AI drafts"]] as item}<button
        class:active={view === item[0]}
        onclick={() => (item[0] === "review" ? startReview() : show(item[0]))}
        >{item[1]}</button
      >{/each}
  </nav>
  {#if error}<p class="error" role="alert">{error}</p>{/if}{#if message}<p
      role="status"
    >
      {message}
    </p>{/if}
  {#if overview}
    <div class="scope">
      <label
        >Deck / subdecks <select
          bind:value={deckId}
          onchange={(event) => {
            deckId = Number(event.currentTarget.value);
            offset = 0;
            if (view === "review") resetReview();
            if (view === "browser") void browse();
          }}
          >{#each overview.decks as deck}<option value={deck.id}
              >{deck.name}{deck.archived ? " (archived)" : ""}</option
            >{/each}</select
        ></label
      ><label
        >Note type <select
          bind:value={typeId}
          onchange={(event) => {
            typeId = Number(event.currentTarget.value);
            if (view === "editor") switchType();
          }}
          >{#each overview.types as typ}<option value={typ.id}
              >{typ.name}</option
            >{/each}</select
        ></label
      >
    </div>
    {#if view === "decks"}
      <div class="metrics">
        <span><strong>{overview.stats.total}</strong> cards</span><span
          ><strong>{overview.stats.due}</strong> due</span
        ><span><strong>{overview.stats.new}</strong> available new</span><span
          ><strong>{overview.stats.streak}</strong> day streak</span
        ><span
          >{overview.stats.suspended} suspended · {overview.stats.leeches} leeches</span
        >
      </div>
      <div class="toolbar">
        <button onclick={() => editDeck()}>New deck / subdeck</button><button
          onclick={() => editType()}>New note type</button
        >{#if practiceResume}<button onclick={resumePractice} disabled={busy}
            >Resume last practice queue</button
          >{/if}
      </div>
      {#each overview.decks as deck}<article class="deck">
          <div>
            <h2>{deck.name}</h2>
            <p>
              {deck.due} due · {deck.new} new · {Math.round(
                deck.settings.retention * 100,
              )}% retention · {deck.settings.timezone}{deck.archived
                ? " · archived"
                : ""}
            </p>
          </div>
          <div>
            <button
              onclick={() => {
                deckId = deck.id;
                mode = "regular";
                void startReview();
              }}
              disabled={!!deck.archived}>Study</button
            ><button onclick={() => editDeck(deck)}>Settings</button><button
              onclick={() => archiveDeck(deck)}
              disabled={busy}>{deck.archived ? "Restore" : "Archive"}</button
            >
          </div>
        </article>{/each}
      <h2>Note types & templates</h2>
      <div class="toolbar">
        {#each overview.types as typ}<button onclick={() => editType(typ)}
            >{typ.name}</button
          >{/each}
      </div>
      <h2>Review activity · last 90 active days</h2>
      <div class="graph">
        {#each [...overview.stats.days].reverse() as day}<div
            title={`${day.day}: ${day.reviews} reviews, ${day.again} Again, ${Math.round(day.seconds)} seconds`}
          >
            <span
              style={`height:${Math.max(3, Math.min(100, day.reviews * 3))}px`}
            ></span><small>{day.day.slice(5)}</small>
          </div>{/each}
      </div>
      {#if !overview.stats.days.length}<p>No completed reviews yet.</p>{/if}
    {:else if view === "browser"}
      <form
        class="toolbar"
        onsubmit={(e) => {
          e.preventDefault();
          offset = 0;
          void browse();
        }}
      >
        <input
          aria-label="Search notes"
          bind:value={search}
          placeholder="Search text, tag:name, is:due/new/review/learning/suspended/leech/archived, deck:name"
        /><button>Search</button>
      </form>
      <p>
        {total} notes found. Editing retains schedules of unchanged card ordinals;
        deleted templates/clozes remove their cards.
      </p>
      {#each notes as note}<article class="note">
          <div class="note-fields">
            {#each Object.entries(note.fields) as [name, text]}<p>
                <strong>{name}</strong>
                {text.slice(0, 240)}{text.length > 240 ? "…" : ""}
              </p>{/each}
          </div>
          <p>{note.tags.join(" · ")}{note.archived ? " · archived" : ""}</p>
          <div class="toolbar">
            <button onclick={() => newNote(note)}>Edit</button><button
              onclick={() => noteAction(note, "archive", !note.archived)}
              disabled={busy}>{note.archived ? "Restore" : "Archive"}</button
            ><button
              class="danger"
              onclick={() => noteAction(note, "delete")}
              disabled={busy}>Delete</button
            >
          </div>
          {#each note.cards as card}<div class="card-row">
              <span
                >Card {card.ordinal + 1}: {card.state} · {new Date(
                  card.due,
                ).toLocaleString()} · {card.reviews} reviews / {card.lapses} lapses{card.leech
                  ? " · LEECH"
                  : ""}{card.buried_until &&
                new Date(card.buried_until) > new Date()
                  ? " · buried"
                  : ""}</span
              ><button
                onclick={() => cardAction(card, "suspend", !card.suspended)}
                disabled={busy}
                >{card.suspended ? "Unsuspend" : "Suspend"}</button
              ><button
                onclick={() =>
                  cardAction(
                    card,
                    "bury",
                    !card.buried_until ||
                      new Date(card.buried_until) <= new Date(),
                  )}
                disabled={busy}
                >{card.buried_until && new Date(card.buried_until) > new Date()
                  ? "Unbury"
                  : "Bury today"}</button
              >
            </div>{/each}
        </article>{/each}
      <div class="toolbar">
        <button
          disabled={offset === 0}
          onclick={() => {
            offset = Math.max(0, offset - 50);
            void browse();
          }}>Previous</button
        ><span>{offset + 1}–{Math.min(offset + 50, total)} / {total}</span
        ><button
          disabled={offset + 50 >= total}
          onclick={() => {
            offset += 50;
            void browse();
          }}>Next</button
        >
      </div>
    {:else if view === "editor"}
      <h2>{editing ? "Edit note" : "New note"}</h2>
      <p>
        Cloze syntax: {"{{c1::answer::hint}}"}. Multiple indices create separate
        cards. Formatting supports safe HTML, **bold**, `code`, local images and
        [sound:filename]. Remote images/scripts are never loaded.
      </p>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveNote();
        }}
      >
        {#each selectedType?.fields || [] as field}<label
            >{field}<textarea
              bind:value={editorFields[field]}
              rows="5"
              required={field === selectedType?.fields[0]}
            ></textarea></label
          >{/each}<label
          >Tags (space separated)<input bind:value={editorTags} /></label
        >
        <div class="toolbar">
          <button disabled={busy}>Save note</button><button
            type="button"
            onclick={() => show("browser")}>Cancel</button
          ><label class="upload"
            >Attach image / audio<input
              type="file"
              accept=".png,.jpg,.jpeg,.gif,.webp,.mp3,.ogg,.wav"
              onchange={upload}
              disabled={busy}
            /></label
          >
        </div>
      </form>
      {#if overview.media.length}<details>
          <summary>Local media ({overview.media.length})</summary
          >{#each overview.media as media}<p>
              {media.name} · {media.mime} · {Math.round(media.size / 1024)} KiB
            </p>{/each}
        </details>{/if}
    {:else if view === "deck-editor" && cfg}
      <h2>Deck settings</h2>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveDeck();
        }}
      >
        <label
          >Name (Parent::Child creates a subdeck)<input
            bind:value={deckName}
            required
          /></label
        >
        <div class="settings-grid">
          <label
            >New cards per day<input
              type="number"
              min="0"
              max="10000"
              bind:value={cfg.new_limit}
            /></label
          ><label
            >Reviews per day<input
              type="number"
              min="0"
              max="10000"
              bind:value={cfg.review_limit}
            /></label
          ><label
            >Desired retention (0.70–0.99)<input
              type="number"
              min="0.7"
              max="0.99"
              step="0.01"
              bind:value={cfg.retention}
            /></label
          ><label
            >Maximum interval (days)<input
              type="number"
              min="1"
              max="36500"
              bind:value={cfg.maximum_interval}
            /></label
          ><label
            >Learning steps (seconds)<input bind:value={learningText} /></label
          ><label
            >Relearning steps (seconds)<input
              bind:value={relearningText}
            /></label
          ><label
            >Leech suspension after lapses<input
              type="number"
              min="1"
              max="100"
              bind:value={cfg.leech_threshold}
            /></label
          ><label
            >IANA timezone<input bind:value={cfg.timezone} required /></label
          >
        </div>
        <label class="check"
          ><input type="checkbox" bind:checked={cfg.bury_siblings} />Bury
          siblings until next local midnight</label
        >
        <p>
          Learning/relearning always bypass daily limits. Retention changes
          affect future reviews, not existing due dates.
        </p>
        <button disabled={busy}>Save settings</button>
      </form>
    {:else if view === "type-editor"}
      <h2>Note type / template editor</h2>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveType();
        }}
      >
        <label>Name<input bind:value={typeName} required /></label><label
          >Kind<select bind:value={typeKind}
            ><option value="template">Custom templates</option><option
              value="basic">Basic</option
            ><option value="reversed">Reversed</option><option value="cloze"
              >Cloze</option
            ></select
          ></label
        ><label>Fields (comma separated)<input bind:value={fieldNames} /></label
        ><label
          >Templates JSON<textarea
            bind:value={templateJson}
            rows="12"
            spellcheck="false"
          ></textarea></label
        >
        <p>
          Each template has name, front, back. Use {"{{Field}}"}, {"{{FrontSide}}"},
          {"{{cloze:Text}}"}, {"{{#Field}}…{{/Field}}"} and {"{{^Field}}…{{/Field}}"}.
          Field names of in-use types are fixed. Template edits can remove
          cards; their review history remains but is detached.
        </p>
        <button disabled={busy}>Save note type</button>
      </form>
    {:else if view === "review"}
      <div class="toolbar">
        <label
          >Study mode<select bind:value={mode} onchange={resetReview}
            ><option value="regular">Scheduled review</option><option
              value="filtered">Filtered practice (does not reschedule)</option
            ><option value="cram">Cram (does not reschedule)</option></select
          ></label
        ><label
          >Filter<input
            bind:value={search}
            oninput={resetReview}
            placeholder="tag:name or search text"
          /></label
        ><button onclick={startReview} disabled={busy}>Start / refresh</button
        ><button onclick={undo} disabled={busy}>Undo last review</button>
      </div>
      <p>
        {remaining} eligible cards · {mode === "regular"
          ? "FSRS schedule"
          : "Practice only: regular due dates unchanged"}
      </p>
      <label class="check"
        ><input type="checkbox" bind:checked={typedEnabled} />Type your answer
        before revealing (self-graded)</label
      >
      {#if review}<article class="review-card" aria-live="polite">
          <small>{review.state} · {review.mode}</small>
          <div class="card-content">{@html review.front}</div>
          {#if typedEnabled}<label
              >Your answer<input
                bind:value={typedAnswer}
                readonly={revealed}
                autocomplete="off"
              /></label
            >{/if}{#if revealed}<hr />
            <div class="card-content">{@html back}</div>
            <div class="grades">
              {#each labels as label, index}<button
                  onclick={() => grade(index + 1)}
                  disabled={busy}
                  >{index + 1} · {label}{#if intervals[index]}<small
                      >{interval(intervals[index].seconds)}</small
                    >{/if}</button
                >{/each}
            </div>{:else}<button class="reveal" onclick={reveal} disabled={busy}
              >Reveal answer · Space</button
            >{/if}
        </article>{:else}<p>
          No card available. Refresh when a learning card becomes due, or use a
          practice filter.
        </p>{/if}
      <p class="hint">
        Outside inputs: Space reveals / grades Good; 1–4 grades only after
        reveal.
      </p>
    {:else if view === "history"}
      <div class="toolbar">
        <h2>Review history</h2>
        <button onclick={undo} disabled={busy}>Undo last review</button><button
          onclick={loadHistory}>Refresh</button
        >
      </div>
      <table>
        <thead
          ><tr
            ><th>When</th><th>Card</th><th>Grade</th><th>Mode</th><th>Status</th
            ></tr
          ></thead
        ><tbody
          >{#each historyRows as row}<tr
              ><td>{new Date(row.reviewed_at).toLocaleString()}</td><td
                >{row.card_id || "Deleted card"}</td
              ><td>{labels[row.rating - 1]}</td><td>{row.mode}</td><td
                >{row.undone ? "Undone" : "Recorded"}</td
              ></tr
            >{/each}</tbody
        >
      </table>
    {:else if view === "transfer"}
      <h2>Import / export</h2>
      <p>
        APKG uses real SQLite collections in ZIP archives. Both older Anki
        packages and current Zstandard-compressed packages with normalized note
        types and protobuf media manifests are supported. Existing note GUIDs
        are skipped. Unsafe executable/SVG media is not imported.
      </p>
      <p>
        CSV/TSV follows the selected note type's field order, optional header,
        then optional space-separated tags. Export a single note type for CSV;
        use APKG for mixed collections. APKG retains exact kitty scheduling
        state on reimport and available Anki FSRS memory; older scheduling uses
        estimated stability. The web upload limit is 11 MiB; each media file is
        limited to 8 MiB.
      </p>
      <label
        >Format<select bind:value={format}
          ><option value="apkg">Anki package (.apkg)</option><option value="csv"
            >CSV</option
          ><option value="tsv">TSV</option></select
        ></label
      >
      <div class="toolbar">
        <label class="upload"
          >Import into selected deck / type<input
            type="file"
            accept={format === "apkg"
              ? ".apkg"
              : format === "csv"
                ? ".csv"
                : ".tsv,.txt"}
            onchange={importFile}
            disabled={busy}
          /></label
        ><button onclick={exportFile} disabled={busy}
          >Export selected deck + subdecks</button
        >
      </div>
    {:else if view === "generate"}
      <h2>Generate draft cards from selected notes</h2>
      <p>
        Paste notes or select text in Canvas → Make flashcards. AI suggestions
        are never studied or saved automatically. Review privacy, model and
        budget before sending, then fact-check and accept each card.
      </p>
      <label
        >Source notes<textarea bind:value={sourceText} rows="8"
        ></textarea></label
      >
      <AIReview
        purpose="flashcards"
        text={sourceText}
        {demo}
        onresult={receiveAI}
      />
      {#each drafts as draft, index}<article class="draft">
          <label
            >Front<textarea bind:value={draft.front} disabled={draft.accepted}
            ></textarea></label
          ><label
            >Back<textarea bind:value={draft.back} disabled={draft.accepted}
            ></textarea></label
          ><label
            >Source quote<textarea
              bind:value={draft.source_quote}
              disabled={draft.accepted}
            ></textarea></label
          >
          <p>Tags: {draft.tags.join(", ")}</p>
          <button
            onclick={() => acceptDraft(index)}
            disabled={busy || draft.accepted}
            >{draft.accepted
              ? "Accepted and saved"
              : "I checked the facts — accept card"}</button
          >
        </article>{/each}
      {#if rawAI}<details>
          <summary>Raw provider output (not saved)</summary>
          <pre>{rawAI}</pre>
        </details>{/if}
    {/if}
  {:else}<p>Loading local flashcards…</p>{/if}
</section>

<style>
  .flashcards {
    color: var(--ink);
    max-width: 1100px;
    margin: auto;
    padding: 1.5rem;
  }
  header,
  .deck,
  .toolbar,
  .scope,
  .card-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    flex-wrap: wrap;
  }
  header h1 {
    margin: 0;
  }
  p,
  .hint,
  small {
    color: var(--muted);
  }
  nav,
  .toolbar,
  .scope {
    margin: 1rem 0;
  }
  nav {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
  }
  button,
  input,
  textarea,
  select {
    font: inherit;
  }
  button {
    background: var(--surface-raised);
    color: var(--ink);
    border: 1px solid var(--border);
    border-radius: 0.5rem;
    padding: 0.55rem 0.8rem;
    cursor: pointer;
  }
  button.active,
  .reveal {
    background: var(--accent);
    color: var(--surface);
  }
  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  label {
    display: block;
    margin: 0.8rem 0;
  }
  label input:not([type="checkbox"]),
  label textarea,
  label select {
    display: block;
    width: 100%;
    box-sizing: border-box;
    margin-top: 0.3rem;
  }
  input,
  textarea,
  select {
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.4rem;
    padding: 0.6rem;
  }
  textarea {
    resize: vertical;
    min-height: 4rem;
  }
  .scope label {
    flex: 1;
    min-width: 180px;
  }
  .toolbar input {
    flex: 1;
    min-width: 180px;
  }
  .metrics {
    display: flex;
    gap: 1.5rem;
    flex-wrap: wrap;
    background: var(--surface-raised);
    padding: 1rem;
    border-radius: 0.6rem;
  }
  .metrics strong {
    font-size: 1.5rem;
  }
  article {
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 0.7rem;
    padding: 1rem;
    margin: 1rem 0;
  }
  .deck h2 {
    font-size: 1.1rem;
    margin: 0;
  }
  .deck p {
    margin: 0.4rem 0;
  }
  .card-row {
    border-top: 1px solid var(--border);
    padding: 0.5rem 0;
  }
  .card-row span {
    flex: 1;
    font-size: 0.85rem;
  }
  .note-fields p {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .danger,
  .error {
    color: var(--warning);
  }
  .error {
    border: 1px solid var(--warning);
    padding: 0.75rem;
    border-radius: 0.5rem;
  }
  .settings-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 0 1rem;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .review-card {
    max-width: 750px;
    margin: 1.5rem auto;
    text-align: center;
  }
  .card-content {
    font-size: 1.3rem;
    overflow-wrap: anywhere;
    padding: 1.5rem 0;
  }
  .card-content :global(img) {
    max-width: 100%;
    max-height: 400px;
  }
  .card-content :global(audio) {
    max-width: 100%;
  }
  .grades {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.5rem;
  }
  .grades small {
    display: block;
  }
  .reveal {
    margin: 1rem;
  }
  .graph {
    display: flex;
    align-items: flex-end;
    gap: 0.3rem;
    overflow-x: auto;
    min-height: 130px;
    padding: 0.8rem 0;
  }
  .graph div {
    text-align: center;
    min-width: 35px;
  }
  .graph span {
    display: block;
    background: var(--accent);
    border-radius: 0.2rem 0.2rem 0 0;
  }
  .graph small {
    font-size: 0.65rem;
  }
  .upload {
    border: 1px dashed var(--border);
    padding: 0.5rem;
    border-radius: 0.5rem;
  }
  .upload input {
    max-width: 290px;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.9rem;
  }
  td,
  th {
    padding: 0.7rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  button:focus-visible,
  input:focus-visible,
  textarea:focus-visible,
  select:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
