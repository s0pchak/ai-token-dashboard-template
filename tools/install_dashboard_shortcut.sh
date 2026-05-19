#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/update_dashboard.sh"
BIN_DIR="$HOME/.local/bin"
COMMAND_NAME="${DASHBOARD_COMMAND_NAME:-maxxreport}"
COMMAND_PATH="$BIN_DIR/$COMMAND_NAME"

if [[ ! -f "$TARGET" ]]; then
  echo "error: expected updater at $TARGET" >&2
  exit 1
fi

if [[ ! -x "$TARGET" ]]; then
  echo "error: updater is not executable: $TARGET" >&2
  exit 1
fi

mkdir -p "$BIN_DIR"

{
  printf '#!/usr/bin/env bash\n'
  printf 'exec %q "$@"\n' "$TARGET"
} > "$COMMAND_PATH"

chmod +x "$COMMAND_PATH"

echo "Installed $COMMAND_NAME shortcut at $COMMAND_PATH"
echo "It points to $TARGET"
