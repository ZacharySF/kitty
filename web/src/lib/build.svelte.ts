/**
 * The active build in this window (docs/platform-design.md Q8/Q9). Loaded before the
 * app mounts, replaced by every `build` push on the live channel, and the only place
 * interface preferences are read from or written to: `setPreferences` updates this
 * window at once and saves to the build file through the server.
 */
import { api, ApiError, type Problem } from "./api";
import {
  DEFAULT_BUILD, collectLegacy, deepMerge, hasCustomStyle, hasSafeParam, resolveSafe, withSafeParam,
  type Build, type BuildResponse, type BuildSnapshot, type BuildTheme, type HistoryEntry, type PreferencePatch,
} from "./buildCore";
import { onLive, type LiveMessage } from "./live";
import { appearanceOf, applyAppearance } from "./appearance";
import { applyBuildLook } from "./themes";
import type { ThemeId } from "./themes/catalog";

class BuildStore {
  /** False until the first load answered (or failed). */
  loaded = $state(false);
  /** sha256 of the file on disk, and of the build in effect (they differ while the file has an error). */
  revision = $state("");
  activeRevision = $state("");
  /** The build in effect here: the server's active build plus preferences still being saved. */
  build = $state.raw<Build>(DEFAULT_BUILD);
  /** The file's text and its problems (an invalid file keeps the last good build applied). */
  text = $state("");
  problems = $state.raw<Problem[]>([]);
  invalid = $state(false);
  restartRequired = $state(false);
  history = $state.raw<HistoryEntry[]>([]);
  /** The server was started with --safe; `?safe` in this window's URL; this window's toggle. */
  serverSafe = $state(false);
  urlSafe = $state(false);
  safeToggle = $state<boolean | null>(null);
  safe = $derived(resolveSafe({ url: this.urlSafe, server: this.serverSafe, toggle: this.safeToggle }));
  /** The build editor's checked look, shown in this window while it is being typed. */
  preview = $state.raw<BuildTheme | null>(null);
  custom = $derived(hasCustomStyle(this.preview ?? this.build.theme));
  /** The last failure to load or save (shown as a notice in every window). */
  error = $state("");
  editorOpen = $state(false);
}

export const buildStore = new BuildStore();

/** The server's active build; `buildStore.build` adds the preferences still in flight. */
let server: Build = DEFAULT_BUILD;
const pending: PreferencePatch[] = [];
const changeListeners = new Set<(next: Build, previous: Build) => void>();

/** Call `listener` whenever the build in effect changes (a push, a load, a preference). */
export function onBuildChange(listener: (next: Build, previous: Build) => void): () => void {
  changeListeners.add(listener);
  return () => changeListeners.delete(listener);
}

let appliedLook = "";
let lookReady: Promise<void> = Promise.resolve();
function applyLook() {
  applyAppearance(appearanceOf(buildStore.build.ui.appearance));
  const look = buildStore.preview ?? buildStore.build.theme;
  const key = JSON.stringify([look, buildStore.safe]);
  if (key === appliedLook) return;
  appliedLook = key;
  lookReady = applyBuildLook(look, buildStore.safe);
}

/** Settles once the look in effect has its theme's motif module (await before the first paint). */
export function lookApplied(): Promise<void> {
  return lookReady;
}

function effective(next: Build) {
  const previous = buildStore.build;
  // Every key present, so readers need no fallbacks (a server build always is; this guards drafts).
  next = deepMerge(DEFAULT_BUILD, next);
  buildStore.build = next;
  applyLook();
  for (const listener of [...changeListeners]) listener(next, previous);
}

function adopt(snapshot: BuildSnapshot) {
  buildStore.revision = snapshot.revision;
  buildStore.activeRevision = snapshot.active_revision;
  buildStore.text = snapshot.text;
  buildStore.problems = snapshot.problems;
  buildStore.invalid = snapshot.invalid;
  buildStore.restartRequired = snapshot.restart_required;
  buildStore.serverSafe = snapshot.safe;
  server = snapshot.build;
  effective(pending.reduce<Build>((build, patch) => deepMerge(build, patch), server));
}

/** Move this browser's pre-build preferences into the build once, then forget them. */
async function moveLegacy(alreadyMoved: boolean) {
  let storage: Storage;
  try { storage = localStorage; } catch { return; }
  const { patch, keys } = collectLegacy(storage);
  if (!keys.length) return;
  let moved = false;
  if (!alreadyMoved) {
    try {
      moved = (await api<{ moved: boolean }>("/build/local", patch)).moved;
    } catch (cause) {
      // Unreachable server: try again next start. Refused values are dropped.
      if (!(cause instanceof ApiError)) return;
    }
  }
  for (const key of keys) {
    try { storage.removeItem(key); } catch { /* Nothing more to do. */ }
  }
  if (moved) await reloadBuild();
}

/** Fetch the build again (a hello from a newer build, or after an install). */
export async function reloadBuild(): Promise<void> {
  const data = await api<BuildResponse>("/build");
  adopt(data);
  buildStore.history = data.history;
}

let started = false;
/**
 * Load the build before the app mounts and follow the live channel from then on.
 * Never throws: without a server answer the window runs on the default build.
 */
export async function loadBuild(): Promise<void> {
  buildStore.urlSafe = hasSafeParam(location.search);
  if (!started) {
    started = true;
    onLive("build", (message) => adopt(message as unknown as BuildSnapshot));
    onLive("hello", (message: LiveMessage) => {
      if (typeof message.build === "string" && message.build !== buildStore.activeRevision)
        reloadBuild().catch((cause) => { buildStore.error = `Could not load the build: ${(cause as Error).message}`; });
    });
  }
  try {
    const data = await api<BuildResponse>("/build");
    adopt(data);
    buildStore.history = data.history;
    buildStore.error = "";
    await moveLegacy(data.local_preferences_moved);
  } catch (cause) {
    buildStore.error = `Could not load the build; using the default look: ${(cause as Error).message}`;
  } finally {
    buildStore.loaded = true;
    applyLook();
  }
}

/**
 * Change interface preferences (and the build's name, author or description): applied in
 * this window at once, then saved to the build file. A refused save reverts the change
 * and shows the error; resolves to whether it was saved.
 */
export async function setPreferences(patch: PreferencePatch): Promise<boolean> {
  pending.push(patch);
  effective(deepMerge(buildStore.build, patch));
  try {
    await api("/build/preferences", patch);
    pending.splice(pending.indexOf(patch), 1);
    // Saved: the file has it now, even before its push arrives (a later refusal must keep it).
    server = deepMerge(server, patch);
    return true;
  } catch (cause) {
    pending.splice(pending.indexOf(patch), 1);
    effective(pending.reduce<Build>((build, next) => deepMerge(build, next), server));
    buildStore.error = `Could not save this preference: ${(cause as Error).message}`;
    return false;
  }
}

/**
 * Choose the base theme (`theme.base`): applied at once, saved to the build; settles once the
 * save answered and the theme's motif module is in.
 */
export async function setTheme(id: ThemeId): Promise<boolean> {
  const saved = await setPreferences({ theme: { base: id } });
  await lookApplied();
  return saved;
}

/** Show the build editor's checked look in this window only (null: the active build). */
export function previewLook(look: BuildTheme | null): void {
  buildStore.preview = look;
  applyLook();
}

/** Safe mode on or off for this window (Ctrl+Alt+Shift+S); `?safe` in the URL follows. */
export function toggleSafe(): void {
  const next = !buildStore.safe;
  buildStore.safeToggle = next;
  buildStore.urlSafe = next;
  history.replaceState(history.state, "", withSafeParam(location.href, next));
  applyLook();
}
