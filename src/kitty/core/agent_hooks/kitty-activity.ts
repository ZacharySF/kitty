// kitty! activity extension for omp (docs/agent-skills-design.md Q3 D).
// kitty! loads it per launch only (`omp --extension .private/agents/kitty-activity.ts`);
// it is never installed in ~/.omp. On each tool call it reports the tool's name, and the
// skill's name when the tool reads `skill://<name>`, as display-only tokens on this
// herdr pane (`herdr pane report-metadata … --token kitty-tool=… --token kitty-skill=…`),
// and writes omp's one-line intent (at most 120 characters; herdr token values hold 80)
// to activity/<pane>.json beside this file. Nothing else leaves the session, and any
// failure is silent: omp never waits on kitty!.

import { spawn } from "node:child_process";
import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** The fields of omp's `tool_execution_start` extension event this reads. */
type ToolStart = { toolName?: unknown; args?: unknown; intent?: unknown };
type ExtensionAPI = { on(event: "tool_execution_start", handler: (event: ToolStart) => void): void };

const pane = process.env.HERDR_PANE_ID;
const herdr = process.env.HERDR_BIN_PATH || "herdr";
// A nested omp started from this session's shell inherits the pane; only the root reports.
const nested = process.env.OMPCODE === "1";
const folder = path.join(path.dirname(fileURLToPath(import.meta.url)), "activity");
const report = path.join(folder, `${(pane || "").replace(/[^\w.-]/g, "_")}.json`);
const MIN_INTERVAL_MS = 2000;

/** One short display name: herdr token values hold 80 characters. */
function clean(value: unknown): string {
  return typeof value === "string" ? value.trim().replace(/[^\w.:@/+-]+/g, "").slice(0, 80) : "";
}

export default function (pi: ExtensionAPI) {
  if (!pane || nested) return;
  let skill = "";
  let last = "";
  let lastAt = 0;

  pi.on("tool_execution_start", (event) => {
    const tool = clean(event?.toolName);
    if (!tool) return;
    const target = event.args && typeof event.args === "object" && "path" in event.args ? event.args.path : undefined;
    const used = typeof target === "string" && target.startsWith("skill://") ? clean(target.slice(8).split("/")[0].split(":")[0]) : "";
    if (used) skill = used;
    const intent = typeof event.intent === "string" ? event.intent.split(/\s+/).filter(Boolean).join(" ").slice(0, 120) : "";
    try {
      mkdirSync(folder, { recursive: true, mode: 0o700 });
      const temporary = `${report}.${process.pid}.tmp`;
      writeFileSync(temporary, JSON.stringify({ tool, skill, intent, at: Date.now() }), { mode: 0o600 });
      renameSync(temporary, report);
    } catch {
      // The herdr tokens below still carry the tool and skill.
    }
    const stamp = `${tool}\t${used}`;
    if (stamp === last && Date.now() - lastAt < MIN_INTERVAL_MS) return;
    last = stamp;
    lastAt = Date.now();
    const args = ["pane", "report-metadata", pane, "--source", "kitty:activity", "--token", `kitty-tool=${tool}`, "--ttl-ms", "600000"];
    if (used) args.push("--token", `kitty-skill=${used}`);
    try {
      const child = spawn(herdr, args, { stdio: "ignore" });
      child.on("error", () => {});
      child.unref();
    } catch {
      // No herdr to report to: kitty! falls back to the session log.
    }
  });
}
