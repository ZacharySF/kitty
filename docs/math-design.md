# rika! math: design

rika! math is a new **Math** view in the Study group, built to make you exceptional at the math that quant interviews and tech work test. It opens focus-first on one centred problem or sprint, with drawers for the topic graph, history and settings.

- **Content.**
  - A curated topic graph across the quant and tech strands, with parametrised problem generators that make endless fresh instances.
  - Grinstead & Snell's open probability textbook, bundled under its GNU FDL licence.
  - A way to log problems from your own books by reference.
- **Practice.** Two modes come first: timed mental-math sprints (Zetamac-style, plus a multiple-choice variant with wrong-answer penalties) and topic lessons (worked example, then guided steps, then solo problems).
- **Mastery.** Graph mastery in the style of Math Academy: topics unlock along prerequisites, and each topic gets spaced review.
- **Checking.** Answers are checked exactly on the device, and algebra by SymPy.
- **AI.** When an answer is wrong, you can paste your working and AI explains where it went wrong; the engine stays the grader.

The design was decided in a grill-with-ui interview paired with a design lab (branch `lab/math`, `web/math-lab.html`). The research brief is summarised under Verified facts.

## Terms

- **Topic**: one skill in the math graph (for example conditional probability), with prerequisites. *Avoid:* lesson, unit.
- **Generator**: a template that makes fresh problems for a topic, with an exactly computed answer. *Avoid:* question bank.
- **Sprint**: a timed mental-arithmetic run, Zetamac-style (default 120 s). *Avoid:* drill.

## Why

User: "I want a math extension that I can do math learning on. I want to get exceptional at math as I want to go into quant and just be good at math in general for tech."

## Locked decisions

### Q1: curriculum sources

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Built-in topic graph (about 60–120 topics) with generators** | **good** | **good** | **good** | **good** |
| **B. Bundled open textbooks (Grinstead & Snell) with exercises** | **mixed** | **mixed** | **mixed** | **good** |
| **C. Your own books by reference (book · section · problem · answer · confidence)** | **mixed** | **good** | **good** | **good** |
| D. AI-generated problems, checked before shown | good | mixed | mixed | poor |

**Decision: A + B + C.**

- **A, topic graph.** Topics and generators are authored for rika! and released under MIT.
  - The strands follow the research syllabus:
    - arithmetic and estimation;
    - counting and combinatorics;
    - probability, conditional probability and Bayes;
    - expected value, variance and market-making basics;
    - statistics;
    - linear algebra;
    - calculus;
    - the discrete math needed for tech.
  - Every generator has a test that cross-checks its answer function by enumeration or Monte Carlo over many seeds.
- **B, Grinstead & Snell.**
  - Bundled with its GNU FDL licence file, kept outside the MIT tree.
  - Its exercises are linked to topics.
  - Its text is never fed to AI.
- **C, own books.** You log problems from the books you own (Zhou, Crack, *Fifty Challenging Problems*, Stat 110) by reference, quoting none of their text.

D lost on cost per problem and non-deterministic quality.

### Q3: mastery model

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. Per-topic spaced-repetition card in one queue with flashcards | mixed | mixed | good | good |
| **B. Graph mastery: locked → ready → learning → mastered, gated on prerequisites; spaced review per topic; partial credit to encompassed topics** | **good** | **good** | **mixed** | **good** |
| C. Knowledge tracing / Elo | mixed | good | mixed | good |
| D. Simple thresholds | mixed | poor | good | good |

**Decision: B.**

- **States.** A topic is locked until its prerequisites are mastered, then ready, then learning, then mastered.
- **Review.** Each topic has an FSRS schedule (reusing `fsrs>=6.3`, as in `core/flashcards.py`). Every review uses a fresh generated instance, graded by correctness and time.
- **Partial credit ("FIRe-lite").** Practising a topic gives partial implicit credit to the simpler topics it directly encompasses.
- **Explanations.** Every recommendation says why: "unlocked because you mastered Bayes".
- **Storage.** The math graph lives in its own tables, with acyclicity enforced. It is projected read-only into a mind-map or graph view.
- **Risk.** Rated mixed because the credit weights are heuristics. They are tuned after real use.

### Q4: answer checking

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Exact numbers, fractions and multiple choice, on device (live "3/7 ≈ 0.4286" echo)** | **good** | **good** | **good** | **good** |
| **B. Algebraic answers checked by SymPy for equivalence, with common-mistake feedback** | **good** | **good** | **mixed** | **mixed** |
| C. Self-grade after reveal | mixed | mixed | good | good |
| D. Step checking, line by line | good | good | mixed | poor |

**Decision: A + B.**

- **A, exact local checks.**
  - Numeric answers are checked locally; floats need a stated tolerance or number of significant figures.
  - Algebraic answers are written in a MathLive field.
- **B, SymPy.**
  - The LaTeX goes to the Python backend: `parse_latex` (lark), then `simplify` equivalence plus numeric probing, with a timeout.
  - STACK-style mistake detectors in each template produce specific feedback.
- **Not chosen.** Self-grading and step checking. Problems that can't be machine-checked, such as proofs, brainteasers and market making, are therefore out of the first release's practice modes, which matches Q2.
- **Risk.** Rated mixed because SymPy adds about 7 MB, a round trip per check, and LaTeX ambiguity.

### Q6 + Q7: AI involvement

