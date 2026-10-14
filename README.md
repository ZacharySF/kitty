# applyai

A personal job-search tool: find jobs across several tracks, filter out the ones
I am not eligible for, pick the right resume, apply through a fast queue, and
track every application to its outcome.

**Two rules the code enforces, not just documents:**

1. It never submits an application. It opens pages; I submit.
2. It never invents answers, qualifications, or facts about the applicant. Screening
   answers come only from `config/answers.yaml`. The local resume editor creates
   bounded, fact-preserving candidates for explicit review; it never silently promotes them.

Stages 1-7 are built: tracker, discovery, LLM triage, the daily flow and apply
session, a niche-company finder, Gmail status proposals with reports and export,
and an MCP server for my agent.

## Layout

```
config/           yaml I edit; *.example files are committed, the real ones are gitignored
data/             apply.db (real) and demo.db (throwaway) - gitignored
resumes/          my resume files - gitignored
src/applyai/
  core/           all behaviour, no UI imports - the CLI, TUI and MCP server call this
  db/             schema migrations and the connection
  tui/            Textual screens
  cli.py          the command line
  mcp_server.py   the MCP server (`applyai mcp`), a thin layer like the CLI
  web_server.py   loopback-only website adapter (`applyai-web`)
tests/            pytest, including headless TUI tests
web/              Svelte 5 / TypeScript website; built assets in ignored web/dist/
```

The layout rule: nothing under `core/` imports Textual or Typer, so the CLI, the
TUI and the MCP server all call the same functions.

## Setup

```bash
uv sync                  # create .venv and install
uv run applyai init      # copy config examples, ask ~5 questions, create the database
uv run applyai doctor    # check config, schema, keys, resumes, source health
```

`init` never overwrites a config file that already exists, and `--yes` skips the
questions. If you would rather not type `uv run` every time:

Only generic `config/*.yaml.example` templates are shared. Initialization copies
missing templates without changing existing local YAML. Keep applicant facts,
preferences, answers, watchlists and source selections in ignored `config/*.yaml`.
`PRIVATE_CONTEXT.md` is an optional ignored local notebook for personal context;
it is not application configuration. Never commit it, `.env`, resumes, databases,
exports, cached responses, discovered jobs or application history.

```bash
source .venv/bin/activate    # then just `applyai ...`
```

## Try it on fake data first

```bash
uv run applyai --demo demo reset    # ~30 invented jobs, all labelled "(demo)"
uv run applyai --demo tui           # learn the keys without touching real data
```

`--demo` uses `data/demo.db`. `demo reset` refuses to write to any file that is
not named `demo.db`, so it cannot wipe real applications.

## Local website

The Svelte website uses the same Python core and SQLite database as the CLI/TUI.
Its workbench builds on the Inference website template with a dark-fantasy theme:
charcoal and muted violet surfaces, antique-gold accents, serif headings and
etched borders. The body font is self-hosted; headings use system serif fonts.
The supplied artwork stays bundled at `web/public/art/application-background.png`.
No Rust service is needed because the existing core already owns the behavior.

From the project root, with Node 22+ and npm available:

```bash
npm --prefix web ci
npm --prefix web run build
uv run applyai-web                 # http://127.0.0.1:8765
```

On NixOS without Node on PATH, prefix either npm command with
`nix shell nixpkgs#nodejs_22 --command`.
Use the printed `127.0.0.1` address, not `localhost`: the server validates its
exact loopback Host/Origin. `--port`, `--root` and `--static-dir` override the port,
workspace and built-asset directory. The refresh button and command palette reload
configuration from disk; saves in My profile reload it automatically.

For safe exploration without touching real jobs, runtime YAML or resumes:

```bash
uv run applyai-web --demo --port 8766
```

Unlike CLI `--demo`, the website demo creates a disposable temporary workspace
from public examples; edits disappear when the process exits. External discovery,
browser filling, Gmail, notifications and paid operations are disabled. Synthetic
text resume placeholders correctly appear as unavailable PDFs; real test PDFs can
be registered inside the disposable workspace.

### Start here

Home shows a three-step guide and checks whether your name/email and a usable
resume PDF are saved. These checks describe saved data, not application readiness.

1. **My profile → My resumes:** enter your verified details in the ordinary form,
   review the save, then upload a resume PDF. Blank fields stay blank. Basic-form
   saves preserve other profile settings and reject stale edits; leaving with an
   unsaved profile, configuration or answer draft prompts before discarding it.
2. **Find jobs → Saved jobs:** search for a role or company. Open a job to read it,
   or select checkboxes and click **Save selected**. Your shortlist is in Saved jobs.
3. **Select jobs → Prepare selected:** review the included jobs, exact resumes and
   missing information. Approved preparation opens Firefox and fills known fields.
   Check each form and submit it yourself. **My applications** tracks the outcomes.

**Prepare applications** also lists recommended open, unsubmitted A/B jobs.
Alternative groups, job limits and browser progress remain under preparation
options. **More filters** exposes ranking, source and other filters; selected-job
actions and job details have their own expandable advanced controls.

The sidebar keeps the daily flow and setup links visible. **More tools** contains
new jobs, the application list, reminders, job sources, company discovery, reports,
email updates, ranking and application questions. Configuration/YAML, imports,
diagnostics, schedules and resume-generation/version tools remain available in
labeled expandable sections. **How to apply** returns to the guide; hotkeys reveal
collapsed controls before focusing them.

The website supports:

- Structured daily brief: track goals, deadlines, follow-ups, quiet applications,
  expected openings, source problems, mail proposals and outstanding questions.
- Inbox, ranked opportunities, A/B apply queue, saved views, source/eligibility/
  status/date filters, favorites and selection across pages.
- Manual posting entry, optional public-page metadata lookup, track/tier decisions,
  deadline overrides, persistent notes, queue/skip/later and durable ineligibility.
- Pipeline, reminders, explicit recording of an already-submitted application,
  configured follow-ups, dated evidence and append-only status undo.
