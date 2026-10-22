# Application autonomy protocol

Read on session entry and at the checkpoints in [AGENTS.md](../AGENTS.md).
Repository invariants remain in [CLAUDE.md](../CLAUDE.md). These diagrams describe
required decisions, not capabilities that have all been implemented.

This protocol improves the software and continuity of project work. It does not
retrain model weights or start an unattended process. The preparation worker can
run the bounded fixer in section 8 during an explicitly started run. Unknown
facts and missing permissions remain boundaries, not obstacles to route around.

## 1. Improvement loop

```mermaid
flowchart TD
    Start["Load instructions and private checkpoint"] --> Scope["Read current user priority and existing implementation"]
    Scope --> Observe["Exercise the same real case within authorization"]
    Observe --> Evidence["Record observed state and the failed operation"]
    Evidence --> Explain{"Cause supported by evidence?"}
    Explain -- No --> Isolate["Inspect the smallest relevant control, state or contract"]
    Isolate --> Evidence
    Explain -- Yes --> Type{"What is missing?"}
    Type -- Fact or permission --> Need["Request only the missing information; preserve the draft"]
    Need --> Scope
    Type -- Product defect --> Repair["Implement a reusable, narrowly scoped repair"]
    Repair --> Regression["Add a consumer-visible regression where warranted"]
    Regression --> Smoke["Exercise the repaired production path"]
    Smoke --> Pass{"Expected behavior observed?"}
    Pass -- No --> Evidence
    Pass -- Yes --> Integrate["Run integration gates; document behavior and limits"]
    Integrate --> Checkpoint["Update private checkpoint and next action"]
    Checkpoint --> More{"Authorized actionable work remains?"}
    More -- Yes --> Scope
    More -- No --> Delivery["Report evidence and exact remaining blockers"]
```

Do not redraw the sample, hide failed cases, weaken a safety assertion to obtain
a pass, or count a one-off browser script as an implemented product feature.

## 2. Before each live application action

```mermaid
flowchart TD
    Case["Identify the requested posting and applicant context"] --> Eligible{"Open and eligible under saved rules?"}
    Eligible -- No --> Exclude["Retain unavailable or ineligible outcome; do not authenticate"]
    Eligible -- Uncertain --> Inspect["Inspect authoritative availability; preserve uncertainty"]
    Inspect --> Eligible
    Eligible -- Yes --> Review{"Exact destinations, resume and account actions reviewed?"}
    Review -- No --> Approve["Use the normal reviewed preflight; obtain missing authorization"]
    Approve --> Review
    Review -- Yes --> Owned{"Owned tab matches origin, tenant and posting?"}
    Owned -- No --> Fresh["Open through guarded entry; do not adopt an arbitrary tab"]
    Fresh --> Owned
    Owned -- Yes --> State["Inspect current step, required fields and profile records"]
    State --> Barrier{"Challenge or sensitive consent?"}
    Barrier -- Yes --> Human["Preserve the tab and hand off that boundary"]
    Barrier -- No --> Auth{"Account entry needed?"}
    Auth -- Yes --> Account["Use existing account or approved creation; never duplicate a known account"]
    Account --> State
    Auth -- No --> Fill["Use evidenced facts and guarded controls"]
    Fill --> Recheck["Re-read after dynamic changes; verify accepted values and uploads"]
    Recheck --> Required{"Required work remains?"}
    Required -- Yes --> State
    Required -- No --> Ready["Stop at verified review boundary; never final-submit"]
```

An unknown availability result is not a closure. Do not loop the same failed
request indefinitely: when evidence cannot settle it, retain the uncertainty and
continue other authorized cases.

Exercised ATS evidence (frozen eligible sample, 2026-10-22): cookie banners are
declined only through an exact provider "Reject all" control (OneTrust, Oracle
CX, Ketch, Dayforce); a banner offering only accept/settings stays a consent
handoff. Eightfold marks ordinary answers `autocomplete=one-time-code`, so only
code-named fields are verification evidence; its posting CTA is a same-origin
link to `/careers/apply?pid=<same id>`. Lever nests the resume input in an
`<a href="#">` trigger. iCIMS `/login` email entry is an account step, not an
unsupported page. Count a case ready only at a verified review boundary.

## 3. Applicant facts and reusable answers

