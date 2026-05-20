# The Maxx Report

Local telemetry for extremely normal AI usage.

Count tokens. Question choices.

Are you even token maxxing, bro?

The Maxx Report is a tiny command center for watching your AI coding assistants inhale context like the meter is broken. It reads local Codex, Claude Code, and OpenCode transcript logs on your machine, turns the evidence into `data/usage.json` and `data/usage.js`, then opens as a dumb HTML page in your browser. OpenCode traffic routed through OpenRouter shows up automatically with the OpenRouter model id (e.g. `moonshotai/kimi-k2.6`).

It is not OpenAI billing truth. It is not Anthropic billing truth. It is a mirror held up to your choices.

Run the importer, open `index.html`, and look at it locally. If you also want to deploy it to a cloud host so you can pull it up from your phone, there are configs for that too.

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
- Render, Netlify, Vercel, Cloudflare Pages, GitHub Pages, Fly, and friends do not rummage through your laptop.
- Running the updater locally is the only step that reads local Codex or Claude Code logs.

If you make your fork public, inspect `data/usage.json` before pushing. The dashboard cannot save you from GitHub.

## Quickstart

1. Create your own copy from the GitHub template or fork. This is the socially acceptable part.
2. Clone your copy locally.

   ```bash
   git clone git@github.com:YOUR_USER/the-maxx-report.git
   cd the-maxx-report
   ```

