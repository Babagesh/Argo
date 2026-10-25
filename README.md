# Argo (Scrummy)

Argo turns meeting summaries into Jira actions. The UI lives inside Jira as a Forge app.

- `forge-app/` – Forge app (UI Kit frontend in `src/frontend`, resolvers in `src/index.ts`)
- `agent-server/` – Python listener: Cartesia speech-to-text -> Claude -> Jira, saving each run as a meeting
- `spacetimedb/` – SpacetimeDB module: meetings database plus the REST API (HTTP handlers)

## How a meeting is recorded
Starting `cartesia_stream.py` creates a new meeting (status `live`). While it runs, every finished speech
turn, every "Hey Argo" command and every Jira operation is saved under that meeting. Stopping it (Ctrl+C)
summarizes the transcript and marks the meeting `complete`. The Forge page reads it back.

## Set up the meetings database
Needs the SpacetimeDB CLI (https://spacetimedb.com/install) and `spacetime login`.

```
cd spacetimedb && npm install
API_KEY=<secret> spacetime publish <db-name>      # <secret>: e.g. `openssl rand -hex 32`
```
The API is then at `https://<host>/v1/database/<db-name>/route` (`/meetings`, `/meeting?id=`, ...).
Use the same secret in three places:
- `agent-server/.env`: `ARGO_API_BASE=<that route URL>`, `ARGO_API_KEY=<secret>`
- Forge: `forge variables set -e development ARGO_API_BASE <url>` and
  `forge variables set -e development --encrypt ARGO_API_KEY <secret>`, then `forge deploy -e development`

Without `ARGO_API_BASE`/`ARGO_API_KEY` the listener still works; it just doesn't save anything.

## Run the listener
```
cd agent-server && pip install -r requirements.txt && cp .env.example .env   # fill in keys
python cartesia_stream.py --device blackhole --title "Sprint planning"
```
`JIRA_DRY_RUN` defaults to on for voice commands; set `JIRA_DRY_RUN=0` to make real Jira changes.

## Run the Forge app
Requires Node 20 or 22 and the Forge CLI: `npm i -g @forge/cli`.

```
forge login
cd forge-app && npm install
forge deploy                                  # upload your code
forge install --site <your>.atlassian.net --product jira   # first time only
forge tunnel                                  # live-reload while developing
```
Re-run `forge install --upgrade` after changing scopes or modules in `manifest.yml`.
