# kitty spec

Where the addendum at the bottom conflicts with Part 1 or Part 3, the addendum wins.

## Part 1 - Hard rules (apply to every stage)

1. Never submit an application. The tool opens pages; I submit.
2. Never invent answers, resume content, or facts about me. The approved local resume editor may make evidence-backed candidate edits, with protected facts and PDF validation. Only explicit review and promotion may change a future default; preserve historical versions and open applications. Triage remains short evidence-linked notes, never generated resume text.
3. Screening answers only ever come from config/answers.yaml, which I write.
4. Respect rate limits, cache aggressively, polite over fast. LinkedIn off by default.
5. Simple readable code I can understand. Small functions, type hints, docstrings that say what and why. No clever metaprogramming.
6. Core library is UI-free (no textual/typer imports in core). TUI, CLI and MCP are thin layers over shared behavior.
7. SQLite is the local source of truth. Secrets, runtime YAML, watchlists, resumes, discovered jobs and application history are gitignored; commit only generic .example files and synthetic fixtures.
8. Complete a requested development task through implementation and isolated verification without per-stage approval pauses. Ask for material unresolved choices or genuinely risky actions lacking explicit authorization; report exercised results. See the development-autonomy addendum.

## Part 2 - Stage 1

Stage 1: done.

## Part 3 - Full spec (Stages 2-7)

### TRACKS (tracks.yaml, editable)
- Tracks are user-configurable; examples include quant, gpu, cpu (architecture / RTL / ASIC / FPGA / validation), embedded, software, power, research and parttime. Part-time geography and pay rules belong in local configuration; rules-only tracks never call an LLM.
- Each track: search_terms, sources, location_rules, eligibility_rules, default_resume, triage_depth (none|rules|llm), weekly_target.
- A job can match multiple tracks.

### TIERS
- A = dream / strong fit, careful review. B = good fit, goes to the fast apply queue. C = filtered but kept and visible, always with a reason string.
- Goal is volume without chasing perfect companies. Weekly applied vs target per track on the Dashboard.

### DISCOVERY (Stage 2)
- Adapter registry: each source is one module implementing fetch(since) -> AdapterResult(jobs: list[NormalizedJob], status: ok|partial|error|rate_limited, error: str|None, stats). A failing adapter logs a clear error, updates the sources table, and never stops the run. A rate-limited or erroring source must never look like "0 jobs".
- TUI Sources screen: each source's last_run, last_success, last_status, last_count, last_error; key to run one source; fragile adapters flagged.
- Per-source pacing from sources.yaml, httpx with timeouts and retries on transport errors only (never auto-retry a 429; back off and report it). Cache raw responses on disk with TTL.
- Sources and specifics:
  * SimplifyJobs/Summer2027-Internships: parse .github/scripts/listings.json on the dev branch, not the README. Known fields: id (UUID - use as source_job_id), company_name, title, locations, url, date_posted, date_updated (unix), terms, active, is_visible, source (contributor username, not ATS), company_url, category, sponsorship, degrees. active=false -> mark job closed. Use degrees for the grad-degree filter. Normalize category variants through a mapping dict. Unknown fields: log once, don't fail. Test fixtures must be synthetic, not copies of saved postings.
  * northwesternfintech/2027QuantInternships: inspect its format first. Keep per-firm notes; set school_selective + note when notes say things like "extremely school-selective" or "MIT only".
  * Greenhouse: boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true (public, no auth).
  * Lever, SmartRecruiters: public posting endpoints.
  * Ashby: GET api.ashbyhq.com/posting-api/job-board/<name>?includeCompensation=true. Use workplaceType, not isRemote. A board that returns 0 jobs after previously returning jobs = warning status.
  * Workday (fragile): POST https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs with JSON {"appliedFacets":{}, "limit":20, "offset":0, "searchText":"intern"}; paginate by offset; details via GET .../job/<externalPath>. Always send searchText (Workday paginates to at most 2,000 results and returns empty pages past that; limit maxes at 20 and larger values return HTTP 400; only the first page carries total, later pages return 0). 1-2 s between requests. Respect robots.txt.
  * JobSpy (python-jobspy): Indeed, Google, ZipRecruiter, Glassdoor. LinkedIn off by default. Conservative results_wanted, our own delay between calls, cache. Indeed allows only one of hours_old / job_type+is_remote / easy_apply per search; Google needs a google_search_term written like a Google Jobs query.
  * USAJOBS: GET https://data.usajobs.gov/api/search with headers Host: data.usajobs.gov, User-Agent: <configured email>, Authorization-Key: <key from .env>. Focus on internships / Pathways / student programs.
  * Hacker News "Who is hiring" monthly threads via the HN Algolia API.
  * Manual import: paste URL or JD text (for Handshake and other portals with no API).
