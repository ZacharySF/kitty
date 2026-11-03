# Agent skills view: design

You can see each agent's skills and what it is using right now in two places.

- **Agent cards.** Every live agent card, in Rika's history drawer and as a chip in the Rika bar, shows a "now: skill · tool" chip and a Skills (N) button.
- **"All agent skills" page.** It catalogues every agent kind's skills, MCP tools and plugins, even when no agent is running.

How it gets the information, and what you control:
- **Sources.** kitty! learns what an agent is doing from three places, in order: hooks in the agents it launches, its own omp extension, and, as a fallback, the tail of the agent's session log.
- **Privacy.** kitty! reads names only, plus omp's one-line intent. Nothing is stored or synced, and the feature is off until you enable it for each agent kind.
- **Control.** You can open a skill's file, copy its path, and switch skills on or off for a launch kitty! or Rika starts. Your own agent configs are never edited.

The design was decided in a grill-with-ui interview paired with a design lab (branch `lab/skills`, `web/skills-lab.html`). Research: `research-agent-skills-view.md` in the session, summarised under Verified facts.

## Terms

- **Agent skill**: a `SKILL.md` capability an agent can load (lavish, grill-with-ui, …). It is not the same as kitty!'s job-search skills (`config/skills.yaml`). *Avoid:* skill (unqualified).
- **Now chip**: a label on an agent card showing the skill and tool it is using right now, e.g. "now: lavish · read".

## Why

User: "I want to be able to go to agents and see what skills they have and what currently they use."

## Locked decisions

### Q3: how "using now" is detected

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Read the tail of each running agent's session log (herdr points to it) for the last tool and skill** | **good** | **mixed** | **mixed** | **good** |
| **B. Hooks in agents kitty! launches report tool and skill as herdr pane tags** | **good** | **good** | **good** | **good** |
| C. Hooks write it into the pane title | mixed | poor | good | good |
| **D. A kitty! extension for omp reports its tools, skills and intent** | **good** | **good** | **good** | **good** |

**Decision: A + B + D** (multi-select).

For agents kitty! or Rika launches, the signal comes from per-launch hooks, never installed globally:
- **Claude Code:** `--settings` hooks.
- **Codex:** `-c hooks…`.
- **omp:** the omp extension, `--extension .private/agents/kitty-activity.ts`.

These report through `herdr pane report-metadata … --token tool=… --token skill=…`. kitty! reads the tokens from the `agent list` call it already makes.

For agents you attached yourself, kitty! reads the last ≤64 KB of the session log named by herdr's `agent_session`. It extracts only the last tool name and the last skill name:

| Agent | Tool comes from | Skill comes from |
|---|---|---|
| omp | `tool_execution_start.toolName` | a read of `skill://x` |
| Claude | the last tool call | the `Skill` tool's `input.skill` |
| Codex | the last tool call | an `exec` reading `…/skills/<x>/SKILL.md` |

The first source that answers wins. When nothing answers, the chip shows "—".

Why C lost: it overwrites the agent's own task title, and Codex has no title hook.

Risks of the fallback:
- Session-log formats can change in any release; Codex calls its format unstable.
- Hooks spawn a process per tool call.

### Q4: privacy

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Names only, from a fixed list of files; in memory; never stored, synced or shown to paired apps** | **good** | **good** | **good** | **good** |
| **B. Also omp's one-line intent, capped at 120 characters** | **good** | **good** | **mixed** | **good** |
| **C. Off until enabled, per agent kind** | **mixed** | **good** | **good** | **good** |
| D. Only what herdr reports; kitty! never opens agent files | poor | good | good | good |

**Decision: A + B + C.**

kitty! reads only the following, and only after you enable that agent kind in Settings → Terminal (or Rika → Agents), "Show agent skills and activity":
- `*/SKILL.md` frontmatter (`name`, `description`) under the known skill roots;
- MCP server names and enabled flags (every other value is dropped while parsing);
- plugin names;
- session-log tails, reduced to a tool name, a skill name, and omp's intent line (≤120 characters).

