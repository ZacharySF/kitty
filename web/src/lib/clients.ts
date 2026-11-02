// Paired outside programs (platform design Q7): the kitty Tern plugin and the browser
// extension. kitty! approves or denies their pairing requests and revokes their tokens;
// the programs themselves only ever call /api/ingress.
import { api } from "./api";

export type ClientScope = "capture" | "read";
export type ClientKind = "tern-plugin" | "browser-extension";
export interface PairedClient {
  id: string;
  name: string;
  kind: ClientKind;
  scopes: ClientScope[];
  /** The extension origin it paired from; null for a program outside a browser. */
  origin: string | null;
  created_at: string;
  last_used_at: string | null;
}
export interface PairingRequest {
  id: string;
  name: string;
  kind: ClientKind;
  scopes: ClientScope[];
  origin: string | null;
  /** Shown by the requesting program too; approve only when both match. */
  code: string;
  requested_at: string;
  expires_in: number;
}
export interface ClientsState {
  clients: PairedClient[];
  requests: PairingRequest[];
  /** This kitty!'s address, which a person pastes into the browser extension. */
  address: string | null;
}

/** Fired after any approval, denial or revocation so every open view refreshes. */
export const CLIENTS_EVENT = "kitty-clients-changed";

const KINDS: Record<ClientKind, string> = { "tern-plugin": "Tern plugin", "browser-extension": "Browser extension" };
const SCOPES: Record<ClientScope, string> = {
  capture: "capture notes and page snapshots",
  read: "read recent page and task titles",
};

export function kindLabel(kind: string): string {
  return Object.hasOwn(KINDS, kind) ? KINDS[kind as ClientKind] : kind;
}

export function scopeLabel(scopes: string[]): string {
  return scopes.map((scope) => (Object.hasOwn(SCOPES, scope) ? SCOPES[scope as ClientScope] : scope)).join(" · ");
}

export function loadClients(): Promise<ClientsState> {
  return api<ClientsState>("/clients");
}

async function change(action: "approve" | "deny" | "revoke", id: string): Promise<ClientsState> {
  const next = await api<ClientsState>(`/clients/${action}`, { id });
  window.dispatchEvent(new CustomEvent(CLIENTS_EVENT, { detail: next }));
  return next;
}

export function decide(id: string, approve: boolean): Promise<ClientsState> {
  return change(approve ? "approve" : "deny", id);
}

export function revoke(id: string): Promise<ClientsState> {
  return change("revoke", id);
}
