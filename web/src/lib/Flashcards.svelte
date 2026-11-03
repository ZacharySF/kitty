<script lang="ts">
  import { buildStore, setPreferences } from "./build.svelte";
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "./api";
  import AIReview from "./AIReview.svelte";
  import EmptyState from "./EmptyState.svelte";
  import SectionGraph, { type SectionNode } from "./SectionGraph.svelte";
  import GradeKeys, { GRADES, type HeldKey } from "./flashcards/GradeKeys.svelte";
  import ReviewStack, { FLY_MS } from "./flashcards/ReviewStack.svelte";
  import DeckTiles from "./flashcards/DeckTiles.svelte";
  import MemoryStats from "./flashcards/stats/MemoryStats.svelte";
  import CardDetail, { STATE_COLOR, isBuried } from "./flashcards/CardDetail.svelte";
  import Select, { type SelectOption } from "./ui/Select.svelte";
  import OverflowMenu, { type OverflowItem } from "./ui/OverflowMenu.svelte";
  import Popover from "./ui/Popover.svelte";
  import {
    DUPLICATE_WARNING, addCard, checkCard, loadFronts, normalizeFront, preselectDeck, rememberDeck, rememberedDeck,
  } from "./flashcards/newCard";
  import { focusContext } from "./panelFullscreen";
  import type { Crumb } from "./breadcrumb.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  const pane = useWorkspaceContext();
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
    parent_id: number | null;
    settings: Settings;
    archived: number;
    revision: number;
    due: number;
    new: number;
    learning: number;
    review: number;
    recall: number | null;
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
    note_id?: number;
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
  let answerFace = $state(false);
  let held = $state<HeldKey>(null);
  let heldToken = 0;
  let flying = $state(0);
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
  type Reference = { kind: string; id: number | string; title: string; snippet: string; shared_terms: string[]; score: number; view: string };
  let references = $state<Record<string, Reference[]>>({linked: [], unlinked: [], related: []});
  let referenceError = $state("");
  /** How the Decks view draws the collection: due-ring tiles or the deck → note graph (build: ui.flashcards.look). */
  const look = $derived(buildStore.build.ui.flashcards.look);
  function setLook(next: "tiles" | "graph") {
    void setPreferences({ ui: { flashcards: { look: next } } });
  }
  let graphFocus = $derived(deckId ? `deck:${deckId}` : null);
  let graphRevision = $state(0);
  /** Bumped after every overview load so the memory stats refresh with it. */
  let loads = $state(0);
  let noteRequest = 0;
  function allDecks() {
    noteRequest += 1;
    editing = null;
    deckId = 0;
    search = "";
    offset = 0;
    error = "";
    view = "decks";
    graphRevision += 1;
  }
  function focusDeck(id: number) {
    noteRequest += 1;
    deckId = id;
    editing = null;
    error = "";
    view = "decks";
  }
  async function selectGraphNode(node: SectionNode) {
    if (!pane.active || typeof node.ref !== "number") return;
    if (node.entity === "deck") {
      focusDeck(node.ref);
    } else if (node.entity === "flashcard_note") {
      const request = ++noteRequest;
      error = "";
      try {
        const result = await api<{note: Note}>(`/flashcards/note?id=${node.ref}`);
        if (!pane.active || request !== noteRequest) return;
        newNote(result.note);
      } catch (e) {
        if (request !== noteRequest) return;
        failure(e);
        graphRevision += 1;
      }
    }
  }
  async function deleteGraphNote(node: SectionNode) {
    if (!pane.active || node.entity !== "flashcard_note" || typeof node.ref !== "number" || busy) return;
    try {
      const result = await api<{note: Note}>(`/flashcards/note?id=${node.ref}`);
      await noteAction(result.note, "delete");
    } catch (cause) { failure(cause); }
  }
  $effect(() => {
    const id = view === "deck-editor" ? deckEditing?.id || 0 : deckId;
    const byId = new Map((overview?.decks ?? []).map(deck => [deck.id, deck]));
    const chain: Deck[] = [];
    const seen = new Set<number>();
    let deck = byId.get(id);
    while (deck && !seen.has(deck.id)) {
      seen.add(deck.id);
      chain.unshift(deck);
      deck = deck.parent_id ? byId.get(deck.parent_id) : undefined;
    }
    const crumbs: Crumb[] = chain.map(deck => ({
      label: deck.name.split("::").at(-1) || deck.name,
      onclick: () => focusDeck(deck.id),
    }));
    if (view === "editor") crumbs.push({label: editing ? `Card ${editing.id}` : "New card"});
    if (view === "review" && review?.note_id) crumbs.push({label: `Note ${review.note_id}`});
    pane.setTrail("flashcards", crumbs, allDecks);
    return () => pane.clearTrail("flashcards");
  });
  $effect(() => {
    const active = pane.active && (view === "editor" || (view === "review" && revealed));
    const content = view === "editor" ? Object.values(editorFields).join("\n") : `${review?.front || ""}\n${back}`;
    const noteId = view === "editor" ? editing?.id : review?.note_id;
    let current = true;
    references = {linked: [], unlinked: [], related: []};
    referenceError = "";
    const timer = setTimeout(async () => {
      if (!active || !content.trim()) return;
      try {
        const result = await api<Record<string, Reference[]>>("/backlinks/related", {
          text: content.replace(/<[^>]*>/g, " ").slice(0, 20000),
          ...(noteId ? {exclude: `flashcard_note:${noteId}`} : {}),
        });
        if (current) references = result;
      } catch (e) {
        if (current) referenceError = e instanceof Error ? e.message : String(e);
      }
    }, 350);
    return () => { current = false; clearTimeout(timer); };
  });
  function openReference(ref: Reference) {
    if (!pane.active) return;
    if (ref.kind === "note_page") sessionStorage.setItem("kitty.notes.open-page", String(ref.id));
    location.hash = `#${ref.view}`;
    if (ref.kind === "note_page") window.dispatchEvent(new CustomEvent("kitty-open-note", {detail: {page_id: Number(ref.id)}}));
    if (ref.kind === "flashcard_note") {
      deckId = 0;
      offset = 0;
      search = `nid:${ref.id}`;
      view = "browser";
      void browse();
    }
  }
  function linkReference(ref: Reference) {
    const escaped = ref.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const mention = new RegExp(escaped, "i");
    const field = Object.keys(editorFields).find((name) => mention.test(editorFields[name]));
    if (field) editorFields[field] = editorFields[field].replace(mention, (match) => `[[${match}]]`);
  }
  let selectedType = $derived(overview?.types.find((t) => t.id === typeId));
  let selectedDeck = $derived(overview?.decks.find((deck) => deck.id === deckId));
  let scopeLabel = $derived(selectedDeck?.name || "All decks");
  let editorView = $derived(["editor", "deck-editor", "type-editor"].includes(view));
  /** Whether any deck can take a new card. */
  let canAdd = $derived(!!overview?.decks.some((deck) => !deck.archived));
  const deckOption = (deck: Deck, index: number): SelectOption => ({
    value: String(deck.id),
    label: deck.name.replaceAll("::", " › "),
    color: `var(--c${(index % 6) + 1})`,
    hint: deck.archived ? "archived" : undefined,
  });
  /** Q16: the header chip — All decks, then every deck with today's load (due + new, as on the tiles). */
  let scopeOptions = $derived<SelectOption[]>([
    { value: "0", label: "All decks", count: `${(overview?.stats.due ?? 0) + (overview?.stats.new ?? 0)} due` },
    ...(overview?.decks ?? []).map((deck, index) => ({ ...deckOption(deck, index), count: `${deck.due + deck.new} due` })),
  ]);
  let editorDeckOptions = $derived((overview?.decks ?? []).map(deckOption));
  let addDeckOptions = $derived(editorDeckOptions.filter((option) => !option.hint));
  let typeOptions = $derived<SelectOption[]>((overview?.types ?? []).map((typ) => ({ value: String(typ.id), label: typ.name })));
  const kindOptions: SelectOption[] = [
    { value: "template", label: "Custom" }, { value: "basic", label: "Basic" }, { value: "reversed", label: "Reversed" }, { value: "cloze", label: "Cloze" },
  ];
  const modeOptions: SelectOption[] = [
    { value: "regular", label: "Scheduled" }, { value: "filtered", label: "Filtered practice" }, { value: "cram", label: "Cram" },
  ];
  const formatOptions: SelectOption[] = [{ value: "apkg", label: "Anki .apkg" }, { value: "csv", label: "CSV" }, { value: "tsv", label: "TSV" }];
  /** Q6: rare tools and note types behind ⋯. */
  let moreItems = $derived<OverflowItem[]>([
    { label: "New deck", onselect: () => editDeck() },
    ...(practiceResume ? [{ label: "Resume last practice queue", onselect: resumePractice }] : []),
    { label: "Practice & review options", onselect: () => void show("review") },
    { label: "Review history", onselect: () => void show("history") },
    { label: "Import / export", onselect: () => void show("transfer") },
    { label: "AI drafts", onselect: () => void show("generate") },
    { separator: true },
    { label: "New note type", onselect: () => editType() },
    ...(overview?.types ?? []).map((typ) => ({ label: `Edit ${typ.name}`, onselect: () => editType(typ) })),
  ]);
  function setScope(id: number) {
    if (id === deckId) return;
    if (view === "decks") return focusDeck(id);
    deckId = id;
    offset = 0;
    if (view === "review") resetReview();
    if (view === "browser") void browse();
  }
  /** The one way to start a card: the quick-add popover for the deck in scope (also N). */
  function quickCard() {
    window.dispatchEvent(new CustomEvent("kitty-quick-card", { detail: { deck_id: deckId || null } }));
  }
  function reviewDue() {
    mode = "regular";
    search = "";
    void startReview();
  }
  const labels = GRADES.map((grade) => grade.label);
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
      if (!deckId && view !== "decks") deckId = overview.decks[0]?.id || 0;
      graphRevision += 1;
      loads += 1;
      if (!typeId) typeId = overview.types[0]?.id || 0;
      if (pane.active) await consumePendingNavigation();
    } catch (e) {
      failure(e);
    }
  }
  $effect(() => {
    revision;
    void load();
  });
  $effect(() => {
    if (pane.active) void consumePendingNavigation();
    else noteRequest += 1;
  });
  async function consumePendingNavigation() {
    if (!pane.active) return;
    try {
      const pending = sessionStorage.getItem("kitty.flashcards.open-note");
      if (pending && /^\d+$/.test(pending)) {
        sessionStorage.removeItem("kitty.flashcards.open-note");
        deckId = 0; offset = 0; search = `nid:${pending}`; view = "browser";
        await browse();
      }
      if (!pane.active) return;
      const text = sessionStorage.getItem("kitty.flashcards.import-text");
      if (text) {
        sessionStorage.removeItem("kitty.flashcards.import-text");
        selectedText(new CustomEvent("kitty-study-text", {detail: {text, purpose: "flashcards"}}));
      }
    } catch { /* Live navigation remains available if storage is disabled. */ }
  }
  async function browse() {
    try {
      const query=new URLSearchParams({search,offset:String(offset),limit:"50"});
      if(deckId)query.set("deck_id",String(deckId));
      const r = await api<{ notes: Note[]; total: number }>(
        `/flashcards/browser?${query}`,
      );
      notes = r.notes;
      total = r.total;
      if (!cardRows.some((row) => row.card.id === selectedCard)) selectedCard = cardRows[0]?.card.id || 0;
    } catch (e) {
      failure(e);
    }
  }
  /** The browser lists cards, not notes: one row per generated card. */
  let cardRows = $derived(notes.flatMap((note) => note.cards.map((card) => ({ note, card }))));
  let selectedCard = $state(0);
  let selectedRow = $derived(cardRows.find((row) => row.card.id === selectedCard));
  const plain = (text: string) => text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim() || "(empty)";
  function dueLabel(iso: string) {
    const days = (new Date(iso).getTime() - Date.now()) / 86400000;
    if (days < -0.5) return `${Math.round(-days)}d overdue`;
    if (days < 1) return "due today";
    if (days < 2) return "due tomorrow";
    return `due in ${Math.round(days)}d`;
  }
  function toggleFilter(term: string) {
    const terms = search.split(/\s+/).filter(Boolean);
    search = (terms.includes(term) ? terms.filter((item) => item !== term) : [...terms, term]).join(" ");
    offset = 0;
    void browse();
  }
  /** Inline add row (Q66): front, Tab, back, Enter saves and readies the next card. */
  let addFront = $state(""),
    addBack = $state(""),
    addPick = $state(0),
    addFronts = $state<string[]>([]),
    addProblem = $state(""),
    addAdded = $state(""),
    addConfirmed = $state(""),
    adding = $state(false);
  let addFrontField = $state<HTMLInputElement>(),
    addBackField = $state<HTMLInputElement>();
  /** The scope's deck, or the row's own pick while All decks is in scope. */
  let addDeck = $derived(deckId || addPick);
  let addCheck = $derived(checkCard({ front: addFront, back: addBack }, addFronts));
  $effect(() => {
    if (overview && !overview.decks.some((deck) => deck.id === addPick && !deck.archived))
      addPick = preselectDeck(overview.decks, null, rememberedDeck());
  });
  $effect(() => {
    if (pane.active && deckId) rememberDeck(deckId);
  });
  $effect(() => {
    const id = view === "browser" ? addDeck : 0;
    loads;
    let current = true;
    loadFronts(api, id).then((items) => { if (current) addFronts = items; }, (e) => { if (current) failure(e); });
    return () => { current = false; };
  });
  async function addInline() {
    if (!pane.active || adding || !overview) return;
    adding = true;
    addProblem = "";
    try {
      const draft = { front: addFront, back: addBack }, deck = addDeck;
      const result = await addCard(api, { deckId: deck, types: overview.types, draft, fronts: addFronts, allowDuplicate: addConfirmed === normalizeFront(addFront) });
      if (!result.saved) {
        if (result.check.problem) {
          addProblem = result.check.problem;
          (result.check.field === "back" ? addBackField : addFrontField)?.focus();
        } else addConfirmed = normalizeFront(addFront);
        return;
      }
      addFronts = [...addFronts, draft.front];
      addAdded = `Added to ${overview.decks.find((item) => item.id === deck)?.name ?? "the deck"}.`;
      addFront = addBack = addConfirmed = "";
      addFrontField?.focus();
      await load();
      await browse();
      const card = notes.find((note) => note.id === result.saved?.id)?.cards[0];
      if (card) selectedCard = card.id;
    } catch (e) {
      addProblem = e instanceof Error ? e.message : String(e);
    } finally {
      adding = false;
    }
  }
  function addEdited() {
    addProblem = "";
    addAdded = "";
  }
  /** A card saved from the quick-add popover shows up here straight away. */
  async function cardsChanged() {
    await load();
    if (view === "browser") await browse();
  }
  async function show(next: string) {
    noteRequest += 1;
    if (next === "decks") { focusDeck(deckId); return; }
    view = next;
    error = "";
    if (next === "browser") await browse();
    if (next === "history") await loadHistory();
  }
  async function operate<T = unknown>(
    path: string,
    body: unknown,
    after?: (result: T) => Promise<void>,
  ) {
    busy = true;
    error = "";
    message = "";
    try {
      const result = await api<T>(path, body);
      await load();
      if (after) await after(result);
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  function newNote(note: Note | null = null) {
    if (!pane.active) return;
    noteRequest += 1;
    if (!note && !deckId) deckId = overview?.decks.find(deck => !deck.archived && deck.name === "Default")?.id
      || overview?.decks.find(deck => !deck.archived)?.id || overview?.decks[0]?.id || 0;
    editing = note;
    if (note) {
      deckId = note.deck_id;
      typeId = note.type_id;
      editorFields = { ...note.fields };
      editorTags = note.tags.join(" ");
    } else {
      typeId = overview?.types.find((type) => type.kind === "basic" && type.fields.includes("Front") && type.fields.includes("Back"))?.id || typeId;
      editorFields = Object.fromEntries(
        (selectedType?.fields || []).map((f) => [f, ""]),
      );
      editorTags = "";
    }
    view = "editor";
  }
  function switchType() {
    // Keep the other format's draft fields in memory; only selected fields are saved.
    editorFields = {
      ...editorFields,
      ...Object.fromEntries((selectedType?.fields || []).map((field) => [field, editorFields[field] || ""])),
    };
  }
  async function saveNote() {
    if (!pane.active || busy || !deckId || !selectedType) return;
    await operate(
      "/flashcards/note",
      {
        id: editing?.id,
        revision: editing?.revision,
        deck_id: deckId,
        type_id: typeId,
        fields: Object.fromEntries(selectedType.fields.map((field) => [field, editorFields[field] || ""])),
        tags: editorTags.split(/\s+/).filter(Boolean),
        archived: editing?.archived || false,
      },
      async () => {
        search = "";
        offset = 0;
        view = "browser";
        await browse();
      },
    );
  }
  function editDeck(deck: Deck | null = null, name = "") {
    if (!pane.active) return;
    noteRequest += 1;
    deckEditing = deck;
    deckName = deck?.name || name;
    // A snapshot: the deck is reactive state, which structuredClone cannot copy.
    cfg = structuredClone($state.snapshot(deck?.settings) || defaultSettings);
    learningText = cfg.learning_steps.join(",");
    relearningText = cfg.relearning_steps.join(",");
    view = "deck-editor";
  }
  async function saveDeck() {
    if (!pane.active || busy || !cfg) return;
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
    const creating = !deckEditing;
    await operate<{ deck: Pick<Deck, "id" | "name"> }>(
      "/flashcards/deck",
      {
        id: deckEditing?.id,
        revision: deckEditing?.revision,
        name: deckName,
        settings: value,
        archived: deckEditing?.archived || false,
      },
      async ({ deck }) => {
        deckId = deck.id;
        if (creating && pane.active) {
          newNote();
          message = `Created “${deck.name}”. Add its first question and answer below.`;
        } else focusDeck(deck.id);
      },
    );
  }
  function editType(typ: NoteType | null = null) {
    noteRequest += 1;
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
    answerFace = false;
    intervals = [];
    remaining = 0;
    studyId = null;
  }
  async function startReview(resume: boolean | Event = false) {
    // A submitted rating may finish after this pane is hidden; advance its local queue without focusing it.
    if (!pane.active && resume !== true) return;
    noteRequest += 1;
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
      answerFace = false;
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
    if (!pane.active || !review || revealed || busy) return;
    busy = true;
    try {
      const r = await api<{ back: string; intervals: typeof intervals }>(
        "/flashcards/reveal",
        { token: review.token },
      );
      back = r.back;
      intervals = r.intervals;
      revealed = true;
      answerFace = true;
    } catch (e) {
      failure(e);
    } finally {
      busy = false;
    }
  }
  async function grade(rating: number) {
    if (!pane.active || !review || !revealed || busy) return;
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
    if (!pane.active) return;
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
  async function cardAction(card: Pick<Card, "id" | "revision">, action: string, value = true) {
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
    if (!pane.active) return;
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
    if (!pane.active || busy) return;
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
    window.addEventListener("kitty-flashcards-changed", cardsChanged);
    void consumePendingNavigation();
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
    return () => {
      window.removeEventListener("kitty-study-text", selectedText);
      window.removeEventListener("kitty-flashcards-changed", cardsChanged);
    };
  });
  /** Sinks `key` on the keyboard strip until `action` settles (at least briefly, so a tap is visible). */
  async function press(key: HeldKey, action: () => Promise<void>) {
    if (busy) return;
    const token = ++heldToken, pressedAt = performance.now();
    held = key;
    await action();
    setTimeout(() => { if (token === heldToken) held = null; }, Math.max(0, 140 - (performance.now() - pressedAt)));
  }
  /** Sends the top card off the pile in the grade's direction, then records the grade. */
  async function sendOff(rating: number) {
    if (!pane.active || !review || !revealed || busy || flying) return;
    flying = rating;
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    await new Promise((resolve) => setTimeout(resolve, still ? 0 : FLY_MS));
    await grade(rating);
    flying = 0;
  }
  function flip() {
    if (!revealed) void press("space", reveal);
    else answerFace = !answerFace;
  }
  function space() {
    void press("space", () => (revealed ? sendOff(3) : reveal()));
  }
  function keydown(event: KeyboardEvent) {
    if (!pane.active || event.ctrlKey || event.altKey || event.metaKey || event.repeat || event.defaultPrevented) return;
    // N opens the quick-add popover for the deck in view, outside text fields and dialogs.
    if (event.key.toLowerCase() === "n" && focusContext(event.target) === "other" && !document.querySelector("dialog[open]")) {
      event.preventDefault();
      quickCard();
      return;
    }
    if (view !== "review" || busy) return;
    const target = event.target as HTMLElement;
    // The focused top card of the stack still takes Space and 1–4.
    if (
      target.closest("input,textarea,select,button,a,[role=button]:not([data-review-card])") ||
      target.isContentEditable
    )
      return;
    if (event.code === "Space") {
      event.preventDefault();
      space();
    } else if (revealed && /^[1-4]$/.test(event.key)) {
      event.preventDefault();
      const rating = Number(event.key);
      void press(rating, () => sendOff(rating));
    }
  }
</script>

{#snippet referencePanel()}
  {#if referenceError || Object.values(references).some(items=>items.length)}
  <Panel>
    <aside aria-label="References">
      {#each [["linked", "Linked"], ["unlinked", "Unlinked"], ["related", "Related"]] as [key, label]}
        {#if references[key]?.length}
          <h3>{label}</h3>
          {#each references[key] as ref}
            <div class="reference-item">
              <Button size="sm" variant="ghost" onclick={() => openReference(ref)}>{ref.title}</Button>
              <p>{ref.snippet}</p>
              {#if key === "unlinked" && view === "editor" && Object.values(editorFields).some((value) => value.toLocaleLowerCase().includes(ref.title.toLocaleLowerCase()))}<Button size="xs" variant="ghost" onclick={() => linkReference(ref)}>Link</Button>{/if}
              {#if ref.shared_terms.length}<small>{ref.shared_terms.join(" · ")}</small>{/if}
            </div>
          {/each}
        {/if}
      {/each}
      {#if referenceError}<p role="alert">{referenceError}</p>{/if}
    </aside>
  </Panel>
  {/if}
{/snippet}

<svelte:window onkeydown={keydown} />
<section class="flashcards" aria-label="Flashcards workspace">
  <header>
    <h1>Flashcards</h1>
    {#if demo}<span class="demo">Demo</span>{/if}
    {#if overview && !editorView}<span class="deck-chip"><Select kind="search" label="Deck scope" options={scopeOptions} value={String(deckId)}
      onchange={(next) => setScope(Number(next))} oncreate={(name) => editDeck(null, name)} createAlways
      createLabel={(name) => (name ? `New deck “${name}”` : "New deck")} /></span>{/if}
    {#if !editorView}<div class="toolbar primary-actions">
      <Button size="sm" onclick={quickCard} disabled={!overview || busy || !overview.decks.some(deck => !deck.archived)} title="New card · N"
        >New card <kbd aria-hidden="true">N</kbd></Button>
      <Button intent="primary" onclick={reviewDue} disabled={!overview || busy || !overview.stats.total}>Review due</Button>
    </div>{/if}
  </header>
  {#if !editorView}
    <nav aria-label="Flashcard views">
      {#each [["decks", "Decks"], ["browser", "Cards"], ["stats", "Stats"]] as item}
        <Button size="sm" variant="ghost" aria-current={view === item[0] ? "page" : undefined} onclick={() => void show(item[0])}>{item[1]}</Button>
      {/each}
      <span class="grow"></span>
      {#if view === "decks" && overview?.stats.total}<div class="look" role="radiogroup" aria-label="Deck overview look">
        {#each [["tiles", "Tiles"], ["graph", "Graph"]] as const as [value, label]}
          <button role="radio" aria-checked={look === value} onclick={() => setLook(value)}>{label}</button>
        {/each}
      </div>{/if}
      <OverflowMenu label="More flashcard actions" items={moreItems} />
    </nav>
  {/if}
  {#if error}<p class="error" role="alert">{error}<button class="icon-button" aria-label="Dismiss flashcard error" onclick={() => (error = "")}>×</button></p>{/if}
  {#if message}<p role="status">{message}<button class="icon-button" aria-label="Dismiss flashcard message" onclick={() => (message = "")}>×</button></p>{/if}
  {#if overview}
    {#if view === "decks"}
      {#if !overview.stats.total}
        <Panel>
          <EmptyState eyebrow="Start with one question" title="Your first card is a question and an answer.">
            <p>{canAdd ? "Press N or New card to write a front and a back." : "Make a deck, then write a front and a back."}</p>
            {#snippet actions()}
              <Button intent={canAdd ? undefined : "primary"} variant={canAdd ? "ghost" : undefined} onclick={() => editDeck()}>New deck</Button>
            {/snippet}
          </EmptyState>
        </Panel>
      {/if}
      {#if look === "graph"}
        <SectionGraph section="flashcards" revision={graphRevision} focus={graphFocus} onselect={selectGraphNode} ondelete={deleteGraphNote} />
      {:else if overview.stats.total}
      <DeckTiles
        decks={overview.decks}
        selected={deckId}
        {busy}
        onselect={(deck) => focusDeck(deck.id === deckId ? 0 : deck.id)}
        onreview={(deck) => { deckId = deck.id; reviewDue(); }}
        onsettings={(deck) => editDeck(overview?.decks.find((item) => item.id === deck.id) ?? null)}
        onarchive={(deck) => { const full = overview?.decks.find((item) => item.id === deck.id); if (full) void archiveDeck(full); }}
      />
      {/if}
    {:else if view === "stats"}
      <MemoryStats {deckId} revision={loads} />
    {:else if view === "browser"}
      <form
        class="toolbar browser-search"
        onsubmit={(e) => {
          e.preventDefault();
          offset = 0;
          void browse();
        }}
      >
        <input
          type="search"
          aria-label="Filter cards"
          title="is:due/new/review/learning/suspended/leech/archived, tag:name, deck:name"
          bind:value={search}
          placeholder="Filter cards, tag:name or deck:name"
        /><Button type="submit" size="sm">Filter</Button>
      </form>
      <div class="filters" role="group" aria-label="Quick filters">
        {#each ["due", "new", "learning", "review", "leech", "suspended"] as filter}
          {@const on = search.split(/\s+/).includes(`is:${filter}`)}
          <button aria-pressed={on} onclick={() => toggleFilter(`is:${filter}`)}>{filter}</button>
        {/each}
        <span>{total} notes in {scopeLabel}{search ? ` matching “${search}”` : ""}</span>
      </div>
      {#if canAdd}<form
        class="add-row"
        aria-label="Add a card"
        onsubmit={(event) => {
          event.preventDefault();
          void addInline();
        }}
      >
        <span class="plus" aria-hidden="true">＋</span>
        {#if deckId}<span class="add-deck" title="New cards go to the deck in scope">{selectedDeck?.name}</span>
        {:else}<span class="add-deck-pick"><Select label="Deck for the new card" kind="list" options={addDeckOptions} value={String(addPick)}
          onchange={(next) => (addPick = Number(next))} /></span>{/if}
        <input
          bind:this={addFrontField}
          bind:value={addFront}
          aria-label="New card front"
          placeholder="Front — a question or cue"
          autocomplete="off"
          oninput={() => { addConfirmed = ""; addEdited(); }}
          onkeydown={(event) => {
            // Enter with the back still empty moves on to it, like Tab.
            if (event.key === "Enter" && !event.isComposing && addFront.trim() && !addBack.trim()) {
              event.preventDefault();
              addBackField?.focus();
            }
          }}
        />
        <input
          bind:this={addBackField}
          bind:value={addBack}
          aria-label="New card back"
          placeholder="Back — the answer"
          autocomplete="off"
          oninput={addEdited}
        />
        <Button type="submit" size="sm" disabled={adding}>Add</Button>
        <p class="add-note" class:problem={addProblem} class:warning={!addProblem && addCheck.duplicate} class:added={!addProblem && !addCheck.duplicate && addAdded} role="status">
          {addProblem || (addCheck.duplicate ? DUPLICATE_WARNING : addAdded) || "Tab moves to the back · Enter saves and starts the next card"}
        </p>
      </form>{/if}
      {#if !total}<Panel><EmptyState title={search ? "No matching cards" : "No cards in this scope yet"}>
        <p>{search ? "Try a broader search." : "Write a front and a back in the row above."}</p>
        {#snippet actions()}
          {#if search}<Button variant="ghost" onclick={() => { search = ""; offset = 0; void browse(); }}>Clear search</Button>{/if}
        {/snippet}
      </EmptyState></Panel>{:else}
      <div class="split-browser">
        <div class="card-list">
          <ul aria-label="Cards">
            {#each cardRows as { note, card } (card.id)}
              {@const shown = card.suspended ? "suspended" : card.leech ? "leech" : card.state}
              <li>
                <button
                  class:on={card.id === selectedCard}
                  aria-current={card.id === selectedCard ? "true" : undefined}
                  onclick={() => (selectedCard = card.id)}
                  oncontextmenu={(event) => {
                    if (window.getSelection()?.toString()) return;
                    event.preventDefault(); if (!busy) void noteAction(note, "delete");
                  }}
                >
                  <span class="dot" style:background={STATE_COLOR[shown]}></span>
                  <span class="front">{plain(Object.values(note.fields)[0] || "")}{note.cards.length > 1 ? ` · card ${card.ordinal + 1}` : ""}</span>
                  <span class="meta">{shown}{shown === "new" || card.suspended ? "" : ` · ${dueLabel(card.due)}`}{isBuried(card.buried_until) ? " · buried" : ""}{note.archived ? " · archived" : ""}</span>
                </button>
              </li>
            {/each}
          </ul>
          <div class="pager">
            <button
              disabled={offset === 0}
              onclick={() => {
                offset = Math.max(0, offset - 50);
                void browse();
              }}>Previous</button
            ><span>{offset + 1}–{Math.min(offset + 50, total)} / {total} notes</span
            ><button
              disabled={offset + 50 >= total}
              onclick={() => {
                offset += 50;
                void browse();
              }}>Next</button
            >
          </div>
        </div>
        {#if selectedRow}
          {@const note = selectedRow.note}
          <CardDetail cardId={selectedRow.card.id} revision={loads} {busy} onaction={(card, action, value) => void cardAction(card, action, value)}>
            {#snippet noteActions()}
              <button onclick={() => newNote(note)} disabled={busy}>Edit</button>
              <button onclick={() => noteAction(note, "archive", !note.archived)} disabled={busy}>{note.archived ? "Restore note" : "Archive note"}</button>
              <button class="danger" onclick={() => noteAction(note, "delete")} disabled={busy}>Delete note</button>
            {/snippet}
          </CardDetail>
        {/if}
      </div>
      {/if}
    {:else if view === "editor"}
      <h2>{editing ? "Edit card" : "New card"}</h2>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveNote();
        }}
      >
        <div class="toolbar editor-pickers">
          <span class="picker"><span>Deck</span><Select label="Save card in deck" kind="search" options={editorDeckOptions} value={String(deckId)}
            onchange={(next) => (deckId = Number(next))} disabled={busy} /></span>
          <span class="picker" title="Cloze, reversed and custom formats may create several cards; changing format can add or remove cards when saved."
            ><span>Format</span><Select label="Card format" kind="list" options={typeOptions} value={String(typeId)}
            onchange={(next) => { typeId = Number(next); switchType(); }} disabled={busy} /></span>
        </div>
        {#each selectedType?.fields || [] as field}<label
            >{field === "Front" ? "Front · question" : field === "Back" ? "Back · answer" : field}<textarea
              bind:value={editorFields[field]}
              rows="5"
              placeholder={field === "Front" ? "Ask one clear question or give a retrieval cue…" : field === "Back" ? "Write the answer you will reveal after trying…" : `Enter ${field}…`}
              required={field === selectedType?.fields[0]}
            ></textarea></label
          >{/each}
        <div class="toolbar editor-extras">
          <label class="tags">Tags<input bind:value={editorTags} placeholder="space separated"
            title={"Cloze: {{c1::answer::hint}}. Safe HTML, **bold**, `code`, local images and [sound:file] work."} /></label>
          <label class="upload" title="Reference it with <img src=&quot;file.png&quot;> or [sound:file]. Uploading does not save the card.">Attach image / audio<input
            type="file"
            accept=".png,.jpg,.jpeg,.gif,.webp,.mp3,.ogg,.wav"
            onchange={upload}
            disabled={busy}
          /></label>
          {#if overview.media.length}<Popover label="Local media" placement="bottom-end">
            {#snippet trigger(props)}<button type="button" class="media-button" {...props}>Media · {overview?.media.length}</button>{/snippet}
            <ul class="media-list">{#each overview.media as media}<li>{media.name} · {media.mime} · {Math.round(media.size / 1024)} KiB</li>{/each}</ul>
          </Popover>{/if}
        </div>
        <div class="toolbar editor-actions">
          <Button type="submit" intent="primary" disabled={busy || !deckId || !selectedType}>{busy ? "Saving…" : editing ? "Save changes" : "Save card"}</Button>
          <Button type="button" variant="ghost" onclick={() => void show("browser")} disabled={busy}>Cancel</Button>
        </div>
      </form>
      {@render referencePanel()}
    {:else if view === "deck-editor" && cfg}
      <h2>{deckEditing ? "Deck settings" : "New deck"}</h2>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveDeck();
        }}
      >
        <label
          >Deck name<input
            bind:value={deckName}
            placeholder="Parent::Child makes a subdeck"
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
            >Time zone<Select kind="timezone" label="Time zone" value={cfg.timezone} onchange={(next) => cfg && (cfg.timezone = next)} /></label
          >
        </div>
        <label class="check" title="Learning and relearning always bypass daily limits; retention changes affect future reviews only."
          ><input type="checkbox" bind:checked={cfg.bury_siblings} />Bury
          siblings until next local midnight</label
        >
        <div class="toolbar editor-actions">
          <Button type="submit" intent="primary" disabled={busy || !deckName.trim()}>{deckEditing ? "Save settings" : "Create deck & add first card"}</Button>
          <Button type="button" variant="ghost" onclick={() => void show("decks")} disabled={busy}>Cancel</Button>
        </div>
      </form>
    {:else if view === "type-editor"}
      <h2>Note type / template editor</h2>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveType();
        }}
      >
        <label>Name<input bind:value={typeName} required /></label><span class="picker"
          ><span>Kind</span><Select label="Kind" options={kindOptions} bind:value={typeKind} /></span
        ><label>Fields (comma separated)<input bind:value={fieldNames} /></label
        ><label
          >Templates JSON<textarea
            bind:value={templateJson}
            rows="12"
            spellcheck="false"
            title={"Each template has name, front, back. Use {{Field}}, {{FrontSide}}, {{cloze:Text}}, {{#Field}}…{{/Field}} and {{^Field}}…{{/Field}}."}
          ></textarea></label
        >
        <p class="hint">Template edits can remove cards; their review history is detached.</p>
        <div class="toolbar"><Button type="submit" intent="primary" disabled={busy}>Save note type</Button><Button type="button" variant="ghost" onclick={() => void show("decks")} disabled={busy}>Cancel</Button></div>
      </form>
    {:else if view === "review"}
      <h2>{mode === "regular" ? "Review due cards" : "Practice cards"}</h2>
      <div class="toolbar review-options">
        <Select label="Study mode" options={modeOptions} bind:value={mode} onchange={resetReview} />
        <input
          aria-label="Filter"
          bind:value={search}
          oninput={resetReview}
          placeholder="Filter: tag:name or text"
        /><Button size="sm" onclick={() => void startReview()} disabled={busy}>Start / refresh</Button>
        <label class="check"><input type="checkbox" bind:checked={typedEnabled} />Type your answer</label>
        <span class="grow"></span>
        <span class="count">{remaining} eligible cards</span>
        <Button size="sm" variant="ghost" onclick={undo} disabled={busy}>Undo last review</Button>
      </div>
      {#if review}<div class="review-card" aria-live="polite">
          <ReviewStack
            token={review.token}
            front={review.front}
            {back}
            caption={`${review.state} · ${review.mode}`}
            {revealed}
            {answerFace}
            {remaining}
            {flying}
            onflip={flip}
            onswipe={(rating) => void press(rating, () => sendOff(rating))}
          />
          {#if typedEnabled}<label
              >Your answer<input
                bind:value={typedAnswer}
                readonly={revealed}
                autocomplete="off"
              /></label
            >{/if}
          <GradeKeys {revealed} {intervals} {held} ongrade={(rating) => void press(rating, () => sendOff(rating))} onspace={space} />
          {#if revealed}{@render referencePanel()}{/if}
        </div>{:else}<Panel><EmptyState level={3} title={busy ? "Preparing your review…" : "No eligible card right now"}>
          <p>Learning cards return at their scheduled time; limits reset at midnight.</p>
          {#snippet actions()}
            <Button onclick={() => void startReview()} disabled={busy}>Refresh review</Button>
          {/snippet}
        </EmptyState></Panel>{/if}
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
      <div class="toolbar">
        <span class="picker" title="APKG keeps rika! scheduling and Anki FSRS memory; CSV/TSV follow one note type's field order, then tags. Uploads up to 11 MiB."
          ><span>Format</span><Select label="Format" options={formatOptions} bind:value={format} /></span>
        {#if format !== "apkg"}<span class="picker" title={`Field order: ${selectedType?.fields.join(" → ") ?? ""}`}
          ><span>Card format</span><Select label="CSV / TSV card format" kind="list" options={typeOptions} value={String(typeId)}
          onchange={(next) => (typeId = Number(next))} /></span>{/if}
      </div>
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
            disabled={busy || (format !== "apkg" && (!deckId || !typeId))}
          /></label
        ><button onclick={exportFile} disabled={busy}
          >Export selected deck + subdecks</button
        >
      </div>
    {:else if view === "generate"}
      <h2>Generate draft cards from selected notes</h2>
      <p class="hint">AI drafts are never saved until you fact-check and accept each.</p>
      <label
        >Source notes<textarea bind:value={sourceText} rows="8"
        ></textarea></label
      >
      <AIReview
        purpose="flashcards"
        text={sourceText}
        {demo}
        active={pane.active}
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
  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    flex-wrap: wrap;
  }
  /* Q16 A: title, the deck chip beside it, then the one New card and Review due. */
  header {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  header .demo {
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
    text-transform: uppercase;
  }
  header .primary-actions {
    margin: 0 0 0 auto;
  }
  .deck-chip :global(.select-trigger) {
    min-width: 0;
    max-width: 300px;
    padding: 0 8px 0 12px;
    border-radius: 999px;
    font-weight: 500;
  }
  .primary-actions kbd {
    margin-left: 4px;
    padding: 0 4px;
    border: 1px solid var(--hairline);
    border-radius: 4px;
    color: var(--muted);
    font: 500 10px/14px var(--font-mono);
  }
  .primary-actions,
  .editor-actions {
    justify-content: flex-start;
  }
  .grow { flex: 1; }
  .picker {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
  }
  .media-list { margin: 0; padding: 8px 12px; list-style: none; font: 12px var(--font-mono); }
  .media-button { padding: 0 10px; height: var(--control-h); }
  .look {
    display: inline-flex;
    gap: 2px;
    padding: 3px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
  }
  .look button {
    padding: 4px 12px;
    border: 0;
    border-radius: 999px;
    background: none;
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .look button[aria-checked="true"] {
    background: var(--wash);
    color: var(--ink);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--violet) 35%, var(--border));
  }
  .review-options .count { color: var(--muted); font: 12px var(--font-mono); }
  .editor-actions { padding-top: 0.8rem; border-top: 1px solid var(--border); }
  header h1 {
    margin: 0;
  }
  p,
  .hint,
  small {
    color: var(--muted);
  }
  nav,
  .toolbar {
    margin: 1rem 0;
  }
  nav {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    flex-wrap: wrap;
  }
  button,
  input,
  textarea {
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
  /* The app's pressed-toggle look (styles.css): solid accent with its contrasting text.
     The doubled class outranks a theme's own control fill (lib/themes/liquid-glass). */
  :global(:root body) nav :global(button.br-btn.br-btn[aria-current="page"]) {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--on-accent);
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
  label textarea {
    display: block;
    width: 100%;
    box-sizing: border-box;
    margin-top: 0.3rem;
  }
  input,
  textarea {
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
  .toolbar input {
    flex: 1;
    min-width: 180px;
  }
  article {
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 0.7rem;
    padding: 1rem;
    margin: 1rem 0;
  }
  .browser-search { margin-bottom: 0.5rem; }
  .filters {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin-bottom: 0.8rem;
  }
  .filters button {
    padding: 3px 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: none;
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .filters button[aria-pressed="true"] {
    border-color: var(--violet);
    background: color-mix(in srgb, var(--violet) 16%, transparent);
    color: var(--ink);
  }
  .filters span {
    margin-left: auto;
    color: var(--muted);
    font-size: 0.85rem;
  }
  /* Q66 inline add row: one line of front, back and Add above the list. */
  .add-row {
    display: grid;
    grid-template-columns: auto auto minmax(0, 1fr) minmax(0, 1fr) auto;
    align-items: center;
    gap: 8px;
    margin-bottom: 0.8rem;
    padding: 8px 10px 6px;
    border: 1px dashed color-mix(in srgb, var(--violet) 45%, var(--hairline));
    border-radius: 14px;
    background: color-mix(in srgb, var(--violet) 6%, transparent);
  }
  .add-row:focus-within {
    border-style: solid;
    border-color: var(--violet);
  }
  .add-row .plus {
    color: var(--violet);
    font-size: 15px;
  }
  .add-row .add-deck {
    max-width: 180px;
    min-height: 0;
    margin: 0;
    padding: 4px 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--muted);
    background: color-mix(in srgb, var(--ink) 5%, transparent);
    font: 500 11.5px/1.3 var(--font-ui);
  }
  .add-deck-pick :global(.select-trigger) {
    min-width: 0;
    max-width: 180px;
    border-radius: 999px;
  }
  span.add-deck {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .add-row input {
    min-width: 0;
    padding: 6px 10px;
    border-radius: 8px;
    font-size: 13px;
  }
  /* Beats the frame's glass field rule (styles.css) so the focused face lights up. */
  :global(:root body) .add-row input:focus-visible {
    border-color: var(--violet);
    outline: none;
  }
  .add-note {
    grid-column: 3 / -1;
    max-width: none;
    margin: 0;
    color: var(--subtle);
    font: 10.5px var(--font-mono);
  }
  .add-note.problem { color: var(--error); }
  .add-note.warning { color: var(--warning); }
  .add-note.added { color: var(--success); }
  @media (max-width: 760px) {
    .add-row { grid-template-columns: auto minmax(0, 1fr) auto; }
    .add-row .add-deck, .add-row .add-deck-pick { grid-column: 2 / -1; justify-self: start; }
    .add-row input:first-of-type, .add-note { grid-column: 2 / -1; }
    .add-row input:last-of-type { grid-column: 2; }
  }
  /* Q42 split browser: the list stays put beside the selected card. */
  .split-browser {
    display: grid;
    grid-template-columns: minmax(220px, 0.9fr) minmax(0, 1.3fr);
    min-height: 420px;
    border: 1px solid var(--hairline);
    border-radius: 14px;
    background: color-mix(in srgb, var(--solid-panel) 70%, transparent);
  }
  .card-list {
    display: grid;
    grid-template-rows: 1fr auto;
    min-width: 0;
    padding: 10px 8px 10px 10px;
    border-right: 1px solid var(--hairline);
  }
  .card-list ul {
    max-height: 560px;
    margin: 0;
    padding: 0;
    overflow: auto;
    list-style: none;
  }
  .card-list li button {
    display: grid;
    grid-template-columns: 7px minmax(0, 1fr);
    column-gap: 8px;
    align-items: center;
    width: 100%;
    padding: 6px 8px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    text-align: left;
  }
  .card-list li button:hover {
    background: color-mix(in srgb, var(--ink) 5%, transparent);
  }
  .card-list li button.on {
    background: color-mix(in srgb, var(--violet) 14%, transparent);
  }
  .card-list .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
  }
  .card-list .front {
    overflow: hidden;
    font-size: 13px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .card-list .meta {
    grid-column: 2;
    color: var(--subtle);
    font: 10px var(--font-mono);
  }
  .pager {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    padding-top: 8px;
    color: var(--muted);
    font: 10.5px var(--font-mono);
  }
  .pager button {
    padding: 3px 10px;
    font-size: 0.8rem;
  }
  @media (max-width: 760px) {
    .split-browser { grid-template-columns: 1fr; }
    .card-list { border-right: 0; border-bottom: 1px solid var(--hairline); }
    .card-list ul { max-height: 260px; }
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
    display: grid;
    gap: 0.9rem;
    max-width: 640px;
    margin: 1.5rem auto;
    overflow: hidden;
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
  textarea:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  @media (max-width: 600px) {
    .flashcards { padding: 1rem; }
  }
</style>
