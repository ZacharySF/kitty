/**
 * Agent skills (docs/agent-skills-design.md; served by kitty.web_agent_skills): each
 * coding agent's skills, MCP tools and plugins, names only, and what a running agent
 * uses now. Pure helpers shared by Rika's Agents cards, the bar chips, the All agent
 * skills view and the new-agent checklist.
 */
export type AgentKind = "omp" | "claude" | "codex";
export const AGENT_KINDS: AgentKind[] = ["omp", "claude", "codex"];
export type AgentSkill = { name: string; description: string; path: string };
export type NamedFlag = { name: string; enabled: boolean };
export type KindCatalog = {
  kind: AgentKind;
  label: string;
  /** "Show agent skills and activity" for this kind; off answers empty groups. */
  enabled: boolean;
  skills: AgentSkill[];
  mcp: NamedFlag[];
  plugins: NamedFlag[];
};
/** `can_open`: the desktop app's opener is there (a plain kitty-web site has none). */
export type Catalog = { kinds: KindCatalog[]; can_open: boolean };
/** What a running agent uses now; `label` is "skill · tool", or "—" when nothing answers. */
export type AgentNow = {
  skill: string | null;
  tool: string | null;
  /** omp's one-line intent (≤120 characters). */
  intent: string | null;
  via: "hook" | "extension" | "log" | null;
  label: string;
};

export const UNKNOWN = "—";

/** The now chip's text: "now: lavish · read" or "now: —". */
export const nowText = (now: AgentNow) => `now: ${now.label}`;

/** The chip's tooltip: the intent when omp gave one, and where the answer came from. */
export function nowTitle(now: AgentNow): string {
  const source = { hook: "from kitty!'s launch hook", extension: "from kitty!'s omp extension", log: "from the agent's session log" };
  return [now.intent, now.via ? `What it is using now, ${source[now.via]}` : "Nothing reported yet"].filter(Boolean).join(" · ");
}

const shellQuote = (text: string) => `'${text.replaceAll("'", "'\\''")}'`;

/**
 * The flags a skill checklist adds to one launch, as the agent reads them (the same
 * shapes kitty.core.agent_skills.launch_arguments builds; the activity hook is left out).
 */
export function launchPreview(kind: AgentKind, skills: string[], off: ReadonlySet<string>): string {
  const dropped = skills.filter((name) => off.has(name));
  if (!dropped.length) return kind;
  if (kind === "omp") {
    const on = skills.filter((name) => !off.has(name));
    return on.length ? `omp --skills ${shellQuote(on.join(","))}` : "omp --no-skills";
  }
  if (kind === "claude")
    return `claude --settings ${shellQuote(JSON.stringify({ skillOverrides: Object.fromEntries(dropped.map((name) => [name, "off"])) }))}`;
  return `codex -c ${shellQuote(`skills.config=[${dropped.map((name) => `{name=${JSON.stringify(name)},enabled=false}`).join(",")}]`)}`;
}
