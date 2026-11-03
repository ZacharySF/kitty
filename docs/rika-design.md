# Rika: the agent that runs rika!

Rika is rika!'s own consumer agent, modelled on Meta's Muse (September 2026: a personal agent that acts across your apps, has a real-time voice mode, and asks you to approve critical actions). You type to it or talk to it. It operates every part of the app: canvas, notes, nodes, mind maps, flashcards, calendar, study, job search, email, settings. It conducts the other agents: it starts omp, Claude Code and Codex in herdr panes and Tern windows, hands them work, keeps them unblocked and reports back.

What it does for you:
- It takes ten-minute brain dumps and turns each into one reviewed plan.
- It works toward goals you set.
- It drafts email.
- It routes every request to the smallest model tier that can handle it, and learns over time to spend fewer tokens.

Rika lives in the **Rika bar**, which replaces the old herdr dock at the bottom of the window. The design was decided in a grill-with-ui interview paired with a design lab (branch `lab/rika`, `web/rika-lab.html`). Every question has a card there showing the "today" state and every option.

## Terms

- **Rika**: the app's own consumer agent you talk or type to; it operates every part of rika! and directs other agents. *Avoid:* assistant, first mate, copilot.
- **Rika bar**: the bar at the bottom of the window where the herdr dock handle used to be; you type or hold Space there to talk to Rika. *Avoid:* dock, prompt box.
- **Worker agent**: a coding or task agent Rika starts and directs, namely omp, Claude Code or Codex in a herdr pane or Tern window. *Avoid:* subagent.
- **Brain dump**: a long spoken or typed ramble that Rika turns into one plan of actions at the end. *Avoid:* voice note.
- **Tier**: a model size class Rika routes to:
  - **fast**: the cheapest tier, with the lowest latency;
  - **standard**;
  - **deep**: the strongest and most expensive tier.
- **History drawer**: the side drawer holding Rika's conversation, plans, approvals and the Agents section.

## Why

In the user's words:
- "I want kitty to kind of be like Muse. Specifically I want a rika agent (just called rika) and this manages everything: it will pull things from canvas, pull nodes, add things, basically manage the whole app, and this runs herdr and tern sessions. This should be a consumer agent you talk to that runs the app."
- "Click the bottom where the current herdr lives and this be replaced by rika. If you hold down space in the type bar you can speech to text to rika and an animation plays showing that you're talking."
- "Rika can manage all the agents and know everything."
- "I want rika to know when to use smaller agents and faster ones, and rika itself talk to speech is the quickest version of whatever OpenRouter model you are using, but it thinks and does things according to what is needed. This should be trained to what uses less tokens but use the standard and higher ones when needed."
- "If you tell rika, for example, to add this node, it gets on to adding a node."
- "College students brain dump their stuff into Claude using speech to text… they will talk to Claude for 10 minutes then one shot it. I want rika to be this but with everything it already has and better."
- "I want goals in kitty that you set, and it can always look for things for you to match your goals, and it can reply to emails and whatnot, but be on top of everything and manage omp sessions and developer-side things too."
- Rika is to be designed fresh; the firstmate skill was not used.

## Locked decisions

### Q1 (Rika bar): Rika replaces the dock entirely; terminals open as Tern windows

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. Rika bar replaces the dock handle; its panel has tabs Rika · Terminal · Agents | good | good | good | good |
| B. Rika bar above the dock handle; terminal dock stays separate | mixed | good | good | good |
| **C. Rika replaces the dock entirely; terminals open only as Tern windows or panes Rika starts** | **mixed** | **mixed** | **mixed** | **good** |

**Decision: C** (the user chose it over the recommended A).
- The bottom of the window becomes the Rika bar.
- The herdr terminal dock is removed: the docked panel, its S/M/L heights, Float, and its Terminal and Agents tabs.
- Terminals open as Tern windows, or as herdr panes surfaced through Tern windows, that Rika (or the user, through Rika or Ctrl K) starts.
- The herdr runtime stays as Rika's backend for worker agents.
- Live agents move to the Rika bar and the history drawer (Q19).

UX and correctness are mixed because a terminal is no longer one click inside the main window. Tern windows are native windows on top, so they need focus management and need to come back when the app is reopened.

- **A lost:** the user wants Rika to own the bottom outright.
- **B lost:** it keeps two bottom surfaces.

Migration: the v0.3.25 dock state (`kitty.dock`) is retired. Clean cutover, no shim.

### Q5 (Voice): on-device Whisper

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. On-device Whisper (bundled, offline, private; about 1 s for a short command)** | **good** | **good** | **good** | **good** |
| B. Cloud transcription via OpenRouter | good | good | mixed | mixed |
| C. On-device by default, cloud for long dumps if allowed | good | mixed | good | good |

