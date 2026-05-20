#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DATA_PATHS=("data/usage.json" "data/usage.js")
REMOTE_NAME="${DASHBOARD_REMOTE:-origin}"
COMMIT_MESSAGE="${DASHBOARD_COMMIT_MESSAGE:-Update token dashboard data}"
DO_COMMIT=1
DO_PUSH=1

usage() {
  cat <<'USAGE'
Usage: tools/update_dashboard.sh [options]

Regenerates the static dashboard data bundle from local Codex and Claude Code logs.
When this directory is a Git worktree with a configured remote, the command
commits only data/usage.json and data/usage.js, then pushes the current branch.

Options:
  --no-commit        Refresh data files only; do not commit or push.
  --no-push          Commit data changes, but do not push.
  --remote NAME      Git remote to push to. Defaults to origin or $DASHBOARD_REMOTE.
  --message TEXT     Commit message. Defaults to "Update token dashboard data".
  -h, --help         Show this help text.
USAGE
}

die() {
  echo "error: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-commit)
      DO_COMMIT=0
      DO_PUSH=0
      shift
      ;;
    --no-push)
      DO_PUSH=0
      shift
      ;;
    --remote)
      [[ $# -ge 2 ]] || die "--remote requires a value"
      REMOTE_NAME="$2"
      shift 2
      ;;
    --message)
      [[ $# -ge 2 ]] || die "--message requires a value"
      COMMIT_MESSAGE="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

command -v uv >/dev/null 2>&1 || die "uv is required. Install from https://docs.astral.sh/uv/"

echo "Refreshing bundled dashboard data from local Codex, Claude Code, and OpenCode logs..."
uv run --quiet python -m py_compile tools/refresh_token_data.py
uv run --quiet python tools/refresh_token_data.py

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Dashboard data refreshed. This directory is not a Git worktree, so commit/push was skipped."
  exit 0
fi

if [[ "$DO_COMMIT" -eq 0 ]]; then
  echo "Dashboard data refreshed. Commit/push skipped because --no-commit was set."
  exit 0
fi

if [[ "$DO_PUSH" -eq 1 ]] && ! git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  echo "Dashboard data refreshed. No '$REMOTE_NAME' remote is configured, so commit/push was skipped."
  echo "The refreshed data files are left in the working tree."
  exit 0
fi

# data/usage.json and data/usage.js are gitignored so they never commit by
# accident. This is an explicit publish, so force-add them.
git add -f -- "${DATA_PATHS[@]}"

if git diff --cached --quiet -- "${DATA_PATHS[@]}"; then
  echo "Dashboard data refreshed. No bundled data changes to commit."
  exit 0
fi

git commit -m "$COMMIT_MESSAGE" -- "${DATA_PATHS[@]}"

if [[ "$DO_PUSH" -eq 0 ]]; then
  echo "Committed bundled data changes. Push skipped because --no-push was set."
  exit 0
fi

CURRENT_BRANCH="$(git branch --show-current)"
if [[ -z "$CURRENT_BRANCH" ]]; then
  echo "Committed bundled data changes. Push skipped because HEAD is detached."
else
  git push "$REMOTE_NAME" "HEAD:$CURRENT_BRANCH"
  echo "Pushed bundled data changes to $REMOTE_NAME/$CURRENT_BRANCH."
fi
