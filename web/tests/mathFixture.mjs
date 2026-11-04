// A synthetic /api/math backend for tests/math.browser.test.mjs, served through Playwright
// routes so the UI runs against known topics, problems and answers. It follows the
// contract in docs/GUIDE.md (Math); all content is invented for the tests (the
// "Grinstead & Snell" exercises too: none of the book's text is used).
import { FULL_TOPICS } from "./mathTopics.mjs";

const PAST = new Date(Date.now() - 2 * 86_400_000).toISOString();
const FUTURE = new Date(Date.now() + 6 * 86_400_000).toISOString();
const YESTERDAY = new Date(Date.now() - 86_400_000).toISOString();

export const TOPICS = [
  { id: "mental", strand: "arithmetic", title: "Mental arithmetic", prereqs: [], state: "mastered", due_at: PAST, stability_days: 9 },
  { id: "fractions", strand: "arithmetic", title: "Fractions", prereqs: ["mental"], state: "mastered", due_at: FUTURE, stability_days: 14 },
  { id: "algebra", strand: "arithmetic", title: "Simplifying expressions", prereqs: ["fractions"], state: "mastered", due_at: FUTURE, stability_days: 6 },
  { id: "perm", strand: "counting", title: "Permutations", prereqs: ["mental"], state: "mastered", due_at: FUTURE, stability_days: 20 },
  { id: "basic", strand: "probability", title: "Basic probability", prereqs: ["fractions", "perm"], state: "mastered", due_at: FUTURE, stability_days: 12 },
  { id: "cond", strand: "probability", title: "Conditional probability", prereqs: ["basic"], state: "mastered", due_at: PAST, stability_days: 4 },
  { id: "bayes", strand: "probability", title: "Bayes' rule", prereqs: ["cond"], state: "learning", progress: 0.6, why: "Unlocked because you mastered Conditional probability" },
  { id: "ev", strand: "ev", title: "Expected value", prereqs: ["basic"], state: "ready" },
  { id: "mm", strand: "ev", title: "Market making", prereqs: ["bayes", "ev"], state: "locked" },
  { id: "mean", strand: "statistics", title: "Mean and variance", prereqs: ["basic"], state: "ready" },
  { id: "hyp", strand: "statistics", title: "Hypothesis tests", prereqs: ["mean"], state: "locked" },
];

const FRACTION = { prompt_md: "A bag holds 3 red and 4 blue marbles. One is drawn at random. What is $P(\\text{red})$?", answer_kind: "fraction", answer: [3, 7], canonical: "$\\frac{3}{7}$" };
const PROBLEMS = {
  algebra: { prompt_md: "Simplify $\\dfrac{2x^{2} + 4x + 2}{x + 1}$ for $x \\ne -1$.", answer_kind: "expression", variables: ["x"], canonical: "$2(x+1)$" },
  ev: { prompt_md: "A fair die is rolled. What is $\\mathbb{E}[X]$?", answer_kind: "choice", choices: ["$3$", "$3.5$", "$4$", "$\\frac{7}{3}$"], answer: "$3.5$", canonical: "$3.5$" },
  bayes: { prompt_md: "Urn A has 3 red and 1 blue; urn B has 1 red and 3 blue. Pick an urn at random and draw red. What is $P(A \\mid \\text{red})$?", answer_kind: "fraction", answer: [3, 4], canonical: "$\\frac{3}{4}$" },
};

export const LESSON = {
  topic_id: "bayes",
  title: "Bayes' rule",
  example: {
    prompt_md: "1% of people have a disease. The test catches 90% of cases and gives a false positive 5% of the time. You test positive: what is $P(D \\mid +)$?",
    steps: [
      "Bayes' rule: $P(D \\mid +) = \\frac{P(+ \\mid D)\\,P(D)}{P(+)}$",
      "Total probability: $P(+) = 0.9 \\cdot 0.01 + 0.05 \\cdot 0.99 = 0.0585$",
      "Divide: $P(D \\mid +) = \\frac{0.009}{0.0585} \\approx 0.154$",
    ],
  },
  guided: [
    { problem_id: "guided-1", prompt_md: "2% of parts are faulty. The scanner flags 80% of faulty parts and 10% of good ones. A part is flagged: what is $P(F \\mid +)$?",
      steps: ["Bayes' rule: $P(F \\mid +) = \\frac{P(+ \\mid F)\\,P(F)}{P(+)}$", "Total probability: $P(+) = 0.8 \\cdot 0.02 + 0.1 \\cdot 0.98 =$ [[blank]]"], blank_step: 1 },
    { problem_id: "guided-2", prompt_md: "2% of parts are faulty. The scanner flags 80% of faulty parts and 10% of good ones. A part is flagged: what is $P(F \\mid +)$?",
      steps: ["Bayes' rule: $P(F \\mid +) = \\frac{P(+ \\mid F)\\,P(F)}{P(+)}$", "Total probability: $P(+) = 0.114$", "Divide: $P(F \\mid +) = \\frac{0.016}{0.114} \\approx$ [[blank]]"], blank_step: 2 },
  ],
  solo_count: 5,
  stop_after_misses: 3,
};
const GUIDED = { "guided-1": 0.114, "guided-2": 0.14 };

