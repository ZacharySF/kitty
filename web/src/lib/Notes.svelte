<script lang="ts">
  import { onMount } from "svelte";
  import { api, type TaskRecord } from "./api";
  import AIReview from "./AIReview.svelte";
  import {
    bounds,
    CANVAS_INK,
    CANVAS_PAPER,
    clone,
    downloadBlob,
    drawingInput,
    editable,
    emptyDocument,
    expandGroups,
    exportSVG,
    finishGesture,
    finishStroke,
    fit,
    hit,
    hitTop,
    lassoSelection,
    removeElements,
    renderCanvas,
    resizeElement,
    svgPNG,
    uid,
    world,
    zoomAt,
    type Bounds,
    type CanvasDocument,
    type CanvasElement,
    type ElementKind,
    type Point,
    type Viewport,
  } from "./notesCanvas";
  let {
    revision = 0,
    demo = false,
    onflashcards,
  }: {
    revision?: number;
    demo?: boolean;
    onflashcards?: (text: string, pageId: number) => void;
  } = $props();
  type Notebook = {
    id: number;
    title: string;
    subject: string;
    project: string;
    revision: number;
  };
  type Page = {
    id: number;
    notebook_id: number;
    title: string;
    revision: number;
    viewport_revision: number;
    document: CanvasDocument;
    viewport: Viewport;
  };
  type PageSummary = {
    id: number;
    notebook_id: number;
    title: string;
    revision: number;
  };
  type Preferences = {
    allow_ai: boolean;
    retention_days: number;
    revision: number;
  };
  type Tool = ElementKind | "select" | "lasso" | "pan" | "eraser";
  type Gesture = {
    mode:
      | "draw"
      | "move"
      | "resize"
      | "rect-select"
      | "lasso"
      | "erase"
      | "pan"
      | "pinch";
    pointer: number;
    start: Point;
    last: Point;
    before: CanvasDocument;
    view: Viewport;
    selected: string[];
    box?: Bounds;
    id?: string;
    distance?: number;
    centroid?: Point;
  };
  let notebooks = $state<Notebook[]>([]),
    pages = $state<PageSummary[]>([]),
    prefs = $state<Preferences>({
      allow_ai: false,
      retention_days: 30,
      revision: 1,
    });
  let notebookId = $state(0),
    pageId = $state(0),
    pageRevision = $state(1),
    viewRevision = $state(1),
    title = $state("");
  let doc = $state<CanvasDocument>(emptyDocument()),
    view = $state<Viewport>({ x: 0, y: 0, zoom: 1 });
  let tool = $state<Tool>("pen"),
    color = $state(CANVAS_INK),
    fillColor = $state("#fff2a8"),
    lineWidth = $state(3),
    fontSize = $state(20),
    layerId = $state("default");
  let selected = $state<string[]>([]),
    outline = $state(false),
    overlay = $state<Point[]>([]),
    search = $state("");
  let loading = $state(false),
    error = $state(""),
    saveStatus = $state("Saved locally"),
    viewError = $state(""),
    busy = $state(false),
    dirty = $state(false),
    viewDirty = $state(false),
    blocked = $state(false);
  let undoStack = $state<CanvasDocument[]>([]),
    redoStack = $state<CanvasDocument[]>([]),
    canvas = $state<HTMLCanvasElement>(),
    stage = $state<HTMLDivElement>(),
    width = $state(900),
    height = $state(600),
    imageEpoch = $state(0);
  let summary = $state(""),
    summarySelection = $state("");
  let newNotebook = $state(""),
    subject = $state(""),
    project = $state(""),
    editNotebook = $state(false),
    newLayer = $state(""),
    restoreNotebook = $state(true),
    operationStatus = $state("");
  let gesture: Gesture | null = null,
    space = false,
    contentGeneration = 0,
    viewGeneration = 0,
    saveTimer: ReturnType<typeof setTimeout> | undefined,
    viewTimer: ReturnType<typeof setTimeout> | undefined,
    searchTimer: ReturnType<typeof setTimeout> | undefined;
  let saving: Promise<void> | null = null,
    viewSaving: Promise<void> | null = null,
    ink = CANVAS_INK,
    paper = CANVAS_PAPER;
  const images = new Map<string, HTMLImageElement>(),
    pointers = new Map<number, Point>();
  let penOnly = $state(false),
    penActive: number | null = null,
    penGuardUntil = 0;
  function savePenMode() {
    try {
      localStorage.setItem("kitty.notes.pen-only", String(penOnly));
    } catch {
      /* The current writing mode still works. */
    }
  }
  const activeNotebook = $derived(notebooks.find((n) => n.id === notebookId));
  const selectedElements = $derived(
    doc.elements.filter((e) => selected.includes(e.id)),
  );
  const selectedText = $derived(
    selectedElements
      .filter((e) => e.kind === "text" || e.kind === "sticky")
      .map((e) => e.text ?? "")
      .filter(Boolean)
      .join("\n\n"),
  );
  const textElements = $derived(
    doc.elements.filter((e) => e.kind === "text" || e.kind === "sticky"),
  );
  const tools: { id: Tool; label: string; key: string }[] = [
    { id: "select", label: "Select", key: "V" },
    { id: "lasso", label: "Lasso", key: "L" },
    { id: "pan", label: "Pan", key: "H" },
    { id: "pen", label: "Pen", key: "P" },
    { id: "highlighter", label: "Highlight", key: "B" },
    { id: "eraser", label: "Eraser", key: "E" },
    { id: "text", label: "Text", key: "T" },
    { id: "sticky", label: "Sticky", key: "N" },
    { id: "rect", label: "Rectangle", key: "R" },
    { id: "ellipse", label: "Ellipse", key: "O" },
    { id: "line", label: "Line", key: "I" },
    { id: "arrow", label: "Arrow", key: "A" },
    { id: "connector", label: "Connect", key: "C" },
  ];

  async function loadIndex() {
    try {
      const data = await api<{
        notebooks: Notebook[];
        pages: PageSummary[];
        preferences: Preferences;
      }>(`/notes${search ? "?search=" + encodeURIComponent(search) : ""}`);
      notebooks = data.notebooks;
      pages = data.pages;
      prefs = data.preferences;
      if (!notebookId && notebooks.length) notebookId = notebooks[0].id;
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  function recoveryKey(id = pageId) {
    return `kitty.notes.pending.${id}`;
  }
  function keepRecovery() {
    if (!pageId) return;
    try {
      sessionStorage.setItem(
        recoveryKey(),
        JSON.stringify({ revision: pageRevision, title, document: doc }),
      );
    } catch {
      error =
        "Browser recovery storage is full. Save now or export before closing.";
    }
  }
  function remember(before: CanvasDocument) {
    undoStack = [...undoStack.slice(-29), clone(before)];
    redoStack = [];
  }
  function scheduleContent() {
    dirty = true;
    blocked = false;
    contentGeneration++;
    saveStatus = "Unsaved changes";
    keepRecovery();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => void saveContent(), 650);
  }
  function resumeAutosave() {
    if (dirty && !blocked) {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => void saveContent(), 650);
    }
    if (viewDirty && !viewError) {
      clearTimeout(viewTimer);
      viewTimer = setTimeout(() => void saveView(), 700);
    }
  }
  function commit(next: CanvasDocument, before = clone(doc)) {
    doc = next;
    remember(before);
    scheduleContent();
  }
  async function saveContent(): Promise<void> {
    if (saving) {
      await saving;
      if (dirty && !blocked) return saveContent();
      return;
    }
    if (!dirty || !pageId || gesture || blocked) return;
    const id = pageId,
      generation = contentGeneration,
      document = clone(doc),
      pageTitle = title,
      rev = pageRevision;
    saveStatus = "Saving locally…";
    saving = (async () => {
      try {
        const result = await api<{ revision: number }>(`/notes/pages/${id}`, {
          revision: rev,
          title: pageTitle,
          document,
        });
        if (pageId !== id) return;
        pageRevision = result.revision;
        if (generation === contentGeneration) {
          dirty = false;
          saveStatus = "Saved locally";
          try {
            sessionStorage.removeItem(recoveryKey(id));
          } catch {
            /* Recovery is best effort. */
          }
        } else {
          keepRecovery();
          saveStatus = "Unsaved changes";
        }
      } catch (cause) {
        if (pageId === id) {
          error = (cause as Error).message;
          blocked = true;
          saveStatus = "Save failed — local edits preserved; export or retry";
        }
      } finally {
        saving = null;
      }
    })();
    await saving;
    if (dirty && !blocked) await saveContent();
  }
  function scheduleView() {
    viewDirty = true;
    viewGeneration++;
    clearTimeout(viewTimer);
    viewTimer = setTimeout(() => void saveView(), 700);
  }
  async function saveView(): Promise<void> {
    if (viewSaving) {
      await viewSaving;
      if (viewDirty && !viewError) return saveView();
      return;
    }
    if (!viewDirty || !pageId || gesture || viewError) return;
    const id = pageId,
      generation = viewGeneration,
      viewport = clone(view),
      rev = viewRevision;
    viewSaving = (async () => {
      try {
        const result = await api<{ viewport_revision: number }>(
          `/notes/pages/${id}/viewport`,
          { revision: rev, viewport },
        );
        if (pageId === id) {
          viewRevision = result.viewport_revision;
          if (generation === viewGeneration) viewDirty = false;
        }
      } catch (cause) {
        viewError = (cause as Error).message;
      } finally {
        viewSaving = null;
      }
    })();
    await viewSaving;
    if (viewDirty && !viewError) await saveView();
  }
  async function openPage(id: number, force = false) {
    if (loading) return;
    if (gesture) cancelGesture();
    await saveContent();
    await saveView();
    if (
      dirty &&
      !force &&
      !confirm(
        "These changes have not saved. Export first if needed. Discard local edits and open another page?",
      )
    )
      return;
    loading = true;
    error = "";
    try {
      const page = await api<Page>(`/notes/pages/${id}`);
      pageId = page.id;
      notebookId = page.notebook_id;
      title = page.title;
      pageRevision = page.revision;
      viewRevision = page.viewport_revision;
      doc = page.document;
      view = page.viewport;
      layerId =
        doc.layers.find((l) => l.visible && !l.locked)?.id ?? doc.layers[0].id;
      selected = [];
      undoStack = [];
      redoStack = [];
      dirty = false;
      viewDirty = false;
      blocked = false;
      viewError = "";
      saveStatus = "Saved locally";
      summary = "";
      summarySelection = "";
      try {
        const raw = sessionStorage.getItem(recoveryKey(id));
        if (raw) {
          const pending = JSON.parse(raw);
          if (confirm("Recover unsaved local edits for this page?")) {
            doc = pending.document;
            title = pending.title;
            dirty = true;
            contentGeneration++;
            if (pending.revision !== pageRevision) {
              blocked = true;
              error =
                "Recovered edits are based on an older version. Export them before reloading; they will not overwrite the newer page.";
              saveStatus = "Recovered version conflict";
            } else {
              scheduleContent();
            }
          } else sessionStorage.removeItem(recoveryKey(id));
        }
      } catch {
        error =
          "Could not restore browser recovery data; the server copy is loaded.";
      }
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      loading = false;
    }
  }
  async function createNotebook() {
    busy = true;
    error = "";
    try {
      const n = await api<Notebook>("/notes/notebooks", {
        title: newNotebook,
        subject,
        project,
      });
      notebookId = n.id;
      newNotebook = "";
      await loadIndex();
      await createPage();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  async function updateNotebook() {
    if (!activeNotebook) return;
    busy = true;
    try {
      await api(`/notes/notebooks/${notebookId}`, {
        revision: activeNotebook.revision,
        title: newNotebook,
        subject,
        project,
      });
      editNotebook = false;
      newNotebook = "";
      await loadIndex();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  async function createPage() {
    if (!notebookId) return;
    await saveContent();
    if (dirty) return;
    try {
      const page = await api<Page>("/notes/pages", {
        notebook_id: notebookId,
        title: "Untitled page",
      });
      await loadIndex();
      await openPage(page.id);
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  async function deletePage() {
    if (
      !pageId ||
      !confirm(
        "Permanently delete this page? Export it first if you want a copy.",
      )
    )
      return;
    try {
      await saveContent();
      if (dirty) return;
      await api(`/notes/pages/${pageId}/delete`, {
        revision: pageRevision,
        confirm: true,
      });
      try {
        sessionStorage.removeItem(recoveryKey());
      } catch {
        /* best effort */
      }
      pageId = 0;
      doc = emptyDocument();
      await loadIndex();
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  function applyUndo(redo = false) {
    if (gesture) cancelGesture();
    const source = redo ? redoStack : undoStack;
    if (!source.length) return;
    const next = source[source.length - 1];
    if (redo) {
      redoStack = source.slice(0, -1);
      undoStack = [...undoStack, clone(doc)];
    } else {
      undoStack = source.slice(0, -1);
      redoStack = [...redoStack, clone(doc)];
    }
    doc = clone(next);
    selected = selected.filter((id) => doc.elements.some((e) => e.id === id));
    scheduleContent();
  }
  function eraseAt(p: Point) {
    const ids = new Set(
      doc.elements
        .filter(
          (e) =>
            editable(e, doc) && hit(e, p, lineWidth / view.zoom, doc.elements),
        )
        .map((e) => e.id),
    );
    if (ids.size) doc = removeElements(doc, ids);
  }
  function localPoint(event: PointerEvent): Point {
    const rect = canvas!.getBoundingClientRect();
    return [
      event.clientX - rect.left,
      event.clientY - rect.top,
      event.pressure > 0 ? event.pressure : 0.5,
    ];
  }
  function pointerDown(event: PointerEvent) {
    if (!pageId || busy || loading || !canvas) return;
    const input = drawingInput(
      event,
      tool,
      penOnly,
      penActive !== null,
      Date.now() < penGuardUntil,
    );
    event.preventDefault();
    if (
      input === "ignore" ||
      (event.pointerType === "pen" &&
        penActive !== null &&
        penActive !== event.pointerId)
    )
      return;
    if (event.pointerType === "pen") {
      if (gesture) cancelGesture();
      penActive = event.pointerId;
      penGuardUntil = Date.now() + 500;
    }
    canvas.focus();
    const screen = localPoint(event);
    pointers.set(event.pointerId, screen);
    canvas.setPointerCapture(event.pointerId);
    if (pointers.size === 2) {
      if (gesture) cancelGesture(false);
      const [a, b] = [...pointers.values()];
      gesture = {
        mode: "pinch",
        pointer: event.pointerId,
        start: world(screen[0], screen[1], view),
        last: screen,
        before: clone(doc),
        view: clone(view),
        selected: [...selected],
        distance: Math.max(1, Math.hypot(a[0] - b[0], a[1] - b[1])),
        centroid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 1],
      };
      return;
    }
    if (pointers.size > 2 || gesture) return;
    const p = world(screen[0], screen[1], view);
    p[2] = screen[2];
    const mode = space ? "pan" : input;
    const base = {
      pointer: event.pointerId,
      start: p,
      last: p,
      before: clone(doc),
      view: clone(view),
      selected: [...selected],
    };
    if (mode === "pan") {
      gesture = { ...base, mode: "pan", start: screen, last: screen };
      return;
    }
    if (
      event.pointerType !== "pen" &&
      event.pointerType !== "touch" &&
      event.button !== 0
    )
      return;
    if (mode === "select") {
      const b = bounds(selectedElements);
      if (
        b &&
        Math.hypot(p[0] - b.x - b.w, p[1] - b.y - b.h) < 10 / view.zoom
      ) {
        gesture = { ...base, mode: "resize", box: b };
        return;
      }
      const found = hitTop(doc, p, 5 / view.zoom);
      if (found) {
        selected = expandGroups(
          doc,
          event.shiftKey
            ? [...new Set([...selected, found.id])]
            : selected.includes(found.id)
              ? selected
              : [found.id],
        );
        gesture = { ...base, mode: "move", selected: [...selected] };
      } else {
        if (!event.shiftKey) selected = [];
        gesture = { ...base, mode: "rect-select" };
        overlay = [p, p];
      }
      return;
    }
    if (mode === "lasso") {
      gesture = { ...base, mode: "lasso" };
      overlay = [p];
      return;
    }
    if (mode === "eraser") {
      gesture = { ...base, mode: "erase" };
      eraseAt(p);
      return;
    }
    const layer = doc.layers.find((l) => l.id === layerId);
    if (!layer?.visible || layer.locked) {
      error = "Choose a visible unlocked layer to draw.";
      return;
    }
    if (mode === "connector") {
      const found = hitTop(doc, p, 6 / view.zoom);
      if (!found) return;
      if (selected.length !== 1 || selected[0] === found.id) {
        selected = [found.id];
        saveStatus = "Choose the second element to connect";
        return;
      }
      const from = doc.elements.find((e) => e.id === selected[0]);
      if (!from) return;
      const e: CanvasElement = {
        id: uid(),
        kind: "connector",
        layer: layerId,
        x: Math.min(from.x, found.x),
        y: Math.min(from.y, found.y),
        w: Math.abs(found.x - from.x),
        h: Math.abs(found.y - from.y),
        color,
        fill: "none",
        width: lineWidth,
        points: [
          [0, 0, 1],
          [1, 1, 1],
        ],
        from: from.id,
        to: found.id,
      };
      commit({ ...doc, elements: [...doc.elements, e] });
      selected = [e.id];
      return;
    }
    const x = doc.snap ? Math.round(p[0] / 25) * 25 : p[0],
      y = doc.snap ? Math.round(p[1] / 25) * 25 : p[1];
    const e: CanvasElement = {
      id: uid(),
      kind: mode as ElementKind,
      layer: layerId,
      x,
      y,
      w: 0,
      h: 0,
      color,
      fill: mode === "sticky" ? fillColor : "none",
      width: lineWidth,
    };
    if (mode === "text" || mode === "sticky") {
      e.w = mode === "sticky" ? 240 : 320;
      e.h = mode === "sticky" ? 180 : 100;
      e.text = mode === "sticky" ? "New sticky note" : "New text";
      e.fontSize = fontSize;
      commit({ ...doc, elements: [...doc.elements, e] });
      selected = [e.id];
      outline = true;
      return;
    }
    e.points = [[0, 0, p[2]]];
    if (mode !== "pen" && mode !== "highlighter") e.points.push([0, 0, p[2]]);
    doc = { ...doc, elements: [...doc.elements, e] };
    gesture = { ...base, start: [x, y, p[2]], mode: "draw", id: e.id };
  }
  function pointerMove(event: PointerEvent) {
    if (event.pointerType === "pen") penGuardUntil = Date.now() + 500;
    if (!pointers.has(event.pointerId) || !canvas) return;
    const screen = localPoint(event);
    pointers.set(event.pointerId, screen);
    if (!gesture) return;
    event.preventDefault();
    if (gesture.mode === "pinch") {
      if (pointers.size < 2) return;
      const [a, b] = [...pointers.values()];
      const c: Point = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 1],
        distance = Math.hypot(a[0] - b[0], a[1] - b[1]);
      view = zoomAt(
        gesture.view,
        gesture.centroid![0],
        gesture.centroid![1],
        distance / gesture.distance!,
      );
      view = {
        ...view,
        x: view.x + c[0] - gesture.centroid![0],
        y: view.y + c[1] - gesture.centroid![1],
      };
      return;
    }
    if (event.pointerId !== gesture.pointer) return;
    if (gesture.mode === "pan") {
      view = {
        ...gesture.view,
        x: gesture.view.x + screen[0] - gesture.start[0],
        y: gesture.view.y + screen[1] - gesture.start[1],
      };
      return;
    }
    const p = world(screen[0], screen[1], view);
    p[2] = screen[2];
    const dx = p[0] - gesture.start[0],
      dy = p[1] - gesture.start[1];
    if (gesture.mode === "draw") {
      const e = doc.elements.find((e) => e.id === gesture!.id);
      if (!e) return;
      if (e.kind === "pen" || e.kind === "highlighter") {
        const events = event.getCoalescedEvents?.() ?? [event];
        for (const sample of events.length ? events : [event]) {
          const s = localPoint(sample),
            q = world(s[0], s[1], view);
          q[2] = s[2];
          if (e.points!.length >= 20000) {
            error = "Stroke limit reached; lift the pen to continue.";
            break;
          }
          const last = e.points!.at(-1)!;
          if (
            Math.hypot(q[0] - e.x - last[0], q[1] - e.y - last[1]) >
            0.2 / view.zoom
          )
            e.points!.push([q[0] - e.x, q[1] - e.y, q[2]]);
        }
        doc = { ...doc, elements: [...doc.elements] };
      } else {
        const endX = doc.snap ? Math.round(p[0] / 25) * 25 : p[0],
          endY = doc.snap ? Math.round(p[1] / 25) * 25 : p[1],
          x = Math.min(gesture.start[0], endX),
          y = Math.min(gesture.start[1], endY);
        const next = {
          ...e,
          x,
          y,
          w: Math.abs(endX - gesture.start[0]),
          h: Math.abs(endY - gesture.start[1]),
          points: [
            [gesture.start[0] - x, gesture.start[1] - y, 1],
            [endX - x, endY - y, 1],
          ] as Point[],
        };
        doc = {
          ...doc,
          elements: doc.elements.map((v) => (v.id === e.id ? next : v)),
        };
      }
    } else if (gesture.mode === "erase") {
      const steps = Math.min(
        100,
        Math.max(
          1,
          Math.ceil(
            Math.hypot(p[0] - gesture.last[0], p[1] - gesture.last[1]) /
              Math.max(1, lineWidth / view.zoom),
          ),
        ),
      );
      for (let i = 1; i <= steps; i++)
        eraseAt([
          gesture.last[0] + ((p[0] - gesture.last[0]) * i) / steps,
          gesture.last[1] + ((p[1] - gesture.last[1]) * i) / steps,
          1,
        ]);
    } else if (gesture.mode === "move") {
      const mx = doc.snap ? Math.round(dx / 25) * 25 : dx,
        my = doc.snap ? Math.round(dy / 25) * 25 : dy;
      doc = {
        ...doc,
        elements: gesture.before.elements.map((e) =>
          gesture!.selected.includes(e.id)
            ? { ...e, x: e.x + mx, y: e.y + my }
            : e,
        ),
      };
    } else if (gesture.mode === "resize") {
      const b = gesture.box!,
        next = { ...b, w: Math.max(10, b.w + dx), h: Math.max(10, b.h + dy) };
      doc = {
        ...doc,
        elements: gesture.before.elements.map((e) =>
          gesture!.selected.includes(e.id) && editable(e, gesture!.before)
            ? resizeElement(e, b, next)
            : e,
        ),
      };
    } else if (gesture.mode === "rect-select") {
      overlay = [
        gesture.start,
        [p[0], gesture.start[1], 1],
        p,
        [gesture.start[0], p[1], 1],
        gesture.start,
      ];
    } else if (gesture.mode === "lasso") {
      overlay = [...overlay, p];
    }
    gesture.last = p;
  }
  function pointerUp(event: PointerEvent) {
    if (!pointers.has(event.pointerId)) return;
    pointerMove(event);
    pointers.delete(event.pointerId);
    if (event.pointerId === penActive) {
      penActive = null;
      penGuardUntil = Date.now() + 500;
    }
    if (canvas?.hasPointerCapture(event.pointerId))
      canvas.releasePointerCapture(event.pointerId);
    if (!gesture) return;
    if (gesture.mode === "pinch") {
      scheduleView();
      const next = [...pointers.entries()][0];
      if (next)
        gesture = {
          mode: "pan",
          pointer: next[0],
          start: next[1],
          last: next[1],
          before: clone(doc),
          view: clone(view),
          selected: [...selected],
        };
      else {
        gesture = null;
        resumeAutosave();
      }
      return;
    }
    if (gesture.pointer !== event.pointerId) return;
    const g = gesture;
    gesture = null;
    if (g.mode === "pan") {
      scheduleView();
      resumeAutosave();
      return;
    }
    if (g.mode === "rect-select" || g.mode === "lasso") {
      selected = expandGroups(doc, [
        ...g.selected,
        ...lassoSelection(doc, overlay),
      ]);
      overlay = [];
      resumeAutosave();
      return;
    }
    if (g.mode === "draw")
      doc = {
        ...doc,
        elements: doc.elements.map((e) =>
          e.id === g.id ? finishStroke(e) : e,
        ),
      };
    if (JSON.stringify(g.before) !== JSON.stringify(doc)) {
      doc = finishGesture(g.before, doc, false);
      remember(g.before);
      scheduleContent();
    }
    overlay = [];
    resumeAutosave();
  }
  function cancelGesture(clear = true) {
    if (gesture) {
      doc = finishGesture(gesture.before, doc, true);
      view = gesture.view;
      selected = gesture.selected;
      gesture = null;
    }
    overlay = [];
    if (clear) {
      penActive = null;
      penGuardUntil = Date.now() + 500;
      for (const id of pointers.keys())
        if (canvas?.hasPointerCapture(id)) canvas.releasePointerCapture(id);
      pointers.clear();
    }
    resumeAutosave();
  }
  function wheel(event: WheelEvent) {
    if (!pageId) return;
    event.preventDefault();
    if (gesture) return;
    const r = canvas!.getBoundingClientRect();
    if (event.ctrlKey || event.metaKey || !event.shiftKey)
      view = zoomAt(
        view,
        event.clientX - r.left,
        event.clientY - r.top,
        Math.exp(-event.deltaY * 0.0015),
      );
    else view = { ...view, x: view.x - event.deltaY, y: view.y - event.deltaX };
    scheduleView();
  }
  function deleteSelected() {
    if (!selected.length) return;
    const ids = new Set(
      selectedElements.filter((e) => editable(e, doc)).map((e) => e.id),
    );
    commit(removeElements(doc, ids));
    selected = [];
  }
  function groupSelection(ungroup = false) {
    if (!selected.length) return;
    const group = ungroup ? undefined : uid();
    commit({
      ...doc,
      elements: doc.elements.map((e) =>
        selected.includes(e.id) && editable(e, doc) ? { ...e, group } : e,
      ),
    });
  }
  function patchText(id: string, value: string) {
    const target = doc.elements.find((e) => e.id === id);
    if (!target || !editable(target, doc)) return;
    commit({
      ...doc,
      elements: doc.elements.map((e) =>
        e.id === id ? { ...e, text: value } : e,
      ),
    });
  }
  function patchLayer(
    id: string,
    patch: Partial<CanvasDocument["layers"][number]>,
  ) {
    commit({
      ...doc,
      layers: doc.layers.map((l) => (l.id === id ? { ...l, ...patch } : l)),
    });
    selected = selected.filter((id) => {
      const e = doc.elements.find((e) => e.id === id);
      return e && editable(e, doc);
    });
  }
  function reorderLayer(id: string, delta: number) {
    const index = doc.layers.findIndex((l) => l.id === id),
      to = index + delta;
    if (to < 0 || to >= doc.layers.length) return;
    const layers = [...doc.layers];
    [layers[index], layers[to]] = [layers[to], layers[index]];
    commit({ ...doc, layers });
  }
  function reorderSelection(front: boolean) {
    const ids = new Set(
      selectedElements.filter((e) => editable(e, doc)).map((e) => e.id),
    );
    const picked = doc.elements.filter((e) => ids.has(e.id)),
      other = doc.elements.filter((e) => !ids.has(e.id));
    commit({
      ...doc,
      elements: front ? [...other, ...picked] : [...picked, ...other],
    });
  }
  function keydown(event: KeyboardEvent) {
    if (event.defaultPrevented || !(event.target instanceof HTMLElement))
      return;
    const target = event.target as HTMLElement;
    if (
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
      target.isContentEditable ||
      target.closest("button, a, summary")
    )
      return;
    if (event.key === " ") {
      space = true;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (event.key === "Escape") {
      cancelGesture();
      selected = [];
      event.stopPropagation();
      return;
    }
    if (event.ctrlKey || event.metaKey) {
      if (event.key.toLowerCase() === "z") {
        event.preventDefault();
        event.stopPropagation();
        applyUndo(event.shiftKey);
      } else if (event.key.toLowerCase() === "y") {
        event.preventDefault();
        event.stopPropagation();
        applyUndo(true);
      } else if (event.key.toLowerCase() === "s") {
        event.preventDefault();
        event.stopPropagation();
        blocked = false;
        void saveContent();
      } else if (event.key.toLowerCase() === "g") {
        event.preventDefault();
        event.stopPropagation();
        groupSelection(event.shiftKey);
      }
      return;
    }
    if (["Delete", "Backspace"].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      deleteSelected();
      return;
    }
    const next = tools.find(
      (t) => t.key.toLowerCase() === event.key.toLowerCase(),
    );
    if (next) {
      tool = next.id;
      event.preventDefault();
      event.stopPropagation();
    }
    if (event.key === "0") {
      view = { x: 0, y: 0, zoom: 1 };
      scheduleView();
      event.stopPropagation();
    }
    if (event.key === "f") {
      view = fit(doc.elements, width, height);
      scheduleView();
      event.stopPropagation();
    }
  }
  async function awaitImport<T>(started: { task: TaskRecord }): Promise<T> {
    let task = started.task;
    while (task.status === "running") {
      await new Promise((resolve) => setTimeout(resolve, 500));
      task = await api<TaskRecord>(`/tasks/${task.id}`);
    }
    if (task.status !== "completed")
      throw new Error(task.error ?? "Local import failed");
    return task.result as T;
  }
  async function importReference(event: Event) {
    const input = event.target as HTMLInputElement,
      file = input.files?.[0];
    input.value = "";
    if (!file || !pageId) return;
    if (file.size > 12 * 1024 * 1024) {
      error = "Import is limited to 12 MiB.";
      return;
    }
    const layer = doc.layers.find((l) => l.id === layerId);
    if (!layer?.visible || layer.locked) {
      error = "Choose a visible unlocked layer before importing.";
      return;
    }
    busy = true;
    error = "";
    operationStatus = "Rasterizing reference locally…";
    try {
      const data = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("Cannot read file"));
        reader.readAsDataURL(file);
      });
      const result = await awaitImport<{
        attachments: { id: string; width: number; height: number }[];
      }>(await api("/notes/attachments", { data, name: file.name }));
      const center = world(width / 2, height / 2, view);
      let y = center[1];
      const added = result.attachments.map((a) => {
        const w = Math.min(800, a.width),
          h = (a.height * w) / a.width;
        const e: CanvasElement = {
          id: uid(),
          kind: "image",
          layer: layerId,
          x: center[0] - w / 2,
          y,
          w,
          h,
          color,
          fill: "none",
          width: 1,
          attachment: a.id,
        };
        y += h + 30;
        return e;
      });
      commit({ ...doc, elements: [...doc.elements, ...added] });
      selected = added.map((e) => e.id);
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
      operationStatus = "";
    }
  }
  async function exportPage(kind: "json" | "svg" | "png") {
    if (!pageId) return;
    busy = true;
    error = "";
    try {
      const name =
        (title || "Notebook page")
          .replace(/[^a-zA-Z0-9 _-]/g, "")
          .slice(0, 80) || "Notebook page";
      if (kind === "json") {
        // Export the actual local content even when a stale-tab save is blocked.
        const refs = [
          ...new Set(
            doc.elements
              .filter((e) => e.kind === "image")
              .map((e) => e.attachment!),
          ),
        ];
        const attachments = [];
        let bytes = 0;
        for (const id of refs) {
          const response = await fetch(`/api/notes/attachments/${id}`);
          if (!response.ok)
            throw new Error("Reference unavailable; export stopped");
          const blob = await response.blob();
          bytes += blob.size;
          if (bytes > 12 * 1024 * 1024)
            throw new Error(
              "Portable page exceeds 12 MiB of references. Split references across pages.",
            );
          const data = await new Promise<string>((resolve, reject) => {
            const r = new FileReader();
            r.onload = () => resolve(String(r.result).split(",")[1]);
            r.onerror = () => reject(new Error("Cannot export reference"));
            r.readAsDataURL(blob);
          });
          attachments.push({ id, name: "Reference.png", data });
        }
        downloadBlob(
          new Blob(
            [
              JSON.stringify(
                {
                  format: "kitty-notes",
                  version: 1,
                  title,
                  notebook: {
                    title: activeNotebook?.title ?? "",
                    subject: activeNotebook?.subject ?? "",
                    project: activeNotebook?.project ?? "",
                  },
                  document: doc,
                  viewport: view,
                  attachments,
                },
                null,
                2,
              ),
            ],
            { type: "application/json" },
          ),
          `${name}.kitty.json`,
        );
      } else {
        const svg = await exportSVG(doc);
        downloadBlob(
          kind === "svg"
            ? new Blob([svg], { type: "image/svg+xml" })
            : await svgPNG(svg),
          `${name}.${kind}`,
        );
      }
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  async function importPage(event: Event) {
    const input = event.target as HTMLInputElement,
      file = input.files?.[0];
    input.value = "";
    if (!file || (!restoreNotebook && !notebookId)) return;
    if (file.size > 20 * 1024 * 1024) {
      error = "Portable JSON is limited to 20 MiB.";
      return;
    }
    busy = true;
    operationStatus = "Restoring page locally…";
    try {
      await saveContent();
      if (dirty) return;
      const page = await awaitImport<Page>(
        await api("/notes/import", {
          notebook_id: restoreNotebook ? 0 : notebookId,
          page: JSON.parse(await file.text()),
        }),
      );
      await loadIndex();
      await openPage(page.id);
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
      operationStatus = "";
    }
  }
  function makeFlashcards() {
    if (!selectedText) return;
    if (onflashcards) onflashcards(selectedText, pageId);
    else
      window.dispatchEvent(
        new CustomEvent("kitty-study-text", {
          detail: {
            purpose: "flashcards",
            text: selectedText,
            page_id: pageId,
          },
        }),
      );
  }
  function previewSummary() {
    if (!selectedText) return;
    if (!prefs.allow_ai) {
      error =
        "Enable selected-text AI sharing under Local privacy first. No notebook content has been sent.";
      return;
    }
    summarySelection = selectedText;
  }
  async function savePrivacy() {
    try {
      prefs = await api("/notes/preferences", {
        revision: prefs.revision,
        allow_ai: prefs.allow_ai,
        retention_days: Number(prefs.retention_days),
      });
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  async function cleanup() {
    if (
      !confirm(
        "Remove unreferenced image/PDF rasters older than the retention setting? Drawings, pages, and referenced images are never removed.",
      )
    )
      return;
    try {
      const result = await api<{ removed: number }>("/notes/cleanup", {
        confirm: true,
      });
      saveStatus = `Removed ${result.removed} unused local references`;
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  onMount(() => {
    try {
      penOnly = localStorage.getItem("kitty.notes.pen-only") === "true";
    } catch {
      /* Use normal pointer input when storage is unavailable. */
    }
    void loadIndex().then(() => {
      if (pages.length) void openPage(pages[0].id);
    });
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) {
        width = Math.max(200, rect.width);
        height = Math.max(300, rect.height);
      }
    });
    if (stage) observer.observe(stage);
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty) {
        keepRecovery();
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const visibility = () => {
      if (document.hidden) {
        if (gesture) cancelGesture();
        void saveContent();
        void saveView();
      }
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      observer.disconnect();
      if (gesture) cancelGesture();
      clearTimeout(saveTimer);
      clearTimeout(viewTimer);
      clearTimeout(searchTimer);
      if (dirty) keepRecovery();
      void saveContent();
      void saveView();
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("visibilitychange", visibility);
    };
  });
  $effect(() => {
    if (revision > 0 && !dirty && !gesture) void loadIndex();
  });
  $effect(() => {
    for (const e of doc.elements) {
      if (e.kind === "image" && e.attachment && !images.has(e.attachment)) {
        const image = new Image();
        images.set(e.attachment, image);
        image.onload = () => imageEpoch++;
        image.onerror = () => {
          error = "A registered local reference could not load.";
        };
        image.src = `/api/notes/attachments/${e.attachment}`;
      }
    }
  });
  $effect(() => {
    imageEpoch;
    if (canvas)
      renderCanvas(
        canvas,
        doc,
        view,
        images,
        selected,
        overlay,
        width,
        height,
        ink,
        paper,
      );
  });
</script>

<svelte:window
  onkeydown={keydown}
  onkeyup={(event) => {
    if (event.key === " ") space = false;
  }}
/>
<section class="notes-workspace" aria-label="Infinite canvas notebooks">
  <header class="notes-heading">
    <div>
      <span class="eyebrow">THINK BEYOND THE PAGE</span>
      <h2>Notebooks & infinite canvas</h2>
      <p class="muted">
        Local drawings, ideas, reference pages, and active recall.{demo
          ? " Demo edits stay in this workspace."
          : ""}
      </p>
    </div>
    <span role="status" class:unsaved={dirty}>{saveStatus}</span>
  </header>
  {#if operationStatus}<p class="muted" role="status">{operationStatus}</p>{/if}
  {#if error}<div class="error-banner" role="alert">
      {error}<button
        onclick={() => (error = "")}
        aria-label="Dismiss notes error">×</button
      >
    </div>{/if}
  {#if viewError}<p class="error-banner" role="alert">
      Viewport: {viewError}. Content saves independently.
      <button
        onclick={() => {
          viewError = "";
          void saveView();
        }}>Retry viewport</button
      >
    </p>{/if}
  <div class="notebook-layout">
    <aside class="notebook-sidebar" aria-label="Notebook navigation">
      <label
        >Search pages & text<input
          type="search"
          bind:value={search}
          oninput={() => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => void loadIndex(), 250);
          }}
          placeholder="Words, subject, project"
        /></label
      >
      <label
        >Notebook<select
          value={notebookId}
          onchange={async (e) => {
            await saveContent();
            if (!dirty) notebookId = Number(e.currentTarget.value);
          }}
          ><option value={0}>Choose a notebook</option
          >{#each notebooks as notebook}<option value={notebook.id}
              >{notebook.title}</option
            >{/each}</select
        ></label
      >
      {#if activeNotebook}<p class="muted">
          Subject: {activeNotebook.subject || "Unlinked"}<br />Project: {activeNotebook.project ||
            "Unlinked"}
        </p>
        <button
          class="button"
          onclick={() => {
            newNotebook = activeNotebook!.title;
            subject = activeNotebook!.subject;
            project = activeNotebook!.project;
            editNotebook = true;
          }}>Edit notebook links</button
        >{/if}
      <nav aria-label="Pages">
        {#each pages.filter((p) => p.notebook_id === notebookId) as page}<button
            class:active={page.id === pageId}
            disabled={loading || busy}
            onclick={() => void openPage(page.id)}>{page.title}</button
          >{/each}{#if !pages.some((p) => p.notebook_id === notebookId)}<p
            class="muted"
          >
            No matching pages.
          </p>{/if}
      </nav>
      <button
        class="button"
        disabled={!notebookId || busy || loading}
        onclick={createPage}>＋ New page</button
      >
      <details open={!notebooks.length || editNotebook}>
        <summary>{editNotebook ? "Edit notebook" : "New notebook"}</summary>
        <form
          onsubmit={(e) => {
            e.preventDefault();
            void (editNotebook ? updateNotebook() : createNotebook());
          }}
        >
          <label
            >Title<input
              required
              maxlength="200"
              bind:value={newNotebook}
            /></label
          ><label
            >Subject link<input
              maxlength="200"
              bind:value={subject}
              placeholder="e.g. Biology"
            /></label
          ><label
            >Project link<input
              maxlength="200"
              bind:value={project}
              placeholder="e.g. Research presentation"
            /></label
          ><button class="button" disabled={busy}
            >{editNotebook ? "Save notebook" : "Create notebook"}</button
          >{#if editNotebook}<button
              type="button"
              onclick={() => {
                editNotebook = false;
                newNotebook = "";
              }}>Cancel</button
            >{/if}
        </form>
      </details>
      <details>
        <summary>Local privacy & retention</summary>
        <p class="muted">
          Stored only in the local database and private reference directory.
          Unsaved recovery stays in this browser session. No full notebook is
          sent to AI.
        </p>
        <label class="check"
          ><input type="checkbox" bind:checked={prefs.allow_ai} />Allow explicit
          selected-text AI sharing</label
        ><label
          >Unused reference retention (days)<input
            type="number"
            min="0"
            max="3650"
            bind:value={prefs.retention_days}
          /></label
        >
        <p class="muted">
          0 removes unused references immediately when you choose cleanup. Pages
          and used references are kept until you delete them.
        </p>
        <button class="button" onclick={savePrivacy}>Save privacy</button
        ><button class="button" onclick={cleanup}
          >Clean unused references</button
        >
      </details>
    </aside>
    <div class="canvas-main">
      <div class="page-bar">
        <label
          >Page title<input
            disabled={!pageId}
            value={title}
            maxlength="200"
            oninput={(e) => {
              title = e.currentTarget.value;
              scheduleContent();
            }}
          /></label
        ><button
          class="button"
          disabled={!dirty || busy}
          onclick={() => {
            blocked = false;
            error = "";
            void saveContent();
          }}>Save / retry</button
        ><button
          class="button"
          disabled={!pageId || loading}
          onclick={() => {
            if (
              !dirty ||
              confirm("Discard local edits and reload the saved page?")
            ) {
              dirty = false;
              blocked = false;
              try {
                sessionStorage.removeItem(recoveryKey());
              } catch {
                /* best effort */
              }
              void openPage(pageId, true);
            }
          }}>Reload saved</button
        ><button class="button" disabled={!pageId || busy} onclick={deletePage}
          >Delete page</button
        >
      </div>
      <div class="tools" role="toolbar" aria-label="Drawing tools">
        {#each tools as item}<button
            class:active={tool === item.id}
            aria-pressed={tool === item.id}
            title={`${item.label} (${item.key})`}
            disabled={!pageId}
            onclick={() => {
              if (gesture) cancelGesture();
              tool = item.id;
            }}>{item.label}</button
          >{/each}
      </div>
      <div class="pen-controls">
        <label class="check"
          ><input
            type="checkbox"
            bind:checked={penOnly}
            onchange={savePenMode}
          />Pen writing mode — fingers only pan or pinch</label
        ><span class="muted"
          >Pressure-sensitive ink · pen side button / inverted tip erases ·
          palms ignored while writing</span
        >
      </div>
      <div class="canvas-controls">
        <label>Ink<input type="color" bind:value={color} /></label><label
          >Sticky fill<input type="color" bind:value={fillColor} /></label
        ><label
          >{tool === "eraser"
            ? "Eraser radius (screen px)"
            : "Stroke width"}<input
            type="range"
            min="1"
            max="60"
            bind:value={lineWidth}
          /><span>{lineWidth}</span></label
        ><label
          >Text size<input
            type="number"
            min="8"
            max="120"
            bind:value={fontSize}
          /></label
        ><button disabled={!undoStack.length} onclick={() => applyUndo()}
          >Undo</button
        ><button disabled={!redoStack.length} onclick={() => applyUndo(true)}
          >Redo</button
        ><label class="check"
          ><input
            type="checkbox"
            checked={doc.grid}
            onchange={(e) => commit({ ...doc, grid: e.currentTarget.checked })}
          />Grid</label
        ><label class="check"
          ><input
            type="checkbox"
            checked={doc.snap}
            onchange={(e) => commit({ ...doc, snap: e.currentTarget.checked })}
          />Snap</label
        ><button
          onclick={() => {
            view = zoomAt(view, width / 2, height / 2, 0.8);
            scheduleView();
          }}>−</button
        ><output>{Math.round(view.zoom * 100)}%</output><button
          onclick={() => {
            view = zoomAt(view, width / 2, height / 2, 1.25);
            scheduleView();
          }}>＋</button
        ><button
          onclick={() => {
            view = { x: 0, y: 0, zoom: 1 };
            scheduleView();
          }}>Reset (0)</button
        ><button
          onclick={() => {
            view = fit(doc.elements, width, height);
            scheduleView();
          }}>Fit (F)</button
        ><button aria-pressed={outline} onclick={() => (outline = !outline)}
          >Text outline</button
        >
      </div>
      <div class="canvas-stage" bind:this={stage}>
        <canvas
          bind:this={canvas}
          style={`width:${width}px;height:${height}px;`}
          aria-label="Infinite drawing canvas. Write with a pressure-sensitive pen. Use drawing tools or the accessible text outline for notes."
          tabindex="0"
          onblur={() => {
            space = false;
          }}
          onpointerdown={pointerDown}
          onpointermove={pointerMove}
          onpointerup={pointerUp}
          onpointercancel={(e) => {
            if (pointers.has(e.pointerId)) cancelGesture();
          }}
          onlostpointercapture={(e) => {
            if (
              gesture &&
              gesture.pointer === e.pointerId &&
              pointers.has(e.pointerId)
            )
              cancelGesture();
          }}
          onwheel={wheel}
          oncontextmenu={(e) => e.preventDefault()}
        ></canvas>
        {#if !pageId}<div class="canvas-empty">
            <h3>A place for your next idea</h3>
            <p>Create a notebook and page to start drawing.</p>
          </div>{/if}
      </div>
      <p class="canvas-hint">
        Hold Space or middle-drag to pan · wheel to zoom · Shift-wheel to pan ·
        two fingers to pinch · drag selection corner to resize · Ctrl/Cmd-Z undo
        · Escape cancels · {doc.elements.length}/2000 elements
      </p>
      <div class="selection-actions">
        <button disabled={!selected.length} onclick={deleteSelected}
          >Delete selection</button
        ><button disabled={selected.length < 2} onclick={() => groupSelection()}
          >Group</button
        ><button
          disabled={!selected.length}
          onclick={() => groupSelection(true)}>Ungroup</button
        ><button
          disabled={!selected.length}
          onclick={() => reorderSelection(true)}>Bring forward</button
        ><button
          disabled={!selected.length}
          onclick={() => reorderSelection(false)}>Send backward</button
        ><label
          >Move selection to layer<select
            disabled={!selected.length}
            value={layerId}
            onchange={(e) => {
              const next = e.currentTarget.value;
              if (doc.layers.find((l) => l.id === next)?.locked) {
                error = "Target layer is locked.";
                return;
              }
              layerId = next;
              commit({
                ...doc,
                elements: doc.elements.map((v) =>
                  selected.includes(v.id) && editable(v, doc)
                    ? { ...v, layer: next }
                    : v,
                ),
              });
            }}
            >{#each doc.layers as layer}<option value={layer.id}
                >{layer.name}</option
              >{/each}</select
          ></label
        ><button disabled={!selectedText} onclick={makeFlashcards}
          >Make flashcards from selected text</button
        ><button disabled={!selectedText} onclick={previewSummary}
          >Summarize selection…</button
        >
      </div>
      {#if summarySelection && prefs.allow_ai}<section
          class="ai-review"
          aria-label="Review selected-text AI request"
        >
          <h3>Summarize only this selected text</h3>
          <AIReview
            purpose="summarize"
            text={summarySelection}
            {demo}
            onresult={(draft) => {
              summary = draft;
              summarySelection = "";
            }}
          /><button onclick={() => (summarySelection = "")}
            >Close summary review</button
          >
        </section>{/if}
      {#if summary}<section class="ai-review">
          <h3>Summary draft — verify before using</h3>
          <pre>{summary}</pre>
          <button onclick={() => (summary = "")}>Dismiss</button>
        </section>{/if}
      {#if outline}<section
          class="text-outline"
          aria-label="Accessible editable text outline"
        >
          <h3>Text outline</h3>
          <p class="muted">
            Select entries for active recall. Text edits autosave locally;
            locked layers remain read-only.
          </p>
          {#each textElements as element}<article>
              <label class="check"
                ><input
                  type="checkbox"
                  checked={selected.includes(element.id)}
                  onchange={(e) =>
                    (selected = e.currentTarget.checked
                      ? [...new Set([...selected, element.id])]
                      : selected.filter((id) => id !== element.id))}
                />{element.kind === "sticky" ? "Sticky note" : "Text"} · {doc.layers.find(
                  (l) => l.id === element.layer,
                )?.name}</label
              ><textarea
                aria-label={`Edit ${element.kind} ${element.id.slice(0, 8)}`}
                rows="3"
                maxlength="20000"
                readonly={!editable(element, doc)}
                value={element.text ?? ""}
                oninput={(e) => patchText(element.id, e.currentTarget.value)}
              ></textarea><button
                onclick={() => {
                  selected = [element.id];
                  view = fit([element], width, height);
                  scheduleView();
                }}>Locate on canvas</button
              >
            </article>{/each}{#if !textElements.length}<p>
              Add Text or Sticky notes anywhere on the canvas.
            </p>{/if}
        </section>{/if}
      <details class="layers">
        <summary>Layers ({doc.layers.length})</summary>
        <p class="muted">
          Layers are drawn bottom to top. Hidden layers are not exported
          visually. Locked layers cannot be drawn on, moved, resized, or erased.
        </p>
        {#each doc.layers as layer, i}<div class="layer-row">
            <input
              type="radio"
              name="notes-layer"
              aria-label={`Draw on ${layer.name}`}
              checked={layerId === layer.id}
              onchange={() => (layerId = layer.id)}
            /><input
              aria-label="Layer name"
              maxlength="100"
              value={layer.name}
              onchange={(e) =>
                patchLayer(layer.id, { name: e.currentTarget.value })}
            /><label class="check"
              ><input
                type="checkbox"
                checked={layer.visible}
                onchange={(e) =>
                  patchLayer(layer.id, { visible: e.currentTarget.checked })}
              />Show</label
            ><label class="check"
              ><input
                type="checkbox"
                checked={layer.locked}
                onchange={(e) =>
                  patchLayer(layer.id, { locked: e.currentTarget.checked })}
              />Lock</label
            ><button
              disabled={i === 0}
              onclick={() => reorderLayer(layer.id, -1)}>Lower</button
            ><button
              disabled={i === doc.layers.length - 1}
              onclick={() => reorderLayer(layer.id, 1)}>Raise</button
            >
          </div>{/each}
        <form
          onsubmit={(e) => {
            e.preventDefault();
            if (doc.layers.length >= 32) return;
            const id = uid();
            commit({
              ...doc,
              layers: [
                ...doc.layers,
                {
                  id,
                  name: newLayer || "New layer",
                  visible: true,
                  locked: false,
                },
              ],
            });
            layerId = id;
            newLayer = "";
          }}
        >
          <input
            aria-label="New layer name"
            maxlength="100"
            bind:value={newLayer}
          /><button disabled={doc.layers.length >= 32 || !pageId}
            >Add layer</button
          >
        </form>
      </details>
      <div class="export-actions">
        <label class="file-button"
          >Import image / PDF<input
            type="file"
            accept="image/png,image/jpeg,image/webp,application/pdf"
            disabled={!pageId || busy}
            onchange={importReference}
          /></label
        ><label class="check"
          ><input type="checkbox" bind:checked={restoreNotebook} />Restore
          original notebook & links in a new notebook</label
        ><label class="file-button"
          >Restore JSON page<input
            type="file"
            accept="application/json,.json"
            disabled={(!restoreNotebook && !notebookId) || busy}
            onchange={importPage}
          /></label
        ><button disabled={!pageId || busy} onclick={() => exportPage("json")}
          >Export portable JSON</button
        ><button disabled={!pageId || busy} onclick={() => exportPage("svg")}
          >Export SVG</button
        ><button disabled={!pageId || busy} onclick={() => exportPage("png")}
          >Export PNG</button
        ><span class="muted"
          >PDF: up to 20 pages, rasterized locally for safe annotation. PNG
          export bounded to 4096 px.</span
        >
      </div>
    </div>
  </div>
</section>

<style>
  .notes-workspace {
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--border);
    border-radius: 14px;
    overflow: hidden;
  }
  .notes-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    padding: 24px;
  }
  .notes-heading h2 {
    margin: 6px 0;
  }
  .notes-heading p {
    margin: 0;
  }
  .unsaved {
    color: var(--warning);
  }
  .notebook-layout {
    display: grid;
    grid-template-columns: 240px minmax(0, 1fr);
    border-top: 1px solid var(--border);
  }
  .notebook-sidebar {
    padding: 16px;
    border-right: 1px solid var(--border);
    background: var(--surface-raised);
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .notebook-sidebar nav {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 300px;
    overflow: auto;
  }
  .notebook-sidebar nav button {
    text-align: left;
  }
  .canvas-main {
    min-width: 0;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .page-bar,
  .tools,
  .canvas-controls,
  .selection-actions,
  .export-actions,
  .layer-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .page-bar label {
    flex: 1;
    min-width: 180px;
  }
  .tools {
    padding: 8px;
    background: var(--surface-raised);
    border-radius: 9px;
  }
  .tools button,
  .canvas-controls button,
  .selection-actions button,
  .export-actions button,
  .layer-row button {
    font-size: 12px;
  }
  .canvas-controls label {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
  }
  .canvas-controls input[type="number"] {
    width: 65px;
  }
  .canvas-controls input[type="range"] {
    width: 80px;
  }
  .canvas-controls input[type="color"] {
    width: 30px;
    padding: 2px;
    height: 28px;
  }
  .canvas-stage {
    position: relative;
    height: clamp(400px, 62vh, 780px);
    min-width: 0;
    overflow: hidden;
    background: var(--canvas);
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .canvas-stage canvas {
    display: block;
    touch-action: none;
    cursor: crosshair;
    outline-offset: -3px;
  }
  .canvas-empty {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    pointer-events: none;
  }
  .canvas-hint {
    font-size: 12px;
    color: var(--muted);
    margin: 0;
  }
  .active {
    background: var(--accent) !important;
    color: var(--surface) !important;
    border-color: var(--accent) !important;
  }
  .notes-workspace label {
    display: flex;
    flex-direction: column;
    gap: 5px;
    font-size: 12px;
    color: var(--muted);
  }
  .notes-workspace label.check {
    flex-direction: row;
    align-items: center;
    gap: 6px;
  }
  .notes-workspace input,
  .notes-workspace select,
  .notes-workspace textarea {
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 7px;
    min-width: 0;
  }
  .notes-workspace button,
  .file-button {
    border: 1px solid var(--border);
    background: var(--surface-raised);
    color: var(--ink);
    border-radius: 6px;
    padding: 7px 10px;
    cursor: pointer;
  }
  .notes-workspace button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  .notes-workspace details {
    padding: 8px;
    border: 1px solid var(--border);
    border-radius: 7px;
  }
  .notes-workspace summary {
    cursor: pointer;
    font-size: 13px;
    font-weight: 600;
  }
  .notes-workspace form {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 12px;
  }
  .layer-row {
    padding: 7px 0;
  }
  .layer-row > input:not([type]) {
    flex: 1;
  }
  .layer-row input[type="radio"] {
    width: auto;
  }
  .layers form {
    flex-direction: row;
  }
  .text-outline,
  .ai-review {
    padding: 16px;
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .text-outline article {
    padding: 10px 0;
    border-top: 1px solid var(--border);
    display: grid;
    gap: 8px;
  }
  .text-outline textarea {
    width: 100%;
    box-sizing: border-box;
    resize: vertical;
  }
  .ai-review pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 350px;
    overflow: auto;
    font-family: inherit;
    background: var(--surface);
    padding: 10px;
  }
  .file-button {
    position: relative;
    overflow: hidden;
    display: inline-flex !important;
    font-size: 12px !important;
  }
  .file-button input {
    position: absolute;
    inset: 0;
    opacity: 0;
    cursor: pointer;
    width: 100%;
  }
  .error-banner {
    margin: 0 14px 12px;
    padding: 10px;
    border: 1px solid var(--warning);
    background: var(--surface-raised);
    color: var(--warning);
    overflow-wrap: anywhere;
  }
  .error-banner button {
    margin-left: 8px;
  }
  .muted {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }
  @media (max-width: 1000px) {
    .notebook-layout {
      grid-template-columns: 180px minmax(0, 1fr);
    }
    .notes-heading {
      padding: 16px;
    }
  }
  @media (max-width: 720px) {
    .notebook-layout {
      grid-template-columns: 1fr;
    }
    .notebook-sidebar {
      border-right: 0;
      border-bottom: 1px solid var(--border);
    }
    .notebook-sidebar nav {
      max-height: 120px;
    }
    .notes-heading {
      align-items: flex-start;
      flex-direction: column;
    }
    .canvas-stage {
      height: 480px;
    }
  }
</style>
