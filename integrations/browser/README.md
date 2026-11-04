# rika! for the browser

A browser extension that mirrors the page you are reading into rika!, beside your notes,
and sends selections to rika!'s Inbox. It is platform design Q11 (the live page mirror)
and Q12 (one codebase for Firefox and Chromium-family browsers). MIT licensed, like rika!.

- **Firefox** (140 or later): a sidebar (`sidebar_action`) and an event page.
- **Chrome, Brave, Edge, Chromium** (120 or later): a side panel (`chrome.sidePanel`) and a
  service worker.

Both are Manifest V3 builds of the same code (`webextension-polyfill`). The toolbar button
opens the panel.

## What it does

The panel shows:

- **Connection**: rika!'s address and the pairing code; *Connect* pairs this browser.
- **This tab**: *Mirror this tab to rika!* (off until you switch it on, per tab), the reason
  when a page cannot be mirrored, and *Send selection to rika! Inbox*.
- **Never mirror**: what is always excluded, and your own rules.

While mirroring is on for a tab, the extension sends rika! the tab's address, title,
readable text ([Mozilla Readability](https://github.com/mozilla/readability)), your selection
and a JPEG screenshot (`tabs.captureVisibleTab`). It sends a frame when you switch mirroring
on, then at most one every 3 seconds while the page changes, scrolls or the selection
changes, and one a minute while the page is still. Screenshots across all tabs stay at least
600 ms apart (Chrome allows two a second). A tab in the background sends text only: a browser
can only capture the visible tab.

In rika!, open a note (Notes → Writing) and press **Mirror** in the page bar: the latest
screenshot, the title (a link to the page), the address and the readable text appear in a
pane beside the note. Quote a paragraph with its *Quote* button, select text in the pane and
press *Quote selection*, or press *Quote the browser selection*. The quote goes into the note
after the block your caret was last in (or at the end): a callout headed by the page title,
linked to the page, and the date and time, holding the passage as a quote.

*Send selection to rika! Inbox* puts the selection into rika!'s Inbox page the same way,
without mirroring.

## Privacy and retention

- Nothing is sent until you switch mirroring on for a tab or press *Send selection*.
- **Never mirrored or sent**, whatever you switch on: browser pages, files and extension pages
  (anything that is not `http`/`https`), the browsers' add-on stores, private and incognito
  windows, banking and payment sites, password managers, sign-in pages (by host, such as
  `accounts.google.com`, and by path, such as `/login`), and any page showing a password
  field. A mirrored tab that reaches one of these stops mirroring, and rika! drops its frame.
  Add your own rules in the panel: `example.com` (with subdomains), `*.example.com`,
  `*shop*` or `example.com/private`.
- **rika! keeps frames in memory only**: the latest screenshot and text of each mirrored tab
  (one frame per tab, at most 4 tabs per browser and 8 in all; screenshots at most 2 MiB,
  JPEG, PNG or WebP, decoded to check them). They are never written to rika!'s database or
  disk. A mirror is dropped when you switch mirroring off, close the tab, reach an excluded
  page, after 150 seconds without a frame, when you revoke the extension in rika!, and when
  rika! stops. Only passages you quote are saved, as blocks in your note.
- Everything goes only to rika! on this computer (`127.0.0.1`): the extension refuses any
  other address, and its pages' content security policy allows connections to
  `http://127.0.0.1:*` only.

## Network calls

Only the background (service worker or event page) and the panel call rika!. The content
script, injected into a tab only while you mirror it or send its selection, reads the page and
reports changes; it makes no network calls (Chrome's Local Network Access rules apply to page
contexts, and rika! refuses web pages' origins anyway). rika! accepts this extension's
token only from the extension origin that paired it (CORS answers only that origin).

## Connecting to rika!

rika! writes its address to the owner-only `.private/endpoint.json` for local programs. A
browser extension cannot read files, so you paste the address once: rika! shows it in
**Settings → Connected apps** (with *Copy address*). `kitty-web` uses
`http://127.0.0.1:8765` by default, which the panel suggests; the desktop app uses its own
port. `localhost` works too (it becomes `127.0.0.1`, which rika! requires).

*Connect* first checks that rika! answers at that address, then asks to pair and shows a
code. rika! shows the same code in a bar at the top of every window and in Settings →
Connected apps: approve it there. The extension asks only for the `capture` scope. Its token
is kept in the extension's local storage (`storage.local`).

If rika! later refuses the token (you revoked the extension, or rika!'s workspace is new),
the extension forgets it and pairs again by itself: the panel shows a new code to approve.
*Forget this connection* forgets the token here; revoke it in rika! as well.

## Building

```sh
cd integrations/browser
npm ci
npm run build      # dist/chromium, dist/firefox, dist/kitty-browser-<target>-<version>.zip
npm test           # node tests: deny list, pacing, messages, payloads, client, pairing, background logic, manifests
npm run check      # TypeScript
npm run lint:firefox   # Mozilla's web-ext lint (addons-linter) on dist/firefox
```

The build bundles Readability (Apache-2.0) and webextension-polyfill (MPL-2.0) from npm,
unmodified; their licences are in `THIRD_PARTY_NOTICES.txt` in each build. The code is not
minified. `web-ext lint` reports two `innerHTML` warnings, both inside Readability, which
parses a detached copy of the page.

The end-to-end test loads the Chromium build into Playwright's Chromium against
`kitty-web --demo`: `web/tests/browserMirror.browser.test.mjs`.

## Installing (unpacked)

**Chrome, Brave, Edge, Chromium:** open `chrome://extensions` (`brave://extensions`,
`edge://extensions`), turn on *Developer mode*, choose *Load unpacked* and pick
`integrations/browser/dist/chromium`. Pin the rika! button and press it to open the side
panel.

**Firefox:** open `about:debugging#/runtime/this-firefox`, choose *Load Temporary Add-on…*
and pick `integrations/browser/dist/firefox/manifest.json`. The toolbar button (or View →
Sidebar → rika!) opens the sidebar. A temporary add-on is removed when Firefox restarts.
If Firefox holds back access to web pages, switching mirroring on asks for it.

For a permanent Firefox install the add-on must be signed by Mozilla. rika! is not on
addons.mozilla.org; sign it yourself as an **unlisted** add-on (self-distributed, not
public): create API keys at addons.mozilla.org, then
`npx web-ext sign --source-dir dist/firefox --channel unlisted --api-key … --api-secret …`
and install the signed `.xpi`. Nothing in this repository submits or signs anything.
Firefox asks you to consent to the data the manifest declares (browsing activity and website
content, sent to rika! on this computer).

## Opening the panel as a page

`panel.html?tab=<tab id>` follows that tab instead of the active one; the automated test uses
this, since a test cannot press the toolbar button.