It never reads credentials: `~/.claude/.credentials.json`, `~/.codex/auth.json`, omp's `agent.db`, and any `env` or `headers` values are all off limits.

Where the data goes:
- It is held in memory only.
- It is served only behind the page guard.
- It is never exposed on `/api/ingress/*` and never included in peer sync.
- A test asserts that with the option off, kitty! opens no file under `~/.omp`, `~/.claude` or `~/.codex`.

D lost because it can't list installed skills at all.

### Q5: control

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. Show only | mixed | good | good | good |
| B. Show + Open file / Copy path | good | good | good | good |
| **C. B + per-launch on/off for agents kitty! (or Rika) starts; your own configs untouched** | **good** | **good** | **good** | **good** |
| D. Edit the agents' own config files | good | mixed | poor | good |

**Decision: C.** Each skill row has **Open file** and **Copy path**. Opening uses the desktop opener, which is a small Tauri command.

The new-agent popover, and Rika's `start_worker` tool, take a skill checklist that becomes launch arguments:

| Agent | How the checklist is passed |
|---|---|
| omp | `--skills` |
| Claude | `--settings '{"skillOverrides":{…:"off"}}'` |
| Codex | `-c skills.config=[…]` (verify the TOML-array syntax before shipping) |

User files are never written. D lost because it breaks kitty!'s locked rule against changing agent config, and because round-tripping YAML, JSON and TOML risks the user's comments and settings.

## Routine choices

- **Q1, where it lives:** D.
  - Each live agent card gets a now chip and a **Skills (N)** button. The cards sit in Rika's history drawer Agents section, and the compact chip sits in the Rika bar, per Rika Q19.
  - A full **All agent skills** page sits under Plan, beside Tern. It covers every agent kind, works with no agent running, and is linked from each card.
  - Both read one backend source.
- **Q2, what counts:** B. Skills, MCP tools and plugins, names only, in three groups per agent kind: Skills, Tools (MCP), Plugins. Each row shows name and description. The global/workspace/plugin/built-in/synced split (option C) was not chosen. A row may still show its path via Copy path.

## Verified facts

- All three agents use the Agent Skills layout: `<root>/<name>/SKILL.md` with YAML `name`/`description` ([agentskills.io/specification](https://agentskills.io/specification)).
- None of the three agents has one command that lists every skill it loads.
- omp merges nine sources, including Claude's and Codex's folders.
- On this machine:
  - omp exposes 9 skills;
  - no MCP servers are configured for any agent;
  - herdr's hooks are installed for omp, Claude and Codex.
- herdr's `agent list` carries `agent_session`: a path for Claude and omp, an id for Codex. kitty! polls the list every 3 s in `HerdrRuntime.activity()` (`src/kitty/core/terminal.py`) but currently drops this field.
- herdr supports `pane report-metadata` key/value tokens, which come back in the same list.
- `claude mcp list` spawns servers to health-check them, so kitty! parses config files instead of running the CLIs.

## Risks

- **Format drift.** Transcript and discovery formats change between agent releases. *Mitigation:* the hooks and the omp extension are the primary sources; log parsing is the fallback and shows "—" on anything it doesn't recognise; each agent kind has its own parser tests.
- **Hook latency.** A process spawn per tool call is added. *Mitigation:* tokens are rate-limited with a TTL.
- **Codex trust.** Codex skips new hooks until the user trusts them in `/hooks`. *Mitigation:* fall back to the log tail and tell the user once.
- **Herdr integration missing elsewhere.** The herdr integrations are the user's own global install and may be absent on other machines. *Mitigation:* fall back to the newest session file for the pane's working directory.

## Deferred

None.

## Open threads

- **Build after Rika phase 1**, which moves the agent cards into the history drawer:
  1. Backend `src/kitty/core/agent_skills.py`, pure and read-only: `installed(kind, workspace)` and `now(agent)`.
  2. Hooks and the omp extension in `.private/agents/`.
  3. The card chip and Skills panel.
  4. The All agent skills page.
  5. Per-launch checklists.
- **Verify before shipping:**
  - Codex `-c` array syntax;
  - whether an omp extension can import `discoverSkills()`;
  - herdr token value length.
