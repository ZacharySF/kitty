// The live mirrors this window knows (platform design Q11): loaded from GET /api/mirror and
// kept current by the live channel's `mirror` and `mirror_end` messages. Shared by the
// Notes page bar (a dot while a tab is mirrored) and the Mirror pane.
import { api } from "./api";
import { keepLive, onLive, type LiveMessage } from "./live";
import { applyFrame, endMirror, fromListing, type Mirror, type MirrorFrame, type MirrorListing, type Retention } from "./mirror";

export const mirrors = $state<{ list: Mirror[]; retention: Retention | null; error: string }>({ list: [], retention: null, error: "" });

let started = false;
let loading: Promise<void> | null = null;

function reload(): Promise<void> {
  loading ??= api<MirrorListing>("/mirror")
    .then((listing) => {
      mirrors.list = fromListing(listing);
      mirrors.retention = listing.retention;
      mirrors.error = "";
    })
    .catch((cause) => { mirrors.error = (cause as Error).message; })
    .finally(() => { loading = null; });
  return loading;
}

/** Start following mirrors (idempotent); the first caller loads the current list. */
export function followMirrors(): void {
  if (started) return;
  started = true;
  keepLive();
  onLive("mirror", (message: LiveMessage) => {
    const { list, stale } = applyFrame(mirrors.list, message as unknown as MirrorFrame);
    mirrors.list = list;
    if (stale) void reload();
  });
  onLive("mirror_end", (message: LiveMessage) => { mirrors.list = endMirror(mirrors.list, String(message.id)); });
  // A (re)connected socket may have missed frames, or rika! restarted and dropped them all.
  onLive("hello", () => void reload());
  void reload();
}
