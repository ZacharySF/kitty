import { api } from "./api";
import type { AgentKind, Catalog, KindCatalog } from "./agentSkills";

const BASE = "/terminal/agent-skills";

/**
 * The page's one copy of the agent skills catalogue (GET /api/terminal/agent-skills),
 * shared by Rika's Agents cards, the All agent skills view and the new-agent checklist.
 * The server reads nothing for a kind until its "Show agent skills and activity" is on.
 */
class AgentSkillsStore {
  catalog = $state<Catalog | null>(null);
  error = $state("");
  /** The last Open file / Copy path outcome, announced in a status line. */
  status = $state("");
  /** The kind the All agent skills view shows; a card's link sets its agent's kind. */
  focus = $state<AgentKind>("omp");
  private loading: Promise<void> | null = null;

  kind(kind: string): KindCatalog | undefined {
    return this.catalog?.kinds.find((item) => item.kind === kind);
  }

  /** Loads once; `fresh` reads the folders again (the server caches a few seconds). */
  load(fresh = false): Promise<void> {
    if (this.loading) return this.loading;
    if (this.catalog && !fresh) return Promise.resolve();
    this.loading = api<Catalog>(BASE)
      .then((catalog) => {
        this.catalog = catalog;
        this.error = "";
      })
      .catch((cause) => {
        this.error = (cause as Error).message;
      })
      .finally(() => {
        this.loading = null;
      });
    return this.loading;
  }

  async setEnabled(kind: AgentKind, enabled: boolean) {
    try {
      this.catalog = await api<Catalog>(`${BASE}/settings`, { kind, enabled });
      this.error = "";
    } catch (cause) {
      this.error = (cause as Error).message;
    }
  }

  async open(kind: AgentKind, path: string) {
    try {
      await api(`${BASE}/open`, { kind, path });
      this.status = `Opened ${path}`;
    } catch (cause) {
      this.status = (cause as Error).message;
    }
  }

  async copy(path: string) {
    try {
      await navigator.clipboard.writeText(path);
      this.status = `Copied ${path}`;
    } catch {
      this.status = `Could not copy; the path is ${path}`;
    }
  }
}

export const agentSkills = new AgentSkillsStore();
