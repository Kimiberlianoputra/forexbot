#!/usr/bin/env bash
#
# XAU/USD Signal Bot — uninstaller.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="xauusd-signal-bot"
TRACKER_SERVICE="xauusd-tracker"
SERVICE_DIR="$HOME/.config/systemd/user"
VENV_DIR="$PROJECT_DIR/venv"

echo "============================================================"
echo "  XAU/USD Signal Bot — Uninstaller"
echo "============================================================"
echo

# Stop and disable services
for s in "$SERVICE_NAME" "$TRACKER_SERVICE"; do
    if systemctl --user list-unit-files 2>/dev/null | grep -q "$s.service"; then
        echo "→ Stopping and disabling $s.service…"
        systemctl --user stop "$s.service" 2>/dev/null || true
        systemctl --user disable "$s.service" 2>/dev/null || true
        rm -f "$SERVICE_DIR/$s.service"
        echo "✓ Removed $s.service"
    fi
done
systemctl --user daemon-reload

# Remove venv
if [ -d "$VENV_DIR" ]; then
    echo "→ Removing virtual environment…"
    rm -rf "$VENV_DIR"
    echo "✓ venv removed"
fi

echo
echo "Note: .env was NOT removed (it contains your API keys)."
echo "      Delete it manually if you want:  rm $PROJECT_DIR/.env"
echo
