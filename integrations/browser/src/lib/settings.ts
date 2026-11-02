// What the extension keeps in browser.storage.local: kitty!'s address, the token kitty!
// issued when the person approved this browser, a pairing in progress, and the person's
// own deny-list rules. The token never leaves this storage except as the Authorization
// header to that address.
import browser from "webextension-polyfill";
import { Kitty } from "./kitty.ts";

export type Pending = { address: string; request: string; code: string; expires: number };
export type Settings = {
  address: string | null;
  token: string | null;
  client: { id: string; name: string } | null;
  pending: Pending | null;
  /** kitty! refused the token (revoked or a reinstall of kitty!): the panel pairs again. */
  needsPairing: boolean;
  rules: string[];
};

const DEFAULTS: Settings = { address: null, token: null, client: null, pending: null, needsPairing: false, rules: [] };
/** The one storage.local key holding Settings (the background watches it for changes). */
export const SETTINGS_KEY = "kitty";

export async function loadSettings(): Promise<Settings> {
  const stored = (await browser.storage.local.get(SETTINGS_KEY))[SETTINGS_KEY] as Partial<Settings> | undefined;
  return { ...DEFAULTS, ...(stored ?? {}) };
}

export async function saveSettings(patch: Partial<Settings>): Promise<Settings> {
  const next = { ...(await loadSettings()), ...patch };
  await browser.storage.local.set({ [SETTINGS_KEY]: next });
  return next;
}

/** kitty! with this browser's token, or null while not paired. */
export function connected(settings: Settings): Kitty | null {
  return settings.address && settings.token ? new Kitty(settings.address, settings.token) : null;
}