- Resume registration/upload, vocabulary coaching with posting evidence, audited
  candidate generation, exact diff/fact/PDF review, promotion and rollback.
  Historical application resumes stay pinned.
- Browser application preparation for selected jobs, the A/B queue or capped
  favorites. Preflight shows the chosen resumes and blockers. Approval is bound
  to the reviewed job/configuration/PDF identities; changes require another review.
  Hash-addressed approved PDF copies stay in private runtime data so later human
  submission uses the approved bytes.
- Screening-question logging/collection and user-authored saved answers. Unknown
  answers remain blank. No applicant facts or screening responses are invented.
- Editable basic profile details without YAML, plus validated, stale-safe advanced
  configuration editing, previewed CSV import, local diagnostics, schedule
  generation and explicitly authorized notifications.
- Source health, run-one/discover, enable/disable and bounded posting refresh.
  Discovery is restricted to enabled free adapters; paid web search is excluded.
- Company-finder proposals and explicit watchlist approval/rejection.
- Read-only Gmail authorization/checking and explicit status-proposal decisions.
- Rules-only triage, fresh cost estimates, capped paid immediate/batch runs and
  collection of existing batch results. Paid operations require explicit approval.
- Outcome reports and formula-safe CSV/ZIP or JSON exports of jobs, applications
  and status history.

**Fast favoriting:** press **Ctrl+F** (⌘F on macOS), type a search, then right-click
a result to save it. In New jobs, Ctrl+F searches company names literally; `/`
searches titles and companies. Mark checkboxes (Shift-click for a page range), or
choose **Select all matching** to include every matching page, then right-click
any marked result to save the whole selection. **Save selected** also works on
touchscreens. Right-click always saves;
it does not toggle saved jobs off. `*` toggles a selection off only if all are
already favorites. Selection is limited to 10,000 results; narrow larger searches.

**Keyboard:** F1–F10 mirror the primary TUI sections; Ctrl/⌘K opens command and job
search; `?` shows the full contextual shortcut reference. Arrow keys navigate
jobs/pipeline; Space marks; Enter opens details. Job actions include `t` tier,
`k` queue state, `R` resume, `e` notes, `s` status, `u` undo, `d` deadline,
`n` next action and `o` posting. In Prepare applications, `a` records a submission,
`s` skips, `l` defers, `x` marks ineligible and `n`/`p` move between rows.
`f` favorites first, then opens preparation; Shift+F plans favorite autofill.
Letter shortcuts pause while typing or using dialogs. Native browser print,
reload and close-tab shortcuts are not intercepted.

**Preparation is not submission:** Firefox fills supported fields with your
verified profile/answers and attaches the approved resume. Review every answer
and press the employer's final Submit yourself. MFA, CAPTCHAs, consent and unknown
questions remain manual. The latest preparation results show filled fields and
remaining blockers directly, rather than hiding them under advanced options.
A later tab/browser failure preserves earlier job results.

**Employer accounts:** save the account email and password in
`.private/application-credentials.env` (owner-only mode `600`). In the website's
review plan, choose **Sign in** for existing accounts or **Create account** for
new ones, review the exact HTTPS websites listed, and check the authorization
box. This authorizes only that run, not a global policy change. Use separate runs
when sites need different account actions; ApplyAI cannot determine whether you
already have an account. Credentials are never displayed in the website.
Changed credentials, account choices or destinations require a fresh review.
Verification, consent, CAPTCHA and unsupported signup forms still need you.

**Google sign-in:** choose **Google — reuse my signed-in job account** in the
website's review plan. The displayed identity comes from **My profile → email**;
this mode does not load or send the private employer password. Check that email,
review the employer origins and authorize the run.

- Use the dedicated **Firefox window opened by ApplyAI**, not everyday Chrome.
  Its `data/firefox` profile persists job-site cookies between preparation runs.
- If the account is not signed in, finish Google sign-in manually in that Firefox.
  ApplyAI can select one exact, already signed-in account on a recognized Google
  chooser; it never adds an account, enters a Google password, or accepts permissions.
- After a manual handoff, return to the same employer application tab and review a
  new plan. Retained fields are reused only when the application identity can be
  verified; a different posting or an unidentifiable changed URL is not adopted.

Both same-tab flows and new popups explicitly associated with the employer tab
are supported. Unrelated tabs are never selected. Unrecognized chooser markup,
Google security blocks, verification and consent remain manual. Google Drive's
resume-picker button is not account sign-in; the filler attaches the approved
local PDF instead. Google reuse is authorized through the website, not by setting
`account_mode: google` alone in CLI/TUI configuration.

The optional watcher records only supported confirmation evidence after your own
submission; stopping releases browser control without closing your prepared tabs.
Ctrl+C waits for background work so accepted paid batch IDs/results can be
persisted before the server exits.

The server binds only to `127.0.0.1`, checks Host/Origin and browser fetch metadata,
requires JSON plus a custom header for mutations, denies framing/CORS, and only
serves registered, hash-verified PDFs. This is a trusted-user local application,
not a remotely deployable multi-user service.

For frontend development, keep the API at port 8765 and run
`npm --prefix web run dev` in a second terminal. Vite proxies `/api` only after
validating the local browser origin. `npm --prefix web run check` checks Svelte
and TypeScript. Backend verification uses isolated synthetic workspaces:

```bash
uv run pytest -q tests/test_web_server.py tests/test_web_jobs.py tests/test_web_tasks.py \
  tests/test_web_automation.py tests/test_web_resumes.py tests/test_web_profile.py \
  tests/test_web_insights.py
```

For browser smoke checks, use the disposable demo, not real application records.
Check simple profile save and discard protection, PDF upload, the home setup
checklist, Ctrl+F, cross-page selection/right-click, preparation-plan review,
disclosure hotkeys, append-only status undo and desktop/mobile layouts.
Demo restrictions intentionally prevent testing live employer, Gmail or paid calls.

## Finding jobs (Stage 2)

```bash
uv run applyai discover                       # every enabled source
uv run applyai discover -s greenhouse -s nuft2027   # just these
uv run applyai discover -s employers -s workable    # direct engineering/employer boards
uv run applyai discover -s research -s yc_internships -s websearch
uv run applyai discover -s local_employers -s starbucks # configured nearby part-time employers
```