**Decision: A.**
- **Why an engine of its own:** the desktop webview (WebKitGTK) has no Web Speech API, so rika! ships its own engine.
- **Engine:** whisper.cpp (or faster-whisper), run by the Python backend or the Tauri shell, with a small multilingual model downloaded on first use.
- **Streaming:** audio is transcribed in chunks, so a ten-minute dump transcribes as you speak.
- **Privacy:** audio never leaves the device.
- **Discussion:** the user asked that no Space keycap or hold hint be drawn in the bar. Holding Space just starts listening, and the breathing bar (Q4) is the only signal. Shortcut help still lists it.

B lost on privacy and per-minute cost. C lost because one engine is simpler and A's accuracy is enough.

### Q9 (Actions): undoable in-app changes happen; outward or irreversible ones wait for approval

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Reversible in-app changes apply at once with undo; anything outward or irreversible asks first** | **good** | **good** | **good** | **good** |
| B. Asks before every change | poor | good | good | good |
| C. Does everything, daily summary | good | mixed | poor | good |

**Decision: A**, the line Muse draws.

Applied at once, with Undo:
- edits to notes, blocks, canvas, nodes and mind maps;
- cards, tasks, calendar entries, goals;
- navigation and layout.

These wait for one tap in the history drawer (Q20):
- sending email;
- deleting anything not restorable from Trash;
- spending money (paid model runs above the budget, job applications that submit);
- running shell commands;
- starting a worker agent;
- publishing or pushing;
- changing settings or modules.

Approvals reuse kitty!'s existing review → approve → budget flow (`web_ai.py`). B lost as unusable. C lost because a wrong email or deletion can't be undone.

### Q11 (Agents): Rika conducts worker agents

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Conductor: starts worker agents in herdr panes or Tern windows, hands them tasks, watches status, answers or escalates their questions, reports back** | **good** | **good** | **mixed** | **mixed** |
| B. Observer only | mixed | good | good | good |
| C. Rika codes itself | mixed | poor | mixed | poor |

**Decision: A.**
- **Starting:** Rika launches omp, Claude Code or Codex through the existing `start_agent` / Tern integration.
- **Handing off:** it writes a task brief, sends it to the pane, and polls herdr activity (state, task, last line, session pointer).
- **Unblocking:**
  - Simple, safe questions from a blocked agent: Rika answers them itself.
  - Decisions with stakes: Rika escalates to the user.
- **Reporting:** it summarises results back into the conversation.

Starting an agent counts as an outward action under Q9. Risk and cost are mixed because workers run commands and spend tokens, which the approval and budgets cover.

### Q12 (Models): a tiered router

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. fast tier for talk, simple edits, classification; standard for planning; deep for hard or long tasks; escalate on failure or low confidence** | **good** | **good** | **good** | **good** |
| B. One model for everything | mixed | good | good | poor |
| C. User picks per request | poor | good | good | mixed |

**Decision: A.** Tiers map to OpenRouter models chosen in Settings → AI, with defaults set per provider family.
- **Voice and talking** always use the fast tier, the "quickest version" the user asked for.
- **Planning a brain dump** uses standard.
- **Multi-step work across many tools, or failed attempts,** use deep.

Every reply carries a tier chip, and the user can override a tier per request. Escalation happens when a tool call fails validation, the plan is incomplete, or the model's own confidence is low.

### Q16 (Inbox): drafts, plus auto-send for kinds you approve

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. Drafts only; one-tap send | good | good | good | good |
| **B. Drafts by default; auto-send kinds of reply you've enabled (scheduling, confirmations)** | **good** | **good** | **mixed** | **good** |
| C. Read and summarise only | poor | good | good | good |

**Decision: B.**
- Rika drafts replies in the user's voice through Gmail, using the existing OAuth.
- Auto-send is off for every kind until the user turns it on for that kind, in Settings → Rika → Email.
- Each auto-sent mail is logged in the history drawer with Undo-send, where Gmail's delay allows.

Risk is mixed because a wrong auto-reply can't be unsent.

### Q17 (Engine): Rika runs inside kitty!'s backend

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Inside kitty!'s Python backend: a tool-calling loop over kitty!'s own APIs through OpenRouter, with worker agents for coding** | **good** | **good** | **good** | **good** |
| B. An omp session with a kitty! extension | mixed | mixed | mixed | mixed |
| C. A separate service process | mixed | good | mixed | good |

**Decision: A.** A new `kitty.core.rika` package holds the agent loop, router, tools, memory and approvals. It is exposed through `/api/rika/*` plus a WebSocket for streaming replies and action events.
- **Tools:** thin wrappers over existing core functions (blocks, schema_maps, flashcards, calendar, study, opportunities, jobs, terminal/tern, Gmail).
- **Deterministic actions** run as direct tool calls with no model.
- **Reused as-is:** kitty!'s OpenRouter key, budgets and approvals.

B lost because it makes a consumer agent depend on a terminal tool. C lost because it duplicates auth and data access.

## Routine choices

