// Settings → Keys (platform design Q10). The server reports whether each key is set and
// where it is stored; a value is sent once to /api/keys/set and never comes back.
import { api } from "./api.ts";

export type KeyBackend = "keyring" | "file" | "env";
export interface KeyStatus {
  name: string;
  label: string;
  /** The environment variable `kitty keys run` injects it as. */
  env: string;
  custom: boolean;
  set: boolean;
  backend: KeyBackend | null;
  /** An owner-only file copy exists (it can be moved to the keyring). */
  in_file: boolean;
  error: string | null;
}
export interface KeysState {
  keys: KeyStatus[];
  keyring: boolean;
  keyring_label: string | null;
  /** Where a new key is stored. */
  store: "keyring" | "file";
  /** Generated employer account passwords (APPLY_PW_*), listed per employer in Settings → Accounts. */
  account_passwords: { count: number; in_file: number };
  /** Visible warnings, e.g. account passwords held in owner-only files without a keyring. */
  warnings: string[];
}

export const KEY_NAME = /^[A-Z][A-Z0-9_]{0,63}$/;

const BACKENDS: Record<KeyBackend, string> = {
  keyring: "OS keyring",
  file: "owner-only file",
  env: "environment (.env)",
};

export function backendLabel(backend: KeyBackend | null): string {
  return backend ? BACKENDS[backend] : "not set";
}

/** Upper-cases a typed name; "" when it is not a valid key name. */
export function keyName(value: string): string {
  const name = value.trim().toUpperCase();
  return KEY_NAME.test(name) ? name : "";
}

export function loadKeys(): Promise<KeysState> {
  return api<KeysState>("/keys");
}

export function setKey(name: string, value: string): Promise<KeysState> {
  return api<KeysState>("/keys/set", { name, value });
}

export function removeKey(name: string): Promise<KeysState> {
  return api<KeysState>("/keys/remove", { name });
}

export function moveKey(name: string): Promise<KeysState> {
  return api<KeysState>("/keys/move", { name });
}
