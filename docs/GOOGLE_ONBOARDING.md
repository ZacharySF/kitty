# Optional Google connection and first-run guide

rika! works locally without an account. The first-run welcome page offers
**Continue with Google** and **Continue locally**. Connecting Google identifies
you in this workspace; it does **not** secure the local API, create a cloud
workspace, sync data, connect Gmail/Calendar/Drive, configure AI or LearnHarness,
or sign in to employer sites. Those integrations remain separate.

The browser website and Tauri desktop app both run rika!'s local, loopback-only
backend. This feature does not introduce a public/hosted website or a hosted auth
service. Do not expose the local backend through a public proxy expecting Google
connection to enforce access control.

## Google Cloud setup (owner action)

No production credentials are included. Do not reuse inference's OAuth client or
copy its environment. Use your own Google Cloud project:

1. Open [Google Auth Platform](https://console.cloud.google.com/auth/overview).
   Select or create the project intended for rika!.
2. In **Branding**, set the app name, support email and developer contact details.
   Follow Google's requirements for your chosen audience and publication status.
3. In **Audience**, choose the appropriate user type. For an external app in
   **Testing**, add each account that will test rika! to **Test users**. An
   Internal app is restricted to the selected Google Workspace organization.
4. In **Data Access**, request only the standard identity scopes:
   `openid`, `email`, `profile`. Do not add Gmail, Drive or Calendar permissions.
5. In **Clients → Create client**, choose **Desktop app**, give it a recognizable
   name such as `kitty local`, and create it. Copy the **client ID** and the
   **client secret** shown for that client. Google's token endpoint rejects a
   Desktop client's code exchange without its secret (`client_secret is
   missing`), even with PKCE. For installed apps Google does not treat this
   secret as confidential, but keep it out of the repository anyway. Never use a
   **Web application** client or its secret in kitty.
6. Put the following in the selected workspace's private `.env`, or set them in
   the environment that starts rika!'s backend:

   ```dotenv
   KITTY_GOOGLE_CLIENT_ID=your-desktop-client-id.apps.googleusercontent.com
   KITTY_GOOGLE_CLIENT_SECRET=your-desktop-client-secret
   ```

   rika! offers **Continue with Google** only when both are set. The secret is
   sent only in the backend's token request, never in the authorization URL,
   the web page or logs.

   For the packaged app, the workspace is the directory selected with
   `--workspace`, `KITTY_HOME`, or the default desktop workspace—not necessarily
   the source checkout. Never commit this private `.env`. `.env.example` contains
   only empty examples. No JSON credential download needs to be committed.
7. Restart **your own** rika! instance to load the new configuration. Do not
   restart another running instance or change its workspace. Open
   **Settings → Welcome & account → Connect Google**.
8. Choose **Continue with Google**, then **Open Google sign-in**. This explicit
   second link avoids popup blockers and opens the system browser in Tauri,
   never an embedded Google login. Complete consent/challenges yourself. Return
   to rika! when the callback page says sign-in finished. rika! checks the
   result and displays the verified email, or a readable error.

The backend binds a dedicated callback listener directly to a random available
`127.0.0.1` port before constructing the authorization URL. Its redirect is
`http://127.0.0.1:<actual-port>/callback`. Do not try to register one fixed Tauri
backend port or use an out-of-band copy/paste code. Desktop OAuth clients support
loopback redirects. A firewall must allow the browser on the same machine to
reach that listener; a remote browser on another machine cannot use it.

If Google reports `redirect_uri_mismatch`, confirm the client really is a
**Desktop app** client. If it reports `org_internal`, review Audience. If access
is refused while Testing, check the configured test-user list. App publishing,
branding verification and Google account consent belong to the owner; rika!
never provisions them automatically.

## Connection lifetime and security boundary

- Authorization-code flow with per-attempt cryptographic state, S256 PKCE and an
  OpenID nonce. Each attempt expires after five minutes; cancellation or a new
  attempt invalidates the previous completion ticket.
- The separate loopback callback validates the exact Host, path and state.
  Existing API Host/Origin/custom-header checks are unchanged.
- The backend exchanges the code directly with Google's HTTPS token endpoint,
  then verifies Google's ID-token signature, issuer, audience and timestamps
  with `google-auth`, plus nonce, verified email and stable `sub`.
- Only the initiating rika! page holds the separate, one-use completion ticket.
  The system-browser callback does not gain access to workspace data or create
  a Google session in the Tauri webview.
- Google access/refresh/ID tokens, authorization codes, nonce and PKCE verifier
  are not persisted. No offline Google access is requested. Codes and provider
  errors are excluded from access logs and user-facing diagnostics.
- The workspace retains only the verified Google subject, email and display
  name in local SQLite. The identity label survives restarting rika! until
  **Disconnect Google** clears it. It is a saved identity connection, not proof
  of a fresh Google login on every launch and not a local API login session.
- **Disconnect Google** clears the identity and pending attempt without deleting
  notes, resumes, application history, module choices or tutorial progress.
  Revoking a Google grant, if desired, is a separate owner action in Google
  Account → Third-party connections.

## Onboarding and tutorial

The transparent Harness-style welcome uses the existing Kitty theme, controls
and typography. After the optional connection step, **Show me around** starts a
highlighted guide over the actual application:

1. Workspace tabs and focused panels.
2. Modules in Settings, including restart-only activation and preserved data.
3. Atlas map, section list and navigation/search shortcuts.
4. Quick notes, or an explanation of how to enable their module.
5. Study, or an explanation of how to enable its module.
6. Terminal dock only when its module is active **and a visible dock surface is
   present** (`[data-kitty-dock]` or `.terminal-dock`). This branch does not add
   the separately developed dock or start Herdr, a shell, a study timer or AI.

Every step has Back/Next and a skip control. Escape skips the guide. Finishing
or skipping records dismissal in the workspace database, so an ephemeral desktop
port, browser reload or restart does not repeat onboarding. Closing mid-tour
before dismissal starts the tour again next time. Reopen it at any time with
**Settings → Welcome & account → Take the guided tour**. Reopening a completed
tour does not reset its stored completion flag. Google is never required to
finish, skip or reopen onboarding. No applicant profile facts are inferred from
the Google identity or changed by this guide.

## Verification boundaries

Behavioral tests use synthetic accounts, real loopback HTTP callbacks and locally
signed test ID tokens with isolated certificate responses. They cover PKCE,
state/nonce, ID-token issuer/audience/time/verified-email validation, cancellation,
expiry, one-use completion, identity persistence/disconnection and tutorial
persistence. UI verification uses an isolated workspace, not private user data.
A successful synthetic flow is **not** a claim that live Google consent passed.
Live Google verification requires the owner-created Desktop client ID and secret,
an eligible test account and owner-completed consent/challenges.

References:
- [Google desktop OAuth: system browser, PKCE and loopback redirects](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Google OpenID Connect: identity claims and verification](https://developers.google.com/identity/protocols/oauth2/openid-connect)
- [Python Google ID-token verification](https://googleapis.dev/python/google-auth/latest/reference/google.oauth2.id_token.html)

The inference reference's branded authentication composition, explicit
availability/error states and first-run guidance informed this UI. Its Better
Auth/PostgreSQL organization authorization was not copied into rika!'s local
single-workspace backend.
