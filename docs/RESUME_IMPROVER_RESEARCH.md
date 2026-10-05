# Statistics-driven resume improver research

Research reference date: 2026-10-04. The local deterministic editor now creates
candidate HTML/PDF artifacts with evidence-backed patches, exact review, immutable
versions and explicit promotion/rollback. This document distinguishes that bounded
implementation from the broader rewriting capabilities evaluated below. It does not
claim a new runtime verification result or a hosted-product benchmark.

## Implemented local contract

The read-only coach (`core/resume_coach.py`) counts configured vocabulary by track.
`core/resume_editor.py` proposes bounded edits; `core/resume_versions.py` manages
local rendering, validation, artifacts and decisions. Editable HTML and associated
CSS are private local inputs, not published applicant examples.

- Freeze the active, non-ineligible posting cohort, source coverage, timestamps and
  deduplication policy. Compare raw posting counts with distinct-employer demand;
  keep unrelated tracks separate and require a minimum sample.
- Anchor original resume facts and source hashes. JDs supply quoted evidence of
  demand, never applicant facts or executable instructions.
- Apply narrowly audited clarity substitutions and demand-ranked ordering of
  already-supported skills/bullets within their semantic sections. Preserve contact,
  education, dates, numerical claims, project status, qualifications, links and style.
- Write a distinct candidate source/PDF and audit manifest. Exact patches, diff,
  fact anchors, JD evidence, coverage, warnings, review items and validation are
  available before a decision. Missing skills are not silently inserted.
- Render locally from frozen resources and validate PDF text/layout. Failed
  validation, changed source or mismatched artifact hashes block promotion.
- Explicit promotion changes only the default for future unchosen jobs. Existing
  job choices, open attachments and historical application associations stay fixed.
  Rejection retains audit artifacts; rollback restores the prior default.

No hosted AI, resume uploads or paid AI calls are part of this editor. Vocabulary
frequency and supported coverage are measurements, not interview probabilities or
universal ATS scores. Lexical misses require alias/evidence review, not invented
experience; for example, “technical communication” can be an alias of communication.

### Commands and review

```sh
uv run applyai resumes improve --track <TRACK> --source resumes/source.html
uv run applyai resumes versions --track <TRACK>
uv run applyai resumes review <VERSION> --open
uv run applyai resumes promote <VERSION>
uv run applyai resumes reject <VERSION>
uv run applyai resumes rollback --track <TRACK>
```

`--source` may be omitted only when unambiguous. `--min-postings` sets the minimum
sample (default 5). Generation creates a candidate, never a new default.
On Resume (`F7`), `g` generates, `v` opens versions/exact review, and `b` requests
rollback. In exact review, `o` opens the local PDF, `p` explicitly approves/promotes,
and `x` rejects. Promotion and rollback require confirmation.

## Tools worth comparing

Features below are documented by the vendors/projects, not a hands-on benchmark or an endorsement of their hiring-outcome claims. Free limits and subscriptions can change.

