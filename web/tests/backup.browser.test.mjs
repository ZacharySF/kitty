// Settings → Backup in a real browser against a stand-in /api/backup (tests/backupHarness.js):
// History, Sync and Backup named apart, a backup, a preview with counts, a refused destination,
// a restore, and the GitHub backup's warning beside its control. Nothing touches disk.
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/backup.browser.test.mjs
// KITTY_SCREENSHOTS=<folder> also saves screenshots of the page before and after a restore.
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
const shots = process.env.KITTY_SCREENSHOTS;
let server;
let browser;
let base;

before(async () => {
  server = await createServer({ root, logLevel: "error", server: { host: "127.0.0.1", port: 0 } });
  await server.listen();
  base = server.resolvedUrls.local[0];
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  await server?.close();
});

async function harness() {
  const context = await browser.newContext({ viewport: { width: 900, height: 1000 }, colorScheme: "dark" });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/backup-harness.html", base).href, { timeout: 90000 });
  await page.getByRole("region", { name: "Backup", exact: true }).waitFor({ timeout: 60000 });
  return { page, context, problems };
}
const posts = (page) => page.evaluate(() => window.backupHarness.posts());

test("History, Sync and Backup are named apart; backing up, previewing and restoring into a new folder", async () => {
  const { page, context, problems } = await harness();
  const panel = page.getByRole("region", { name: "Backup", exact: true });
  await panel.getByText("Earlier versions of a page, inside this workspace. Not a backup.").waitFor();
  await panel.getByText("Copies changes to paired devices, mistakes too. Not a backup.").waitFor();
  assert.equal(await panel.getByRole("link", { name: "Devices" }).getAttribute("href"), "#settings/devices");
  await panel.getByText("None yet").waitFor();

  // The GitHub backup keeps its privacy warning beside its own control.
  const github = page.getByRole("region", { name: "GitHub backup" });
  await github.getByText(/stored on GitHub unencrypted, so GitHub can read it/).waitFor();
  await github.getByRole("textbox", { name: "GitHub repository" }).waitFor();

  // Every control sits on the 28px control line.
  const folder = panel.getByRole("textbox", { name: "Backup folder" });
  assert.equal(await folder.inputValue(), "/home/synthetic/rika-backups");
  for (const control of [folder, panel.getByRole("button", { name: "Back up now" }), panel.getByRole("textbox", { name: "Backup file" })]) {
    assert.equal(Math.round((await control.boundingBox()).height), 28);
  }

  await panel.getByRole("button", { name: "Back up now" }).click();
  await panel.getByText("/home/synthetic/rika-backups/rika-backup-20261117-090000.zip").waitFor();
  await page.getByRole("status").filter({ hasText: "Saved 5.0 MB" }).waitFor();
  assert.equal(await panel.getByRole("textbox", { name: "Backup file" }).inputValue(),
    "/home/synthetic/rika-backups/rika-backup-20261117-090000.zip");

  // The open workspace is refused before anything is written.
  const preview = panel.getByRole("button", { name: "Preview" });
  assert.equal(await preview.isDisabled(), true);
  await panel.getByRole("textbox", { name: "Restore into" }).fill("/home/synthetic/workspace");
  await preview.click();
  await panel.getByRole("alert").filter({ hasText: "That is the open workspace" }).waitFor();
  assert.equal(await panel.getByRole("button", { name: "Restore", exact: true }).isDisabled(), true);

  // A new folder: counts and warnings, then a restore and the commands that open the copy.
  await panel.getByRole("textbox", { name: "Restore into" }).fill("/home/synthetic/restored");
  assert.equal(await panel.getByRole("button", { name: "Restore", exact: true }).count(), 0, "a preview outlived its fields");
  await preview.click();
  const result = panel.getByLabel("Restore preview");
  await result.getByText("Flashcard reviews").waitFor();
  assert.equal((await result.locator("dd").allTextContents()).length, 9);
  await result.getByText("Not in this backup: Keys and passwords, Sign-ins").waitFor();
  if (shots) await page.screenshot({ path: join(shots, "backup-preview.png"), fullPage: true });
  await panel.getByRole("button", { name: "Restore", exact: true }).click();
  const done = panel.getByLabel("Restored");
  await done.getByText("/home/synthetic/restored", { exact: true }).waitFor();
  await done.getByText("kitty-workspace --workspace /home/synthetic/restored").waitFor();
  await done.getByText("KITTY_HOME=/home/synthetic/restored kitty-web").waitFor();
  if (shots) await page.screenshot({ path: join(shots, "backup-restored.png"), fullPage: true });

  assert.deepEqual(await posts(page), [
    ["/api/backup/create", { folder: "/home/synthetic/rika-backups" }],
    ["/api/backup/preview", { archive: "/home/synthetic/rika-backups/rika-backup-20261117-090000.zip", into: "/home/synthetic/workspace" }],
    ["/api/backup/preview", { archive: "/home/synthetic/rika-backups/rika-backup-20261117-090000.zip", into: "/home/synthetic/restored" }],
    ["/api/backup/restore", { archive: "/home/synthetic/rika-backups/rika-backup-20261117-090000.zip", into: "/home/synthetic/restored" }],
  ]);
  const headers = await page.evaluate(() => window.backupHarness.calls.find((call) => call.method === "POST").headers);
  assert.equal(headers["X-Kitty-Request"], "1");
  assert.deepEqual(problems, []);
  await context.close();
});
