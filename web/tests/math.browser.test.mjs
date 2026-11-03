// rika! math in a real browser: the app shell runs on the disposable synthetic backend
// (`kitty-web --demo`), the /api/math contract on tests/mathFixture.mjs (invented topics
// and problems), so every check sees known states. Flashcards and Study are the real demo.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/math.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as createNetServer } from "node:net";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";
import { installMathFixture } from "./mathFixture.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));
const VIEWPORT = { width: 1280, height: 850 };
const pinnedPort = Number(process.env.KITTY_TEST_PORT) || 0;
let backend;
let server;
let browser;
let base;
let origin;

function freePort() {
  if (pinnedPort) return Promise.resolve(pinnedPort);
  return new Promise((resolve, reject) => {
    const probe = createNetServer().once("error", reject).listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

before(async () => {
  backend = spawn("uv", ["run", "--quiet", "kitty-web", "--demo", "--port", String(await freePort())], { cwd: repository, stdio: ["ignore", "pipe", "pipe"] });
  origin = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`demo backend did not start:\n${output}`)), 120_000);
    const read = (chunk) => {
      output += chunk;
      const match = output.match(/website: (http:\/\/\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    };
    backend.stdout.on("data", read);
    backend.stderr.on("data", read);
    backend.once("exit", (code) => { clearTimeout(timer); reject(new Error(`demo backend exited (${code}):\n${output}`)); });
  });
  server = await createServer({
    root,
    logLevel: "error",
    server: {
      host: "127.0.0.1",
      port: pinnedPort ? pinnedPort + 1 : 0,
      strictPort: Boolean(pinnedPort),
      proxy: { "/api": { target: origin, changeOrigin: true, configure: (proxy) => proxy.on("proxyReq", (request) => request.setHeader("Origin", origin)) } },
    },
  });
  await server.listen();
  base = server.resolvedUrls.local[0];
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
});
after(async () => {
  await browser?.close();
  await server?.close();
  backend?.kill();
});

/**
 * A fresh returning-user profile on `hash` with the math fixture. `ai: true` reports a
 * non-demo workspace so the reviewed AI flow is enabled (its paid run is a route too);
 * `clock` installs Playwright's clock so a sprint's time can be skipped.
 */
async function open(hash, { storage, history, streak, ai = false, clock = false } = {}) {
  const context = await browser.newContext({ viewport: VIEWPORT });
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue());
  await context.addInitScript(() => localStorage.setItem("kitty.dock", JSON.stringify({ mode: "hidden", shown: "terminal", float: false, step: "m", size: null, offset: { right: 16, bottom: 16 } })));
  if (storage)
    await context.addInitScript((entries) => {
      if (sessionStorage.getItem("seeded")) return;
      sessionStorage.setItem("seeded", "1");
      for (const [key, value] of Object.entries(entries)) localStorage.setItem(key, value);
    }, storage);
  if (ai)
    await context.route("**/api/bootstrap", async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...(await response.json()), demo: false } });
    });
  const math = await installMathFixture(context, { history, streak });
  const page = await context.newPage();
  page.setDefaultTimeout(15_000);
  const problems = [];
  const foreign = [];
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname) && !["data:", "blob:"].includes(url.protocol)) foreign.push(request.url());
  });
  if (clock) await page.clock.install();
  await page.goto(new URL(`#${hash}`, base).href);
  return { page, problems, foreign, context, math };
}

const modes = (page) => page.getByRole("navigation", { name: "Math modes" });

