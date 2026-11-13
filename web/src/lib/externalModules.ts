/** Pure helpers for add-on modules installed from GitHub releases (Settings → Modules). */

const OWNER = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;
const REPOSITORY = /^[A-Za-z0-9._-]{1,100}$/;

/**
 * `owner/repo` from what the user typed: the short form, or a pasted
 * `https://github.com/owner/repo` link. Null when it is neither.
 */
export function parseRepository(text: string): string | null {
  const value = text
    .trim()
    .replace(/^(?:https?:\/\/)?(?:www\.)?github\.com\//i, "")
    .replace(/\/+$/, "")
    .replace(/\.git$/i, "");
  const [owner, repo, ...rest] = value.split("/");
  if (rest.length || !owner || !repo || !OWNER.test(owner) || !REPOSITORY.test(repo) || repo === "." || repo === "..") return null;
  return `${owner}/${repo}`;
}

/** The `/api` path a module's own routes live under; its views reach nothing else. */
export function modulePath(module: string, path: string): string {
  const rest = path.replace(/^\/+/, "");
  return `/modules/${module}${rest ? `/${rest}` : ""}`;
}