| Option (Q6) | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. No AI; authored hints | mixed | good | good | good |
| B. On-request hint after an attempt | good | good | good | mixed |
| **C. AI explains why a step is wrong** | **good** | **good** | **good** | **mixed** |
| D. Rika voice mock interviewer | good | mixed | mixed | poor |

| Option (Q7, the follow-up) | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. Add step checking now so AI explains the first wrong line | good | good | mixed | mixed |
| **B. No step checking: paste or type your working after a wrong answer and AI explains where it went wrong; the engine still grades** | **good** | **good** | **good** | **mixed** |
| C. No AI until step checking | mixed | good | good | good |

**Decision: Q6 C, realised through Q7 B.**

- **When it's offered.** Only after the engine marks an answer wrong, an "Explain my mistake" action appears.
- **What you send.** You type or paste your working.
- **Approval.** The request goes through kitty!'s existing review → approve → budget flow, as a new `math-explain` purpose in `web_ai.py`. The cost is previewed before you approve.
- **Prompt rules.** The prompt forbids giving the final answer. It points to the first faulty step in your working and explains why.
- **Grading.** AI never decides correctness.

Not chosen:
- **Authored hints and on-request hints** (Q6 A, B).
- **The mock interviewer** (Q6 D). Rika may add it later.
- **Step checking** (Q7 A). It is deferred, so AI's pointer is less exact than a flagged line would be.

## Routine choices

- **Q2, practice modes:** A + B ship first.
  - **Sprint.** Zetamac defaults: 120 s; addition 2–100 + 2–100; subtraction is reversed addition; multiplication 2–12 × 2–100; division is reversed multiplication. There is also a multiple-choice variant with wrong-answer penalties. It shows a score trend and a daily streak.
  - **Topic lesson.** A worked example, then faded guided steps, then 3–8 solo problems. It ends early after repeated misses.
  - **Next releases.** Mixed review of due topics comes next, once mastery data exists. Mock interviews come after that.
- **Q5, where it lives:** A.
  - **The view.** A new **Math** view in the Study group, focus-first: one centred problem or sprint. The left drawer holds the topic graph; the right drawer holds history and settings.
  - **Launchers on top.** Ctrl K ("Mental math sprint", "Next math lesson") and an Up next row on home.
  - **Links into the rest of the app.** Practice can run inside a study focus phase and tag the session with a Math subject. Exam dates and plans can target math topics. A Formulas flashcard deck renders with KaTeX.
- **Rendering:** KaTeX (MIT) is added app-wide. This also fixes imported Anki math decks, which show raw `\( … \)` today.

## Verified facts

- **What firms say:**
  - Jane Street says its trading interview won't require complicated math and stresses quick, reliable reasoning.
  - Optiver's postings name probability, calculus and linear algebra.
  - SIG emphasises probability and game theory.
  - Citadel's quantitative research FAQ points to the same core.
  - Optiver's "80 questions in 8 minutes" arithmetic test appears in candidate reports, not on Optiver's pages.
- **Math Academy's model:** a prerequisite knowledge graph, mastery gating, spaced and interleaved review on new instances, and worked examples fading to solo problems ([How our AI works](https://www.mathacademy.com/how-our-ai-works)).
- **AI as a crutch:** a 2025 PNAS study found students with unguarded AI access scored 17% worse once it was removed. Guarded, hint-style AI didn't harm learning.
- **Licences:**

  | Material | Licence | Use |
  |---|---|---|
  | Grinstead & Snell | GNU FDL | Bundled |
  | OpenStax | Non-commercial share-alike; forbids AI ingestion | Linked only |
  | MIT OCW, Axler | Non-commercial | Linked only |
  | Project Euler, Stat 110 | No licence that allows bundling | Linked only |
  | Zhou, Crack, Mosteller, Brilliant, Jane Street puzzles | All rights reserved | Referenced only |

- **kitty! today:** it has FSRS-6 scheduling (`src/kitty/core/flashcards.py`), the study timer and guide, exam plans, `create_event`, the mind-map graph model and `/api/brain`. It has no math rendering or checking.

## Risks

- **Wrong answer keys destroy trust.** Every generator ships with an enumeration or Monte Carlo cross-check test over many seeds.
- **Licence boundary.** FDL content lives in its own folder with its own licence file. OpenStax and other non-commercial content is never bundled or sent to AI.
- **SymPy parsing ambiguity and latency.** Checks use a timeout and numeric probing as a second opinion. When parsing is ambiguous, the checker says so rather than marking the answer wrong.
- **Bundle size.** SymPy plus lark and KaTeX increase the install size. They are acceptable for the desktop app; keep the web bundle lazy.
- **AI explanations read your free-form working.** They are approval-gated with a cost preview, and the final answer is never revealed.

## Deferred

- **Step checking** (Q4 D). This would reopen when line-by-line working is wanted for algebra and calculus lessons. AI explanations would then move to the flagged line (Q7 A).
- **Mixed review and mock interviews** (Q2 C, D) come after the first release. A Rika voice mock interviewer (Q6 D) is a candidate once Rika's voice ships.

## Open threads

- **Build order:**
  1. KaTeX rendering, which also fixes Flashcards.
  2. The math tables, migration and topic graph, with about 40 generator-backed topics across arithmetic, counting → probability → EV, and basic statistics.
  3. The sprint.
  4. Lessons.
  5. Graph mastery with FSRS.
  6. SymPy checking.
  7. Grinstead & Snell import and own-book logging.
  8. "Explain my mistake".
  9. The remaining strands (linear algebra, calculus, discrete math).
