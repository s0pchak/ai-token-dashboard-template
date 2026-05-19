<img width="575" height="95" alt="Screenshot 2026-05-19 at 3 23 28 PM" src="https://github.com/user-attachments/assets/f63a178b-3bbe-4190-93fa-2ae05200cbb5" />


# The Maxx Report

Local telemetry for extremely normal AI usage.

Count tokens. Question choices.

Are you even token maxxing, bro?

The Maxx Report is a tiny command center for watching your AI coding assistants inhale context like the meter is broken. It reads local Codex and Claude Code transcript logs, turns the evidence into `data/usage.json` and `data/usage.js`, then ships as dumb static files so the dashboard still works while your laptop is asleep.

It is not OpenAI billing truth. It is not Anthropic billing truth. It is a mirror held up to your choices.

This repo is the forkable starter pack. Bring your own logs, your own Render URL, and a willingness to see the receipt.

## What It Shows

- Total tokens, because one number should be allowed to hurt.
- Stacked daily token bars by model, for the model custody battle.
- Session length by day, measured from first counted token to last counted token.
- Tokens and call counts by model/provider.
- Hover details for the exact day you are emotionally processing.

## Privacy, Obviously

This repository serves whatever is committed under `data/`. Public fork with real data equals public confession.

- A private live repo can hold your real generated `data/usage.*` bundle.
- A public template repo should ship only sample or sanitized data unless you are doing performance art.
- Static hosting serves the committed `data/usage.json` and `data/usage.js` files.
- Render and other static-site builds do not rummage through your laptop.
- Running the updater locally is the only step that reads local Codex or Claude Code logs.

If you make your fork public, inspect `data/usage.json` before pushing. The dashboard cannot save you from GitHub.

## Quickstart

1. Create your own copy from the GitHub template or fork. This is the socially acceptable part.
2. Clone your copy locally.

   ```bash
   git clone git@github.com:YOUR_USER/the-maxx-report.git
   cd the-maxx-report
   ```

3. Generate your dashboard data. Count tokens. Question choices.

   ```bash
   tools/update_dashboard.sh
   ```

4. Commit and push the generated bundle if the updater did not do it for you.

   ```bash
   git add data/usage.json data/usage.js
   git commit -m "Update token dashboard data"
   git push
   ```

5. Create a Render static site from your repo. The included `render.yaml` keeps the site static and serves the committed bundle, no daemon, no server, no laptop hostage situation.

## Make It Yours With An Agent

This is the part where you burn a few more tokens to make the token dashboard about burning tokens. Civilization may not recover, but at least the bars will line up.

After cloning your fork, give your coding agent a prompt like:

```text
Make this dashboard feel like my personal AI usage command center.
Keep the existing data importer and update workflow intact.
Rename the project, adjust the hero copy, tune the colors, and make the graph
and model breakdown look polished enough to send to friends as evidence.
Do not publish private usage data. Keep sample data in public repos.
Run the existing tests and a browser smoke before committing.
```

Good customization targets:

- `index.html` for product name and hero copy.
- `styles.css` for visual treatment.
- `app.js` for labels, chart annotations, and tooltip copy.
- `README.md` for your fork's setup instructions.

Avoid changing `tools/refresh_token_data.py` unless you are adding a real provider or fixing importer behavior.

## Install The Shortcut

Install or update the local `maxxreport` command from this checkout, because typing long paths is not token maxxing:

```bash
tools/install_dashboard_shortcut.sh
```

The installer creates `~/.local/bin/maxxreport`, pointing at this checkout's `tools/update_dashboard.sh`.

Make sure `~/.local/bin` is on your `PATH`, then refresh and publish the report from anywhere:

```bash
maxxreport
maxxreport --no-push
maxxreport --no-commit
```

Re-run `tools/install_dashboard_shortcut.sh` after moving the checkout.

## Updating Data

Run from the dashboard checkout when the numbers need fresh shame:

```bash
tools/update_dashboard.sh
```

The command regenerates `data/usage.json` and `data/usage.js`, validates the importer syntax, commits only those bundled data files when this directory is a Git worktree, and pushes the current branch to the configured remote.

Useful options:

```bash
tools/update_dashboard.sh --no-commit
tools/update_dashboard.sh --no-push
tools/update_dashboard.sh --remote origin
tools/update_dashboard.sh --message "Update dashboard data"
```

If the directory is not a Git worktree, or the configured remote does not exist, the command refreshes local files and reports that publishing was skipped.

## Configuration

The importer/update tooling can be configured with environment variables:

- `DASHBOARD_CODEX_DIRS`: comma-separated Codex transcript directories to scan. Defaults should cover the normal local Codex session locations.
- `DASHBOARD_CLAUDE_PROJECTS_DIR`: Claude Code projects directory to scan. Use this when Claude Code data lives outside the default location.
- `DASHBOARD_TIMEZONE`: IANA timezone used for daily grouping, such as `America/New_York`.
- `DASHBOARD_OWNER_HANDLE`: GitHub handle shown beside the big token total. Defaults to the owner from `origin` when it points at GitHub.
- `DASHBOARD_REMOTE`: Git remote used by the updater when pushing. Defaults to `origin`.
- `DASHBOARD_COMMIT_MESSAGE`: commit message used for generated data updates.

Example:

```bash
DASHBOARD_TIMEZONE=America/Los_Angeles maxxreport --no-push
DASHBOARD_OWNER_HANDLE=your-handle maxxreport --no-push
```

## Render Hosting

`render.yaml` is configured as a Render static site. Its build command checks that `data/usage.json` and `data/usage.js` are present, then Render serves the repository root as static files. The cloud does not calculate your sins. It only displays the ones you committed.

To deploy:

1. Push a repo that contains committed `data/usage.json` and `data/usage.js`.
2. In Render, create a new Blueprint or static site from that repo.
3. Keep the service private if the committed data is private to you.

The cloud build does not run the local importer and does not read `~/.codex`, Claude Code project logs, or any other local machine files.

## Local Viewing

Open `index.html` directly in a browser, or serve the directory with any static file server.

```bash
python3 -m http.server 8768 --bind 127.0.0.1
```

Then open `http://127.0.0.1:8768/`.

## Publishing A Template

Before publishing a template repo:

- Replace real `data/usage.json` and `data/usage.js` with sample or sanitized data.
- Remove personal live-site links from documentation.
- Keep `render.yaml` fork-neutral so each user connects their own repo in Render.
- Make the description sound like The Maxx Report, not like a printer driver.

## Scope

This dashboard summarizes local transcript usage events. It is useful for personal trend tracking, model mix review, and rough assistant-usage visibility. It is not account billing truth. It is personal telemetry for people who looked at their token graph and whispered, "normal."
