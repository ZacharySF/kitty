# Application autonomy and privacy research

Research and implementation checkpoint: 2026-10-04. Scope: reduce manual preparation and fill applications from verified profile/answers, without pre-creating accounts where guest applications exist. Privacy is separate from autonomy. Preparation states, safe resumption and narrowly guarded Workday navigation are implemented; account creation, authentication, challenge solving and submission are not.

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
- `core/apply/preflight.py` recognizes actual closed/login/verification/challenge states. Workday entry and Next/Continue actions require known structure, exact non-final labels and repeated guards immediately before clicking. Unknown layouts stop unsupported; loops and transitions are bounded.
- Required fields, custom-control support, validation errors across application frames and upload completion are checked before readiness. A local file selection or a passive CAPTCHA iframe alone proves neither upload completion nor a challenge.
- Preparation is independent of submission: unattempted → attempted → partial/blocked/ready. Migration 007 preserves legacy fill timestamps as attempts, never as readiness. Only ready is excluded from automatic retries.
- Retained tabs must match the recorded context and application identity. Existing values and attachments are preserved; lost contexts restart saved facts with an explicit warning. A new upload on a populated retained form is deferred to the user because employer-side resume parsing may overwrite manual values.
- Workday gate/review recognition retains actual `current step N of M` prefixes as evidence and normalizes them for matching. Detecting a gate does not authorize entering credentials or creating an account.
- `applyai refresh` checks bounded favorites/top-ranked stored postings against authoritative details. Timestamped source evidence, authority arbitration and identity checks are separate from discovery sightings. Stale or unverified is not the same as closed.
- Today, notifications, Doctor and Sources expose partial failures with DNS, robots, cooldown and coverage guidance. No new source expansion or paid service is required for these repairs.

## Remaining reliability research

1. **Tenant-specific controls and step layouts.** Workday's [Career Sites documentation](https://doc.workday.com/workday-education/en-us/course-manuals/recruiting-for-administrators/career-sites.html) lists configurable contact, experience, questions, voluntary disclosures, terms and final-review sections. Candidate Home can prepopulate existing data. A successful fixture does not prove every tenant's custom fields; unsupported structures must remain explicit blockers.
2. **Authentication and consent stay human boundaries.** Apply Manually is not Apply Without Account. A tenant may require Create Account/Sign In. Reuse the dedicated browser session after the user handles authentication; do not copy everyday-browser cookies or automate credentials, verification or legal acceptance.
3. **Upload evidence varies by ATS.** Selected files, server acknowledgement and resume parsing are different stages. Pending uploads cannot authorize ready. A review-only page may lack proof of prior required answers or attachment completion, so it remains conservative rather than claiming readiness.
4. **Authoritative coverage is incomplete.** Some employer portals are JavaScript-only, blocked by robots, account-gated or lack a uniquely identifiable detail record. Refresh must abstain rather than attach another job's description or interpret transport errors as closure.
5. **Just-in-time account assistance would be a separate approval boundary.** It still creates an account. Credential storage, employer scope, verification handoff and terms consent would need an explicit design first; none is enabled here.

Success is verified forms ready for the user's review plus accurately recorded submissions, not fields touched or imported posting counts. No promise of filling every employer application unattended.

## Privacy recommendations and limits

- Keep the dedicated local profile and local-only personal configuration. Do not copy everyday-browser cookies, use disposable identities, or assume incognito hides identity from the employer.
- Minimize logs and retention of resumes, form content, session tokens, and browser-control access. The current Firefox launch uses `--remote-allow-system-access`; local programs able to reach its debugging interface have powerful access. Review whether this privilege can be removed in a separate compatibility-verified change.
- `navigator.webdriver` suppression already exists in browser.py. It is not anonymity: the employer can still receive network information and every value entered/uploaded. Masking one browser signal cannot guarantee undetectability or prevent fingerprinting.
- Prefer normal Firefox privacy protections over custom fingerprint spoofing or proxy rotation. [Mozilla's tracking-protection guidance](https://developer.mozilla.org/en-US/docs/Web/Privacy/Guides/Firefox_tracking_protection) explains that blocking third-party dependencies can break page behavior; validate uploads, location lookups, and verification widgets before stronger settings are enabled.
- Applications intentionally disclose identity and qualifications. The achievable target is less unnecessary tracking and less manual repetition, not anonymous applications. Required authentication, verification, CAPTCHA, robots restrictions, and rate limits remain boundaries, not obstacles to bypass.
