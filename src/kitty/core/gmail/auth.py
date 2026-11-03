"""Gmail OAuth: local tokens with automatic refresh, read-only unless opted in.

The client secret (a "Desktop app" OAuth client I create in Google Cloud
Console) lives in config/gmail_client.json; tokens live under data/; all are
gitignored. Two tokens, never mixed:

* data/gmail_token.json (`kitty gmail auth`): gmail.readonly only. Job-status
  sync (`kitty gmail check`, Insights) uses only this token, so it can read
  mail but never send, draft, delete or label it.
* data/gmail_compose_token.json (`kitty gmail auth --compose`, or Connect in
  Settings → Rika → Email): opt-in, for Rika's email drafts (design Q16), with
  gmail.readonly plus gmail.compose, which lets Rika create, update, delete and
  send drafts; Rika sends only a reply you tapped Send on or a kind you turned
  auto-send on for. Nothing else uses it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from kitty.settings import Settings

#: The job-status token's one scope. Never widen it.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
#: Rika's separate opt-in token: read the inbox and manage drafts (create, update,
#: delete, send). Only kitty.core.rika.mail uses it.
COMPOSE_SCOPES = [*SCOPES, "https://www.googleapis.com/auth/gmail.compose"]

SETUP_STEPS = """\
Gmail is not set up. One-time setup (about 5 minutes):
  1. Open https://console.cloud.google.com/ and create a project (e.g. "kitty").
  2. APIs & Services > Library: search "Gmail API" and click Enable.
  3. APIs & Services > OAuth consent screen: choose External, fill in the app
     name and your email, then under Audience > Test users add your own Gmail
     address. Leave the app in Testing.
  4. APIs & Services > Credentials > Create credentials > OAuth client ID,
     application type "Desktop app". Download the JSON.
  5. Save it as config/gmail_client.json (gitignored).
  6. Run `kitty gmail auth`: a browser opens; sign in and allow read-only
     access. The token is saved to data/gmail_token.json (gitignored).
While the app stays in Testing, Google expires the token after 7 days; run
`kitty gmail auth` again when a check says the token expired."""


class GmailSetupError(Exception):
    """Gmail cannot be used yet: no client file, no token, or a dead token.

    The message is meant to be shown to me as-is.
    """


@dataclass(frozen=True)
class GmailSetup:
    """What exists on disk, for `kitty gmail status`."""

    client_path: Path
    token_path: Path
    client_present: bool
    token_present: bool

    @property
    def ready(self) -> bool:
        return self.client_present and self.token_present


def client_path(settings: Settings) -> Path:
    """The OAuth client secret I download from Google Cloud Console."""
    return settings.config_dir / "gmail_client.json"


def token_path(settings: Settings, *, compose: bool = False) -> Path:
    """Where the access/refresh token is stored (under data/, so gitignored)."""
    return settings.data_dir / ("gmail_compose_token.json" if compose else "gmail_token.json")


def _command(compose: bool) -> str:
    return "kitty gmail auth --compose" if compose else "kitty gmail auth"


def setup_state(settings: Settings, *, compose: bool = False) -> GmailSetup:
    """Which of the two files exist (the compose token's when `compose`)."""
    client, token = client_path(settings), token_path(settings, compose=compose)
    return GmailSetup(client, token, client.exists(), token.exists())


def _save_token(credentials: Credentials, path: Path) -> None:
    """Write the token readable by me only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(credentials.to_json(), encoding="utf-8")
    os.chmod(path, 0o600)


def run_consent_flow(settings: Settings, *, compose: bool = False) -> Path:
    """Open the browser consent page and save the resulting token.

    `compose` asks for Rika's separate drafting token instead of the read-only
    one. Raises GmailSetupError (with the setup steps) when the client file is
    missing or is not a Desktop-app client.
    """
    client = client_path(settings)
    if not client.exists():
        raise GmailSetupError(f"{SETUP_STEPS}\n\nMissing: {client}")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(client), scopes=COMPOSE_SCOPES if compose else SCOPES)
    except ValueError as exc:
        raise GmailSetupError(
            f"{client} is not a Desktop-app OAuth client ({exc}). "
            "Create one with application type 'Desktop app' and download it again."
        ) from exc
    credentials = flow.run_local_server(port=0, open_browser=True)
    path = token_path(settings, compose=compose)
    _save_token(credentials, path)
    return path


def load_credentials(settings: Settings, *, compose: bool = False) -> Credentials:
    """The saved token, refreshed (and re-saved) when it has expired."""
    state = setup_state(settings, compose=compose)
    command = _command(compose)
    if not state.client_present:
        raise GmailSetupError(f"{SETUP_STEPS}\n\nMissing: {state.client_path}")
    if not state.token_present:
        raise GmailSetupError(f"Gmail is not authorised yet: run `{command}`.")
    try:
        credentials = Credentials.from_authorized_user_file(str(state.token_path), COMPOSE_SCOPES if compose else SCOPES)
    except (ValueError, OSError) as exc:
        raise GmailSetupError(
            f"Could not read {state.token_path} ({exc}): run `{command}` again."
        ) from exc
    if not credentials.valid:
        if not credentials.refresh_token:
            raise GmailSetupError(f"The Gmail token cannot be refreshed: run `{command}` again.")
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            raise GmailSetupError(
                f"The Gmail token expired or was revoked ({exc}): run `{command}` again."
            ) from exc
        except GoogleAuthError as exc:
            raise GmailSetupError(f"Could not refresh the Gmail token: {exc}") from exc
        _save_token(credentials, state.token_path)
    return credentials


def authorized_session(settings: Settings, *, compose: bool = False) -> AuthorizedSession:
    """An HTTP session that sends the token and refreshes it when needed."""
    return AuthorizedSession(load_credentials(settings, compose=compose))