const SPRINT = [
  ["2 + 3", 5, [5, 6, 4, 15]], ["12 × 4", 48, [44, 48, 52, 84]], ["81 − 27", 54, [54, 45, 64, 56]], ["96 ÷ 8", 12, [11, 12, 21, 13]],
  ["37 + 45", 82, [72, 82, 83, 92]], ["7 × 19", 133, [133, 126, 143, 131]], ["150 − 64", 86, [96, 86, 76, 84]], ["108 ÷ 9", 12, [12, 13, 11, 21]],
].map(([q, a, choices]) => ({ q, a, choices }));

export const SOURCE = { title: "Grinstead and Snell's Introduction to Probability", short: "Grinstead & Snell", version: "Version dated 22 July 2006",
  licence: "GNU Free Documentation License 1.3", url: "https://math.dartmouth.edu/~doyle/docs/prob/prob.tar.gz" };
const SECTION = { section: "4.1", section_title: "Discrete Conditional Probability", program: false, figure: false, starred: false, card: null };
const DUE_CARD = { due_at: PAST, due: true, reviews: 2, lapses: 0, last_rating: 3 };
/**
 * Invented exercises in the shape of the bundled ones: two checked numerically, two you check
 * yourself. Their canonical answers are bare TeX, as the backend sends them.
 */
export const EXERCISES = [
  { ...SECTION, id: "gs.4.1.13", label: "4.1.13", number: 13, topics: ["bayes", "cond"], graded: "number", part: "b",
    text_md: "A box holds three fair coins and one coin with two heads. A coin is drawn at random and tossed: it shows heads.\n\n(a) Which coin would you bet on?\n\n(b) What is the probability it is the two-headed coin?",
    answer: [2, 5], canonical: "\\frac{2}{5} = 0.4" },
  { ...SECTION, id: "gs.4.1.15", label: "4.1.15", number: 15, topics: ["bayes"], graded: "self", part: null, starred: true,
    text_md: "Show that if $P(A \\mid B) = P(A)$ then $P(B \\mid A) = P(B)$, and give an example where $P(A \\mid B) > P(A)$." },
  { ...SECTION, id: "gs.4.1.20", label: "4.1.20", number: 20, topics: ["bayes", "cond"], graded: "self", part: null, program: true, card: DUE_CARD,
    text_md: "Write a program that simulates the two-urn draw $10\\,000$ times and estimates $P(A \\mid \\text{red})$." },
  { ...SECTION, id: "gs.1.2.4", label: "1.2.4", number: 4, section: "1.2", section_title: "Discrete Probability Distributions", topics: ["basic"], graded: "number", part: null,
    text_md: "Two fair dice are rolled. What is the probability that the sum is $7$?", answer: [1, 6], canonical: "\\frac{1}{6} \\approx 0.1667" },
];
const summary = ({ text_md: _text, part: _part, answer: _answer, canonical: _canonical, number: _number, ...rest }) => rest;

/** Own-book entries already logged: one due, one not. */
const BOOKS = [
  { id: 1, book: "Blitzstein & Hwang", section: "2.3", problem: "14", answer: "3/8", confidence: 2, topic_id: "cond", topic_title: "Conditional probability",
    created_at: PAST, updated_at: PAST, card: DUE_CARD },
  { id: 2, book: "Green Book", section: "", problem: "7", answer: "", confidence: 3, topic_id: null, topic_title: null,
    created_at: PAST, updated_at: PAST, card: { due_at: FUTURE, due: false, reviews: 1, lapses: 0, last_rating: 3 } },
];

