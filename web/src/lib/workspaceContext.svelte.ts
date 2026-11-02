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
