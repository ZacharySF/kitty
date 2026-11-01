// Settings → Devices: device-to-device sync (platform design Q16). Two devices pair once with
// a short code; afterwards they find each other on the local network or at an address the
// user sets (for example a Tailscale name) and sync notes directly, end-to-end encrypted.
import { api } from "./api.ts";

export type DiscoveryState = "on" | "off" | "error";
export type PeerStatus = "idle" | "syncing" | "offline" | "error" | "paused";

export interface ThisDevice {
  id: string;
  name: string;
  /** Grouped hex, e.g. "3f2a 9c01 77be 0d4e"; compare it with the other device. */
  fingerprint: string;
  port: number | null;
  /** "host:port" this device can be reached at. */
  addresses: string[];
  listening: boolean;
  error: string | null;
  paused: boolean;
  discovery: DiscoveryState;
  discovery_error: string | null;
}
export interface Pairing {
  /** "ABC-DEF" */
  code: string;
  expires_at: string;
  attempts_left: number;
  /** Set once a device paired with this code. */
  paired: null | { device_id: string; name: string };
}
export interface DiscoveredDevice {
  device_id: string;
  name: string;
  address: string;
  /** It is showing a pairing code right now. */
  pairing: boolean;
  paired: boolean;
}
export interface Peer {
  device_id: string;
  name: string;
  /** The address the user set (LAN IP, Tailscale name or IP); null when found automatically. */
  address: string | null;
  last_address: string | null;
  paired_at: string;
  last_sync_at: string | null;
  reached_at: string | null;
  status: PeerStatus;
  error: string | null;
  /** This device's changes the peer has not acknowledged yet. */
  pending: number;
  /** Its unacknowledged changes are holding back history compaction. */
  holds_history: boolean;
  online: boolean;
}
export interface DevicesState {
  device: ThisDevice;
  pairing: Pairing | null;
  discovered: DiscoveredDevice[];
  peers: Peer[];
}

export const NAME_MAX = 64;
const CODE_HALF = 3;
const CODE = /^[A-Z0-9]{3}-[A-Z0-9]{3}$/;

/** Formats what the user typed as a pairing code: "abcdef" → "ABC-DEF", "ab" → "AB". */
export function formatCode(value: string): string {
  const chars = value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, CODE_HALF * 2);
  return chars.length > CODE_HALF ? `${chars.slice(0, CODE_HALF)}-${chars.slice(CODE_HALF)}` : chars;
}

/** True when the typed code is complete (six letters or digits). */
export function codeComplete(value: string): boolean {
  return CODE.test(formatCode(value));
}

/** A trimmed device name, or "" when it is empty or longer than the server accepts. */
export function deviceName(value: string): string {
  const name = value.trim();
  return name.length >= 1 && name.length <= NAME_MAX ? name : "";
}

/** A trimmed address, or null for an empty field (which clears an explicit address). */
export function peerAddress(value: string | null): string | null {
  const address = (value ?? "").trim();
  return address ? address : null;
}

function parse(value: string | null): number | null {
  if (!value) return null;
  const time = Date.parse(value);
  return Number.isNaN(time) ? null : time;
}

const plural = (count: number, unit: string) => `${count} ${unit}${count === 1 ? "" : "s"}`;

/** "just now", "5 minutes ago", "3 hours ago", "2 days ago"; "never" without a time. */
export function relativeTime(value: string | null, now: number = Date.now()): string {
  const time = parse(value);
  if (time === null) return "never";
  const seconds = Math.max(0, Math.round((now - time) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${plural(minutes, "minute")} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${plural(hours, "hour")} ago`;
  return `${plural(Math.round(hours / 24), "day")} ago`;
}

/** Seconds left before the pairing code expires (never negative). */
export function secondsLeft(expiresAt: string, now: number = Date.now()): number {
  const time = parse(expiresAt);
  return time === null ? 0 : Math.max(0, Math.ceil((time - now) / 1000));
}

/** "4:05" for the pairing code countdown; "expired" at zero. */
export function countdown(expiresAt: string, now: number = Date.now()): string {
  const left = secondsLeft(expiresAt, now);
  if (left === 0) return "expired";
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

/** The status badge text of a paired device. */
export function statusLabel(peer: Pick<Peer, "status" | "error" | "last_sync_at">, now: number = Date.now()): string {
  switch (peer.status) {
    case "syncing": return "Syncing…";
    case "offline": return "Offline";
    case "paused": return "Paused";
    case "error": return `Error: ${peer.error || "sync failed"}`;
    default: return peer.last_sync_at ? `Synced ${relativeTime(peer.last_sync_at, now)}` : "Not synced yet";
  }
}

export type StatusTone = "ok" | "busy" | "muted" | "bad";
const TONES: Record<PeerStatus, StatusTone> = { idle: "ok", syncing: "busy", offline: "muted", paused: "muted", error: "bad" };
export function statusTone(status: PeerStatus): StatusTone {
  return Object.hasOwn(TONES, status) ? TONES[status] : "muted";
}

/** Nearby devices showing a pairing code that are not paired with this one yet. */
export function pairable(discovered: DiscoveredDevice[]): DiscoveredDevice[] {
  return discovered.filter((device) => device.pairing && !device.paired);
}

/** Where this device listens, or why it does not. */
export function listenLabel(device: Pick<ThisDevice, "listening" | "addresses" | "error">): string {
  if (device.listening) return device.addresses.length ? `Listening at ${device.addresses.join(", ")}` : "Listening";
  return device.error ? `Not listening: ${device.error}` : "Not listening";
}

export function discoveryLabel(device: Pick<ThisDevice, "discovery" | "discovery_error">): string {
  if (device.discovery === "on") return "Finding devices on this network";
  if (device.discovery === "error") return `Finding devices on this network failed${device.discovery_error ? `: ${device.discovery_error}` : ""}`;
  return "Not looking for devices on this network";
}

/** The pending-changes line of a paired device; "" when everything was acknowledged. */
export function pendingLabel(pending: number): string {
  return pending > 0 ? `${plural(pending, "change")} from this device not received yet` : "";
}

export function loadDevices(): Promise<DevicesState> {
  return api<DevicesState>("/devices");
}

export function renameDevice(name: string): Promise<DevicesState> {
  return api<DevicesState>("/devices/name", { name: name.trim() });
}

export function showPairingCode(): Promise<DevicesState> {
  return api<DevicesState>("/devices/pair/show", {});
}

export function cancelPairing(): Promise<DevicesState> {
  return api<DevicesState>("/devices/pair/cancel", {});
}

/** Pairs with the device showing `code` at `address` ("host:port" or "host"); takes up to ~15 s. */
export function enterPairingCode(code: string, address: string): Promise<DevicesState> {
  return api<DevicesState>("/devices/pair/enter", { code: formatCode(code), address: address.trim() });
}

/** Starts a sync with one paired device, or with every one when no id is given. */
export function syncNow(deviceId?: string): Promise<DevicesState> {
  return api<DevicesState>("/devices/sync", deviceId ? { device_id: deviceId } : {});
}

export function unpair(deviceId: string): Promise<DevicesState> {
  return api<DevicesState>("/devices/unpair", { device_id: deviceId });
}

/** Sets the address a paired device is reached at; null or "" clears it. */
export function setPeerAddress(deviceId: string, address: string | null): Promise<DevicesState> {
  return api<DevicesState>("/devices/address", { device_id: deviceId, address: peerAddress(address) });
}

export function setPaused(paused: boolean): Promise<DevicesState> {
  return api<DevicesState>("/devices/pause", { paused });
}