describe("KaTeX app-wide", () => {
  test("a flashcard face renders \\( … \\) and \\[ … \\] with KaTeX from bundled fonts; prices stay text", async () => {
    const { page, problems, foreign, context } = await open("flashcards");
    await page.getByRole("navigation", { name: "Flashcard views" }).waitFor();
    await page.evaluate(async () => {
      const post = async (path, body) => {
        const response = await fetch(`/api${path}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Kitty-Request": "1" }, body: JSON.stringify(body) });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        return data;
      };
      const overview = await (await fetch("/api/flashcards", { cache: "no-store" })).json();
      const basic = overview.types.find((type) => type.kind === "basic");
      const { deck } = await post("/flashcards/deck", { name: "Formulas", settings: {} });
      await post("/flashcards/note", { deck_id: deck.id, type_id: basic.id, fields: { Front: "Variance of \\(X\\sim\\mathrm{Bin}(n,p)\\)? It costs $5 and $10.", Back: "\\[np(1-p)\\]" }, tags: [] });
    });
    await page.reload();
    await page.getByRole("combobox", { name: "Deck scope" }).click();
    await page.getByRole("searchbox", { name: "Search Deck scope" }).fill("Formulas");
    await page.getByRole("option", { name: /^Formulas/ }).first().click();
    await page.getByRole("button", { name: "Review due" }).first().click();
    const stack = page.getByRole("region", { name: /Card stack/ });
    const front = stack.locator(".face.front .content").last();
    await front.locator(".katex").first().waitFor();
    const text = await front.innerText();
    assert.doesNotMatch(text, /\\\(|\\mathrm/, "raw TeX still shows on the front");
    assert.match(text, /It costs \$5 and \$10\./, "prices were read as math");
    assert.equal(await front.locator(".katex .mord").first().isVisible(), true);
    await stack.getByRole("button", { name: "Flip card" }).click();
    await stack.locator(".face.back .katex-display").last().waitFor();
    // KaTeX's own fonts are in use, served from this origin.
    await page.waitForFunction(() => [...document.fonts].some((font) => font.family.replace(/"/g, "") === "KaTeX_Main" && font.status === "loaded"));
    assert.deepEqual(foreign, [], "math fetched something from another origin");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("math view", () => {
  test("Math sits in the Study group; focus-first with the Topics, History and Settings edges, [ and ] slide the drawers", async () => {
    const { page, problems, context } = await open("math");
    await page.getByRole("heading", { name: "Math", level: 2 }).waitFor();
    const study = await page.evaluate(async () => (await import("/src/lib/workspace.ts")).navigationGroups.find((group) => group.label === "Study").views);
    assert.equal(study[1], "math", "Math is not next to Study in the Study group");
    const view = page.getByRole("region", { name: "Math workspace" });
    for (const name of ["Practice", "Sprint", "Lessons"]) await modes(page).getByRole("button", { name }).waitFor();
    // Practice opens on the one next thing: here the next lesson, with why.
    await page.getByText("Next lesson · Bayes' rule").waitFor();
    await page.getByText("Unlocked because you mastered Conditional probability").waitFor();
    for (const name of ["Topics", "History", "Settings"]) assert.equal(await view.getByRole("button", { name, exact: true }).getAttribute("aria-expanded"), "false");
    await page.locator("body").press("[");
    await page.getByRole("complementary", { name: "Topics" }).waitFor();
    await page.locator("body").press("]");
    await page.getByRole("complementary", { name: "History" }).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem("kitty.math.drawers")), JSON.stringify({ left: true, right: "history" }));
    const overflow = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
    assert.ok(overflow <= 1, `the page scrolls ${overflow}px`);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("the topic graph colours every topic by state, rings due reviews and says why each is open or locked", async () => {
    const { page, problems, context } = await open("math", { storage: { "kitty.math.drawers": JSON.stringify({ left: true, right: "" }) } });
    const graph = page.getByRole("group", { name: "Topic graph" });
    await graph.waitFor();
    const states = await graph.locator("[data-topic]").evaluateAll((nodes) => Object.fromEntries(nodes.map((node) => [node.dataset.topic, node.dataset.state])));
    assert.deepEqual(states, {
      mental: "mastered", fractions: "mastered", algebra: "mastered", perm: "mastered", basic: "mastered", cond: "mastered",
      bayes: "learning", ev: "ready", mm: "locked", mean: "ready", hyp: "locked",
    });
    const due = await graph.locator("[data-topic].due").evaluateAll((nodes) => nodes.map((node) => node.dataset.topic).sort());
    assert.deepEqual(due, ["cond", "mental"], "due rings");
    assert.equal(await page.getByRole("region", { name: "Math workspace" }).getByRole("button", { name: "Topics", exact: true }).locator(".count").textContent(), "2");
    // Every prerequisite sits above what it unlocks.
    const below = await graph.evaluate((svg) => {
      const y = (id) => svg.querySelector(`[data-topic="${id}"] .disc`).getAttribute("cy") * 1;
      return [["cond", "bayes"], ["bayes", "mm"], ["ev", "mm"], ["mean", "hyp"]].every(([a, b]) => y(a) < y(b));
    });
    assert.ok(below, "an edge points upwards");
    // Learning shows its progress; mastered a tick; locked a padlock.
    assert.equal(await graph.locator('[data-topic="bayes"] .level').count(), 1);
    assert.equal(await graph.locator('[data-topic="basic"] .tick').count(), 1);
    assert.equal(await graph.locator('[data-topic="mm"] .lock').count(), 1);
    const detail = page.getByRole("region", { name: /details$/ });
    await graph.getByRole("button", { name: "Market making: Locked" }).click();
    assert.equal(await detail.locator("[data-why]").textContent(), "Locked until you master Bayes' rule and Expected value");
    assert.equal(await detail.getByRole("button", { name: "Locked" }).isDisabled(), true);
    await graph.getByRole("button", { name: /^Conditional probability: Mastered, review due/ }).focus();
    await page.keyboard.press("Enter");
    await detail.getByText("Review due now").waitFor();
    await detail.getByRole("button", { name: "Review Conditional probability" }).click();
    await page.getByText("Review · Conditional probability").waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("sprint", () => {
  test("typed: right answers advance at once, time up scores and records the run, and the results show the trend", async () => {
    const { page, problems, context, math } = await open("math", {
      clock: true,
      storage: { "kitty.math.mode": "sprint", "kitty.math.settings": JSON.stringify({ mode: "zetamac", duration_s: 30, ops: ["add", "sub", "mul", "div"], focus: false }) },
      history: [{ score: 9 }, { score: 12 }],
    });
    const sprint = page.getByRole("group", { name: "Mental math sprint" });
    await sprint.getByText("Type the answer; it's taken the instant it's right.", { exact: false }).waitFor();
    await sprint.getByRole("button", { name: "Start sprint" }).click();
    const prompt = sprint.locator("[data-prompt]");
    const answer = sprint.getByRole("textbox", { name: "Answer" });
    await prompt.waitFor();
    assert.equal(await prompt.textContent(), "2 + 3 =");
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "Answer", "the answer box is not focused");
    await answer.pressSequentially("5");
    await page.waitForFunction(() => document.querySelector("[data-prompt]")?.textContent === "12 × 4 =");
    assert.equal(await answer.inputValue(), "", "the box did not clear for the next problem");
    await answer.pressSequentially("44");
    assert.equal(await prompt.textContent(), "12 × 4 =", "a wrong answer moved on");
    await answer.fill("");
    await answer.pressSequentially("48");
    await page.waitForFunction(() => document.querySelector("[data-prompt]")?.textContent === "81 − 27 =");
    assert.equal(await sprint.locator("[data-live-score]").textContent(), "2");
    await page.clock.fastForward("00:31");
    const final = sprint.locator(".final");
    await final.waitFor();
    assert.equal(await final.textContent(), "2");
    await page.waitForFunction(() => document.querySelector('[aria-label="Sprint score trend"]')?.dataset.points === "3");
    assert.equal(math.results.length, 1);
    assert.deepEqual({ score: math.results[0].score, wrong: math.results[0].wrong }, { score: 2, wrong: 0 });
    assert.ok(Math.abs(math.results[0].elapsed_ms - 30_000) < 50, `elapsed ${math.results[0].elapsed_ms}`);
    await sprint.getByText(/3 runs at 30 s, typed · 4-day streak/).waitFor();
    // R restarts from the results.
    await page.keyboard.press("r");
    await prompt.waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("multiple choice: keys 1–4 answer, a wrong pick costs a point, and the run records right minus wrong", async () => {
    const { page, problems, context, math } = await open("math", {
      clock: true,
      storage: { "kitty.math.mode": "sprint", "kitty.math.settings": JSON.stringify({ mode: "mc", duration_s: 30, ops: ["add", "mul"], focus: false }) },
    });
    const sprint = page.getByRole("group", { name: "Mental math sprint" });
    await sprint.getByText(/Four choices: \+1 right, −1 wrong/).waitFor();
    await sprint.getByRole("button", { name: "Start sprint" }).click();
    await sprint.getByText("press 1–4").waitFor();
    const choices = sprint.getByRole("group", { name: "Choices" });
    assert.deepEqual((await choices.getByRole("button").allTextContents()).map((text) => text.trim()), ["15", "26", "34", "415"]);
    await page.keyboard.press("1"); // 5: right
    await page.waitForFunction(() => document.querySelector("[data-prompt]")?.textContent === "12 × 4 =");
    await page.keyboard.press("1"); // 44: wrong
    await page.waitForFunction(() => document.querySelector("[data-prompt]")?.textContent === "81 − 27 =");
    await choices.getByRole("button", { name: "54" }).click(); // right
    assert.equal(await sprint.locator("[data-live-score]").textContent(), "1");
    await sprint.getByText("2 right").waitFor();
    await sprint.getByText("1 wrong").waitFor();
    await page.clock.fastForward("00:31");
    await sprint.locator(".final").waitFor();
    assert.equal(await sprint.locator(".final").textContent(), "1");
    await sprint.getByText(/2 right − 1 wrong/).waitFor();
    assert.deepEqual({ score: math.results[0].score, wrong: math.results[0].wrong }, { score: 1, wrong: 1 });
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("lesson", () => {
  test("Example · Guided · Solo: the blank is highlighted, misses are marked, and three misses in a row end it early", async () => {
    const { page, problems, context, math } = await open("math");
    await page.getByRole("button", { name: "Start the lesson" }).click();
    const lesson = page.getByRole("region", { name: "Lesson" });
    const strip = lesson.getByRole("list", { name: "Lesson stages" });
    assert.deepEqual(await strip.getByRole("button").evaluateAll((nodes) => nodes.map((node) => node.innerText.replace(/\s+/g, " ").trim())),
      ["1 Example watch it solved", "2 Guided fill the faded steps", "3 Solo 5 on your own"]);
    assert.equal(await strip.locator('[aria-current="step"]').textContent().then((text) => text.includes("Example")), true);
    assert.ok(await lesson.locator(".steps .katex").count() >= 3, "the worked example's math is not rendered");
    await lesson.getByRole("button", { name: "I follow, try one" }).click();
    const active = lesson.locator("li.active[data-blank]");
    await active.waitFor();
    const tint = await active.evaluate((node) => getComputedStyle(node).boxShadow);
    assert.notEqual(tint, "none", "the blank step is not highlighted");
    assert.equal(await lesson.locator("li.faded").count(), 1, "the later blank is not shown faded");
    const blank = lesson.getByRole("textbox", { name: "Fill the blank" });
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "Fill the blank");
    await blank.fill("0.2");
    await blank.press("Enter");
    await lesson.getByText("Not quite: multiply each branch, then add.").waitFor();
    assert.match(await blank.getAttribute("class"), /miss/);
    await blank.fill("0.114");
    await blank.press("Enter");
    await lesson.getByText("Right: 0.114.").waitFor();
    assert.equal(await lesson.locator("li.faded").count(), 0);
    await lesson.getByRole("textbox", { name: "Fill the blank" }).fill("0.14");
    await lesson.getByRole("textbox", { name: "Fill the blank" }).press("Enter");
    await lesson.getByRole("button", { name: "Go solo" }).click();
    // Solo: one right, then three misses in a row.
    const answer = async (text) => {
      const box = lesson.getByRole("textbox", { name: "Your answer" });
      await box.fill(text);
      await box.press("Enter");
    };
    await lesson.getByText("Solo · problem 1 of 5").waitFor();
    await answer("3/4");
    await lesson.locator('[data-tone="equal"]').waitFor();
    await lesson.getByRole("button", { name: /Next problem/ }).click();
    for (const [index, label] of [[2, /Next problem/], [3, /Next problem/], [4, /End the lesson/]]) {
      await lesson.getByText(`Solo · problem ${index} of 5`).waitFor();
      await answer("1/2");
      await lesson.locator('[data-tone="different"]').waitFor();
      // Only the first answer is graded: a miss stays a miss.
      for (let retry = 0; retry < 2; retry += 1) await answer("1/3");
      await lesson.getByRole("button", { name: label }).waitFor();
      if (index < 4) await lesson.getByRole("button", { name: label }).click();
    }
    await lesson.getByText("3 of 3 misses in a row end it").waitFor();
    await lesson.getByRole("button", { name: /End the lesson/ }).click();
    await lesson.getByText(/Lesson ended early · Bayes' rule/i).waitFor();
    assert.equal(await lesson.locator("[data-lesson-result]").textContent(), "1 of 4 right");
    assert.equal(await lesson.locator("[data-mastery]").getAttribute("data-mastery"), "learning");
    assert.ok(math.answers.some((item) => item.problem_id === "guided-1") && math.answers.some((item) => item.problem_id === "guided-2"));
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("answers", () => {
  test("numbers and fractions are plain text with a live '3/7 ≈ 0.4286' echo; fractions compare exactly", async () => {
    const { page, problems, context } = await open("math", { storage: { "kitty.math.drawers": JSON.stringify({ left: true, right: "" }) } });
    await page.getByRole("group", { name: "Topic graph" }).getByRole("button", { name: /^Fractions: Mastered/ }).click();
    await page.getByRole("button", { name: "Review Fractions" }).click();
    await page.getByRole("complementary", { name: "Topics" }).waitFor({ state: "detached" });
    const card = page.locator(".problem");
    await card.locator(".prompt .katex").waitFor();
    const box = card.getByRole("textbox", { name: "Your answer" });
    const echo = card.locator("[data-echo]");
    await box.pressSequentially("3/7");
    assert.equal((await echo.textContent()).trim(), "3/7 ≈ 0.4286");
    for (const [typed, shown] of [["6/14", "6/14 ≈ 0.4286"], ["0.43", "0.43 · 2 s.f."], ["42.86%", "42.86% = 0.4286"], ["1/8", "1/8 = 0.125"], ["three", "can't read “three” yet"]]) {
      await box.fill(typed);
      assert.equal((await echo.textContent()).trim(), shown, typed);
    }
    assert.equal(await card.getByRole("button", { name: /Check/ }).isDisabled(), true, "unreadable text can be checked");
    await box.fill("6/14");
    await box.press("Enter");
    const line = card.locator('[data-tone="equal"]');
    await line.waitFor();
    assert.match(await line.innerText(), /Correct\s*\(you wrote 6\/14\)/);
    await line.locator(".katex").waitFor();
    await card.getByRole("button", { name: /Next problem/ }).waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("algebra goes in a MathLive field with bundled assets; feedback names a common mistake, an undecided case and a pass", async () => {
    const { page, problems, foreign, context, math } = await open("math", { storage: { "kitty.math.drawers": JSON.stringify({ left: true, right: "" }) } });
    await page.getByRole("group", { name: "Topic graph" }).getByRole("button", { name: /^Simplifying expressions/ }).click();
    await page.getByRole("button", { name: "Review Simplifying expressions" }).click();
    await page.getByRole("complementary", { name: "Topics" }).waitFor({ state: "detached" });
    const card = page.locator(".problem");
    const field = card.locator("math-field");
    await field.waitFor();
    const enter = async (latex) => {
      await field.evaluate((node, value) => node.setValue(value), latex);
      await card.getByRole("button", { name: /Check/ }).click();
    };
    await enter("x+1");
    await card.locator('[data-tone="mistake"]').waitFor();
    assert.match(await card.locator('[data-tone="mistake"]').innerText(), /Not quite · common mistake[\s\S]*dropped the common factor 2/);
    await enter("2\\sqrt{(x+1)^2}");
    await card.locator('[data-tone="undecided"]').waitFor();
    assert.match(await card.locator('[data-tone="undecided"]').innerText(), /Couldn't decide[\s\S]*Not marked wrong/);
    // Enter in the field checks too.
    await field.evaluate((node) => node.setValue("2\\left(x+1\\right)"));
    await field.focus();
    await page.keyboard.press("Enter");
    await card.locator('[data-tone="equal"]').waitFor();
    assert.deepEqual(math.answers.map((item) => item.answer), ["x+1", "2\\sqrt{(x+1)^2}", "2\\left(x+1\\right)"]);
    // The first decided answer was wrong, so Explain my mistake is offered.
    await card.getByRole("button", { name: "Explain my mistake" }).waitFor();
    assert.deepEqual(foreign, [], "MathLive fetched something from another origin");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Explain my mistake: working, then the reviewed AI request with its cost, approval, and the explanation bubble", async () => {
    const { page, problems, context, math } = await open("math", { ai: true, storage: { "kitty.math.drawers": JSON.stringify({ left: true, right: "" }) } });
    await context.route("**/api/ai/run", (route) => route.fulfill({ json: { task: { id: "task-1", kind: "openrouter-inference", status: "running", started_at: new Date().toISOString(), finished_at: null, result: null, error: null } } }));
    await context.route("**/api/tasks/task-1", (route) => route.fulfill({ json: {
      id: "task-1", kind: "openrouter-inference", status: "completed", started_at: new Date().toISOString(), finished_at: new Date().toISOString(), error: null,
      result: { text: "Your second line divides each term by $x$ instead of by $(x+1)$.", cost_usd: 0.0004 },
    } }));
    await page.getByRole("group", { name: "Topic graph" }).getByRole("button", { name: /^Fractions: Mastered/ }).click();
    await page.getByRole("button", { name: "Review Fractions" }).click();
    await page.getByRole("complementary", { name: "Topics" }).waitFor({ state: "detached" });
    const card = page.locator(".problem");
    assert.equal(await card.getByRole("button", { name: "Explain my mistake" }).count(), 0, "offered before any answer");
    const box = card.getByRole("textbox", { name: "Your answer" });
    await box.fill("1/2");
    await box.press("Enter");
    await card.locator('[data-tone="different"]').waitFor();
    await card.getByRole("button", { name: "Explain my mistake" }).click();
    const explain = page.getByRole("region", { name: "Explain my mistake" });
    await explain.getByRole("textbox", { name: "Your working" }).fill("3 red of 7\nP = 3/6 = 1/2");
    await explain.getByRole("button", { name: "Review the explanation request" }).click();
    const review = explain.locator(".review-box");
    await review.getByText("test/math-model").waitFor();
    await review.getByText(/Maximum token cost: \$0\.001200/).waitFor();
    await review.getByText("Only the problem, your answer and your working are sent.").waitFor();
    assert.deepEqual(math.explained, [{ problem_id: math.answers[0].problem_id, working: "3 red of 7\nP = 3/6 = 1/2" }]);
    const send = review.getByRole("button", { name: "Send approved request" });
    assert.equal(await send.isDisabled(), true, "sends before approval");
    await review.getByRole("checkbox", { name: /I approve/ }).check();
    await send.click();
    const bubble = explain.getByRole("note", { name: "Explanation" });
    await bubble.waitFor();
    assert.match(await bubble.innerText(), /divides each term by/);
    assert.ok(await bubble.locator(".katex").count() >= 2, "the explanation's math is not rendered");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("launchers", () => {
  test("Ctrl K 'Mental math sprint' and 'Next math lesson' open Math on that mode", async () => {
    const { page, problems, context } = await open("study");
    await page.getByRole("heading", { name: "Study", level: 2 }).waitFor();
    const palette = async (text, label) => {
      await page.keyboard.press("Control+k");
      const dialog = page.getByRole("dialog", { name: "Search rika!" });
      await dialog.getByRole("combobox").fill(text);
      await dialog.getByRole("option", { name: new RegExp(label) }).first().click();
    };
    await palette("mental", "Mental math sprint");
    await page.getByRole("group", { name: "Mental math sprint" }).getByRole("button", { name: "Start sprint" }).waitFor();
    assert.equal(await modes(page).getByRole("button", { name: "Sprint" }).getAttribute("aria-pressed"), "true");
    await palette("next math", "Next math lesson");
    await page.getByRole("region", { name: "Lesson" }).getByText("Worked example · Bayes' rule").waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("home Up next shows 'Math: next lesson' and a sprint streak nudge; each opens Math", async () => {
    const { page, problems, context } = await open("today", { history: [{ score: 11 }], streak: 4 });
    const lesson = page.getByRole("button", { name: /Math: next lesson/ });
    await lesson.waitFor({ timeout: 30_000 });
    assert.match(await lesson.innerText(), /Bayes' rule/);
    const nudge = page.getByRole("button", { name: /Sprint today to keep your 4-day streak/ });
    await nudge.waitFor();
    await nudge.click();
    await page.getByRole("group", { name: "Mental math sprint" }).waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("with focus practice on, a sprint starts a Study cycle tagged Math and the header shows the Math tag", async () => {
    const { page, problems, context } = await open("math", {
      storage: { "kitty.math.mode": "sprint", "kitty.math.settings": JSON.stringify({ mode: "zetamac", duration_s: 30, ops: ["add"], focus: true }) },
    });
    try {
      await page.getByRole("group", { name: "Mental math sprint" }).getByRole("button", { name: "Start sprint" }).click();
      const tag = page.locator("[data-focus-subject]");
      await tag.waitFor();
      assert.equal(await tag.getAttribute("data-focus-subject"), "Math");
      assert.match(await tag.innerText(), /Priming · Math · \d+:\d\d/);
      const timer = await page.evaluate(async () => (await (await fetch("/api/study/timer", { cache: "no-store" })).json()).timer);
      const subjects = await page.evaluate(async () => (await (await fetch("/api/study", { cache: "no-store" })).json()).subjects);
      assert.equal(timer.status, "running");
      assert.equal(subjects.find((subject) => subject.id === timer.subject_id)?.name, "Math");
    } finally {
      // The demo backend is shared by this file's tests: leave the timer idle.
      await page.evaluate(async () => {
        const { timer } = await (await fetch("/api/study/timer", { cache: "no-store" })).json();
        await fetch("/api/study/timer", { method: "POST", headers: { "Content-Type": "application/json", "X-Kitty-Request": "1" },
          body: JSON.stringify({ action: "stop", revision: timer.revision, request_id: crypto.randomUUID() }) });
      });
    }
    assert.deepEqual(problems, []);
    await context.close();
  });
});
