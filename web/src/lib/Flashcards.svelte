<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "./api";
  import AIReview from "./AIReview.svelte";
  import SectionGraph, { type SectionNode } from "./SectionGraph.svelte";
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
      days: { day: string; reviews: number; again: number; seconds: number }[];
    };
  };
  let overview = $state<Overview | null>(null),
    notes = $state<Note[]>([]),
    total = $state(0);
  let deckId = $state(0),
    typeId = $state(0),
    view = $state("schema"),
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
  let graphFocus = $state<string | null>(null);
  let graphRevision = $state(0);
  let noteRequest = 0;
  function rootSchema() {
    noteRequest += 1;
    graphFocus = null;
    editing = null;
    deckId = 0;
    search = "";
    offset = 0;
    error = "";
    view = "schema";
    graphRevision += 1;
  }
  function deckSchema(id: number) {
    noteRequest += 1;
    deckId = id;
    graphFocus = `deck:${id}`;
    editing = null;
    error = "";
    view = "schema";
  }
  async function selectGraphNode(node: SectionNode) {
    if (!pane.active || typeof node.ref !== "number") return;
    if (node.entity === "deck") {
      deckSchema(node.ref);
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
    const id = view === "schema"
      ? (graphFocus ? Number(graphFocus.slice(5)) : 0)
      : view === "deck-editor" ? deckEditing?.id || 0 : deckId;
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
      onclick: () => deckSchema(deck.id),
    }));
    if (view === "editor") crumbs.push({label: editing ? `Card ${editing.id}` : "New card"});
    if (view === "review" && review?.note_id) crumbs.push({label: `Note ${review.note_id}`});
    pane.setTrail("flashcards", crumbs, rootSchema);
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
  function reviewDue() {
    mode = "regular";
    search = "";
    void startReview();
  }
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
      if (!deckId && view !== "schema") deckId = overview.decks[0]?.id || 0;
      graphRevision += 1;
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
    } catch (e) {
      failure(e);
    }
  }
  async function show(next: string) {
    noteRequest += 1;
    if (next === "schema") { rootSchema(); return; }
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
  function editDeck(deck: Deck | null = null) {
    if (!pane.active) return;
    noteRequest += 1;
    deckEditing = deck;
    deckName = deck?.name || "";
    cfg = structuredClone(deck?.settings || defaultSettings);
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
        } else deckSchema(deck.id);
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
    return () => window.removeEventListener("kitty-study-text", selectedText);
  });
  function keydown(event: KeyboardEvent) {
    if (
      !pane.active ||
      view !== "review" ||
      busy ||
      event.ctrlKey ||
      event.altKey ||
      event.metaKey ||
      event.repeat ||
      event.defaultPrevented
    )
      return;
    const target = event.target as HTMLElement;
    if (
      target.closest("input,textarea,select,button,a,[role=button]") ||
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
    <div>
      <h1>Flashcards</h1>
      {#if demo}<p>Demo</p>{/if}
    </div>
    {#if !editorView}<div class="toolbar primary-actions">
      <Button size="sm" onclick={() => editDeck()} disabled={!overview || busy}>New deck</Button>
      <Button size="sm" onclick={() => newNote()} disabled={!overview || busy || !overview.decks.some(deck => !deck.archived)}>New card</Button>
      <Button intent="primary" onclick={reviewDue} disabled={!overview || busy || !overview.stats.total}>Review due</Button>
    </div>{/if}
  </header>
  {#if !editorView}
    <nav aria-label="Flashcard views">
      {#each [["schema", "Map"], ["decks", "Decks"], ["browser", "Cards"]] as item}
        <Button size="sm" variant="ghost" aria-current={view === item[0] ? "page" : undefined} onclick={() => void show(item[0])}>{item[1]}</Button>
      {/each}
      <details class="tools-menu">
        <summary>Study tools &amp; more</summary>
        <div class="toolbar">
          <Button size="sm" variant="ghost" onclick={() => void show("review")}>Practice &amp; review options</Button>
          <Button size="sm" variant="ghost" onclick={() => void show("history")}>Review history</Button>
          <Button size="sm" variant="ghost" onclick={() => void show("transfer")}>Import / export</Button>
          <Button size="sm" variant="ghost" onclick={() => void show("generate")}>AI drafts</Button>
        </div>
      </details>
    </nav>
  {/if}
  {#if error}<p class="error" role="alert">{error}<button class="icon-button" aria-label="Dismiss flashcard error" onclick={() => (error = "")}>×</button></p>{/if}
  {#if message}<p role="status">{message}<button class="icon-button" aria-label="Dismiss flashcard message" onclick={() => (message = "")}>×</button></p>{/if}
  {#if overview}
    {#if !editorView}<div class="scope-heading">
      <div><small>Selected study scope</small><h2>{scopeLabel}</h2></div>
      <p>{selectedDeck ? `${selectedDeck.due} due · ${selectedDeck.new} new` : `${overview.stats.due} due · ${overview.stats.new} new`}
        <span>{selectedDeck ? "This deck and its subdecks" : "Your entire collection"}</span>
      </p>
    </div>{/if}
    {#if !["deck-editor", "type-editor"].includes(view)}<div class="scope">
      <label
        >{view === "editor" ? "Save card in deck" : "Change deck scope"} <select
          bind:value={deckId}
          onchange={(event) => {
            deckId = Number(event.currentTarget.value);
            offset = 0;
            if (view === "review") resetReview();
            if (view === "schema") {
              if (deckId) deckSchema(deckId);
              else rootSchema();
            }
            if (view === "browser") void browse();
          }}
          >{#if view !== "editor"}<option value={0}>All decks</option>{/if}{#each overview.decks as deck}<option value={deck.id}
              >{deck.name}{deck.archived ? " (archived)" : ""}</option
            >{/each}</select
        ></label
      >
    </div>{/if}
    {#if view === "schema"}
      {#if !overview.stats.total}
        <Panel>
          <div class="empty-state">
            <small>Start with one question</small>
            <h2>Your first card is a question and an answer.</h2>
            <p>Keep cards in a deck, write a prompt on the front, then reveal the back when you study. Nothing is saved until you choose Save card.</p>
            <div class="toolbar">
              {#if overview.decks.some(deck => !deck.archived)}
                <Button intent="primary" onclick={() => newNote()}>Create your first card</Button>
                <Button variant="ghost" onclick={() => editDeck()}>Create a different deck</Button>
              {:else}
                <Button intent="primary" onclick={() => editDeck()}>Create your first deck</Button>
              {/if}
            </div>
          </div>
        </Panel>
      {/if}
      {#if graphFocus}<Button size="sm" variant="ghost" onclick={rootSchema}>All decks and notes</Button>{/if}
      <SectionGraph section="flashcards" revision={graphRevision} focus={graphFocus} onselect={selectGraphNode} ondelete={deleteGraphNote} />
      <p class="hint">Select a deck to focus its tree, or open a card node to edit it. Use Cards for a searchable list.</p>
    {:else if view === "decks"}
      <div class="metrics">
        <span><strong>{overview.stats.total}</strong> cards</span><span
          ><strong>{overview.stats.due}</strong> due</span
        ><span><strong>{overview.stats.new}</strong> available new</span><span
          ><strong>{overview.stats.streak}</strong> day streak</span
        ><span
          >{overview.stats.suspended} suspended · {overview.stats.leeches} leeches</span
        >
      </div>
      {#if practiceResume}<Button size="sm" variant="ghost" onclick={resumePractice} disabled={busy}>Resume last practice queue</Button>{/if}
      {#each overview.decks as deck}<article class="deck" class:selected-deck={deck.id === deckId}>
          <div>
            <h2><Button variant="ghost" onclick={() => { deckId = deck.id; }}>{deck.name}</Button>{#if deck.id === deckId}<small class="selected-label">Selected</small>{/if}</h2>
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
                reviewDue();
              }}
              disabled={busy || !!deck.archived}>Review due</button>
            <Button size="sm" onclick={() => { deckId = deck.id; newNote(); }} disabled={busy || !!deck.archived}>New card</Button>
            <details><summary>Deck options</summary>
              <div class="toolbar"><button onclick={() => deckSchema(deck.id)}>Open map</button>
                <button onclick={() => editDeck(deck)}>Settings</button><button
              onclick={() => archiveDeck(deck)}
              disabled={busy}>{deck.archived ? "Restore" : "Archive"}</button></div>
            </details>
          </div>
        </article>{/each}
      <details><summary>Note types &amp; templates</summary>
      <div class="toolbar">
        <Button size="sm" onclick={() => editType()}>New note type</Button>
        {#each overview.types as typ}<button onclick={() => editType(typ)}
            >{typ.name}</button
          >{/each}
      </div>
      </details>
      <details><summary>Review activity · last 90 active days</summary>
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
      </details>
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
          placeholder="Search cards or tag:name"
        /><Button type="submit" size="sm">Search</Button>
      </form>
      <p>{total} notes in {scopeLabel}{search ? ` matching “${search}”` : ""}.</p>
      <details><summary>Advanced search &amp; scheduling</summary><p>Search with is:due/new/review/learning/suspended/leech/archived or deck:name. Editing retains schedules of unchanged card ordinals; deleted templates/clozes remove their cards.</p></details>
      {#if !total}<Panel><div class="empty-state">
        <h2>{search ? "No matching cards" : "No cards in this scope yet"}</h2>
        <p>{search ? "Try a broader search, or create a card in the selected deck." : "Write a question on the front and its answer on the back to start studying."}</p>
        <Button intent="primary" onclick={() => newNote()} disabled={!overview.decks.some(deck => !deck.archived)}>New card</Button>
        {#if search}<Button variant="ghost" onclick={() => { search = ""; offset = 0; void browse(); }}>Clear search</Button>{/if}
      </div></Panel>{/if}
      {#each notes as note}<article class="note" oncontextmenu={(event) => {
        if ((event.target as HTMLElement).closest('input,textarea,select,[contenteditable="true"]') || window.getSelection()?.toString()) return;
        event.preventDefault(); if (!busy) void noteAction(note, "delete");
      }}>
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
          <details><summary>Card schedules &amp; actions ({note.cards.length})</summary>
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
          </details>
        </article>{/each}
      <div class="toolbar">
        <button
          disabled={offset === 0}
          onclick={() => {
            offset = Math.max(0, offset - 50);
            void browse();
          }}>Previous</button
        ><span>{total ? offset + 1 : 0}–{Math.min(offset + 50, total)} / {total}</span
        ><button
          disabled={offset + 50 >= total}
          onclick={() => {
            offset += 50;
            void browse();
          }}>Next</button
        >
      </div>
    {:else if view === "editor"}
      <h2>{editing ? "Edit card" : "New card"}</h2>
      <p>{editing ? "Changes stay local until you save them." : "Write one question and one answer. You can add cloze, images or other card formats under the optional controls."}</p>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveNote();
        }}
      >
        <details>
          <summary>Card format · {selectedType?.name || "Choose a format"}</summary>
          <label>Card format<select bind:value={typeId} onchange={(event) => { typeId = Number(event.currentTarget.value); switchType(); }} disabled={busy}>
            {#each overview.types as typ}<option value={typ.id}>{typ.name}</option>{/each}
          </select></label>
          <p>Basic creates a front/back card. Cloze hides marked text; reversed and custom templates may create several cards from one note. Changing format can add or remove cards when saved; switching back before saving retains your draft fields.</p>
        </details>
        {#each selectedType?.fields || [] as field}<label
            >{field === "Front" ? "Front · question" : field === "Back" ? "Back · answer" : field}<textarea
              bind:value={editorFields[field]}
              rows="5"
              placeholder={field === "Front" ? "Ask one clear question or give a retrieval cue…" : field === "Back" ? "Write the answer you will reveal after trying…" : `Enter ${field}…`}
              required={field === selectedType?.fields[0]}
            ></textarea></label
          >{/each}
        <details>
          <summary>Tags, images &amp; formatting</summary>
          <label>Tags (space separated)<input bind:value={editorTags} /></label>
          <p>Cloze syntax: {"{{c1::answer::hint}}"}. Multiple indices create separate cards. Safe HTML, **bold**, `code`, local images and [sound:filename] are supported. Remote images/scripts are never loaded.</p>
          <label class="upload">Attach image / audio<input
            type="file"
            accept=".png,.jpg,.jpeg,.gif,.webp,.mp3,.ogg,.wav"
            onchange={upload}
            disabled={busy}
          /></label>
          <p>Reference an uploaded image with &lt;img src="filename.png"&gt; or audio with [sound:filename]. Uploading media does not save this card.</p>
        </details>
        <div class="toolbar editor-actions">
          <Button type="submit" intent="primary" disabled={busy || !deckId || !selectedType}>{busy ? "Saving…" : editing ? "Save changes" : "Save card"}</Button>
          <Button type="button" variant="ghost" onclick={() => void show("browser")} disabled={busy}>Cancel — do not save</Button>
        </div>
      </form>
      {#if overview.media.length}<details>
          <summary>Local media ({overview.media.length})</summary
          >{#each overview.media as media}<p>
              {media.name} · {media.mime} · {Math.round(media.size / 1024)} KiB
            </p>{/each}
        </details>{/if}
      {@render referencePanel()}
    {:else if view === "deck-editor" && cfg}
      <h2>{deckEditing ? "Deck settings" : "New deck"}</h2>
      <p>{deckEditing ? "Adjust future study sessions for this deck." : "Name your deck, then add its first question and answer. Sensible study defaults are ready to use."}</p>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveDeck();
        }}
      >
        <label
          >Deck name<input
            bind:value={deckName}
            required
          /></label
        >
        <p class="hint">Use Parent::Child to create a subdeck.</p>
        <details open={!!deckEditing}><summary>Study limits &amp; advanced scheduling</summary>
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
        </details>
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
        <div class="toolbar"><Button type="submit" intent="primary" disabled={busy}>Save note type</Button><Button type="button" variant="ghost" onclick={() => void show("decks")} disabled={busy}>Cancel</Button></div>
      </form>
    {:else if view === "review"}
      <h2>{mode === "regular" ? "Review due cards" : "Practice cards"}</h2>
      <details class="review-options"><summary>Study options · {mode === "regular" ? "Scheduled review" : "Practice — no rescheduling"}{search ? " · filtered" : ""}</summary>
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
        ><Button onclick={() => void startReview()} disabled={busy}>Start / refresh</Button>
      </div>
      <label class="check"><input type="checkbox" bind:checked={typedEnabled} />Type your answer before revealing (self-graded)</label>
      {#if practiceResume}<Button size="sm" variant="ghost" onclick={resumePractice} disabled={busy}>Resume last practice queue</Button>{/if}
      </details>
      <p>
        {remaining} eligible cards · {mode === "regular"
          ? "Scheduled study · ratings update future due dates"
          : "Practice only: regular due dates unchanged"}
      </p>
      <div class="toolbar"><p class="hint">Try to recall the answer, flip the card, then rate how well you remembered.</p>
        <Button size="sm" variant="ghost" onclick={undo} disabled={busy}>Undo last review</Button></div>
      {#if review}<article class="review-card" aria-live="polite">
          <small>{review.state} · {review.mode}</small>
          <div class="flip-stage"><div class="flip-card" class:flipped={answerFace}>
            <div class="card-face question-face" aria-hidden={answerFace} inert={answerFace}>
              <small>Question</small><div class="card-content">{@html review.front}</div>
            </div>
            <div class="card-face answer-face" aria-hidden={!answerFace} inert={!answerFace}>
              <small>Answer</small>{#if revealed}<div class="card-content">{@html back}</div>{/if}
            </div>
          </div></div>
          {#if typedEnabled}<label
              >Your answer<input
                bind:value={typedAnswer}
                readonly={revealed}
                autocomplete="off"
              /></label
            >{/if}{#if revealed}
            <Button size="sm" variant="ghost" onclick={() => (answerFace = !answerFace)}>{answerFace ? "Show question" : "Show answer"}</Button>
            {@render referencePanel()}
            <p class="rating-guide">How well did you remember? Again = missed · Hard = struggled · Good = recalled · Easy = effortless.</p>
            <div class="grades">
              {#each labels as label, index}<button
                  onclick={() => grade(index + 1)}
                  disabled={busy}
                  >{index + 1} · {label}{#if intervals[index]}<small
                      >{interval(intervals[index].seconds)}</small
                    >{/if}</button
                >{/each}
            </div>{:else}<Button intent="primary" onclick={reveal} disabled={busy}>Flip to answer · Space</Button>{/if}
        </article>{:else}<Panel><div class="empty-state">
          <h3>{busy ? "Preparing your review…" : "No eligible card right now"}</h3>
          <p>Learning cards appear at their scheduled time. Daily limits reset at local midnight. Add a card, refresh later, or choose a practice mode under Study options.</p>
          <div class="toolbar"><Button onclick={() => void startReview()} disabled={busy}>Refresh review</Button>
            <Button variant="ghost" onclick={() => newNote()} disabled={busy || !overview.decks.some(deck => !deck.archived)}>New card</Button></div>
        </div></Panel>{/if}
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
      {#if format !== "apkg"}
        <label>CSV / TSV card format<select bind:value={typeId}>
          {#each overview.types as typ}<option value={typ.id}>{typ.name}</option>{/each}
        </select></label>
        <p class="hint">Import field order: {selectedType?.fields.join(" → ")}. Choose a destination deck above.</p>
      {/if}
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
  .primary-actions,
  .editor-actions,
  .empty-state .toolbar,
  .tools-menu .toolbar {
    justify-content: flex-start;
  }
  .scope-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
    padding: 1.1rem 1.25rem;
    border: 1px solid var(--border);
    border-radius: 0.7rem;
    background: var(--surface-raised);
  }
  .scope-heading h2 { margin: 0.3rem 0 0; font-size: 1.25rem; overflow-wrap: anywhere; }
  .scope-heading p { margin: 0; }
  .scope-heading span { display: block; margin-top: 0.3rem; font-size: 0.85rem; }
  .empty-state { padding: 1rem; }
  .empty-state h2,
  .empty-state h3 { margin: 0.6rem 0; }
  .empty-state p { max-width: 65ch; line-height: 1.6; }
  details { margin: 0.85rem 0; }
  summary { cursor: pointer; color: var(--ink); font-weight: 600; line-height: 1.5; }
  details[open] > summary { margin-bottom: 0.7rem; }
  summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
  .tools-menu { margin: 0; padding: 0.4rem 0.6rem; }
  .tools-menu .toolbar { margin-bottom: 0.2rem; }
  .selected-deck { border-color: var(--accent); }
  .selected-label { display: inline-block; margin-left: 0.5rem; font-size: 0.75rem; color: var(--accent); }
  .rating-guide { font-size: 0.9rem; line-height: 1.6; }
  .review-options { padding: 0.8rem 1rem; border: 1px solid var(--border); border-radius: 0.6rem; }
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
  nav :global(button[aria-current="page"]) {
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
  .flip-stage { perspective: 1400px; margin: 1rem 0; }
  .flip-card { display: grid; min-height: 240px; transform-style: preserve-3d; transition: transform .48s cubic-bezier(.2,.65,.25,1); }
  .flip-card.flipped { transform: rotateY(180deg); }
  .card-face { grid-area: 1 / 1; padding: 1.5rem; border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border)); border-radius: 12px; background: var(--surface); backface-visibility: hidden; -webkit-backface-visibility: hidden; align-content: center; }
  .card-face > small { color: var(--muted); font-size: .7rem; letter-spacing: .1em; text-transform: uppercase; }
  .answer-face { transform: rotateY(180deg); }
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
  @media (max-width: 600px) {
    .flashcards { padding: 1rem; }
    .grades { grid-template-columns: repeat(2, 1fr); }
    .card-face { padding: 1rem; }
    .card-content { font-size: 1.1rem; }
  }
  @media (prefers-reduced-motion: reduce) {
    .flip-card { transition: none; transform: none; }
    .flip-card.flipped, .answer-face { transform: none; }
    .card-face[aria-hidden="true"] { visibility: hidden; }
  }
</style>
