import { api, ApiError, type ModuleId, type ModulesState, type PackInfo, type PacksState } from "./api";
import { packReady, totals } from "./modulePicker";

const POLL_MS = 500;

/**
 * The page's one copy of the modules (GET /api/modules: names bundled with this build) and
 * their packs (GET /api/packs: sizes from this release's signed list, cached by the server;
 * offline it answers from that cache). The first-run picker, Settings → Modules and a home
 * template's offers all read and change it here, so a download shows the same everywhere.
 */
class ModuleStore {
  modules = $state<ModulesState | null>(null);
  packs = $state<PacksState | null>(null);
  error = $state("");
  packsError = $state("");
  /** What `apply` is doing. */
  phase = $state<"" | "downloading" | "saving">("");
  /** `apply`'s downloads as one measure: the pack arriving now, bytes so far of them all. */
  progress = $state<{ label: string; received: number; total: number } | null>(null);
  /** The add-on module (owner/repo or name) Settings is downloading and checking now; "" when none. */
  addon = $state("");
  /** Whether the desktop app is on the other end (it can restart itself); null until asked. */
  desktop = $state<boolean | null>(null);
  restarting = $state(false);

  get busy() {
    return this.phase !== "";
  }
  get downloading() {
    return !!this.packs?.packs.some((pack) => pack.state === "downloading");
  }

  async load(): Promise<void> {
    try {
      this.modules = await api<ModulesState>("/modules");
      this.error = "";
    } catch (cause) {
      this.error = (cause as Error).message;
    }
    if (this.desktop === null)
      this.desktop = await api<{ available: boolean }>("/desktop").then((state) => state.available, () => null);
  }

  async loadPacks(): Promise<void> {
    try {
      this.packs = await api<PacksState>("/packs");
      this.packsError = "";
    } catch (cause) {
      this.packsError = (cause as Error).message;
    }
  }

  /** Remove one installed pack (its module is off). */
  async remove(id: string): Promise<void> {
    try {
      this.packs = await api<PacksState>("/packs/uninstall", { id });
      this.packsError = "";
    } catch (cause) {
      this.packsError = (cause as Error).message;
    }
  }

  private async download(pack: PackInfo, before: number, total: number): Promise<void> {
    this.progress = { label: pack.label, received: before, total };
    this.packs = await api<PacksState>("/packs/install", { id: pack.id });
    for (;;) {
      const now = this.packs.packs.find((entry) => entry.id === pack.id);
      if (packReady(now)) return;
      if (now?.state !== "downloading") throw new Error(`${pack.label}: ${now?.error ?? "the download stopped"}`);
      this.progress = { label: pack.label, received: before + (now.progress?.received ?? 0), total };
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      this.packs = await api<PacksState>("/packs");
    }
  }

  /**
   * Make `selection` the saved one: download each pack it lacks, one at a time, then save.
   * Nothing is saved when a download fails; what already arrived stays installed.
   */
  async apply(selection: ModuleId[]): Promise<ModulesState> {
    if (this.phase) throw new Error("Modules are already being saved");
    this.phase = "downloading";
    try {
      const modules = this.modules ?? (await api<ModulesState>("/modules"));
      this.packs = await api<PacksState>("/packs");
      const { downloads, bytes } = totals(modules.available, selection, this.packs);
      let done = 0;
      for (const pack of downloads) {
        await this.download(pack, done, bytes);
        done += pack.size ?? 0;
      }
      this.progress = null;
      this.phase = "saving";
      this.modules = await api<ModulesState>("/modules", { enabled: selection });
      this.error = "";
      return this.modules;
    } finally {
      this.phase = "";
      this.progress = null;
    }
  }

  /**
   * Have one pack that belongs to no worker module (Themes, Animations): download it unless
   * it is here already. True when it can be used; a failure is left in `packsError`.
   */
  async fetch(id: string): Promise<boolean> {
    if (this.phase) return false;
    this.phase = "downloading";
    try {
      this.packs = await api<PacksState>("/packs");
      const pack = this.packs.packs.find((entry) => entry.id === id);
      if (!pack || packReady(pack)) return true;
      if (pack.state === "unavailable") throw new Error(this.packs.index.error ?? `${pack.label}'s download is not available`);
      await this.download(pack, 0, pack.size ?? 0);
      this.packsError = "";
      return true;
    } catch (cause) {
      this.packsError = (cause as Error).message;
      return false;
    } finally {
      this.phase = "";
      this.progress = null;
    }
  }

  /** Turn one more module on, keeping the rest of the saved selection. */
  async enable(id: ModuleId): Promise<ModulesState> {
    const modules = this.modules ?? (this.modules = await api<ModulesState>("/modules"));
    return this.apply(modules.available.map((item) => item.id).filter((item) => item === id || modules.configured.includes(item)));
  }

  /**
   * Ask the desktop app to save, stop and reopen. False when it cannot (a plain browser):
   * the caller then says "Restart rika! to finish".
   */
  async restart(): Promise<boolean> {
    try {
      await api("/desktop/command", { command: "restart" });
      this.restarting = true;
      return true;
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) this.desktop = false;
      else this.error = (cause as Error).message;
      return false;
    }
  }
}

export const moduleStore = new ModuleStore();