- **Q2, where conversations show:** D. Replies appear as toasts above the bar, and the history drawer holds the full conversation. The bar never grows into a sheet.
- **Q3, how to start talking:** A. Hold Space in the Rika bar (held past about 0.3 s; a tap types a space). The global shortcut, hands-free toggle and wake word were not chosen.
- **Q4, listening animation:** C. The whole bar breathes with a soft ring of light while listening. There is no Space keycap or hint (Q5 discussion).
- **Q6, spoken replies:** C. Rika speaks only when you spoke to it, and only a short summary. Text-to-speech runs on the fast tier or locally.
- **Q7, brain dumps:** A. A live transcript while you talk. At the end, Rika produces one plan grouped by area (notes, tasks, calendar, cards, jobs, agents) that you approve all of, or tick items from. Rika may ask one question inside the plan if something is ambiguous.
- **Q8, keeping dumps:** A. Each dump is saved as a note: the transcript plus links to everything it created.
- **Q10, what Rika operates:** all of it, A–F:
  - workspace content;
  - the interface;
  - job search;
  - terminals and worker agents;
  - email;
  - app settings and modules.

  The risky areas go through Q9's approvals.
- **Q13, token savings:** A. A local routing memory records, per kind of request, the cheapest tier and shortest tool path that worked. Other savings:
  - deterministic actions skip the model;
  - prompts are cached;
  - tokens and cost are shown per request.

  There is no fine-tuning.
- **Q14, goals:** A. Goals are first-class records (title, why, target date, measures). They link to the notes, tasks, jobs and study sessions that move them, and Rika tracks progress.
- **Q15, proactivity:** A. A daily morning brief in the bar and on home. Instant notifications only for strong, time-sensitive matches. Quiet hours are respected.
- **Q18, memory:** A. A local search index over blocks, edit history, notes, jobs, calendar and emails, plus a preference memory the user can view and edit. Rika fetches only what each request needs.
- **Q19, running agents:** A and B. A chip per running agent in the Rika bar (state dot and name; past four they collapse to a count; clicking one opens its Tern window), plus the full live cards in the history drawer's Agents section. The Agent skills view (separate interview) builds on these cards.
- **Q20, plans and approvals:** A. The history drawer opens by itself for brain-dump plans and outward-action approvals, then closes when you're done.

## Verified facts

- Meta Muse (2026-09): a personal agent that acts across your apps, with real-time voice and approval of critical actions ([Meta newsroom](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent), [ai.meta.com/muse](https://ai.meta.com/muse)).
- kitty!'s AI layer is OpenRouter-based: the key is in `.private/openrouter-key` or `OPENROUTER_API_KEY`, with budgets, `ai_reviews` approvals and price ceilings (`src/kitty/web_ai.py`, `src/kitty/core/triage/openrouter.py`, `config.py`).
- The Tauri webview (WebKitGTK) has no Web Speech API, so speech-to-text needs its own engine.
- herdr's `agent list` carries each agent's session pointer and status, and kitty! polls it every 3 s (`HerdrRuntime.activity()` in `src/kitty/core/terminal.py`). Tern panes come from `tern inspect --json` (`src/kitty/core/tern.py`).
- Tern runs as systemd user services outside the NixOS AppImage sandbox (v0.3.23), so panes see the user's tools.
- Gmail OAuth already exists for job-status tracking.

## Risks

- **Removing the dock (Q1 C):** users lose the in-window terminal. *Mitigation:* Tern windows open fast from the bar or from Ctrl K, agent chips show state at a glance, and the herdr runtime keeps running underneath.
- **Voice model download and CPU (Q5):** *Mitigation:* download the model on first use with progress, pick a small model by default, and let Settings choose a larger one.
- **Wrong actions from mishearing:** *Mitigation:* reversible changes come with Undo, outward ones need approval (Q9), and brain dumps go through plan review (Q7).
- **Auto-send email (Q16 B):** a mistaken reply can't be unsent. *Mitigation:* auto-send is off per kind by default, every send is logged, and Undo-send is offered within Gmail's delay.
- **Cost and runaway workers (Q11, Q12):** *Mitigation:* the existing budgets and daily caps, approval before starting a worker, per-request cost readouts, and escalation limits.
- **Prompt injection from emails, web pages and notes:** content Rika reads is treated as untrusted data, as kitty!'s AI prompts already do. Tool calls that leave the app always need approval, whatever the content says.
- **Privacy:** the memory index and transcripts stay local, and nothing is synced unless the user opts in.

## Deferred

None.

## Open threads

- **Build order:**
  1. The Rika backend: loop, router, a core tool set, approvals and the WebSocket.
  2. The Rika bar with toasts and the history drawer, and the dock removal.
  3. Voice: Whisper and hold-Space.
  4. Brain dumps and plans.
  5. Worker-agent conducting, with agent chips.
  6. Goals and the morning brief.
  7. Email drafts and auto-send.
  8. Routing memory and cost readouts.
- **Related interviews still open:**
  - The math extension, where Rika may act as a voice mock interviewer.
  - The Agent skills view, which adds "now using" and skills to the Agents cards Q19 places.
