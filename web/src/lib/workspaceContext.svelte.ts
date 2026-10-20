import { getContext, setContext } from "svelte";
import type { View } from "./workspace";
import type { Crumb } from "./breadcrumb.svelte";

export type WorkspaceTrail = {
  section: View | null;
  root: (() => void) | null;
  nodes: Crumb[];
};

export type WorkspaceContext = {
  readonly id: string;
  readonly active: boolean;
  readonly trail: WorkspaceTrail;
  setTrail: (section: View, nodes: Crumb[], root?: () => void) => void;
  clearTrail: (section?: View) => void;
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