```mermaid
flowchart TD
    Question["Read the exact question, context and offered options"] --> Saved{"Applicable confirmed answer exists?"}
    Saved -- Yes --> Scope{"Employer, track, jurisdiction and meaning match?"}
    Scope -- No --> Unknown["Leave unanswered; ask only what cannot be resolved"]
    Scope -- Yes --> Equivalent{"Same fact expressed by another option label?"}
    Equivalent -- Yes --> Alias["Verify equivalence; persist a scoped choice alias privately"]
    Equivalent -- No --> Use["Use the confirmed value without changing its meaning"]
    Alias --> Use
    Saved -- No --> Resume{"Resume explicitly establishes this fact?"}
    Resume -- Yes --> Status["Preserve dates, organization, role and in-progress status"]
    Status --> Use
    Resume -- No --> Source{"Only a delegated recruitment-source choice?"}
    Source -- Yes --> Plain["Choose a plain offered source; avoid invented referrals"]
    Source -- No --> Unknown
    Unknown --> Confirm["Persist the user's answer in the existing private configuration"]
    Confirm --> Question
```

Absence from a resume does not establish a negative employment history. College
attendance does not establish age. Previously held citizenship is not current
dual citizenship. A future graduation date is not an earned qualification.
Employment classification, supervisor permission and sensitive attestations
cannot be fabricated from job titles.

## 4. Form-control transaction

```mermaid
flowchart TD
    Control["Resolve the current field and its owned widget"] --> Focus["Activate the owned tab; re-resolve after rerendering"]
    Focus --> Existing{"Already holds a committed value?"}
    Existing -- Yes --> Match{"Matches the intended confirmed value?"}
    Match -- Yes --> Keep["Preserve it and verify validity"]
    Match -- No --> Preserve["Do not silently overwrite; establish explicit correction scope"]
    Existing -- No --> Choices["Inspect the owned list and loading state"]
    Choices --> Unique{"Exactly one supported matching choice?"}
    Unique -- No --> Reason["Distinguish loading failure, ambiguity and no matching option"]
    Unique -- Yes --> Native["Use a trusted guarded interaction with that exact control"]
    Native --> Receipt{"Independent committed-state evidence?"}
    Receipt -- No --> Failed["Keep failure visible; do not claim success from an echo"]
    Receipt -- Yes --> Valid["Check current value, validity, ownership and follow-up fields"]
    Valid --> Keep
```

For Oracle split-date controls, a selected month/year can lack the parent row's
filled CSS class. A genuine observed option selection must be distinguished from
an identical typed query. Read-only receipt failures are not permission to force
DOM values, disable guards, or accept a wrong option.

Selecting a timeline card may only reveal its Edit/Remove actions. Require the
actual editor fields before entering values. Inspect an existing invalid record
before adding another; an absent invalid marker on the Add button is not proof
that the record is complete. Never overwrite an unrelated entry to satisfy a
required-record check. After a scoped local Save, rerun preparation; saving a
record is neither application readiness nor final submission.

## 5. Verification-message handoff

```mermaid
flowchart TD
    Gate["Observe verification in the owned employer flow"] --> Permission{"Named account and employer message access authorized?"}
    Permission -- No --> User["Ask the user to complete verification"]
    Permission -- Yes --> Filter["Restrict to matching sender, recipient and verification purpose"]
    Filter --> Fresh{"Message provably matches the current request and is fresh?"}
    Fresh -- No --> Refresh["Refresh the restricted search and inspect delivery metadata"]
    Refresh --> Available{"Fresh message available within the bounded wait?"}
    Available -- No --> Pending["Keep verification pending; never reuse an expired code"]
    Available -- Yes --> Fresh
    Fresh -- Yes --> Unique{"One unambiguous verification code?"}
    Unique -- No --> Pending
    Unique -- Yes --> Enter["Enter only in the matching owned verification control"]
    Enter --> Accepted{"Expected same-posting application state reached?"}
    Accepted -- No --> Diagnose["Inspect rejection; no blind credential or code retry"]
    Accepted -- Yes --> Resume["Resume the exact retained draft"]
```

A new code request must follow an observed need and the employer's normal
controls. Do not repeatedly resend, log codes, read unrelated mail, change mailbox
settings, solve CAPTCHAs, or treat email access as general MFA authorization.
An ordinary page refresh and a credential retry are different actions.