`discover` pulls the Simplify, NUFT, vansh (`vanshb03/Summer2027-Internships`
listings.json; postings older than 180 days skipped) and speedyapply (the USA
internship tables in the READMEs of `speedyapply/2027-SWE-College-Jobs` and
`2027-AI-College-Jobs`; fragile, a changed layout shows as partial/error) repos,
every watchlist board on Greenhouse, Lever, Ashby, SmartRecruiters, Workday and
Workable (public widget API, one request per board), Indeed (via JobSpy),
the monthly Hacker News "Who is hiring" thread, and USAJOBS student/intern
announcements (off until `USAJOBS_API_KEY` and `USAJOBS_EMAIL` are in `.env`).
It keeps internship/co-op/student titles, routes each posting by title and additional
description evidence, runs the rules-only eligibility filter, and stores it. Previously
seen postings can gain newly matched tracks without resetting hand decisions. It prints
new jobs by source, track and eligibility,
the top filtered-out reasons with an example each, and every `[F]` company with
an open posting.

Additional channels:

- `employers`: Texas Instruments and Dell (Oracle), AMD and Schneider Electric
  (Jibe), Lockheed Martin, Northrop Grumman and Eaton (Eightfold), and Siemens'
  public careers pages. The verified portal URLs and request limits live under
  `sources.yaml` → `employers.options.boards`; these do not require new watchlist
  ATS kinds. A blocked or truncated board remains partial/error, not a clean zero.
- Workday searches `intern`, `co-op`, and `student`, paging past unrelated results.
  `max_results_per_query` caps each board/query. JobSpy searches every configured
  internship term when `max_terms_per_track: 0`; result caps, lookback windows and
  locations are configured locally.
- `research`: a separate, rules-only track for verified paid program applications,
  not fabricated individual lab vacancies or employment claims. TAMU S-REU and DOE
  SULI require a published open application window and funding evidence. GPA, credits
  and other unverified prerequisites keep the record **ambiguous**. Each program
  cycle has its own identity and expires against its current published source deadline,
  not a personal reminder date. NASA deadlines and NSF directory responses alone are
  not proof of an open paid opportunity.
- `yc_internships`: public YC directory candidates checked against their actual
  detail pages for US location, internship type, application availability and expired
  cycles. Old activity is filtered; an absent activity date is an explicit uncertainty,
  not proof of closure. Published minimum school standing reaches the eligibility rules.
- `websearch`: configured public `seed_urls` can be fetched without a search key.
  Only fetched JSON-LD `JobPosting` records enter discovery—not search
  snippets or program landing pages. Employer identity, qualifications, deadlines
  and query/page provenance are retained. Shared URLs and sources still deduplicate.

Brave search is **off even if a key is present**. To opt in, put
`BRAVE_SEARCH_API_KEY` in the gitignored `.env` and set
`websearch.options.enable_search: true`. Defaults cap a run at four API queries,
five results/query and twenty fetched posting pages; changing these may change
provider charges. No key is needed for seed-only runs. Public fetching respects
robots, redirects, response-size limits and rate limits; private-network URLs and
credential forwarding are rejected. Robots delays are per host; waits over 60 seconds
are deferred and reported rather than blocking the entire run.

Some sources require credentials or interactive access. USAJOBS needs its own API
key/email; authenticated campus portals and robots-disallowed directories are not
bypassed or counted as imported jobs. Source-run evidence distinguishes future,
closed, unknown and blocked program states from open opportunities.

Location and pay rules are user configuration, not repository-wide preferences.
Tracks can limit cities, allow specific city/employer combinations, impose minimum
hourly pay, and rank preferred locations or remote work. JobSpy can search nearby
locations through `also_near`; the `starbucks` source can fetch store jobs and pay
ranges from its public careers search. Track-specific application cities come from
`profile.yaml` (`city_by_track`); answer scopes use `tracks` / `exclude_tracks`.

`local_employers` is a separately selectable, robots-aware direct source using
configured Jibe/Oracle boards, including supported grocery and pet-retail portals.
It restricts cities before fetching details, retains store addresses and
published pay, excludes explicitly full-time-only records, and labels unknown hours
and continuous-acceptance listings rather than promising immediate vacancies.
Jibe's structured US hourly salary ranges are retained. Set a board's
`part_time_only: true` to exclude records without explicit part-time title/schedule
evidence. The pay filter compares the highest stated hourly rate; a passing range
does not guarantee that starting wage. Review tip-dependent pay and schedule
uncertainties before applying.
Configure `options.boards` and `options.allow_cities` in `sources.yaml`; the shipped
example is disabled and has no personal geography. City allowlists and search miles
are commute proxies, not measured drive times. Existing active jobs gain newly
matching tracks on discovery; widening geography alone never reopens a closed job.

In **Jobs (`F4`)**, saved view **6 — Nearby part-time - all posting dates** shows
active, not-filtered, unapplied local jobs in `new`/`queued` state without the default
14-day cutoff. Older but still-open store listings otherwise disappear from the
recent view. `c` still narrows the current saved view; clear another track selection
if necessary. In Inbox, `w` shows older new jobs.

To keep finding jobs without me, run discovery on a timer:

```bash
uv run applyai schedule print                  # home-manager block (NixOS), 07:00 and 17:00
uv run applyai schedule print --format systemd # or plain systemd user units / --format cron
uv run applyai schedule print --with-triage    # also collect + send triage batches (spends up to the cap)
```

It is slow on purpose: at least 2 s between requests to any host (5 s for the
repos), and raw responses are cached under `.cache/http/` for each source's TTL.
Cache hits avoid repeated page requests; failed requests are not cached as successes.
A 429 stops requests to the affected host; retained successes are reported as partial.

