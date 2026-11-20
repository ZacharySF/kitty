# Resume templates and the Career section look

The owner chose the look of rika!'s resume templates and of the Career (ApplyAI) section in a
grill-with-ui interview paired with a design lab of live mocks (lab worktree
`kitty-worktrees/resume-career-look-lab`, `web/lab.html`, not shipped). New templates share
one quiet black-and-white family: Source Serif headings over Source Sans body, a centred name
with a pipe-separated contact line, small-caps headings over full-width rules, balanced
10.5pt spacing. Career becomes a left rail with live counts (collapsing to icons inside the
Resume Workshop), opening on an Overview page; Find is a dense sortable job table whose fit
column counts skills covered and whose detail opens on decision buttons and Fit / Posting /
Application tabs; Track shows a funnel strip above a kanban board of compact cards.

## Terms

- **template**: one resume layout in the Resume Workshop that compiles to a PDF (today Clean
  HTML, Clean Typst, Clean LaTeX, Jake's Resume). Avoid: theme, skin.
- **Career section**: the ApplyAI part of rika! under the Career tab: Find (jobs), Track
  (pipeline), Saved, resumes, profile. Avoid: ApplyAI page, jobs app.
- **fit**: how well a posting matches you; today a tier chip such as Low match plus
  eligibility. Avoid: score, rating.

## Why

"Make the resume template look better … and we will change how it looks, same with the
apply-ai (career) section." Only Jake's Resume looked finished; the other templates and the
Career screens felt unfinished and inconsistent with the redesigned Resume Workshop.

## Locked decisions

### Q1 · Which new resume templates exist — **A Classic, B Modern, D Compact** (taken on recommendation at Finish; not answered explicitly)

| Option | UX | Correctness | Risk | Cost |
|---|---|---|---|---|
| **A Classic: Jake's layout refined** | good | good | good | good |
| **B Modern: one sans column, bold name, thin rule** | good | good | good | good |
| C Editorial: serif name, two-tone band, skills sidebar | good | mixed | poor | good |
| **D Compact: dense one-page for long histories** | mixed | good | good | good |

C lost on risk: its sidebar column is what the ATS check flags as two columns, so parsers may
read it out of order. The owner pressed Finish before answering; the recommended set is used
and can be revisited.

### Q5 · Colour — **A: none, black and greys only**

| Option | UX | Correctness | Risk | Cost |
|---|---|---|---|---|
| **A None** | mixed | good | good | good |
| B One accent on headings and rules | good | good | good | good |
| C Accent plus tinted backgrounds | good | mixed | mixed | good |

The owner chose against the recommendation (B). Its cost is less personality on screen; it
prints and parses perfectly. No template offers an accent colour.

### Q7 · Job list — **C: dense sortable table**

| Option | UX | Correctness | Risk | Cost |
|---|---|---|---|---|
| A Today's row tidied | mixed | good | good | good |
| B Two-line card | good | good | good | good |
| **C Dense table, sortable columns** | mixed | good | good | good |
| D Big cards in a grid | mixed | mixed | mixed | good |

Chosen over the recommended two-line card: more jobs per screen and column sorting. Columns
(Q16) and the fit cell (Q15) follow.

### Q8 · Job detail — **B: decision buttons in the header, then Fit / Posting / Application tabs**

| Option | UX | Correctness | Risk | Cost |
|---|---|---|---|---|
| A Today's column, clearer sections | mixed | good | good | good |
| **B Header with Queue, Skip, Save, Tailor resume; tabs** | good | good | good | good |
| C Posting left, fit and actions rail right | good | good | mixed | good |

C lost because a rail beside the list is cramped.

### Q9 · Fit — **B: skill checklist**

| Option | UX | Correctness | Risk | Cost |
|---|---|---|---|---|
| A Word chip | mixed | good | good | good |
| **B Skill checklist (covered by resume or confirmed facts)** | good | good | good | good |
| C Percentage ring | good | poor | poor | good |
| D Chip plus five-segment bar | good | mixed | mixed | good |

C implies precision the ranking does not have. The checklist lives in the detail's Fit tab;
the table shows its compact form (Q15).

### Q10 · Track — **D: funnel summary above a kanban board**

| Option | UX | Correctness | Risk | Cost |
|---|---|---|---|---|
| A Kanban | good | good | good | good |
| B Table grouped by stage | mixed | good | good | good |
| C Timeline of stage dots | good | mixed | mixed | good |
| **D Funnel strip + kanban** | good | good | good | good |

### Q12 · Career navigation — **C: a left rail with counts**

| Option | UX | Correctness | Risk | Cost |
|---|---|---|---|---|
| A Today's text tabs | mixed | good | good | good |
| B One segmented bar | good | good | good | good |
| **C Left rail with counts** | good | good | mixed | good |

Chosen over the recommended segmented bar. Its risk (two left columns in the Workshop) is
settled by Q14.

### Q13 · Career overview — **B: an Overview page**

| Option | UX | Correctness | Risk | Cost |
|---|---|---|---|---|
| A No, open on Find | mixed | good | good | good |
| **B Overview: week goal, new strong matches, deadlines, waiting emails, Rika's suggestion** | good | good | good | good |
| C Overview as a Rika chat | mixed | mixed | mixed | mixed |

### Q14 · The rail inside the Resume Workshop — **A: collapses to icons with count dots; hover or click expands**

| Option | UX | Correctness | Risk | Cost |
|---|---|---|---|---|
| **A Icon rail** | good | good | good | good |
| B Full rail lists resumes; Workshop drops its library | mixed | mixed | mixed | good |
| C Rail hides; back arrow | mixed | good | good | good |

## Routine choices

- **Q2 Type pairing:** B — Source Serif headings, Source Sans body (fonts must be bundled with
  the templates; the lab found Source Sans 3 absent on this machine).
- **Q3 Header:** A — centred name, contact line below separated by pipes.
- **Q4 Section headings:** A — small caps with a full-width rule under.
- **Q6 Density:** B — balanced, 10.5pt, 0.6in margins (lab: a four-role history fills 99% of
  one page).
- **Q11 Application card:** company and role, plus the resume version sent and the
  waiting-email badge (owner's thread note on A); no days-since or next-step lines.
- **Q15 Fit cell in the table:** B — "4 of 6 skills", sortable (taken on recommendation at
  Finish).
- **Q16 Default table columns:** A role and company, B location and remote, C fit, D pay, E
  posted and deadline; queue state hidden by default (taken on recommendation at Finish).

## Verified facts

- The ATS check flags multi-column layouts, which ruled out Editorial (Q1 C).
- The Kitty theme forces `text-transform: none !important` and `letter-spacing: normal
  !important` on every element; any in-app paper preview (template thumbnails) must override
  it for small caps and spaced headings.
- Clean HTML ships as A4; the lab drew it on US letter for comparison.
- Latin Modern, Source Serif 4 and Fira Sans exist only in TeX/rustdoc caches here; Source
  Sans 3 and EB Garamond are not installed.

## Risks

- Bundled fonts: Source Serif/Sans must ship inside the templates (LaTeX packages
  `sourceserifpro`/`sourcesanspro` or bundled OTFs for Typst and HTML) or compiles fall back.
- A dense table on a narrow window: columns must hide by priority.
- Rail counts must come from data the section already loads, without extra polling.

## Deferred

- Q1 and Q15–Q16 were taken on recommendation when the owner pressed Finish; reopen them if
  the result looks wrong.

## Open threads

- None.