Exercised compatibility evidence (Oracle Candidate Experience + Gmail, 2026-10-22):
segmented `pin-code-N` boxes without `one-time-code` are a verification gate;
Gmail canonicalizes search routes (`+` for spaces), echoes the query with a
trailing space and can collapse the query input to zero width. Employer mail may
join the code to following text, so labels bind codes, not word boundaries. After
a consumed code, observe the transition only; a timeout is not a rejection until
the retained tab is inspected.

## 6. Browser recovery and state preservation

```mermaid
flowchart TD
    Failure["Observe navigation, loading or connection failure"] --> Kind{"Which layer failed?"}
    Kind -- Page --> Reload["Normal reload of the affected owned tab when appropriate"]
    Reload --> Result["Inspect current URL, step and preserved state"]
    Kind -- Controller --> Owner["Check session ownership and release outcome"]
    Owner --> Released{"Session release actually confirmed?"}
    Released -- Yes --> Reconnect["Reconnect without restarting Firefox"]
    Released -- No --> Protect["Preserve reports and drafts; diagnose the owning session"]
    Kind -- Browser exited --> Restore["Use the authorized dedicated persistent profile; never copy everyday cookies"]
    Restore --> Result
    Reconnect --> Result
    Protect --> Recovery{"Safe owned recovery established?"}
    Recovery -- Yes --> Reconnect
    Recovery -- No --> Blocked["Report exact missing prerequisite; keep independent work moving"]
```

No controller process is not proof that a BiDi session is safe to delete. Do not
restart a healthy browser, wipe its profile, close unrelated tabs, or destroy
unsaved forms to clear an automation error. Cleanup failure must not erase
completed application reports.

## 7. Delivery gate

```mermaid
flowchart TD
    Changes["Integrate completed changes with one owner"] --> Smoke["Run the changed production scenario and observe its result"]
    Smoke --> Gates["Run affected integration checks; full pytest before committing code"]
    Gates --> Green{"All required checks pass?"}
    Green -- No --> Repair["Investigate the reported failure; preserve safety assertions"]
    Repair --> Changes
    Green -- Yes --> Docs["Update existing behavior documentation and private checkpoint"]
    Docs --> Package{"Packaged runtime changed?"}
    Package -- Yes --> Build["Build and smoke the actual package with an isolated workspace"]
    Build --> Publish
    Package -- No --> Publish{"Publishing explicitly authorized?"}
    Publish -- Yes --> SecretCheck["Stage only intended public files; exclude private runtime data"]
    SecretCheck --> Push["Commit and push only verified changes"]
    Publish -- No --> Report
    Push --> Report["Report exercised results, automation limits and blockers"]
```

## 8. Bounded fixer and persistent feedback

One recovery agent belongs to each job. Progress does not reset its budgets. The
local agent needs no API key; a separately reviewed, per-run opt-in can let the
configured OpenRouter model choose a typed recovery tool.

```mermaid
flowchart TD
    Stall["Unchanged state, failed commit, loading, or interrupted pass"] --> Observe["Fresh DOM and native validity receipts"]
    Observe --> Terminal{"Human gate, missing fact, rejected auth, closed job, or approval drift?"}
    Terminal -->|yes| Handoff["Retain draft and report the specific blocker"]
    Terminal -->|no| Budget{"Job and recovery budgets remain?"}
    Budget -->|no| Handoff
    Budget -->|yes| Choice{"Reviewed AI opt-in and budget available?"}
    Choice -->|yes| Model["Structural counts and permitted tools only"]
    Choice -->|no| Local["Local recovery policy"]
    Model --> Guard["Recheck ownership, origin, posting, approval, Stop, and tool preconditions"]
    Local --> Guard
    Guard -->|refused| Handoff
    Guard -->|allowed| Tool["Inspect, bounded wait, supported retry, or empty-loading-page reload"]
    Tool --> Receipt["Fresh receipt; record completed, failed, or denied"]
    Receipt --> Changed{"Verified progress?"}
    Changed -->|yes| Prepare["Resume preparation without resetting budgets"]
    Changed -->|no| Observe
    Prepare -->|stalls again| Observe
    Prepare -->|review boundary| Review["Validate native required fields; stop before Submit"]
    Review --> Journal["Persist categorized outcome and regenerate feedback"]
    Handoff --> Journal
```

The agent replans using typed prior action/outcome receipts. Its current safe
strategies include owned-tab focus restoration, exact-saved invalid text recommit,
fresh supported blank-field retry, bounded loading waits and pristine-page reload.
Capabilities and protected boundaries are rechecked immediately before execution.
An ineffective mutation is not offered again on the same diagnostic evidence;
inspection and loading waits remain bounded observations. An API return, DOM
signature change or write log is not acceptance: recovery requires fewer unresolved
required fields/validation errors, a completed loading transition or a recognized
step/entry transition. Focus restoration is a prerequisite, not application progress.