3. Install [uv](https://docs.astral.sh/uv/) — it's the only Python runner this repo uses.

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   The repo pins Python via `.python-version` and `pyproject.toml`, so `uv` will fetch the right interpreter automatically. The importer itself has no third-party dependencies.

4. Generate your dashboard data. Count tokens. Question choices.

   ```bash
   tools/update_dashboard.sh --no-commit --no-push
   ```

   The updater shells out to `uv run`. Drop `--no-commit --no-push` once you are ready to publish a bundle.

5. View it locally.

   ```bash
   uv run python -m http.server 8768 --bind 127.0.0.1
   ```

   Then open `http://127.0.0.1:8768/`. You can also just double-click `index.html`; everything is relative paths.

6. (Optional) Deploy to a cloud host. If you want a URL you can hit from your phone, see [Hosting](#hosting).

## Have An Agent Finish The Fork

This is the part where you burn a few more tokens so your agent can wire up the fork, run the importer, and get the thing rendering in your browser without turning the README into a treasure map.

After cloning your fork, give your coding agent a prompt like:

```text
I forked The Maxx Report and want it running with my own local AI usage data.
Read the README, install uv if it's not already there, install the maxxreport
shortcut, run the importer locally, and open the dashboard in my browser.

If I also ask to deploy it, pick one of the included host configs (render.yaml,
netlify.toml, vercel.json, .github/workflows/pages.yml, or fly.toml + Dockerfile)
and walk me through it. Otherwise leave hosting alone.

Keep the data importer and update workflow intact. Do not publish private usage
data to a public repo. If this repo is public, use sample or sanitized data.
Set DASHBOARD_OWNER_HANDLE if my GitHub handle is not inferred correctly.
Run the existing tests and a local browser smoke before committing.
```

Good setup targets:

- `tools/update_dashboard.sh` for the update cycle.
- `tools/install_dashboard_shortcut.sh` for the `maxxreport` command.
- `data/usage.json` and `data/usage.js` for the generated bundle.
- `render.yaml`, `netlify.toml`, `vercel.json`, `.github/workflows/pages.yml`, or `fly.toml` + `Dockerfile` for static hosting. Pick one; ignore the rest.

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

The command regenerates `data/usage.json` and `data/usage.js`, validates the importer syntax, commits only those bundled data files when this directory is a Git worktree, and pushes the current branch to the configured remote. It runs the importer via `uv run` against the pinned Python from `.python-version`; the importer has no third-party dependencies, so `uv` is just managing the interpreter.

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
- `DASHBOARD_OPENCODE_DB`: path to OpenCode's SQLite database. Defaults to `~/.local/share/opencode/opencode.db`. Read-only — the importer opens it with `mode=ro` so it does not interfere with a running OpenCode session.
- `DASHBOARD_TIMEZONE`: IANA timezone used for daily grouping, such as `America/New_York`.
- `DASHBOARD_OWNER_HANDLE`: GitHub handle shown beside the big token total. Defaults to the owner from `origin` when it points at GitHub.
- `DASHBOARD_REMOTE`: Git remote used by the updater when pushing. Defaults to `origin`.
- `DASHBOARD_COMMIT_MESSAGE`: commit message used for generated data updates.

Example:

```bash
DASHBOARD_TIMEZONE=America/Los_Angeles maxxreport --no-push
DASHBOARD_OWNER_HANDLE=your-handle maxxreport --no-push
```

## Showing Your GitHub Handle

The dashboard writes `ownerHandle` into `data/usage.json` when you run the updater.

- If your `origin` remote is a GitHub repo, the owner is inferred automatically.
- If you want a different handle, run `DASHBOARD_OWNER_HANDLE=your-handle maxxreport`.
- Reload your local page (or commit and push if you bother to host) and `@your-handle` shows up beside the token total.

## Hosting

If you want to deploy the dashboard to a cloud host so you can open it from your phone or share a link, pick one of these.

The Maxx Report is a tiny pile of static files: `index.html`, `app.js`, `styles.css`, and the committed `data/usage.json` + `data/usage.js`. Anywhere that can serve a folder will work. The repo ships configs for the obvious choices so you can pick one and ignore the rest. None of these cloud builds run the local importer or read `~/.codex`, Claude Code project logs, or anything else on your machine — they only serve what you committed.

Whichever host you pick, the first step is the same: push a repo that contains committed `data/usage.json` and `data/usage.js`. Keep the repo private if the committed data is private to you.

### Render (`render.yaml`)

1. In Render, create a new Blueprint (or static site) from your repo.
2. Render reads `render.yaml`, serves the repo root, and redeploys on push to `main`.

### Netlify (`netlify.toml`)

1. In Netlify, "Add new site" → "Import an existing project" → pick your repo.
2. Netlify reads `netlify.toml`. Publish directory is the repo root; the build command just checks the bundle is present.

### Vercel (`vercel.json`)

1. In Vercel, "Add New… → Project" → import your repo.
2. Framework preset: "Other". Vercel reads `vercel.json` and serves the repo root.

### Cloudflare Pages

No config file needed.

1. In Cloudflare Pages, "Create a project" → connect your repo.
2. Build command: leave empty (or `test -f data/usage.json && test -f data/usage.js`).
3. Build output directory: `/` (the repo root).

### GitHub Pages (`.github/workflows/pages.yml`)

1. In your repo, Settings → Pages → Source: "GitHub Actions".
2. Push to `main`. The included workflow uploads the repo root and publishes it.

### Fly.io (`fly.toml` + `Dockerfile`)

Fly hosts containers, not static folders, so the repo ships a 5 MB `nginx:alpine` image that serves the committed bundle on port 8080.

```bash
fly launch --copy-config --no-deploy   # accept the defaults, pick a unique app name
fly deploy
```

`fly launch` will offer to rename the app if `the-maxx-report` is taken; let it. The included `nginx.conf` sets the same cache and `X-Content-Type-Options` headers as the other host configs.

### Anywhere else

Any static file server works. Drop the repo root onto S3 + CloudFront, Surge, a VPS with nginx, or whatever you already pay for. The only requirement is serving `index.html`, `app.js`, `styles.css`, and the `data/` directory with their relative paths intact.

## Publishing A Template

Before publishing a template repo:

- Replace real `data/usage.json` and `data/usage.js` with sample or sanitized data.
- Remove personal live-site links from documentation.
- Keep the host configs (`render.yaml`, `netlify.toml`, `vercel.json`, `.github/workflows/pages.yml`, `fly.toml`, `Dockerfile`, `nginx.conf`) fork-neutral so each user connects their own repo.
- Make the description sound like The Maxx Report, not like a printer driver.

## Scope

This dashboard summarizes local transcript usage events. It is useful for personal trend tracking, model mix review, and rough assistant-usage visibility. It is not account billing truth. It is personal telemetry for people who looked at their token graph and whispered, "normal."
