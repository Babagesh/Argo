# Argo for macOS

Argo is a native macOS app for connecting the voice agent to Cartesia, Claude, and Jira, then starting or stopping it without a terminal.

## Build and open

From the Argo repository, run:

```sh
./desktop-app/build-app.sh
open desktop-app/build/Argo.app
```

The app asks for the Cartesia key, Claude key, Jira site, Atlassian account email, and Jira API token. Credentials are saved locally in `~/Library/Application Support/Argo/credentials.json` with owner-only file permissions, so Argo does not request Keychain access. Projects are loaded from Jira and can be searched and selected from the home screen. Board, dry-run, audio input, session logs, and meeting-history options live under **Dev info**.

The build uses `agent-server/.venv/bin/python` when it exists. Install `agent-server/requirements.txt` into that environment first. The generated app bundle is ignored by Git.

This is currently a local development build. Sharing it with customers will require bundling the Python agent and dependencies, then signing and notarizing the macOS app.

For a customer release, replace the Jira email/API-token fields with Atlassian OAuth 2.0 (3LO) authorization. Atlassian recommends a single distributable 3LO app instead of asking customers to create API tokens. The OAuth client secret and rotating refresh-token exchange belong on a small Argo backend, not in the desktop bundle.
