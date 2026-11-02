// Runs in a mirrored tab only: the background injects it when the person switches
// mirroring on (scripting.executeScript), never into other pages. It makes no network
// calls. It reports that the page changed (scroll, resize, selection, DOM changes) and,
// when asked, reads the page: readable text (Mozilla Readability, Apache-2.0), the
// selection, and whether a visible password field is on the page.
import browser from "webextension-polyfill";
import { Readability } from "@mozilla/readability";
import type { Extraction } from "./lib/frame.ts";

declare global {
  interface Window { kittyMirror?: true }
}

const BLOCKS = "p,h1,h2,h3,h4,h5,h6,li,blockquote,pre,figcaption,dt,dd,td,th,caption";
const QUIET_MS = 400;

/** One paragraph per innermost block element of Readability's article (blank lines between). */
function paragraphs(html: string): string {
  const article = new DOMParser().parseFromString(html, "text/html").body;
  const parts: string[] = [];
  for (const element of article.querySelectorAll(BLOCKS)) {
    if (element.querySelector(BLOCKS)) continue;
    const text = (element.textContent ?? "").replace(/\s+/g, " ").trim();
    if (text) parts.push(element.tagName === "LI" ? `• ${text}` : text);
  }
  return parts.length ? parts.join("\n\n") : (article.textContent ?? "").replace(/[ \t]+/g, " ").trim();
}

function visiblePassword(): boolean {
  for (const input of document.querySelectorAll<HTMLInputElement>("input[type=password]")) {
    if (input.getClientRects().length && getComputedStyle(input).visibility !== "hidden") return true;
  }
  return false;
}

let cached: { text: string; title: string } | null = null;

function read(): Extraction {
  if (!cached) {
    let text = "";
    let title = document.title;
    try {
      const article = new Readability(document.cloneNode(true) as Document, { charThreshold: 200 }).parse();
      if (article?.content) text = paragraphs(article.content);
      if (article?.title) title = article.title;
    } catch {
      // Readability cannot handle every page; the visible text below is the fallback.
    }
    if (!text) text = (document.body?.innerText ?? "").replace(/\n{3,}/g, "\n\n").trim();
    cached = { text, title };
  }
  return {
    url: location.href,
    title: document.title || cached.title,
    text: cached.text,
    selection: window.getSelection()?.toString() ?? "",
    passwordField: visiblePassword(),
  };
}

if (!window.kittyMirror) {
  window.kittyMirror = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const changed = (content: boolean) => {
    if (content) cached = null;
    clearTimeout(timer);
    timer = setTimeout(() => void browser.runtime.sendMessage({ kind: "dirty" }).catch(() => {}), QUIET_MS);
  };
  addEventListener("scroll", () => changed(false), { passive: true, capture: true });
  addEventListener("resize", () => changed(false), { passive: true });
  document.addEventListener("selectionchange", () => changed(false));
  new MutationObserver(() => changed(true)).observe(document.documentElement, { subtree: true, childList: true, characterData: true });
  browser.runtime.onMessage.addListener((message: unknown) => {
    if (message && typeof message === "object" && "kind" in message && message.kind === "extract") return Promise.resolve(read());
    return undefined;
  });
}
