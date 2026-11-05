// Settings → Apply → Accounts (proposal-07 Step 4): the employer account registry and the
// owner's ATS-family grants. The server never sends a password, only where it is stored.
import { api } from "./api.ts";

export type AccountState = "unknown" | "created" | "verified" | "active" | "rejected" | "locked" | "manual";

export interface ApplyAccount {
  family: string;
  origin: string;
  login_email: string;
  state: AccountState;
  /** The password-policy profile of the stored password ("shared" = the credential file's). */
  password_profile: "standard" | "strict" | "shared";
  /** "keyring" | "file" | "shared file" | "unavailable", or null when none is stored yet. */
  password_stored: string | null;
  created_at: string;
  updated_at: string;
  account_created_at: string | null;
  verified_at: string | null;
  last_sign_in_at: string | null;
  sign_ins_today: number;
}

export interface FamilyGrant {
  family: string;
  label: string;
  patterns: string[];
  granted: boolean;
  granted_at: string | null;
  plus_alias: boolean;
  sender: string | null;
}

export interface AccountsState {
  accounts: ApplyAccount[];
  grants: FamilyGrant[];
  caps: { new_accounts_per_day: number; new_accounts_today: number; sign_ins_per_tenant_per_day: number };
  store: "keyring" | "file";
  keyring: boolean;
  keyring_locked: boolean;
  warnings: string[];
}

const STATES: Record<AccountState, string> = {
  unknown: "Not created yet",
  created: "Created, awaiting email",
  verified: "Email verified",
  active: "Signed in",
  rejected: "Password refused",
  locked: "Locked by the employer",
  manual: "Manual sign-in",
};

export function stateLabel(state: AccountState): string {
  return STATES[state] ?? state;
}

/** States that need the owner before kitty tries again. */
export function needsOwner(state: AccountState): boolean {
  return state === "rejected" || state === "locked" || state === "manual";
}

export function storedLabel(stored: string | null): string {
  if (stored === "keyring") return "OS keyring";
  if (stored === "file") return "owner-only file";
  if (stored === "shared file") return "shared credential file";
  if (stored === "unavailable") return "keyring unavailable";
  return "none yet";
}

/** "acme.wd5.myworkdayjobs.com" from "https://acme.wd5.myworkdayjobs.com". */
export function hostOf(origin: string): string {
  return origin.replace(/^https:\/\//, "");
}

/** The date part of an ISO timestamp, or "". */
export function day(stamp: string | null): string {
  return stamp ? stamp.slice(0, 10) : "";
}

export function loadAccounts(): Promise<AccountsState> {
  return api<AccountsState>("/apply/accounts");
}

export function setGrant(family: string, granted: boolean): Promise<AccountsState> {
  return api<AccountsState>("/apply/accounts/grant", { family, granted });
}
