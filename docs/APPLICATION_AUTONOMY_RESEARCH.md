# Application autonomy and privacy research

Research checkpoint: 2026-10-06 (sign-in automation added 2026-10-09). Scope: reduce manual preparation and agent orchestration without inventing answers or submitting applications. Preparation states, safe resumption, guarded Workday navigation and metadata-only MCP preparation planning are implemented. Coding tasks now proceed end-to-end within existing permissions. Sign-in and account creation are automated only when explicitly enabled for exact approved origins; challenge solving, consent and submission remain human boundaries.

## Provider capabilities and application gates

Guest application forms can expose contact, resume, education and screening fields
without an account. This does not guarantee challenge-free submission. Workday
tenants can instead route Apply → Apply Manually to Create Account/Sign In;
Apply Manually is not a promise of guest access. A closed posting is a separate
condition, not a login failure. Check authoritative current details rather than
treating a saved active state as proof of a usable application.

Workday is not universally account-required: its [Candidate Home setup documentation](https://doc.workday.com/admin-guide/en-us/human-capital-management/recruiting/career-sites/gtv1538650489786.html) lets each tenant require accounts; when that requirement is off, candidate verification email is enabled. The filler inspects the actual flow instead of inferring a login requirement from the hostname.

[MyGreenhouse's candidate FAQ](https://support.greenhouse.io/hc/en-us/articles/43418495049499-MyGreenhouse-FAQ-for-Candidates) describes a reusable, password-free portal account with email security codes. That optional convenience is distinct from employer guest application forms. The FAQ also states that employers control application data received from candidates; deleting a portal profile does not delete employer-held applications.

## Implemented reliability boundaries

- `core/apply/fields.py` plans only profile.yaml and answers.yaml values. Unknown screening answers stay blank; demographics, consent and attestations are not inferred.
- Dropdowns require a committed choice after blur, not a typed search query. Verified acronym decorations (such as `ECE - Electrical and Computer Engineering`) can match their full names; ambiguous, disabled and different-program choices remain unselected.
- `core/apply/preflight.py` recognizes actual closed/login/verification/challenge states. Workday entry and Next/Continue actions require known structure, exact non-final labels and repeated guards immediately before clicking. Unknown layouts stop unsupported; loops and transitions are bounded.
- Required fields, custom-control support, validation errors across application frames and upload completion are checked before readiness. A local file selection or a passive CAPTCHA iframe alone proves neither upload completion nor a challenge.
- Greenhouse's completed upload receipt is recognized when it replaces the native input. Its labelled upload group, visible filename and removal control must be present, without pending/error state.
- Preparation is independent of submission: unattempted → attempted → partial/blocked/ready. Migration 007 preserves legacy fill timestamps as attempts, never as readiness. Only ready is excluded from automatic retries.
- Retained tabs must match the recorded context and application identity. Existing values and attachments are preserved; lost contexts restart saved facts with an explicit warning. A new upload on a populated retained form is deferred to the user because employer-side resume parsing may overwrite manual values.
- Workday gate/review recognition retains actual `current step N of M` prefixes as evidence and normalizes them for matching. Detecting a gate does not authorize entering credentials or creating an account.
- Website-reviewed Google session reuse binds the Profile email and employer origins to one run. Only an exact already-signed-in account on the official Google chooser can be selected, in the employer tab or its explicitly associated new popup. Employer passwords are never sent to Google. Account addition, consent, verification and security blocks require manual completion in the same dedicated Firefox; resumed tabs still require application identity.
- `kitty refresh` checks bounded favorites/top-ranked stored postings against authoritative details. Timestamped source evidence, authority arbitration and identity checks are separate from discovery sightings. Stale or unverified is not the same as closed.
- Today, notifications, Doctor and Sources expose partial failures with DNS, robots, cooldown and coverage guidance. No new source expansion or paid service is required for these repairs.

## Bounded agent planning

`preparation_plan(track?, limit?)` delegates to the existing `autofill.plan_run`:
favorites-first ordering, optional track scope, configured hard cap, and explicit
exclusion reasons. Ready forms are excluded based on their last verified attempt;
blocked/partial attempts remain candidates, not promises of successful completion.
Job summaries expose stored state, attempt/readiness timestamps and blockers.
Browser-control context IDs and retained URLs are not returned.

This is metadata inspection, not browser execution or a reservation. It does not
launch Firefox, contact employers, fill forms, invent missing answers or change
application/status history. Like existing MCP readers, workspace initialization
may migrate the database and sync local configuration. A read-only tool annotation
does not imply zero SQLite writes.

The conservative choice is to let agents inspect the next batch and its blockers
without adding broad browser authority:

- [OpenAI computer-use guidance](https://developers.openai.com/api/docs/guides/tools-computer-use#run-safely)
  recommends isolated environments, allowlisted actions, bounded/verifiable runs,
  and treating screen/tool content as untrusted. It explicitly treats typing
  sensitive information into a form as transmission: stopping before Submit is
  not the same as read-only activity.
- The [MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
  describes model-controlled tools but recommends human ability to deny calls and
  visible invocation/confirmation controls. Tool annotations are hints, not an
  authorization boundary, and are untrusted unless the server is trusted.
- Actual filling stays in the existing explicitly invoked local workflow.
  Verification, consent, unknown answers and final submission (and authentication,
  unless explicitly enabled for approved origins)
  still require human handling. No hosted-model calls or new paid services were
  added. Employer text and stored blockers remain data, never agent instructions.

## Remaining reliability research

1. **Tenant-specific controls and step layouts.** Workday's [Career Sites documentation](https://doc.workday.com/workday-education/en-us/course-manuals/recruiting-for-administrators/career-sites.html) lists configurable contact, experience, questions, voluntary disclosures, terms and final-review sections. Candidate Home can prepopulate existing data. A successful fixture does not prove every tenant's custom fields; unsupported structures must remain explicit blockers.
2. **Consent and challenges stay human boundaries.** Apply Manually is not Apply Without Account. A tenant may require Create Account/Sign In; the opt-in `authentication` setting can fill one plain sign-in or sign-up form on an exact approved origin, using the dedicated `.private` credentials. Do not copy everyday-browser cookies or automate verification, CAPTCHAs or legal acceptance.
3. **Upload evidence varies by ATS.** Selected files, server acknowledgement and resume parsing are different stages. Pending uploads cannot authorize ready. A review-only page may lack proof of prior required answers or attachment completion, so it remains conservative rather than claiming readiness.
4. **Authoritative coverage is incomplete.** Some employer portals are JavaScript-only, blocked by robots, account-gated or lack a uniquely identifiable detail record. Refresh must abstain rather than attach another job's description or interpret transport errors as closure.
5. **Account assistance is an explicit approval boundary.** Credentials live only in `.private/application-credentials.env`, employer scope is the exact `approved_origins` list, account creation needs its own `allow_account_creation`, and verification and terms consent are handed back to the user. Real tenant sign-in pages (Workday overlays, iCIMS, Taleo) were not exercised; unrecognized layouts stop rather than guess.

Success is verified forms ready for the user's review plus accurately recorded submissions, not fields touched or imported posting counts. No promise of filling every employer application unattended.

## Privacy recommendations and limits

- Keep the dedicated local profile and local-only personal configuration. Do not copy everyday-browser cookies, use disposable identities, or assume incognito hides identity from the employer.
- Minimize logs and retention of resumes, form content, session tokens, and browser-control access. The current Firefox launch uses `--remote-allow-system-access`; local programs able to reach its debugging interface have powerful access. Review whether this privilege can be removed in a separate compatibility-verified change.
- `navigator.webdriver` suppression already exists in browser.py. It is not anonymity: the employer can still receive network information and every value entered/uploaded. Masking one browser signal cannot guarantee undetectability or prevent fingerprinting.
- Prefer normal Firefox privacy protections over custom fingerprint spoofing or proxy rotation. [Mozilla's tracking-protection guidance](https://developer.mozilla.org/en-US/docs/Web/Privacy/Guides/Firefox_tracking_protection) explains that blocking third-party dependencies can break page behavior; validate uploads, location lookups, and verification widgets before stronger settings are enabled.
- Applications intentionally disclose identity and qualifications. The achievable target is less unnecessary tracking and less manual repetition, not anonymous applications. Required authentication, verification, CAPTCHA, robots restrictions, and rate limits remain boundaries, not obstacles to bypass.

## Coding-assistant autonomy

The project policy now authorizes routine requested development through research,
implementation, affected tests/docs and isolated synthetic verification without
asking again at every phase. Independent slices can proceed while a different
slice waits for a real decision. Existing authorization is reused rather than
repeatedly requested; completion requires observed behavior and accurate reporting.

The policy retains approval for material unresolved product choices and
destructive, paid, sensitive or externally consequential actions that were not
already authorized. It does not change the assistant harness, disable permission
prompts, widen a sandbox, authorize publication, or weaken application safeguards.

Evidence and recommendation:

- [OpenAI agent approvals and security](https://developers.openai.com/codex/sandbox)
  distinguishes technical sandbox access from when approval is requested. Its
  Auto workflow permits workspace reads/edits/commands without repeated approval
  while retaining configured boundaries. This supports fewer interruptions, not
  blanket full-access or approval bypass.
- [Anthropic coding-agent best practices](https://code.claude.com/docs/en/best-practices)
  recommends concrete verification criteria, concise project instructions and
  explicit isolation for parallel work. Here, one integration owner verifies
  combined changes using synthetic data and reports only checks actually run.
- [Anthropic sandbox guidance](https://code.claude.com/docs/en/sandboxing)
  distinguishes sandboxed command auto-approval from unrestricted execution and
  warns that shell sandboxing does not contain every tool or connector. Project
  instructions therefore supplement, not replace, enforced security controls.

These are project-level workflow decisions, not claims about the current
assistant client's sandbox configuration. No personal assistant settings changed.