Filtered-out jobs are kept (queue state `not_eligible`, visible in "All jobs");
everything else lands in the Inbox (`F2`). The Sources screen (`F6`) shows each
adapter's last run and error, and `enter` runs one source.
`partial` is a problem, not a healthy empty result: Today, push notifications,
Doctor and Sources show it with targeted recovery guidance. DNS failures call
for resolver/provider investigation; robots restrictions stay restricted; capped
queries need narrower coverage; rate limits need cooldown, not a full rerun.

A job closes (leaves the queues, stays in "All jobs") when Simplify marks it
inactive, or when every source that listed it has answered cleanly
`close_after_missed_runs` runs in a row (`config/sources.yaml`, default 3)
without it. A board that errored, was rate limited or suddenly returned 0 does
not count as a miss. Bounded searches (Workday, JobSpy and the new channels) set
`complete_snapshot: false`: absence from a search never closes a posting. Explicit
source closure/expiry and published research-program deadlines still close records.

### Refresh the shortlist, not the source count

```bash
uv run applyai refresh --limit 10 --dry-run  # inspect favorites-first selection
uv run applyai refresh --limit 10           # bounded authoritative detail checks
uv run applyai refresh --job <ID> --limit 1  # use a local stored job ID; repeat --job
```

Selection prefers favorites, then missing/stale descriptions within best-first
ranking; `--track` narrows it. Checks use single-posting ATS details or an exact
public JobPosting record, with robots restrictions, pacing and caching. Unknown
JavaScript-only pages remain unverified. No paid services or submissions.

Inbox, Jobs and Job Detail distinguish **verified**, **stale**, **unverified**
and **closed**. Successful live evidence expires after seven days; a discovery
sighting or failed check does not renew it. A generic HTTP error, absent search
hit or different posting is not evidence of closure.

Source facts retain source, URL, observation time and authority. Newer
same-authority evidence can replace old qualifications; weaker or empty evidence
cannot erase them. Manual queue decisions, notes, favorites, tiers, resumes and
reminder dates remain separate. Changed descriptions invalidate old model tiers;
late batch answers with changed inputs are cached but not applied.


The Inbox's "opens soon" line lists watchlist companies with no open posting
whose opening is within 30 days either side of today: from
`expected_open_months` in `watchlist.yaml` when set, otherwise from the day
their first posting appeared last cycle (cycles start July 1). It needs a year
of history, so until then only the overrides produce anything.

## Every day (Stage 4)

```bash
uv run applyai today           # due/overdue reminders, deadlines this week, new A/B per track,
                               # weekly progress + streak, failing sources, quiet applications
uv run applyai today --notify  # also push it (ntfy or Telegram, set in .env; off by default)
```

The TUI opens on **Today** (`F1`), the same digest with every line a link.
**Apply** (`F3`) is the apply session: one job at a time from the queue, A tier
first, then B, on one track (`t`) or all. It shows the posting, the resume to
send, the edit notes, my saved answers, preparation state and freshness. `f` fills the
form, I check it and click submit, then `a` records the application (resume,
time) and creates follow-up reminders at +7 / +14 / +21 days (`config/apply.yaml`).
`s` skips, `l` puts it off, `x` marks it not eligible by hand (discover never
undoes that), `q` logs a screening question I could not answer. On the Profile
tab (`F8`) `w` answers a logged question once; the answer goes into `answers.yaml`
exactly as I type it. `applyai status <id> applied` also creates the follow-ups.

**Inbox company search:** press **Ctrl+F**, type a company name or part of one,
then **Enter**. Matching is case-insensitive and company-only, not title/location
search. It preserves the selected track, favorites filter and 14-day/all window;
the counts line shows the active company query. **Escape** clears only that search.
Hidden marked rows are cleared when the results narrow, so batch actions cannot
silently affect a company outside the displayed results.

## Finding companies (Stage 5)

```bash
uv run applyai finder          # propose up to 15 firms that match config/skills.yaml
uv run applyai finder --list   # what is waiting for a decision
```

Sources: the YC companies list (hiring), HN "Who is hiring" posters, sponsor lists
in `config/conferences.yaml`, GitHub organisations in my skill areas, and small
firms with SBIR/STTR awards since three years ago (the bulk CSV without abstracts,
~85 MB, downloaded once and cached 30 days; the awards API answers 403). Each
firm is scored against the keywords of each area in `skills.yaml` (at least two
must match), with a one-line reason quoting what matched; each area stops at its
`per_run_quota` and a run never proposes more than 15. For each one it checks
Greenhouse / Lever / Ashby / SmartRecruiters for a board. On the **Finder** tab
(`F9`) `a` approves (the firm joins `watchlist.yaml`, so discover polls it) and
`r` rejects (never proposed again). SBIR only has award titles, so few SBIR firms
reach two keywords.

## Applying (the tool fills, I submit)

```bash
uv run applyai apply 1 2                       # example local job IDs
uv run applyai apply --top 10 --track embedded # the 10 best new jobs in a track
uv run applyai apply --queued                  # everything already in the apply queue
```

In the TUI: mark rows in the Inbox with `space` and press `f` (or `f` on any job).
applyai opens each application form in its own Firefox window (a separate profile in
`data/firefox/`, so logins stick and my everyday Firefox is never touched), uploads
the track's resume, fills contact and education fields from `profile.yaml` and
screening questions from `answers.yaml`, and stops. **It never submits.** The window stays open; I check
each tab, fill what is left and click submit. Attempted forms with fields enter
the "Apply queue" view (Jobs, key 5); being queued does not mean ready.

Preparation states are **unattempted**, **attempted**, **partial**, **blocked** and
**ready**. Only verified required fields, completed uploads and a recognized
review/submit boundary permit ready; detection of fields alone does not.
Old fill markers migrate to attempted, not ready. Missing answers can be saved
on Profile and retried, including the first answer in an empty answers file.

A valid retained application tab is resumed without navigating away or replacing
manual choices/attachments. If it has gone, the tool explicitly restarts only saved
facts. A new upload on a populated retained form is left manual because employer
resume parsing can overwrite your entries. Login, verification, visible CAPTCHA,
unsupported controls, validation errors and incomplete uploads remain blockers.

