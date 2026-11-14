import { getContext, setContext } from "svelte";
import type { View } from "./workspace";
import type { Crumb } from "./breadcrumb.svelte";
import type { SaveState } from "./ui/StatusDot.svelte";

export type WorkspaceTrail = {
  section: View | null;
  root: (() => void) | null;
  nodes: Crumb[];
};

/** Q3: a view's save state, shown as a dot on its workspace tab instead of status text. */
export type SaveStatus = { state: SaveState; detail?: string };

export type WorkspaceContext = {
  readonly id: string;
  readonly active: boolean;
  readonly trail: WorkspaceTrail;
  setTrail: (section: View, nodes: Crumb[], root?: () => void) => void;
  clearTrail: (section?: View) => void;
  readonly status: SaveStatus | null;
  /** Reports the view's save state to its tab; `null` when it has nothing to save. */
  setStatus: (status: SaveStatus | null) => void;
  /** One tab system: a Notes tab shows one page. The page this tab was opened on, if any. */
  readonly notePage: number | null;
  /** Notes reports the page it shows (and its title, which names the tab). */
  setNotePage: (id: number | null, title: string) => void;
  /** Another tab already showing page `id` takes focus; true when one did. */
  focusNotePage: (id: number) => boolean;
  /** Opens page `id` in a new app tab. */
  openNotePageTab: (id: number) => void;
};

const workspaceKey = Symbol("kitty.workspace-tab");

export function provideWorkspaceContext(context: WorkspaceContext): void {
  setContext(workspaceKey, context);
}

export function useWorkspaceContext(): WorkspaceContext {
  const context = getContext<WorkspaceContext | undefined>(workspaceKey);
  if (!context) throw new Error("Workspace views require a tab context");
  return context;
}
