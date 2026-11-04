// The two manifests, from one description (platform design Q12): Chrome, Brave and Edge get
// a side panel and a background service worker; Firefox gets a sidebar and an event page.
// Both are Manifest V3.

export const TARGETS = ["chromium", "firefox"];
export const GECKO_ID = "kitty-browser@kitty";

const ICONS = { 16: "icons/icon-16.png", 32: "icons/icon-32.png", 48: "icons/icon-48.png", 96: "icons/icon-96.png", 128: "icons/icon-128.png" };

/** Extension pages (panel, background) may reach only rika! on this computer. */
export const CSP = "default-src 'self'; script-src 'self'; object-src 'none'; connect-src http://127.0.0.1:*; img-src 'self'; style-src 'self'";

export function manifest(target, version) {
  if (!TARGETS.includes(target)) throw new Error(`Unknown target ${target}`);
  const common = {
    manifest_version: 3,
    name: "rika!",
    version,
    description: "Mirror the page you are reading into rika! beside your notes, and send selections to rika!'s Inbox.",
    icons: ICONS,
    action: { default_title: "rika!", default_icon: { 16: ICONS[16], 32: ICONS[32] } },
    // storage: address, token and rules; scripting: read a page only when you mirror it or
    // send its selection; alarms: a frame a minute while a mirrored page is still.
    permissions: ["storage", "scripting", "alarms"],
    // Reading pages and tabs.captureVisibleTab need host access; rika! itself is on 127.0.0.1.
    host_permissions: ["<all_urls>"],
    content_security_policy: { extension_pages: CSP },
  };
  if (target === "chromium") {
    return {
      ...common,
      minimum_chrome_version: "120",
      permissions: [...common.permissions, "sidePanel"],
      background: { service_worker: "background.js" },
      side_panel: { default_path: "panel.html" },
    };
  }
  return {
    ...common,
    background: { scripts: ["background.js"] },
    sidebar_action: { default_title: "rika!", default_panel: "panel.html", default_icon: { 16: ICONS[16], 32: ICONS[32] }, open_at_install: false },
    browser_specific_settings: {
      gecko: {
        id: GECKO_ID,
        strict_min_version: "140.0",
        // Firefox's built-in consent: mirroring sends the pages you switch on (their
        // addresses and content) to rika! on this computer.
        data_collection_permissions: { required: ["browsingActivity", "websiteContent"] },
      },
      // Firefox for Android has no sidebar; its built-in consent arrived in 142.
      gecko_android: { strict_min_version: "142.0" },
    },
  };
}
