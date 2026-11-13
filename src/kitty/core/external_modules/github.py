"""A module repository's latest GitHub release, its checksum, and a verified download.

Only ``GET /repos/<owner>/<repo>/releases/latest`` and the release's own asset URLs are
requested. The optional token (Settings → Keys → GitHub token) is sent only to the
GitHub API origin; a redirect to a download host drops it. Downloads stream to disk
while their SHA-256 is computed, bounded in size.

``KITTY_GITHUB_API`` replaces ``https://api.github.com`` for local development and
tests (a loopback stand-in); plain ``http`` is accepted only on that origin.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from kitty.core.external_modules.manifest import ID_RE, VERSION_RE

API = "https://api.github.com"
MAX_RELEASE_BYTES = 2 * 1024 * 1024
MAX_ASSET_BYTES = 1024 ** 3
MAX_CHECKSUM_BYTES = 4096
MAX_REDIRECTS = 5
OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
HEX_RE = re.compile(r"[0-9a-f]{64}")
KEYS_HINT = "If the repository is private, add a GitHub token in Settings → Keys."


class GitHubError(ValueError):
    """The release could not be found, fetched or trusted; the message is the next step."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int | None
    #: The SHA-256 GitHub published for this asset (its ``digest`` field), lower-case hex.
    digest: str | None


@dataclass(frozen=True)
class Release:
    repo: str
    tag: str
    version: str
    module_id: str
    asset: Asset
    #: ``<asset>.sha256``, when the release also publishes one.
    checksum: Asset | None


def api_base() -> str:
    return (os.environ.get("KITTY_GITHUB_API", "").strip() or API).rstrip("/")


def client() -> httpx.Client:
    """The HTTP client for one install (tests replace this function)."""
    return httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=False)


def parse_repo(value: object) -> str:
    """``owner/repo`` from that text or a github.com repository URL."""
    if not isinstance(value, str):
        raise GitHubError("Enter the module's GitHub repository as owner/repo")
    text = value.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.rstrip("/")
    text = text.removesuffix(".git")
    parts = text.split("/")
    if (len(parts) != 2 or not OWNER_RE.fullmatch(parts[0]) or not REPO_RE.fullmatch(parts[1])
            or parts[1] in {".", ".."}):
        raise GitHubError("Enter the module's GitHub repository as owner/repo, like ZacharySF/rika-learn")
    return f"{parts[0]}/{parts[1]}"


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme, (parts.hostname or ""), parts.port


def _allowed(url: str) -> bool:
    parts = urlsplit(url)
    if parts.username or parts.password or not parts.hostname:
        return False
    return parts.scheme == "https" or _origin(url) == _origin(api_base())


def _headers(url: str, token: str, accept: str) -> dict[str, str]:
    headers = {"Accept": accept, "User-Agent": "rika-modules", "X-GitHub-Api-Version": "2022-11-28"}
    if token and _origin(url) == _origin(api_base()):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get(http: httpx.Client, url: str, token: str, accept: str):
    """A streamed GET that follows at most a few redirects itself, re-deciding the token each hop."""
    for _ in range(MAX_REDIRECTS + 1):
        if not _allowed(url):
            raise GitHubError("GitHub pointed the download at an address rika! does not trust; nothing was installed")
        request = http.build_request("GET", url, headers=_headers(url, token, accept))
        try:
            response = http.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise GitHubError(f"Could not reach GitHub ({type(exc).__name__}). Check the internet connection and try again.") from None
        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
            response.close()
            url = urljoin(url, response.headers["Location"])
            continue
        return response
    raise GitHubError("GitHub redirected the download too many times")


def _refusal(status: int, response: httpx.Response, repo: str) -> GitHubError:
    if status == 404:
        return GitHubError(f"No published release was found for {repo}. Check the name; {KEYS_HINT}")
    if status in (401, 403) and response.headers.get("x-ratelimit-remaining") == "0":
        return GitHubError("GitHub's rate limit was reached. Try again in an hour, or add a GitHub token in Settings → Keys.")
    if status in (401, 403):
        return GitHubError(f"GitHub refused access to {repo}: the GitHub token in Settings → Keys is missing, "
                           "expired or cannot read this repository.")
    return GitHubError(f"GitHub answered {status} for {repo}; try again later.")


def _read(response: httpx.Response, limit: int) -> bytes:
    data = bytearray()
    for chunk in response.iter_bytes():
        data += chunk
        if len(data) > limit:
            raise GitHubError("GitHub's answer was larger than expected; nothing was installed")
    return bytes(data)


