// kitty!'s client ingress (platform design Q7) as this extension uses it. Only the
// background and the side panel/sidebar call it, never a content script: Chrome's Local
// Network Access rules apply to page contexts, and kitty! refuses web page origins.
//
// An extension cannot read kitty!'s owner-only .private/endpoint.json (it has no file
// access), so the person pastes kitty!'s address once (Settings → Connected apps shows
// it); kitty-web's default is http://127.0.0.1:8765.
import type { FrameBody, SnapshotBody } from "./frame.ts";

export const DEFAULT_ADDRESS = "http://127.0.0.1:8765";
export const SCOPES = ["capture"] as const;

export class KittyError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;

  constructor(message: string, status: number, retryAfter: number | null = null) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/**
 * kitty!'s origin from what the person typed or pasted, or null. kitty! serves only
 * on 127.0.0.1 and checks the exact Host, so `localhost` becomes 127.0.0.1; no other
 * host is accepted (page text never leaves this computer).
 */
export function normalizeAddress(input: string): string | null {
  let text = input.trim();
  if (!text) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) text = `http://${text}`;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" || url.username || url.password) return null;
  if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") return null;
  // URL drops an explicit :80 (http's default); without any port, kitty-web's default applies.
  const port = url.port || (/^http:\/\/[^/]+:80(\/|$)/i.test(text) ? "80" : "8765");
  return `http://127.0.0.1:${port}`;
}

export type PairTicket = { status: "pending"; request: string; code: string; expires_in: number };
export type PairResult =
  | { status: "pending" | "denied" | "expired" }
  | { status: "approved"; token: string; client: { id: string; name: string; scopes: string[] } };

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** One kitty! at `address`, with this extension's token once paired. */
export class Kitty {
  readonly address: string;
  private readonly token: string | null;
  private readonly fetch: Fetch;

  constructor(address: string, token: string | null, fetcher: Fetch = (input, init) => fetch(input, init)) {
    this.address = address;
    this.token = token;
    this.fetch = fetcher;
  }

  private async call<T>(method: "GET" | "POST", path: string, body?: unknown, auth = true): Promise<T> {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (auth && this.token) headers.Authorization = `Bearer ${this.token}`;
    let response: Response;
    try {
      response = await this.fetch(`${this.address}/api/ingress${path}`, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store", credentials: "omit",
      });
    } catch {
      throw new KittyError(`kitty! is not reachable at ${this.address}. Is it running?`, 0);
    }
    const data = (await response.json().catch(() => null)) as { error?: string } | null;
    if (!response.ok) {
      const retry = Number(response.headers.get("Retry-After"));
      throw new KittyError(data?.error || `kitty! answered ${response.status}`, response.status, Number.isFinite(retry) && retry > 0 ? retry : null);
    }
    return data as T;
  }

  /**
   * Is kitty! at this address? Asks the token route without a token: kitty! answers 401
   * with its Bearer realm. Anything else is not a kitty! (or not this version).
   */
  async probe(): Promise<void> {
    let response: Response;
    try {
      response = await this.fetch(`${this.address}/api/ingress/client`, { cache: "no-store", credentials: "omit" });
    } catch {
      throw new KittyError(`Nothing answered at ${this.address}. Start kitty! or check the address.`, 0);
    }
    const realm = response.headers.get("WWW-Authenticate") ?? "";
    const data = (await response.json().catch(() => null)) as { error?: string } | null;
    if (response.status !== 401 || !(realm.includes('realm="kitty"') || typeof data?.error === "string")) {
      throw new KittyError(`${this.address} did not answer like kitty! does.`, response.status);
    }
  }

  pair(name: string): Promise<PairTicket> {
    return this.call("POST", "/pair", { name, kind: "browser-extension", scopes: [...SCOPES] }, false);
  }

  pairStatus(request: string): Promise<PairResult> {
    return this.call("POST", "/pair/status", { request }, false);
  }

  client(): Promise<{ id: string; name: string; scopes: string[] }> {
    return this.call("GET", "/client");
  }

  frame(body: FrameBody): Promise<{ mirror: string; frame: number; expires_in: number }> {
    return this.call("POST", "/mirror/frame", body);
  }

  stop(tab: number | string): Promise<{ stopped: boolean }> {
    return this.call("POST", "/mirror/stop", { tab: String(tab) });
  }

  /** The selection as a page snapshot in kitty!'s Inbox. */
  snapshot(body: SnapshotBody): Promise<{ page: number; block: string }> {
    return this.call("POST", "/mirror", body);
  }
}