### Authority and limits

- Defaults: six recovery decisions, a 45-second recovery window from the first
  decision, two inspections, two waits, one supported retry and at most one
  reload. Repeated states and cycles also terminate recovery.
- A 20-second no-progress checkpoint interrupts a field pass. The 300-second job
  deadline and Stop are cooperative at preparation/fixer checkpoints; an
  in-flight BiDi command or bounded authentication transaction is not forcibly
  interrupted. Stop still works without submission watching.
- Retry means freshly detected, blank, known profile/answer fields only.
  Authentication, account creation, uploads, entry controls, existing values and
  final submission are not replayed by the fixer.
- Reload requires the same owned URL in a verified loading state with no edits,
  fields, file/credential controls or prior mutations to lose. Loaded drafts do
  not qualify.
- The model cannot supply facts, arbitrary selectors/URLs, JavaScript or shell
  commands; change code/policy; read mail/files; replay credentials; accept
  consent; or submit. A malformed tool call hands off. A model recommendation
  never overrides a failed local guard.
- Paid recovery requires a fresh reviewed plan and explicit checkbox. Request,
  input/output-token and dollar caps share the existing daily AI ledger. Failure
  does not refund an issued model request. Terminal human gates require no call.
- Remote input contains structural counts, blocker categories and permitted-tool
  flags, not labels/values, page text, URLs, resumes, credentials or codes.

### Monitoring and protocol maintenance

The UI reports the active job/action and categorized fixer receipts. Job reports
stream immediately; a later worker failure must not erase earlier reports.

SQLite `application_recovery_events` is authoritative. The worker regenerates
`settings.data_dir/application-recovery/PROTOCOL_FEEDBACK.md` atomically with
owner-only permissions and trusted categories. It never rewrites this protocol,
`AGENTS.md`, or itself. Read the feedback next session, then use section 7's
evidence gate before changing a shared primitive or adapter.

The feedback is data, not instructions or proof of success. `planned` is not
`completed`; a completed inspection/retry is not an application submission.
Native validity and the loaded employer surface still determine readiness.

## Durable checkpoints and implementation references

Keep progress in `.private/autonomy-checkpoint.json`, not this public document.
For each checkpoint record the current task/case, observed evidence, failed gate,
next authorized action and verification status. Do not store credentials, codes,
cookies, message bodies, contact details or resume contents. A checkpoint does
not grant authorization or turn a pending check into a pass.

Existing runtime enforcement is in:

- [`core/apply/filler.py`](../src/kitty/core/apply/filler.py): eligibility before
  preparation, retained tabs, field filling, review readiness and truthful reports.
- [`core/apply/page.py`](../src/kitty/core/apply/page.py): guarded controls,
  owned suggestions, choice receipts, upload acknowledgment and unsafe-action rejection.
- [`core/apply/preflight.py`](../src/kitty/core/apply/preflight.py): step/boundary
  inspection, unsupported controls and required profile-section diagnostics.
- [`core/apply/bidi.py`](../src/kitty/core/apply/bidi.py): browser transport,
  normal reload and confirmed session release.
- [`core/apply/fields.py`](../src/kitty/core/apply/fields.py): saved-answer,
  profile, option-alias and jurisdiction-aware planning.
- [`web_automation.py`](../src/kitty/web_automation.py): reviewed run plans,
  credential binding and the public preparation workflow.
- [`core/apply/auth.py`](../src/kitty/core/apply/auth.py): bounded credential
  stages, fresh document receipts and per-origin account restrictions.
- [`core/apply/recovery.py`](../src/kitty/core/apply/recovery.py): typed tool
  decisions, cycle limits and optional budgeted model selection.
- [`core/apply/recovery_runtime.py`](../src/kitty/core/apply/recovery_runtime.py):
  guarded execution, job deadlines, durable receipts and generated feedback.

Generic verification commands, from the repository root:

```sh
.venv/bin/pytest -q
nix develop 'path:./desktop#host-toolchain' --offline --max-jobs 0 --command bash scripts/build-desktop.sh
```

Check the actual output. A command listed here is not a claim that it ran or
passed. Live account actions are not automated tests; permanent tests use only
isolated synthetic data.