def _asset(item: object) -> Asset | None:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("url"), str):
        return None
    digest = item.get("digest")
    hexdigest = None
    if isinstance(digest, str) and digest.lower().startswith("sha256:") and HEX_RE.fullmatch(digest[7:].lower()):
        hexdigest = digest[7:].lower()
    size = item.get("size")
    return Asset(item["name"], item["url"], size if type(size) is int and size >= 0 else None, hexdigest)


def latest_release(http: httpx.Client, repo: str, token: str, *, expect_id: str | None = None) -> Release:
    """The latest release of `repo` and its ``<id>-<version>.tar.gz`` asset."""
    url = f"{api_base()}/repos/{repo}/releases/latest"
    with closing(_get(http, url, token, "application/vnd.github+json")) as response:
        if response.status_code != 200:
            raise _refusal(response.status_code, response, repo)
        body = _read(response, MAX_RELEASE_BYTES)
    try:
        release = json.loads(body)
    except ValueError:
        raise GitHubError("GitHub's release answer was not readable; try again later.") from None
    if not isinstance(release, dict) or not isinstance(release.get("tag_name"), str):
        raise GitHubError("GitHub's release answer was not readable; try again later.")
    tag = release["tag_name"]
    version = tag[1:] if tag[:1] in ("v", "V") else tag
    if not VERSION_RE.fullmatch(version):
        raise GitHubError(f"The latest release of {repo} is tagged {tag!r}, which is not a version like v1.2.3")
    assets = [asset for asset in map(_asset, release.get("assets") or []) if asset is not None]
    suffix = f"-{version}.tar.gz"
    if expect_id is not None:
        matches = [asset for asset in assets if asset.name == f"{expect_id}{suffix}"]
        wanted = f"{expect_id}{suffix}"
    else:
        matches = [asset for asset in assets
                   if asset.name.endswith(suffix) and ID_RE.fullmatch(asset.name[:-len(suffix)])]
        wanted = f"<id>{suffix}"
    if len(matches) != 1:
        raise GitHubError(f"The latest release of {repo} ({tag}) must have exactly one module file named {wanted}")
    asset = matches[0]
    checksum = next((item for item in assets if item.name == asset.name + ".sha256"), None)
    return Release(repo, tag, version, asset.name[:-len(suffix)], asset, checksum)


def download(http: httpx.Client, asset: Asset, token: str, target: Path, *, limit: int = MAX_ASSET_BYTES) -> str:
    """Stream `asset` into `target`; returns its SHA-256 (lower-case hex)."""
    if asset.size is not None and asset.size > limit:
        raise GitHubError(f"{asset.name} is too large to install")
    digest = hashlib.sha256()
    written = 0
    with closing(_get(http, asset.url, token, "application/octet-stream")) as response:
        if response.status_code != 200:
            raise GitHubError(f"GitHub answered {response.status_code} while downloading {asset.name}; try again later.")
        with target.open("wb") as handle:
            for chunk in response.iter_bytes():
                written += len(chunk)
                if written > limit:
                    raise GitHubError(f"{asset.name} is larger than expected; nothing was installed")
                digest.update(chunk)
                handle.write(chunk)
    return digest.hexdigest()


def published_checksum(http: httpx.Client, release: Release, token: str) -> str:
    """The SHA-256 the release publishes for its module file, or GitHubError when it has none or two disagree."""
    found = []
    if release.asset.digest:
        found.append(release.asset.digest)
    if release.checksum is not None:
        with closing(_get(http, release.checksum.url, token, "application/octet-stream")) as response:
            if response.status_code != 200:
                raise GitHubError(f"GitHub answered {response.status_code} for {release.checksum.name}; try again later.")
            text = _read(response, MAX_CHECKSUM_BYTES).decode("ascii", "replace").strip().split()
        if not text or not HEX_RE.fullmatch(text[0].lower()):
            raise GitHubError(f"{release.checksum.name} does not contain a SHA-256 checksum; nothing was installed")
        found.append(text[0].lower())
    if not found:
        raise GitHubError(f"The release has no SHA-256 checksum for {release.asset.name}, so rika! cannot check "
                          "the download. Ask the module's author to publish one.")
    if len(set(found)) != 1:
        raise GitHubError(f"The release's checksum file disagrees with GitHub's checksum for {release.asset.name}; "
                          "nothing was installed")
    return found[0]