- Dedupe across sources with dedupe_key; record every source in job_sources.
- Stale/closed detection: a job not seen by any of its sources for N runs (config) or with active=false gets closed_at and drops out of queues (still visible in "All jobs").
- opens_at: for watchlist companies, estimate from when their postings first appeared last cycle (stored history), show "opens soon" in Inbox. Per-company expected open months can be overridden in watchlist.yaml (quant roles often open in July).
- First sync of each source: print a summary (N new, by track, by eligibility) instead of flooding; the Inbox opens on "last 14 days" view; everything is still in "All jobs".
- Build order inside Stage 2:
  * Checkpoint A: Simplify + NUFT + watchlist ATS adapters (Greenhouse, Lever, Ashby, SmartRecruiters, Workday), dedupe, track routing, eligibility filter, Inbox and Sources screens. Run a real discover and show: new jobs by source, by track, by eligibility, and the top 10 filtered-out reasons with one example posting each.
  * Checkpoint B: JobSpy, USAJOBS, HN, one at a time, each with a fixture test.
  * Verify each checkpoint and report applications by source once there's data. Commit/push only when explicitly authorized, after the full pytest suite passes.
- Inbox screen: keyboard triage with batch keys - space to multi-select, then set tier / track / skip on all selected. Skipped jobs set queue_state, never create application rows.

### ELIGIBILITY (Stage 2, rules only, runs before any LLM call)
- Evaluate configured standing, graduation-year and degree restrictions against published qualifications. Flag eligible standing explicitly; ambiguous cases remain ambiguous. Every decision stores a reason with the matched phrase. Do not bake one applicant's education into repository-wide rules.

### TRIAGE (Stage 3, Anthropic API)
- Key in .env, model configurable in config. Cached in llm_cache by (job_id, kind, model, input_hash).
- Before any run: print job count, estimated input/output tokens and dollar cost, and ask to confirm. Hard per-run cost cap in config; stop cleanly when reached and record it in runs.stats_json.
- Put resume descriptions + rules in one system block marked for prompt caching (1-hour TTL for batch runs). triage --batch uses the Message Batches API (50% cheaper, async); triage --now for small interactive runs.
- Output (validated JSON): tier, pick one resume with a one-sentence reason, and specific edit suggestions tied to exact JD phrases. Never resume text.
- parttime track: rules only, never calls the LLM.

### APPLY SESSION + DAILY FLOW (Stage 4)
- Apply Session walks one track's queue (A first for careful review, then B fast queue): opens the job URL in my normal browser (Simplify extension does autofill - don't rebuild autofill), shows chosen resume + edit notes + any matching answers from answers.yaml, and warns when the page is Workday or another ATS where Simplify often misses fields. Keys: a applied / s skip / l later / x not eligible. Logs resume + timestamp.
- Unanswered-questions queue: I log a screening question once (q in session), write the answer once, saved to answers.yaml. Never invent answers.
- Weekly targets Dashboard: applied vs target per track, funnel, streak.
- Follow-up reminders: on "applied", create reminders at default +7 / +14 / +21 days (editable in config); suggest archiving after 21 days with no response. Deadline and opens-soon reminders from deadline_at / opens_at.
- kitty today: reminders due today and overdue, deadlines in the next 7 days, count of new A/B jobs per track, weekly progress, source errors. Also a Today tab in the TUI as the default landing screen.
- kitty schedule print: prints the exact cron line / systemd timer unit / Windows schtasks command for a daily discover + digest run. It prints; I install. No GitHub Actions for this (personal data).
- Optional push: ntfy or Telegram bot, token in .env, off by default; sends the digest summary only.