The window is its own program (a separate systemd scope), so closing the terminal
leaves it open; in the window list its app id is `applyai`. Before filling, applyai
switches Firefox's remote control to its non-automation mode, so pages see
`navigator.webdriver = false` (bot checks such as reCAPTCHA read it). That needs
`--remote-allow-system-access`: while the window is open, any program on this
computer that reaches 127.0.0.1:9333 can run code inside that Firefox, so keep only
job-site logins in it.

After filling it keeps watching the tabs (`watch_hours` in `config/apply.yaml`,
default 4; `--no-watch` skips it). When a tab shows its board's confirmation page
(Greenhouse `.../confirmation`, Lever `.../thanks` with "Application submitted!",
Ashby's success message), it records the application with the resume and the
follow-ups, exactly like `applyai status <id> applied` or `a` in the Apply tab.
It only reads the tab's address and text; it never clicks. Other boards (Workday,
company sites) are not watched: mark those by hand. In the terminal Ctrl-C stops
watching; in the TUI watching goes on across tabs, `w` on the Apply tab stops it,
and pressing `f` again keeps watching the earlier tabs too.

Firefox takes one remote-control session at a time and keeps it until the
program holding it ends it (a killed program never does). So while one applyai
watches, another fill says which process holds the window; if none does (an
applyai was killed), close the applyai Firefox window and fill again. Closing the
terminal (SIGHUP) still ends the session cleanly.

It never guesses: a question with no answer in `answers.yaml` is left blank and
goes into the unanswered-questions list on the Profile screen, so I answer it
once. `match:` phrases on an answer make it apply to differently worded
versions of the same question ("authorized to work", "sponsorship"); "U.S.",
"USA" and "United States" count as the same words. `choices:` records my exact
wording for dropdowns that offer no plain "Yes". `companies:` and
`exclude_companies:` keep prior-interview or employment answers from leaking
between companies. A graduation dropdown listing terms ("2032 - Spring") gets
the term of the configured graduation month (Jan-May Spring, Jun-Aug Summer, Sep-Dec Fall).
Comboboxes first open their existing option list and select an exact matching value
(including configured/profile aliases) without typing. If no matching choice appears
during the short list probe, they fall back to search; asynchronous location searches
retain their loading wait. A choice must remain selected after blur to count as filled.
Verified acronym decorations are equivalent to the full option name, so
`ECE - Electrical and Computer Engineering` can match the saved full major.
Ambiguous/disabled choices are refused; a major never matches a different program
merely because its name starts the same way. Editable search text is not proof of
a selection, and failed choices clear that query.
Recognized Workday pages can follow guarded Apply/Apply Manually entry and
non-final Next/Continue steps to Review. Account requirements vary by employer.
Unknown structures, final actions and consent controls are never clicked.
Required native resume uploads inside labelled ARIA upload groups are supported.
When Greenhouse removes the file input after upload, its visible filename/removal
receipt can verify the retained attachment. Pending, failed or incomplete receipts
cannot establish readiness.
An unrelated unsupported required widget no longer prevents known fields and the
resume from being filled, but it still blocks advancing or claiming readiness.
Job-description pages without a recognized application entry remain manual;
opening a tab alone is not reported as successful preparation.

Sign-in automation is off by default. The website can authorize one reviewed run
as described above. For CLI/TUI policy, put the account in
`.private/application-credentials.env` (`APPLICATION_ACCOUNT_EMAIL=...` and
`APPLICATION_ACCOUNT_PASSWORD=...`, `chmod 600`) and list each site's exact HTTPS
origin in `config/apply.yaml`:

```yaml
authentication:
  enabled: true
  allow_account_creation: false   # true also lets it fill Create account / Sign up forms
  account_mode: recognized       # login or create explicitly chooses an account action
  approved_origins:
    - https://example.wd5.myworkdayjobs.com
```

`recognized` only handles an already displayed account form. Explicit `login` or
`create` can first follow one unambiguous approved account-entry link or
non-submitting button. `create` also requires `allow_account_creation: true`.
At most one credential request is sent per job attempt, with no retries or
login/signup fallback. A sent request is not proof an account was created;
the filler checks whether the site advances to the application.

Employer-password mode handles only a plain form: one email/username box, the password (plus
confirmation boxes for a new account) and a single Sign in / Create account
button, posting to the same approved origin. CAPTCHA widgets (even a passive
badge), MFA or email codes, consent checkboxes or "by signing in you agree"
text, extra fields, other SSO-only pages, GET forms, cross-origin frames and boxes
already holding other values all stop for manual handling. The credentials are
redacted from reports, stored notes and browser-error text.

The [application autonomy and privacy research](docs/APPLICATION_AUTONOMY_RESEARCH.md)
records provider account capabilities, implemented preparation/resumption
boundaries and remaining tenant-specific gaps. There is no promise of unattended
support for every site.
Browser-signal suppression is not anonymity; verification, CAPTCHAs, consent,
unknown answers, and final submission still need an appropriate handoff.

The window is system Firefox (`$APPLYAI_FIREFOX` or `firefox` on PATH) started with
remote control (WebDriver BiDi) on `127.0.0.1:9333` (`APPLYAI_BROWSER_PORT`). While
it runs, Firefox shows the striped "under remote control" address bar, and any program
on this computer could drive that window, which is why it uses its own profile. Close
the window when done; the next fill starts it again with the same logins.

To answer questions before the filler meets them, `applyai questions collect`
reads the forms of the queue's jobs without a browser (default: active, `new` and
`queued`, tiers A and B; `--states`, `--tiers`, `--limit`). Greenhouse comes from
its public board API, Lever from the job's `/apply` page; Ashby (its robots.txt
disallows the form API), Workday (sign-in) and other sites are skipped with the
reason in the summary. One request per second per host, cached for a week, and
each question counts a job once, so re-running changes no counts. The company's
own name becomes `{company}`, so "Why Example Robotics?" and "Why Example Systems?" merge.
`applyai questions list` prints what is still unanswered, most-shared first, by
kind (choice, factual), with job counts, example companies and the dropdown
choices; `--include-essays`, `--include-demographic`, `--all`, `--min-jobs N`,
`--json`. Questions profile.yaml fills and file uploads are left out, and
questions the filler logged are listed too.

