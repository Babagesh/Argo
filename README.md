# Argo (Scrummy)

Argo turns meeting summaries into Jira actions. The UI lives inside Jira as a Forge app.

- `forge-app/` – Forge app (UI Kit frontend in `src/frontend`, resolvers in `src/index.ts`)
- `agent-server/` – Python agent backend (stubs)

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