### NICHE COMPANY FINDER (Stage 5, general, not just quant)
- Weekly command. Hard cap default 15 companies per run, never exceeded. Per-area quota from skills.yaml so no single area dominates.
- Candidate adapters (same registry pattern and health reporting):
  * yc-oss API (https://yc-oss.github.io/api/) - static JSON updated daily, tags, hiring list.
  * SBIR.gov awards - API at api.www.sbir.gov/public/api/awards has been under maintenance; default to the bulk CSV without abstracts (~65 MB), cached locally, refreshed monthly; use the API only if it responds.
  * conferences.yaml exhibitor/sponsor lists.
  * GitHub search for orgs with public repos in my skill areas.
  * Company names parsed from HN Who's Hiring.
- Score each vs skills.yaml with a one-line reason; detect ATS kind + slug; TUI screen to approve (joins watchlist.yaml + companies) or reject (never re-proposed, via firm_candidates.norm_name).

### STATUS + REPORTS (Stage 6)
- Gmail read-only OAuth proposes status changes (OA, interview, rejection) as status_events state=proposed; they become real only when I press a key. Token stored locally, gitignored.
- Response-rate report by resume, by track, by source, by tier.
- kitty export: full CSV/JSON export of jobs, applications, status history; escape cells starting with = + - @.

### LATER (Stage 7, optional)
- Own Playwright filler only for sites where Simplify fails - fills, never submits.
- MCP server wrapping the shared core.

### STAGE ORDER
1 tracker + TUI (done) -> 2 discovery + tracks + eligibility + Inbox + sources screen -> 3 LLM triage -> 4 Apply Session + weekly targets + reminders + today digest + scheduling -> 5 niche company finder -> 6 Gmail status + response-rate report + export -> 7 Playwright filler + MCP server.

## Addendum - decisions made since the spec was written

- Track "software": systems / backend / low-latency / Linux / infrastructure SWE.
- Track "power": power systems, electrical, controls, and test/field engineering roles.
  Resume defaults are selected in local configuration, not prescribed applicant files.
- Watchlist entries are local. Notes may carry program/location hints; `ats=none`
  entries keep the reason in notes. Workday boards disallowed by robots.txt stay
  `ats=none`; we respect robots.txt.
- Simplify listings: use the `degrees` field in the eligibility filter (drop
  Master's/PhD-only). Its `source` field is the contributor's username, not the ATS.
- Company-level tracks in `watchlist.yaml` are hints; job routing is decided by job title
  and description against each track's search terms.
- Education, track-specific application cities, location preferences and minimum
  pay belong in local profile/track configuration. City bounds are commute proxies,
  not measured driving times; no repository-wide personal geography is assumed.
- Own form filler is implemented: kitty opens
  the application form in its own browser window, fills it from profile.yaml and
  answers.yaml, uploads the track's resume, logs unanswered questions, and never submits.
  I review and click submit, then mark the job applied.
- 2026-10-02: the filler drives Firefox (system Firefox, separate profile in data/firefox,
  WebDriver BiDi on local port 9333) instead of Chromium; Playwright is no longer a
  dependency. Still fills everything up to submit and never clicks submit.
- Inbox and Jobs sort best first: freshman-friendly, eligible, tier, watchlist priority,
  preferred city or remote, newest.
- Resume coach (Resume tab): per track, how often postings mention each term from
  `config/resume_terms.yaml`, split into matched resume evidence and unmatched terms.
  Candidate editing is a separate, explicitly invoked local operation: bounded
  fact-preserving wording and ordering, exact diff/evidence, validated PDF preview,
  explicit promotion, immutable versions and rollback. No hosted model calls.
- Discovery runs on a schedule I install myself from `kitty schedule print`.
- Triage settings (model, effort, prices, cost cap) are in `config/triage.yaml`, not
  `.env`; only the key stays in `.env`. Default model claude-sonnet-5-5 at effort low.
- Triage sends no contact details: name, email, phone and profile links are redacted
  from resume text. Edit notes are kept only if their JD phrase and resume line are
  verbatim quotes; filtered-out jobs get a free rules tier C.
- `kitty schedule print --with-triage` (opt-in) also collects and sends triage batches
  on the timer, within the per-run cap.
- The Dashboard is the Today tab (F1, landing screen). The Apply tab walks A then B
  with the own filler (`f`) instead of Simplify; follow-up days, archive suggestion and
  deadline window live in `config/apply.yaml`.
- A job marked "x not eligible" keeps reason "marked not eligible by hand" and tier C;
  discover's re-judging never undoes it.
- Finder: a firm needs at least two keywords from one skills.yaml area. SBIR uses the
  bulk CSV without abstracts (award titles only), cached 30 days; the awards API is not
  used while it answers 403 (checked 2026-10-01).
- Gmail proposals: rules only, no LLM; a message is looked at once (gmail_messages);
  messages with no single clear application are skipped and counted; offers are proposed
  only on explicit phrases (pleased to offer, offer letter).
- Reports tab F10 and `kitty report` show response rates by resume, track, source and
  tier; the tier is the job's current tier.
- MCP server: `kitty mcp` over stdio; read tools plus record-only writes (mark_applied,
  set_status, skip, not eligible, confirm/reject proposals, run_discover). Finder
  approval stays in the TUI.
- 2026-10-03: after filling, kitty watches the filled tabs (read-only: URL and visible
  text) and records the application, with follow-ups, when a tab shows the board's
  confirmation page for that posting (Greenhouse, Lever, Ashby). I still click submit;
  other boards are marked by hand.
- 2026-10-03: favorites. I star jobs (jobs.favorited_at, migration 005); favorites are
  independent of queue_state. A like score (core/likeness.py: per-field TF-IDF + cosine
  over title, description, company and tracks; similarity to favorites minus similarity
  to jobs skipped or marked not eligible by hand) is recomputed after every favorite,
  unfavorite or hand skip and at the end of every discover. Best-first order uses it in
  0.05 steps after eligibility, tier and watchlist priority and before place and age;
  with no favorites it has no effect.
- 2026-10-03: `kitty autofill` / Apply `F` fills favorited jobs up to submit in
  background tabs of the kitty Firefox, capped per run (apply.yaml autofill_cap 10,
  autofill_pause_seconds 15), and skips applied, skipped / not eligible, closed
  and verified ready-for-review favorites. Partial/blocked attempts remain retryable. It can run detached
  in its own systemd scope and pushes a summary if a notifier is set. It never submits
  and never creates an application row; the watcher records submissions.
- 2026-10-03: sources vansh2027, speedyapply2027 and workable (repos before ATS boards
  in discover order). Workable: apply.workable.com/robots.txt disallows nothing;
  `/api/v1/widget/accounts/<slug>?details=true`; the API answers 200 with 0 jobs for
  nearly any slug and returns 429 for about 10 minutes after about 100 quick requests,
  so slug guessing does not find boards. Migration 006 rebuilds companies to allow
  ats_kind 'workable'; the migration runner applies every unapplied version (not just
  higher ones) and runs scripts that start with `-- foreign_keys: off` with foreign keys
  off plus a foreign_key_check before COMMIT.
- 2026-10-03: canonical_url keeps gh_jid (the Greenhouse job id on company careers
  pages). Discover matches the older stripped URL only when the id is in the path,
  splits postings that were merged only because gh_jid was dropped, and also matches
  www/no-www, boards/job-boards.greenhouse.io, Workable page vs /apply, and Workday with
  or without the locale segment (ignoring case).
- 2026-10-03: search-as-you-type boxes: the filler waits while a suggestion list shows a
  loading indicator (Lever's .dropdown-loading-results, or aria-busy=true), up to 12 s.
  A list counts as empty only after 2 s with nothing loading. Hidden suggestions are
  ignored. When a suggestion can't take a real mouse click, its own mousedown/mouseup/
  click events are sent instead (Lever picks on mousedown).
- Internship discovery uses configured JobSpy terms, locations, result limits and
  lookback windows; Workday uses intern/co-op/student queries with bounded pagination.
  Description evidence may add tracks after a title match; known postings gain tracks without
  resetting hand decisions. Workable unions repeated-shortcode locations so a foreign
  location cannot hide the same requisition's US opening. A posting's undergraduate
  program year is not a required graduation year.
- Direct employer source `employers` uses verified Oracle, Jibe, Eightfold and Siemens
  portals configured in sources.yaml, with robots-aware public fetching, caps, dedupe,
  qualification descriptions and partial/error reporting. Keyword searches are not
  complete snapshots; missing search hits cannot close stored jobs.
- Track `research` is rules-only, weekly_target 0: paid program applications, explicitly
  distinct from employment. Only confirmed open, paid cycles are imported; unknown
  GPA/credits/other prerequisites stay ambiguous. Application-cycle identity survives
  reused landing-page URLs and stripped title years. Published deadlines close expired
  program records. Future/closed/unknown NASA/DOE/NSF states are evidence, not vacancies.
  Authenticated campus channels and NSF robots restrictions are not bypassed.
- Source `yc_internships` verifies public detail records rather than directory snippets;
  expired cycles and explicitly stale/inactive postings are excluded. Missing recency
  stays uncertain; minimum school standing is preserved as a qualification.
- Source `websearch` accepts configured public seed URLs without a key. Brave API calls
  require both BRAVE_SEARCH_API_KEY and explicit enable_search, with query/result/page
  caps. Only fetched JobPosting JSON-LD
  is normalized, retaining qualifications, dates and provenance. Public-network DNS
  pinning, safe redirects, robots rules, size limits and credential isolation apply.
- 2026-10-04: combobox filling probes the existing option list before typed searches.
  Exact/profile-alias choices such as Electrical Engineering are selected directly;
  a bounded empty probe falls back to search, retaining asynchronous loading waits.
  Selection must survive blur; a click alone does not prove a committed value.
- Application autonomy/privacy research is recorded in
  [APPLICATION_AUTONOMY_RESEARCH.md](APPLICATION_AUTONOMY_RESEARCH.md). Guest forms
  can avoid account preparation, but account/verification requirements are
  employer-specific. Recognized Workday entry/steps now have guarded navigation;
  account assistance remains unimplemented. The never-submit and verified-answers rules remain.
- 2026-10-04: `local_employers` polls configured nearby Jibe/Oracle employer boards;
  explicit full-time-only roles are excluded, unknown schedules and continuous
  acceptance remain labeled. Store addresses distinguish same-title store openings.
  Discovery adds newly matching tracks to active stored jobs without resetting hand
  choices or reviving closed postings. Starbucks local searches are bounded and cannot
  close jobs merely by absence.
- Inbox Ctrl+F filters literal company-name substrings, case-insensitively, within
  the current track/favorites/date window. Enter applies; Escape clears the search.
  Jobs saved view 6 shows nearby part-time opportunities regardless of posting age.
- Statistics-driven resume editing is implemented as an explicitly invoked local,
  deterministic operation: narrow audited wording substitutions and evidence-ranked
  ordering of already-supported skills/bullets within semantic sections, not
  unrestricted rewriting. No hosted AI, uploads, invented qualifications or paid AI
  calls. Missing facts remain review items; contact/education/dates/metrics/project
  status are protected.
- Candidate HTML/PDF, CSS snapshots, exact patches/diff, anchored source facts,
  quoted JD evidence, cohort/source coverage and validation are retained with
  immutable hashes. Source/artifact mismatch or failed validation blocks promotion.
  Frequency and supported coverage are measurements, not an ATS score.
- Explicit promotion changes only the default for future unchosen jobs. Existing
  job choices, open attachments and historical application associations remain
  fixed. Rejection keeps audit artifacts; rollback restores the prior default.
  MCP may inspect versions but cannot generate, promote, reject or roll back them.
- CLI: `kitty resumes improve --track <TRACK> [--source <HTML>] [--min-postings N]`,
  `review <VERSION> [--open]`, `versions [--track <TRACK>]`, `promote <VERSION>`,
  `reject <VERSION>`, `rollback --track <TRACK>` (all under `resumes`).
  Resume (`F7`): `g` generate, `v` versions/exact review, `b` rollback.
  Exact review: `o` opens PDF, `p` explicitly approves/promotes, `x` rejects.
  Open-ended rewriting, a visual source editor and cloud-document editing remain gaps.
- 2026-10-04 reliability checkpoint: qualification restrictions are evaluated within
  clauses; negated or unrelated underclass mentions cannot waive requirements.
  Unresolved qualification relationships remain ambiguous instead of earning boosts.
- Migration 007 replaces legacy fill markers with explicit preparation state,
  timestamps, context, URL and blocker. Existing markers become attempted, not ready.
  Required answers, completed uploads and a recognized review boundary determine
  readiness. Resumption preserves manual values; lost tabs restart saved facts only.
  Authentication (unless opted in), active CAPTCHA, unsupported controls and pending
  uploads stop.
- Workday navigation is ATS-specific: recognized same-posting entry and non-final
  Next/Continue controls only, bounded progression, no automatic consent or submit.
  Populated retained forms defer new resume uploads that could overwrite manual values.
- Employer-history matching retains the actual employer (including an explicitly
  parenthesized alias) and saved answer scopes. A plain prior-employment question
  may use the user's generic company-history answer even when its label appends
  routing-only "If yes, provide additional information; if no, continue" wording.
  Other employers, additional qualifications/attestations and ambiguous or
  excluded answers do not inherit that answer. Explicit question answers retain
  precedence; no negative history is inferred from a resume. Native choice
  commitment and required-field checks still gate Save and Continue, and the
  final application Submit remains manual.
- 2026-10-09: opt-in sign-in automation (`apply.yaml` `authentication`, off by default).
  Credentials come only from a chmod-600 `.private/application-credentials.env`; exact
  approved HTTPS origins only; account creation is a separate opt-in. One recognized
  sign-in/sign-up form per job, no retry, every write/click rechecked in the page.
  CAPTCHA (including passive badges), MFA/verification, consent, extra fields, GET or
  cross-origin forms and existing values stop. Credentials are redacted from reports,
  stored preparation evidence and BiDi errors; wire debug logging is disabled.
- Source facts retain field-level authority/provenance separately from personal
  decisions and reminder dates (migration 008). Changed descriptions invalidate stale
  model ranking; late batch results apply only if the request inputs still match.
- Bounded `kitty refresh` prioritizes favorites and missing/stale descriptions.
  Verified evidence expires after seven days; sightings, cached old observations
  and failed checks do not renew it. Unknown/blocked is not closed.
- Partial discovery failures are surfaced in Today/push, Doctor and Sources with
  targeted recovery. Robots restrictions and persistent DNS failures are not
  solved by repeatedly launching the full discovery pipeline.
- Apply (`F3`) uses `c` for the same track chooser as Inbox and Jobs, including
  every-track selection and cancel-without-change. The selected track scopes both
  the displayed queue and favorite preparation; the old `t` cycling is removed.
- Resume (`F7`) opens a library of all active registered resumes, including
  non-default variants, with effective default tracks, file availability, full
  paths and descriptions. `Enter` / `o` opens only a local PDF; browsing never
  changes defaults or historical attachments. Posting evidence remains a separate
  tab; `t` / `T` selects the coach/candidate track.
- MCP `preparation_plan(track?, limit?)` exposes the existing bounded autofill plan
  and exclusion reasons without browser execution. Job metadata exposes historical
  preparation states, timestamps and blockers, not browser-control context IDs or
  retained URLs. Planning is not live readiness verification, submission or a
  reservation. Normal workspace initialization still migrates/syncs local state.
- Development autonomy: requested local work proceeds through research, code,
  affected tests/docs and synthetic verification without renewed OK per phase.
  This replaces the historical per-stage and automatic checkpoint-publication
  instructions. Material unresolved choices and unapproved destructive, paid,
  sensitive or externally consequential actions still require a decision.
  Existing sandbox/tool permissions and all application human boundaries remain.
  Sources and limits are in `docs/APPLICATION_AUTONOMY_RESEARCH.md`.
- Built-in modules: the core shell, settings, AI-provider/budget controls and local
  storage infrastructure are always available. `applyai` owns career workflows;
  `workspace` owns notes/documents/study/planning; `terminal` owns kitty!'s bundled,
  pinned herdr session (`docs/terminal-dock-design.md`). They do not depend on each
  other. The `modules` list of `config/build.json` (`docs/platform-design.md` Q8;
  a legacy `config/modules.yaml` is migrated into it on first open) stores a
  restart-only selection; absent means every
  module for existing workspaces, while fresh CLI/desktop workspaces start core-only.
  Disabled modules have no UI entries, commands, MCP tools, routes, workers or
  processes, but their data and configuration remain. Existing global migrations
  stay in place.
- Terminal dock (`docs/terminal-dock-design.md` Q1, Q3, Q8, Q10): a window-bottom
  dock shows herdr's real client (transparent, Harness colours) or herdr's own
  per-workspace agent states at a fixed S/M/L height the page ends above (no
  dragged height); hidden, only a thin handle. Float turns it into a bottom-right
  glass card that takes no page space (header drag, corner-grip resize, committed
  on release; hidden, a small pill). Its view, height step, float choice, card
  size and position are kept in the build (`ui.dock` of `config/build.json`). The herdr view is an
  atlas node under Plan that opens the full
  session in a workspace tab. Reading agent states never starts herdr.
- Agents in kitty! (`docs/terminal-dock-design.md` Q2, Q9): either terminal surface
  can open a labelled Agent tab running the chosen installed coding agent (omp by
  default; also Claude and Codex), with the pinned runtime's own skill. Agent
  selection is remembered locally; startup never sends a task or installs global
  configuration. Failed startup preserves the tab for inspection. External agents
  use the displayed named-session CLI commands to create their own labelled tabs
  and target returned pane IDs. The activity strip reads herdr's own detected
  states; it does not infer completion from a launch receipt.
- Study includes LearnHarness, a contained cross-origin frame of the independent
  AGPL-3.0-only upstream app at fixed `http://127.0.0.1:3210`. Native Study tools
  remain separate; upstream app code is unchanged. Provider logins use the
  explicit new-tab link.
- Desktop packages include a production Next standalone server and Node.js.
  **Start LearnHarness** invokes protected
  `POST /api/extensions/learnharness/start` with an empty body; it accepts no URL,
  settings or credentials. Missing/incomplete packages report a reinstall error,
  not source-only setup commands. **Reconnect** remains a read-only probe.
- `GET /api/extensions/learnharness` reports only fixed URL and availability using
  a two-second loopback HEAD probe, without redirects, response bodies,
  credentials, arbitrary URL inputs, import or data proxying. CSP and the desktop
  navigation guard keep exactly that frame origin in-app. Private API Host/Origin
  protections and workspace-module gating remain intact.
- The bundled server starts only on explicit action, binds IPv4 loopback and uses
  an isolated HOME and `data/learnharness/` in the selected workspace, including
  its own `auth.json`. No Kitty keys, applicant data or browser profile is passed.
  Quit terminates only the owned child; records survive restart. A healthy
  independently started service is reused, never migrated or stopped. Bundled
  demo storage is disposable; independent services keep their own persistent data.
- `desktop/package_learnharness.py` builds in a fresh public temporary clone,
  uses the pinned lock patch and `npm ci`, and runs upstream's standalone payload
  guard against leaked data/auth/env files and missing prompts. Desktop resources
  include corresponding upstream source, patch, AGPL license and Node licenses.
  `scripts/learnharness.sh setup|start|stop|status|run` remains an optional developer
  service with its original isolated `data/kitty-extension/` storage.
- No provider/model is configured and no paid request is made by starting the app.
  Teaching generation still requires the user's separately configured LearnHarness
  provider/model. Availability does not establish provider readiness.
- Installation pins LearnHarness 0.3.8 at upstream commit
  `3dc88472eea41eed0d5e5f29c2f16f8d063c415b`; the committed installation
  lockfile patch reproduces nonbreaking `npm audit fix` changes within upstream
  manifest ranges, without changing app code or `package.json`. No forced
  upgrades/downgrades are used. The reviewed audit retains two high findings
  (`ansi-to-react` / `linkify-it` quadratic text-scanning DoS), explicitly
  documented; the breaking suggested downgrade is not applied. Setup refuses
  unknown revisions/conflicting local lockfiles. There is no automatic startup;
  the installed app instead offers the explicit Start action.
- 2026-10-27: Quick notes (Study → Quick notes) are plain-text scratch notes in the
  `quick_notes` table (body ≤64 KiB, tint rose/lilac/sky/mint/honey/frost, pinned,
  revision). Autosave sends the last-seen revision; a stale revision gets HTTP 409
  and never overwrites or deletes. API: `GET /api/quick-notes[/:id]`,
  `POST /api/quick-notes/{create,save,delete}`. `/#quick-note[/<id>]` renders a
  single note; the desktop shell opens only that same-origin route as a transparent,
  undecorated, always-on-top window (from the page, the tray's "New quick note" or
  `kitty-workspace --quick-note`), with the main window's navigation guard and no IPC.
- 2026-10-27: The UI follows LearnHarness's look: default "Harness" theme
  (#141414/#1c1c1c/#434343 with kitty pink accents), mono uppercase chrome, CMU Serif
  Bold titles, pill controls and translucent panels over a theme-colored glow. The
  desktop windows are transparent; a pre-page init script marks
  `<html data-window="transparent">` so browser tabs stay opaque.
- 2026-10-27: Graph node colors are harmonized: every `--c2..--c6` color is pulled
  halfway toward the theme accent `--c1` (`web/src/lib/graphPalette.ts`), so groups
  stay distinguishable but read as one family across all themes.
- 2026-10-27: The top bar and workspace tabs are pinned (`.workbench-top`, sticky
  with a glass backdrop). Its measured height is published as `--workbench-top`;
  page-level sticky panels (job detail, mind-map toolbar) offset by it.
- 2026-10-28: Graphs default to a fresh 2D canvas renderer with theme-colored group
  regions, pill labels that fade with zoom, and a constant-size hover glow. Settings
  → Schemas and every graph's 2D/3D buttons share one remembered browser preference;
  existing 3D layouts remain available where WebGL is supported. This covers the
  atlas, section graphs, saved mind-map graphs and merged schema networks. Both
  renderers expose viewport-coordinate `nodeAt`, `nodePoint` and drag-end callbacks
  for graph interactions. No LearnHarness source or assets are included.
  Shared browser behavior checks run with
  `PLAYWRIGHT_BROWSERS_PATH=<browser-store-path> PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test web/tests/graphRenderers.browser.test.mjs`;
  NixOS browsers are supplied by `nix shell nixpkgs#playwright-driver.browsers`.
- 2026-10-28: The atlas draws only the sections you choose (terminal-dock design
  Q13). Drag a node onto the Sections list to hide it; drag a dimmed row onto the
  graph to show it again; each row's `⋯` menu (also on right-click) offers Open and
  Show/Hide for keyboard and touch. The choice is one browser preference
  (`kitty.atlas.hidden`, `web/src/lib/atlasNodes.ts`) shared live by the atlas in
  every tab. Hidden ids for a disabled module are kept but never drawn, so turning the
  module back on does not resurrect them; unknown or malformed entries are ignored.
  Both renderers run the same checks:
  `node --test web/tests/atlasNodes.browser.test.mjs` (same browser variables as
  above) and `node --experimental-strip-types --test web/tests/atlasNodes.test.mjs`.
- 2026-10-31: The atlas fits the window above the dock. The workbench, tab panel
  and atlas pass `min-height: 0` down the flex/grid chain, the sidebar scrolls on its
  own, and a graph still showing its automatic fit refits whenever its stage resizes
  (dock shown/hidden, F/Escape, window resize); a view the user panned, zoomed or
  focused keeps its position. The atlas stacks (and the page scrolls) only below
  600 CSS px, so the 760×500 native minimum stays side by side even with display
  scaling. Selected `.button` toggles (Jobs Map/List) and fullscreen drawing tools
  keep a complete accent/on-accent pair that the glass button fill cannot split.
  Checked in the real app against the synthetic backend:
  `node --test web/tests/workbench.browser.test.mjs` (same browser variables as
  above; `KITTY_TEST_PORT` optionally pins the backend port and Vite one above it).
- 2026-10-31: The Notes editor is one compact hierarchy (audit V5). The library is
  the only notebook/page navigator (the duplicate notebook strip is gone); one header
  row carries the page tabs, save state, a labelled **Views** panel and canvas
  fullscreen; one editor toolbar carries mode, title, Save and Find; infrequent page
  actions and keyboard help sit in a labelled **More** panel. Writing starts directly
  under the toolbar at the default (1280×850) and minimum (760×500) native sizes.
  Checks: `node --test web/tests/notes.browser.test.mjs` (same browser variables as
  above).
- 2026-11-02: Home holds only widgets (fidelity design Q13): Today and Quick note
  on the left, Agents and Up next on the right. The Sections list left the rail for a
  **Sections** popover in the stage's top-left corner (search, grouped list, each
  row's ⋯: Open, Show on the map, Hide/Show); a 2D node dropped on **Sections** hides
  it and a dimmed row dragged onto the graph shows it. The Brain/2D switch sits beside
  **View** in the stage's top-right corner. The right rail folds to an 18px strip by
  its inner-edge chevron or `]` (`kitty.home.rail`, shared live by every home) and the
  Brain or 2D atlas refits to the wider stage. Checks:
  `node --test web/tests/workbench.browser.test.mjs web/tests/brain.browser.test.mjs
  web/tests/atlasNodes.browser.test.mjs` (same browser variables as above).
- 2026-11-03: Agents reach notes as blocks (platform design, "Block API"). With the
  workspace module, `kitty mcp` adds notes tools (list/get/search/backlinks/tags; create,
  append, insert, update, move, delete to Trash, Inbox, quick note, tag) and the CLI adds
  `kitty notes ls|show|search|new|append|inbox|quick [--json]`, both over
  `kitty.core.block_api`; with applyai off the job tools are absent and with workspace off
  the notes tools are. Reads cite blocks as `((id@seq))`; writes are block transactions
  returning `seq` and the page `revision`, and write only text blocks of note pages.
  Checks: `uv run pytest tests/test_block_api.py tests/test_mcp_blocks.py
  tests/test_notes_cli.py tests/test_web_block_api.py tests/test_modules_mcp.py`.