/** Mixed review once enough is mastered: due topics, a textbook card and an own-book entry, no two strategies in a row. */
const MIXED_ITEMS = [
  { kind: "topic", topic_id: "cond", title: "Conditional probability", strategy: "restrict-sample-space", due_at: PAST, early: false, why: "Review due 2 days ago" },
  { kind: "textbook", exercise_id: "gs.4.1.20", label: "4.1.20", topic_id: "bayes", title: "Bayes' rule", strategy: "bayes", due_at: PAST, graded: "self" },
  { kind: "topic", topic_id: "mental", title: "Mental arithmetic", strategy: "mental-add", due_at: PAST, early: false, why: "Review due 2 days ago" },
  { kind: "book", entry_id: 1, label: "Blitzstein & Hwang · 2.3 · #14", topic_id: "cond", strategy: "restrict-sample-space", due_at: PAST },
  { kind: "topic", topic_id: "fractions", title: "Fractions", strategy: "common-denominator", due_at: FUTURE, early: true, why: "An early review to round out the set" },
];

/** Reads "a/b" or a decimal; null when it can't. */
function value(text) {
  const s = String(text).replace(/\s+/g, "").replace(/−/g, "-");
  const m = /^(-?\d+)\/(-?\d+)$/.exec(s);
  if (m) return Number(m[2]) ? Number(m[1]) / Number(m[2]) : null;
  return /^-?\d*\.?\d+$/.test(s) ? Number(s) : null;
}
const latex = (text) => String(text).replace(/\\left|\\right|\s|\\cdot/g, "").replace(/\\mleft|\\mright/g, "");
/** A number or fraction answer against the exact [numerator, denominator]. */
function numeric(answer, [n, d]) {
  const got = value(answer);
  return got === null ? { correct: null, kind: "undecided", feedback: "Couldn't read that." }
    : Math.abs(got - n / d) < 1e-4 ? { correct: true, kind: "equal" }
    : { correct: false, kind: "different", feedback: `Not quite: ${answer} ≈ ${got.toPrecision(4)}.` };
}