| Tool | Relevant documented capability | Fit / limitation for ApplyAI |
| --- | --- | --- |
| [Jobscan AI Optimize](https://www.jobscan.co/resume-bullet-point-generator) | Upload a resume, compare a target JD, select a skill to generate bullets, then copy the phrase into the resume document. Bullet generator is a Premium feature. | Useful second opinion for JD matching. Its documented copy/paste workflow is not automatically editing our local HTML. A proprietary match score is not a universal ATS score. |
| [Teal AI Bullets](https://help.tealhq.com/en/articles/9519206-using-ai-bullets) | Improve existing bullets, choose among alternatives, generate from a connected JD/keywords/custom prompts, then Use Bullet and Save inside its builder. | Good reference for an evidence + choice + save UI. Teal explicitly warns generated metrics may be inaccurate; this is exactly what ApplyAI must reject rather than copy automatically. |
| [Rezi AI Resume Agent](https://www.rezi.ai/rezi-docs/ai-resume-agent) and [Rezi MCP](https://www.rezi.ai/resume-mcp) | Agent analyzes/rewrites/tailors sections; MCP advertises targeted edits and saving changes to the Rezi account from compatible AI clients. | Closest documented off-the-shelf agent-controlled editor. Requires a Rezi account/authentication and moving resume data into that service. MCP integration was not installed or exercised. Its suggestions still require fact validation. |
| [Resume Worded](https://www.resumeworded.com/score) | Checks impact, brevity, wording, formatting, and bullet quality; paid deeper feedback/rewriting and AutoFix are advertised. | Useful writing-quality comparison, not evidence of skills or guaranteed callbacks. Do not invent quantities just to satisfy a scoring heuristic. |
| [Reactive Resume](https://github.com/reactive-resume/reactive-resume) | Open-source/self-hostable resume editor. The inspected main-branch README advertises structured exports, live preview, document versions, AI proposed edits, job keyword checks, REST API and MCP. | Best external architecture to investigate if a full visual editor is desired. **The README labels that branch upcoming v6**: verify released-version support before depending on those specific APIs/features. Self-hosting also adds an application to maintain; external AI still sends data to the configured provider unless local. |

No need to subscribe to all five. For this repository, prefer the local implementation; use Jobscan/Resume Worded optionally for an independent review, or evaluate Rezi MCP if a hosted editing workflow is explicitly preferred. Reactive Resume is the stronger candidate for a self-hosted visual-editor comparison, subject to release verification.

## Editing transport: local HTML versus a document application

### Local HTML + PDF — recommended

The implemented path edits a source copy structurally, not PDF text. It preserves
CSS and section structure, renders one candidate to a distinct path, and retains
hash-anchored source/PDF snapshots with a revision ledger. The original document
and registered historical bytes are not preview output targets.

Advantages: no extra account, retained layouts, reviewable edits and local storage.
Limits: conservative supported HTML structures and bounded transformations; there
is no dedicated visual source editor or arbitrary prose rewriting.

### Google Docs — optional later

Google's [documents.batchUpdate API](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate) supports atomic groups of edits, OAuth authorization, and revision controls. The inspected documentation also lists `writeMode: SUGGEST`. Prefer the API with `requiredRevisionId` and a separately authorized document over browser click/typing automation. Test suggestion support and export/layout fidelity before adopting it. Do not request whole-Drive access merely to edit one selected document.

A Docs integration would need an explicit source-of-truth decision and import/export mapping. Maintaining both HTML and a cloud document as independently editable masters risks silently overwriting user changes. It adds setup and is not needed to deliver the requested automatic local editing behavior.

## Workflow boundaries and remaining rewriting gaps

1. **Select the target.** Implemented generation targets one track and freezes its cohort. Per-job tailoring is a separate future design, not an implied capability.
2. **Build a fact inventory.** Existing source facts receive anchors. Additional user-confirmed facts need an explicit authoring/confirmation workflow before any broader generator could use them.
3. **Rank edits.** Employer-weighted evidence can reorder supported content. It cannot establish a new skill, stronger responsibility or achievement.
4. **Produce bounded patches.** Each change records original text, replacement, fact anchors, JD quotes and reason. Arbitrary bullet composition, section synthesis and unrestricted paraphrasing remain unimplemented.
5. **Write a candidate.** The implementation writes candidate HTML/PDF rather than copy/paste notes. It never replaces an attachment already used by an open application.
6. **Validate.** Content and artifact checks are promotion gates, not guarantees of every viewer's rendering or hiring outcomes. Inspect the PDF; do not hide overflow by shrinking text.
7. **Version and decide.** Explicit promotion/rejection and rollback retain immutable artifacts and historical associations. There is no automatic promotion mode.

Useful confirmation prompts should be narrow: “Have you used an oscilloscope, and in which project?” rather than asking the user to author a whole resume. If the answer is unknown, no claim is added. Wording changes can be automatic when they remain supported; factual embellishment cannot.

## Safety criteria for implemented and future editing

- A real cohort produces inspectable counts and JD evidence; required/preferred/unknown classifications are explicit.
- The editor writes a real candidate source/PDF and shows the exact diff and visual preview.
- Only verified experience is rewritten; graduation/standing, technical qualifications, metrics, and project completion status cannot drift.
- A “target latency” cannot become an “achieved latency”; mentoring/team/project work cannot become an invented job or promotion.
- Uncertain keyword matches are review items, not silent additions. Existing aliases such as technical communication are reconciled first.
- Rendered output remains readable and extractable, with the intended one-page layout; overflow is reported, not hidden by shrinking text indefinitely.
- Promotion and rollback preserve historical resume versions and application associations.
- Hosted LLM editing is not implemented. Any future integration would need separate approval, privacy/redaction controls and cost estimates/caps; the current editor makes no hosted requests.
- Measure evidence-supported coverage, accepted/rejected edits, readability and artifact validity. Interview response rates can be tracked by immutable version once enough applications exist, but tiny/confounded samples cannot establish causal improvement.

The local HTML/PDF path needs no third-party signup or Google Docs setup. Remaining
open-ended rewriting requires a stronger supported-fact authoring and validation
design, not simply inserting the most frequent terms. Google Docs synchronization,
hosted agents and a visual editor remain optional research, not enabled features.