### Favorites and autofill

- Star jobs I like: `*` in the Inbox, Jobs, Apply and Job Detail screens (★ marks
  them), or `applyai fav <ids...>` / `applyai unfav <ids...>`. `applyai favorites`
  lists them best first; `applyai list --favorites --best` works too. `v` in Inbox /
  Jobs / Apply shows favorites only.
- More like these: best-first ordering (Inbox, Jobs, Apply, `applyai list --best`,
  MCP list_jobs) ranks jobs that look like my favorites (title, company, tracks,
  description words) above ones that look like jobs I skipped or marked not eligible
  by hand. It comes after eligibility, tier and watchlist priority and before place
  and age, so it never overrides a rule. With no favorites it changes nothing. Job
  Detail and Apply show "like your favorites (+0.45): fpga, track cpu, ...".
- `applyai autofill [--limit N] [--track T] [--no-watch] [--detach] [--dry-run]` (or `F`
  on the Apply tab, `f` on a ★ job there, or `f` on Inbox / Jobs with nothing marked)
  fills my favorites' forms in background tabs of the applyai Firefox, only those in the
  track the screen is filtered on (`c` on Inbox / Jobs / Apply; `--track`),
  best first, at most `autofill_cap` per run (config/apply.yaml, default 10), waiting
  `autofill_pause_seconds` (default 15) between jobs. **It never submits.** It skips
  closed, applied, skipped / not eligible and verified ready-for-review favorites,
  and says why. Partial/blocked attempts can resume; Workday is inspected rather than
  skipped by hostname. `--detach` keeps it running after the terminal
  closes (own systemd scope; output in data/autofill.log). With ntfy or Telegram set
  up in .env it pushes the summary. Afterwards it watches the tabs like
  `applyai apply` and records each confirmation page as applied.

## Triage (Stage 3, Anthropic API)

```bash
uv run applyai triage                 # what a run would send and cost; changes nothing
uv run applyai triage --now --limit 5 # small run now, one request at a time
uv run applyai triage --batch         # everything, as one Message Batch (half price, < 24 h)
uv run applyai triage --collect       # apply finished batches
```

Needs `ANTHROPIC_API_KEY` in `.env`. Model, effort, prices and the hard per-run
cost cap (`max_cost_usd_per_run`, default $5) are in `config/triage.yaml`. Every
paid run prints the job count, estimated tokens and dollars first and asks to
confirm (`--yes` skips it, for the schedule). A run stops before it could pass the
cap, and a batch is trimmed to fit; the run's cost and cap hits are in `runs`.

For each job the model returns, as schema-checked JSON: a tier (A careful review,
B fast queue, C kept with a reason), one registered resume with a one-sentence
reason, and up to three edit notes. A note is kept only if its JD phrase is
really in the posting and its resume line is really in that resume; notes say
what to check, never resume text to paste. Answers are cached per (job, model,
prompt), so rerunning costs nothing until a posting or resume changes.

Free, no model call: every discover gives each kept job a rules tier straight
away (A open to underclassmen, B eligible or no bar found, C filtered out with
the reason), so the tier column is never empty; `applyai triage --rules` does the
same on demand. Part-time jobs (triage_depth `rules`) keep that tier; on other
tracks `triage` replaces it with the model's. A tier I set by hand is never
overwritten. My name, email, phone and profile links are removed from resume
text before it is sent.

## Replies from Gmail (Stage 6)

Read-only: the only scope asked for is `gmail.readonly`. One-time setup:

1. Open https://console.cloud.google.com/ and create a project (e.g. "applyai").
2. APIs & Services > Library: search "Gmail API" and click Enable.
3. APIs & Services > OAuth consent screen: choose External, fill in the app name
   and your email, then under Audience > Test users add your own Gmail address.
   Leave the app in Testing.
4. APIs & Services > Credentials > Create credentials > OAuth client ID, type
   "Desktop app". Download the JSON and save it as `config/gmail_client.json`
   (gitignored).
5. `uv run applyai gmail auth`: a browser opens; allow read-only access. The token
   goes to `data/gmail_token.json` (gitignored). While the app is in Testing,
   Google expires it after 7 days; run `gmail auth` again when a check says so.

```bash
uv run applyai gmail status            # set up? last check, proposals waiting
uv run applyai gmail check --days 30   # read recent recruiting mail, propose status changes
uv run applyai proposals               # what is waiting for me
uv run applyai proposals confirm 12    # or: proposals reject 12
```

