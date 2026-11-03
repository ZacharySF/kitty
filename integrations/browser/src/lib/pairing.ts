// Pairing this browser with rika! (platform design Q7), driven by the side panel/sidebar:
// probe the address, ask to pair, show the code rika! shows too, poll until the person
// approves it in rika!, then keep the token. A 401 later (the person revoked this
// browser, or rika!'s data was reset) forgets the token and pairs again.
import { Kitty, KittyError, normalizeAddress } from "./kitty.ts";
import type { Settings } from "./settings.ts";

export const POLL_MS = 2000;

export type PairingView =
  | { state: "unpaired"; message: string }
  | { state: "waiting"; code: string; address: string }
  | { state: "paired"; address: string; name: string }
  | { state: "error"; message: string };

export interface PairingHost {
  load(): Promise<Settings>;
  save(patch: Partial<Settings>): Promise<Settings>;
  kitty(address: string, token: string | null): Kitty;
  now(): number;
  /** The background should re-check mirrored tabs (paired or unpaired). */
  changed(): void;
}

/** The pairing state machine; `view` is what the panel shows. */
export class Pairing {
  private readonly host: PairingHost;
  private readonly name: string;

  constructor(host: PairingHost, name: string) {
    this.host = host;
    this.name = name;
  }

  /** Where things stand, checking a stored token with rika! (a 401 starts pairing again). */
  async check(): Promise<PairingView> {
    const settings = await this.host.load();
    if (settings.pending) return this.poll();
    if (settings.address && settings.token) {
      try {
        const client = await this.host.kitty(settings.address, settings.token).client();
        return { state: "paired", address: settings.address, name: client.name };
      } catch (error) {
        if (error instanceof KittyError && error.status === 401) {
          await this.host.save({ token: null, client: null, needsPairing: true });
          this.host.changed();
          return this.connect(settings.address);
        }
        return { state: "error", message: error instanceof Error ? error.message : String(error) };
      }
    }
    if (settings.needsPairing && settings.address) return this.connect(settings.address);
    return { state: "unpaired", message: "Not connected to rika! yet." };
  }

  /** Ask rika! at `input` to pair; the person approves the code in rika!. */
  async connect(input: string): Promise<PairingView> {
    const address = normalizeAddress(input);
    if (!address) return { state: "error", message: "Enter rika!'s address on this computer, such as http://127.0.0.1:8765." };
    try {
      const kitty = this.host.kitty(address, null);
      await kitty.probe();
      const ticket = await kitty.pair(this.name);
      await this.host.save({ address, pending: { address, request: ticket.request, code: ticket.code, expires: this.host.now() + ticket.expires_in * 1000 } });
      return { state: "waiting", code: ticket.code, address };
    } catch (error) {
      await this.host.save({ address });
      return { state: "error", message: error instanceof Error ? error.message : String(error) };
    }
  }

  /** One poll of a waiting request (the panel calls it every POLL_MS while waiting). */
  async poll(): Promise<PairingView> {
    const settings = await this.host.load();
    const pending = settings.pending;
    if (!pending) return this.check();
    if (this.host.now() > pending.expires) {
      await this.host.save({ pending: null });
      return { state: "unpaired", message: "The pairing request expired. Connect again." };
    }
    let result;
    try {
      result = await this.host.kitty(pending.address, null).pairStatus(pending.request);
    } catch {
      // rika! is briefly unreachable: keep waiting; rika! answers "expired" for a request it lost.
      return { state: "waiting", code: pending.code, address: pending.address };
    }
    if (result.status === "approved") {
      await this.host.save({ token: result.token, client: { id: result.client.id, name: result.client.name }, pending: null, needsPairing: false });
      this.host.changed();
      return { state: "paired", address: pending.address, name: result.client.name };
    }
    if (result.status === "pending") return { state: "waiting", code: pending.code, address: pending.address };
    await this.host.save({ pending: null });
    return { state: "unpaired", message: result.status === "denied" ? "rika! denied the request." : "The pairing request expired. Connect again." };
  }

  /** Forget the token here (revoke it in rika! → Settings → Connected apps as well). */
  async forget(): Promise<PairingView> {
    await this.host.save({ token: null, client: null, pending: null, needsPairing: false });
    this.host.changed();
    return { state: "unpaired", message: "Forgotten here. Revoke it in rika! → Settings → Connected apps too." };
  }
}