/** Installs the routes on `context`; returns the recorded requests for assertions. */
export async function installMathFixture(context, { history = [], streak = 3, mixed = false, full = false } = {}) {
  const state = {
    problems: new Map(),
    answers: [],
    results: [],
    explained: [],
    sprints: history.map((run, index) => ({ id: `past-${index}`, mode: "zetamac", duration_s: 30, score: 10, wrong: 0, elapsed_ms: 30_000, finished_at: YESTERDAY, ...run })),
    streak,
    dealt: 0,
    textbook: new Map(),
    textbookAnswers: [],
    ratings: [],
    books: BOOKS.map((entry) => ({ ...entry, card: { ...entry.card } })),
    bookCalls: [],
  };
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  await context.route(/\/api\/math\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/math/, "");
    const body = request.method() === "POST" ? request.postDataJSON() : null;
    if (path === "/topics") return json(route, { topics: full ? FULL_TOPICS : TOPICS });
    if (path === "/mixed") {
      return json(route, mixed
        ? { default: true, mastered: 6, needed: 5, why: "Due reviews from 6 mastered topics, the textbook and your books, interleaved", held_back: 1, items: MIXED_ITEMS }
        : { default: false, mastered: 2, needed: 5, why: "Mixed review needs more mastered topics", held_back: 0, items: [] });
    }
    if (path === "/textbook") {
      const topic = url.searchParams.get("topic_id");
      return json(route, { source: SOURCE, exercises: EXERCISES.filter((item) => !topic || item.topics.includes(topic)).map(summary) });
    }
    if (path === "/textbook/problems") {
      const exercise = EXERCISES.find((item) => item.id === body.exercise_id);
      if (!exercise) return json(route, { error: "Unknown exercise" }, 404);
      if (exercise.graded === "self") return json(route, { error: "This exercise is self-graded: rate it instead" }, 409);
      const id = `tb-${state.textbook.size + 1}`;
      state.textbook.set(id, { exercise, tries: 0, graded: false });
      return json(route, { problem_id: id, exercise_id: exercise.id, prompt_md: exercise.part ? `${exercise.text_md}\n\nAnswer part (${exercise.part}).` : exercise.text_md, answer_kind: "fraction" });
    }
    if (path === "/textbook/answers") {
      state.textbookAnswers.push(body);
      const issued = state.textbook.get(body.problem_id);
      if (!issued) return json(route, { error: "Unknown problem" }, 404);
      const verdict = numeric(body.answer, issued.exercise.answer);
      if (verdict.correct === false) issued.tries += 1;
      const revealed = issued.tries >= 3;
      const card = { due_at: verdict.correct ? FUTURE : new Date(Date.now() + 86_400_000).toISOString(), due: false, reviews: 1, lapses: verdict.correct ? 0 : 1, last_rating: verdict.correct ? 3 : 1 };
      return json(route, { ...verdict, revealed, tries_left: Math.max(0, 3 - issued.tries), card, ...(verdict.correct || revealed ? { canonical: issued.exercise.canonical } : {}) });
    }
    if (path === "/textbook/ratings") {
      const exercise = EXERCISES.find((item) => item.id === body.exercise_id);
      if (!exercise) return json(route, { error: "Unknown exercise" }, 404);
      if (exercise.graded !== "self") return json(route, { error: "Numeric exercises are graded by their answer" }, 409);
      state.ratings.push(body);
      return json(route, { card: { due_at: new Date(Date.now() + [1, 2, 4, 9][body.rating - 1] * 86_400_000).toISOString(), due: false, reviews: 1, lapses: body.rating === 1 ? 1 : 0, last_rating: body.rating } });
    }
    const exercise = /^\/textbook\/([^/]+)$/.exec(path);
    if (exercise) {
      const found = EXERCISES.find((item) => item.id === decodeURIComponent(exercise[1]));
      if (!found) return json(route, { error: "Unknown exercise" }, 404);
      return json(route, { ...summary(found), text_md: found.text_md, part: found.part, source: SOURCE });
    }
    if (path.startsWith("/books")) {
      state.bookCalls.push({ path, body });
      const now = Date.now();
      const card = (rating) => ({ due_at: new Date(now + [10 / 1440, 1, 3, 7][rating - 1] * 86_400_000).toISOString(), due: false, reviews: 1, lapses: rating === 1 ? 1 : 0, last_rating: rating });
      const title = (id) => TOPICS.find((topic) => topic.id === id)?.title ?? null;
      if (path === "/books" && !body) {
        const problems = [...state.books].sort((a, b) => Number(b.card.due) - Number(a.card.due) || a.card.due_at.localeCompare(b.card.due_at));
        const counts = {};
        for (const entry of state.books) counts[entry.book] = (counts[entry.book] ?? 0) + 1;
        return json(route, { problems, books: Object.keys(counts).sort((a, b) => counts[b] - counts[a]), due: problems.filter((entry) => entry.card.due).length });
      }
      if (path === "/books") {
        if (!body.book?.trim() || !body.problem?.trim() || ![1, 2, 3, 4].includes(body.confidence)) return json(route, { error: "A book, a problem number and a confidence are needed" }, 400);
        const same = state.books.find((entry) => entry.book === body.book && entry.section === (body.section ?? "") && entry.problem === body.problem);
        if (same) return json(route, { error: "That problem is already logged" }, 409);
        const stamp = new Date(now).toISOString();
        const entry = { id: (state.bookId = (state.bookId ?? 2) + 1), book: body.book, section: body.section ?? "", problem: body.problem, answer: body.answer ?? "", confidence: body.confidence,
          topic_id: body.topic_id ?? null, topic_title: title(body.topic_id), created_at: stamp, updated_at: stamp, card: card(body.confidence) };
        state.books.push(entry);
        return json(route, entry);
      }
      const [, id, action] = /^\/books\/(\d+)(?:\/(ratings|delete))?$/.exec(path) ?? [];
      const entry = state.books.find((item) => String(item.id) === id);
      if (!entry) return json(route, { error: "Unknown entry" }, 404);
      if (action === "delete") {
        state.books = state.books.filter((item) => item !== entry);
        return json(route, { ok: true });
      }
      if (action === "ratings") {
        if (body.answer !== undefined) entry.answer = body.answer;
        entry.card = { ...card(body.rating), reviews: entry.card.reviews + 1 };
      } else {
        for (const key of ["book", "section", "problem", "answer", "topic_id"]) if (body[key] !== undefined) entry[key] = body[key];
        entry.topic_title = title(entry.topic_id);
      }
      entry.updated_at = new Date(now).toISOString();
      return json(route, entry);
    }
    if (path === "/next") return json(route, { kind: "lesson", topic_id: "bayes", title: "Bayes' rule", why: "Unlocked because you mastered Conditional probability" });
    if (path === "/problems") {
      const topic = TOPICS.find((item) => item.id === body.topic_id);
      if (topic?.state === "locked") return json(route, { error: "Master Bayes' rule first" }, 409);
      const template = PROBLEMS[body.topic_id] ?? FRACTION;
      const id = (state.problems.size + 1).toString(16).padStart(32, "0");
      state.problems.set(id, { ...template, tries: 0, topic: body.topic_id });
      const { answer: _answer, canonical: _canonical, ...shown } = template;
      return json(route, { problem_id: id, purpose: "review", ...shown });
    }
    if (path === "/answers") {
      state.answers.push(body);
      const mastery = { state: "learning", progress: 0.8 };
      if (body.problem_id in GUIDED) {
        const got = value(body.answer);
        return json(route, got !== null && Math.abs(got - GUIDED[body.problem_id]) <= 0.005
          ? { correct: true, kind: "equal" }
          : { correct: false, kind: "different", feedback: "Not quite: multiply each branch, then add." });
      }
      const problem = state.problems.get(body.problem_id);
      if (!problem) return json(route, { error: "Unknown problem" }, 404);
      let verdict;
      if (problem.answer_kind === "expression") {
        const given = latex(body.answer);
        if (["2(x+1)", "2x+2"].includes(given)) verdict = { correct: true, kind: "equal" };
        else if (given === "x+1") verdict = { correct: false, kind: "mistake", feedback: "You cancelled the $(x + 1)$ but dropped the common factor 2." };
        else if (given.includes("sqrt")) verdict = { correct: null, kind: "undecided", feedback: "Agrees at 5 of 7 test points; $\\sqrt{a^2}$ is $|a|$. Not marked wrong." };
        else verdict = { correct: false, kind: "different", feedback: "At $x = 2$ yours differs from the answer." };
      } else if (problem.answer_kind === "choice") {
        verdict = body.answer === problem.answer ? { correct: true, kind: "equal" } : { correct: false, kind: "different" };
      } else {
        verdict = numeric(body.answer, problem.answer);
      }
      if (verdict.correct === false) problem.tries += 1;
      const revealed = problem.tries >= 3;
      return json(route, { ...verdict, mastery, revealed, ...(verdict.correct || revealed ? { canonical: problem.canonical } : {}) });
    }
    if (path === "/lessons/bayes") return json(route, LESSON);
    if (path === "/sprints" && body) {
      state.dealt += 1;
      const id = `sprint-${state.dealt}`;
      return json(route, { sprint_id: id, problems: Array.from({ length: 40 }, (_, index) => SPRINT[index % SPRINT.length]) , mode: body.mode, duration_s: body.duration_s });
    }
    const result = /^\/sprints\/([^/]+)\/result$/.exec(path);
    if (result) {
      state.results.push({ id: result[1], ...body });
      if (!state.sprints.some((run) => Date.parse(run.finished_at) >= new Date().setHours(0, 0, 0, 0))) state.streak += 1;
      state.sprints.push({ id: result[1], mode: state.lastMode ?? "zetamac", duration_s: state.lastDuration ?? 30, ...body, finished_at: new Date().toISOString() });
      return json(route, { ok: true });
    }
    if (path === "/sprints/history") {
      const duration = Number(url.searchParams.get("duration_s")) || null;
      const sprints = state.sprints.filter((run) => !duration || run.duration_s === duration);
      const best = (mode) => Math.max(0, ...sprints.filter((run) => run.mode === mode).map((run) => run.score));
      return json(route, { sprints, streak_days: state.streak, best: { zetamac: best("zetamac"), mc: best("mc") } });
    }
    if (path === "/explain") {
      state.explained.push(body);
      return json(route, {
        purpose: "math-explain", model: "test/math-model", text: `Problem and working:\n${body.working}`,
        system_prompt: "Point to the first faulty step in the working and say why. Never give the final answer.", max_output_tokens: 400,
        maximum_cost_usd: 0.0012, privacy_notice: "Only the problem, your answer and your working are sent.",
        effect: { id: "a".repeat(32), digest: "b".repeat(64), expires_at: new Date(Date.now() + 600_000).toISOString() },
      });
    }
    return json(route, { error: `No fixture for ${request.method()} ${path}` }, 404);
  });
  // The sprint's mode and length come from its deal; remember them for the recorded run.
  await context.route(/\/api\/math\/sprints$/, async (route) => {
    const body = route.request().postDataJSON();
    state.lastMode = body.mode;
    state.lastDuration = body.duration_s;
    await route.fallback();
  });
  return state;
}