Rules only, no model: a message is classified as rejection, offer, interview or
OA from explicit phrases ("unfortunately", "online assessment", "pleased to
offer"...; phrases inside "if ..." sentences don't count, so "we received your
application" is ignored), and matched to one open application by company name.
Anything ambiguous is skipped and counted. Each message is looked at once
(`gmail_messages` table). A proposal changes nothing until I confirm it: on the
**Pipeline** tab `v` lists proposals (`y` confirm, `n` decline) and `G` checks
Gmail in the background.

## Reports and export (Stage 6)

```bash
uv run applyai report                  # response rate by resume, track, source and tier
uv run applyai export                  # data/export/<date>/{jobs,applications,status_events}.csv
uv run applyai export -f json          # data/export/applyai-<date>.json
```

"Heard back" = OA, interview, offer or rejection; "OA+" = OA or further. The
**Reports** tab (`F10`) shows the same tables; `e` / `j` write the CSV / JSON
export. CSV cells that start with `= + - @` get a leading `'`, so a spreadsheet
never runs them as formulas.

## MCP server for my agent (Stage 7)

`uv run applyai mcp` serves the core over stdio (`--demo mcp` uses the demo DB).
Register it in an MCP client:

```json
{
  "mcpServers": {
    "applyai": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/apply-ai", "applyai", "mcp"]
    }
  }
}
```

Tools: `today`, `list_jobs`, `get_job`, `preparation_plan`, `weekly_progress`,
`list_questions`, `finder_candidates`, `list_proposals`, `list_resume_versions`,
`get_resume_version`, `mark_applied`, `set_status`, `skip_job`, `mark_not_eligible`,
`confirm_proposal`, `reject_proposal`, `run_discover`. None submits anything,
writes or suggests screening answers, or writes resume text; `mark_applied` only
records an application I already submitted. Finder approvals stay in the TUI.
Each call opens and closes its own database connection.

`preparation_plan(track?, limit?)` lets an agent inspect the same favorites-first,
track-scoped batch as local autofill, with the configured hard cap and reasons for
leaving jobs out. `list_jobs`, `get_job` and selected plan jobs expose stored
preparation states, attempt/readiness timestamps and blockers, not browser-control
identifiers. This is a local snapshot, not a live browser check or a reservation:
ready means verified at the last attempt, not submitted. Planning does not open
Firefox, contact employers, fill forms or change applications/statuses; normal
workspace initialization may still migrate SQLite and sync local configuration.

## Resumes and the resume coach

Resumes live in `resumes/` (gitignored) and are registered by label; each track
sends its `default_resume`. Add one from the Profile screen (`F8`, `n`) or with
`applyai resumes add <file> --label <label>`.
Choose labels and track mappings for your own files in local configuration.
Resume sources, generated documents and any rendering scripts containing personal
content must remain in the ignored `resumes/` directory.

**Resume (`F7`) opens the resume library:** all active registered files, including
variants that are not a track default. Select a row to see its full local path and
description; `Enter` or `o` opens that PDF in the local viewer. The table shows
effective default-track assignments and missing files. `r` refreshes the library;
browsing or opening a PDF never changes a default or an application attachment.


The **Posting evidence** tab within Resume (`F7`, or `applyai coach [--track T]`)
counts, per track, how often postings mention each term in
`config/resume_terms.yaml`, with resume-line and job-description evidence.
Counts are vocabulary measurements, not an ATS score or proof of experience.
`t` / `T` changes the coach/candidate track, not the all-resumes library.
The coach remains read-only; candidate generation is separate.

### Local deterministic editing

```bash
uv run applyai resumes improve --track <TRACK> --source resumes/source.html
uv run applyai resumes versions --track <TRACK>
uv run applyai resumes review <VERSION> --open
uv run applyai resumes promote <VERSION>
uv run applyai resumes reject <VERSION>
uv run applyai resumes rollback --track <TRACK>
```

`improve` creates a candidate HTML/PDF and an audit manifest, not a new default.
`--source` can be omitted only when the source is unambiguous; `--min-postings`
sets the minimum cohort size (default 5). Review includes exact patches and diff,
existing-fact anchors, quoted JD evidence, cohort/source coverage, measurements,
warnings, missing-fact review items and PDF/layout validation. Open the PDF and
inspect it before approving.

On **Resume (`F7`)**, `g` generates a candidate, `v` opens version history/review,
and `b` requests rollback. The exact review screen uses `o` to open the local PDF,
`p` to explicitly approve/promote, and `x` to reject. Promotion and rollback require
confirmation. Rejection retains the audit artifacts.

The editor is local and deterministic: **no hosted AI, no uploads and no paid AI
calls**. It supports narrowly audited wording substitutions and evidence-ranked
ordering of already-supported skills/bullets within their semantic sections.
It does not write arbitrary new prose or add a skill because employers mention it.
Contact, education, dates, metrics and project status remain protected; unsupported
facts become review items. Source/artifact hashes and validation guard promotion.
Originals and immutable version artifacts are retained.

Promotion changes only the default for **future unchosen jobs**. Existing job
choices, open application attachments and historical application resume associations
are not rewritten. Rollback restores the prior default, not historical rows.

The [resume improver research](docs/RESUME_IMPROVER_RESEARCH.md) retains primary-source
comparisons of Jobscan, Teal, Rezi, Resume Worded, Reactive Resume and Google Docs.
Open-ended rewriting, a visual source editor and cloud-document integration remain
outside the implemented bounded editor.

## Daily use in Stage 1

```bash
uv run applyai add https://jobs.example.com/postings/123 --track quant
uv run applyai add <url> --fetch                 # fetch the page title to prefill, still editable
uv run applyai status 12 oa --when 2026-10-01    # or press s in the TUI
uv run applyai list --track quant
uv run applyai resumes add resumes/quant.pdf     # stores path + hash + your description
uv run applyai import csv old-applications.csv   # preview, confirm, then write
uv run applyai tui
```

### Getting around

The tabs at the top of every screen follow a job's life, left to right, and can
be clicked: **Today** `F1` (what is due, what is new) → **Inbox** `F2` (decide on
new postings) → **Apply** `F3` (the apply session) → **Jobs** `F4` (everything
kept) → **Pipeline** `F5` (applications by stage) → **Sources** `F6` (where
postings come from), then **Resume** `F7` (resume library and posting evidence), **Profile**
`F8` (facts, resumes, preferences, answers), **Finder** `F9` (companies to
watch) and **Reports** `F10` (who hears back, export). The line under the tabs says what the current screen is for. On Today
every line that needs action is a link to the screen that handles it.

The look is a dark-fantasy theme (red sky, black keep, torchlight). Colours keep
one meaning everywhere: ember = keys and things you can act on, green = good,
gold = look closer, red = problem, grey = done or unimportant. The castle banner
on Today hides itself on terminals shorter than 36 rows.

### Keys

| key | does |
| --- | --- |
| `?` | help overlay, built from the current screen's real bindings |
| `ctrl+p` | command palette: screens, actions, and jobs by company/title |
| `F1`-`F10` | Today, Inbox, Apply, Jobs, Pipeline, Sources, Resume, Profile, Finder, Reports (or click the tabs) |
| `v` `G` | Pipeline: review Gmail proposals (`y` confirm, `n` decline), check Gmail now |
| `a` `r` `R` | Finder: approve into the watchlist, reject for good, run the finder |
| `e` `j` | Reports: export CSV / JSON to data/export/ |
| `a` `s` `l` `x` `q` | Apply session: applied (after I submit), skip, later, not eligible, log a question |
| `w` | Profile: answer a logged screening question (saved to answers.yaml) |
| `f` | fill & open the application form(s): marked Inbox rows, or the selected job. On Inbox / Jobs with nothing marked, and on Apply when the job shown is ★ (or `v` is on), every waiting favorite in the current track instead (like `F`). On Apply a plain fill moves to the next job; filled jobs wait at the end of the queue |
| `*` `v` | Inbox / Jobs / Apply / Job Detail: star or unstar (marked rows or the selected job); show favorites only |
| `F` | Apply: autofill the current track's favorites in background tabs (never submits) |
| `c` | Inbox / Jobs / Apply: choose one track (quant, parttime, ...) or every track; Escape keeps the current selection. On Jobs it narrows the saved view; on Apply it also scopes favorite filling |
| `esc` | back from Job Detail |
| `space` | Inbox: mark a row; `t` tier, `g` add track, `x` skip act on every marked row |
| `w` | Inbox: last 14 days / all new jobs |
| `1`-`9` | switch saved view (titles are on the views line) |
| `/` | filter bar; `escape` clears it |
| `a` | add a job by pasting a URL |
| `enter` | open Job Detail |
| `s` | change status (records the application if there is none yet) |
| `u` | undo the last status change (status changes only) |
| `n` `d` | next action date+note, deadline |
| `k` `t` `R` `e` | queue state, tier, resume pick, my notes |
| `t` `T` | Resume screen: next / previous coach and candidate track; the library still lists every resume |
| `g` `v` `b` | Resume screen: generate local candidate, versions/exact review, rollback default |
| `enter` `o` | Resume library: open the selected local PDF without changing its default or attachments |
| `o` `p` `x` | Resume review: open PDF, explicitly approve/promote, reject |
| `e` `n` | Profile screen: edit a config file in `$EDITOR`, add a resume |
| `o` | open the posting in my normal browser |
| `left` `right` | move between pipeline columns |
| `ctrl+r` | reload the yaml config and sync it |
| `q` | quit |

## Config

| file | committed? | what it is |
| --- | --- | --- |
| `profile.yaml` | example only | applicant facts; nothing is invented. `discipline` fills Major / Discipline / Field of study; `discipline_by_track` supplies track-specific values |
| `tracks.yaml` | example only | tracks: search terms, sources, location and eligibility rules, default resume, triage depth, weekly target |
| `sources.yaml` | example only | discovery adapters, rate limits, cache TTLs |
| `watchlist.yaml` | example only | company boards (`workday`: `tenant/wdN/site`; `workable`: account slug); `ats: none` keeps unpollable companies with reasons in `notes`; optional tags describe programs or locations |
| `answers.yaml` | example only | user-authored screening answers; optional `match`, `choices`, company and track scopes |
| `resume_terms.yaml` | example only | terms counted by the resume coach |
| `triage.yaml` | example only | triage model, effort, prices and per-run cost cap |
| `apply.yaml` | example only | follow-up reminder days, archive suggestions, deadline window |
| `skills.yaml` | example only | focus areas and per-run quotas for the Stage 5 finder |
| `conferences.yaml` | example only | exhibitor/sponsor lists for the Stage 5 finder |
| `gmail_client.json` | no | the Desktop OAuth client for Gmail (Stage 6), downloaded from Google Cloud |
| `views.yaml` | example only | saved views for the list screens |

The table names runtime files under `config/`; Git contains only their
`.yaml.example` counterparts. Example applicant details are fictional and must be
replaced locally before filling real forms.

Every file is validated on load, and errors point at the line:

```
config/tracks.yaml:6  tracks.1.weekly_targt: Extra inputs are not permitted
    fix: unknown key - remove it or check the spelling
```

## Database

SQLite at `data/apply.db` is the single source of truth. Schema changes are
numbered `.sql` files under `src/applyai/db/migrations/`, applied in order and
recorded in `schema_version`; `applyai init` migrates in place.

Two design points worth knowing:

* `status_events` is **append-only**. Undo appends a reverting row that points at
  the row it undoes, so the history shows both steps. Gmail (Stage 6) writes rows
  with `state='proposed'`, which change nothing until I confirm them.
* `jobs.queue_state` (new / queued / later / skipped / not_eligible / applied) is
  separate from `applications.status`. Skipping a job never creates an
  application row.

## Tests

```bash
uv run pytest
```

The TUI tests drive the real app through Textual's pilot, so a broken key binding
fails in CI rather than in front of me.

## Stages

1. **done** - tracker: config, schema, core, CLI, Dashboard / Jobs / Pipeline / Job Detail, CSV import, demo data
2. **done** - discovery: repo feeds, watchlist ATS boards, Indeed, USAJOBS and HN; direct engineering employers, research programs, YC internships and bounded opt-in web search; dedupe, cross-track routing, rules-only eligibility, Inbox + Sources screens
3. **done** - LLM triage: tiers, resume pick and checked edit notes; estimate + confirm + hard cost cap; `--now` and `--batch`; cached per job
4. **done** - Today digest (TUI landing tab + `applyai today`, optional ntfy/Telegram push), Apply session, follow-up reminders, hand "not eligible", answering logged questions, weekly targets + streak, `applyai schedule print`
5. **done** - niche-company finder: YC, HN, conferences, GitHub, SBIR awards; skills.yaml scoring, per-area quotas, hard cap 15, approve/reject on the Finder tab
6. **done** - Gmail read-only status proposals (confirm with a key), response rates by resume / track / source / tier (`applyai report`, Reports tab), `applyai export` (CSV/JSON, formula-safe)
7. **done** - MCP server over `core/` (`applyai mcp`); own Firefox form filler (`applyai apply`; fills, never submits) and the resume coach
- **2026-10-03** - favorites (`*`), "more like these" best-first ranking, `applyai autofill` for favorites (fills, never submits)
- **Local resume editing** - deterministic candidate HTML/PDF, exact evidence-backed review, immutable versions, explicit promotion/rejection and rollback; no hosted AI or invented facts.
