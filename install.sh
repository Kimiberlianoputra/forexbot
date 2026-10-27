#!/usr/bin/env bash
#
# XAU/USD Signal Bot — one-shot installer for Fedora (and other systemd distros).
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="xauusd-signal-bot"
TRACKER_SERVICE="xauusd-tracker"
SERVICE_DIR="$HOME/.config/systemd/user"
VENV_DIR="$PROJECT_DIR/venv"

echo "============================================================"
echo "  XAU/USD Signal Bot — Installer"
echo "============================================================"
echo "  Project: $PROJECT_DIR"
echo

# ── 1. Python ────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "→ Python 3 not found. Installing via dnf…"
    sudo dnf install -y python3 python3-pip python3-devel
else
    echo "✓ Python 3 found: $(python3 --version)"
fi

# ── 2. venv ──────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "→ Creating virtual environment…"
    python3 -m venv "$VENV_DIR"
else
    echo "✓ Virtual environment already exists"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ── 3. Dependencies ──────────────────────────────────────────
echo "→ Installing Python dependencies…"
pip install --upgrade pip >/dev/null
pip install -r "$PROJECT_DIR/requirements.txt"

# ── 4. .env ──────────────────────────────────────────────────
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "→ Creating .env from .env.example"
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "  ⚠  Edit $PROJECT_DIR/.env with your API keys before starting."
else
    echo "✓ .env already exists (not overwriting)"
fi

# ── 5. systemd units ──────────────────────────────────────────
echo "→ Installing systemd user services for project directory: $PROJECT_DIR"
mkdir -p "$SERVICE_DIR"

# Substitute exact project path into both units
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/systemd/$SERVICE_NAME.service" \
    > "$SERVICE_DIR/$SERVICE_NAME.service"
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/systemd/$TRACKER_SERVICE.service" \
    > "$SERVICE_DIR/$TRACKER_SERVICE.service"

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME.service" >/dev/null
systemctl --user enable "$TRACKER_SERVICE.service" >/dev/null
echo "✓ Installed and enabled $SERVICE_NAME and $TRACKER_SERVICE"

# ── 6. Linger (start on boot, survive logout) ────────────────
if ! loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
    echo "→ Enabling linger (service will start on boot and survive logout)…"
    if sudo -n loginctl enable-linger "$USER" 2>/dev/null; then
        echo "✓ Linger enabled"
    elif loginctl enable-linger "$USER" 2>/dev/null; then
        echo "✓ Linger enabled"
    else
        echo "  ⚠  Could not enable linger automatically."
        echo "     Run this manually:  loginctl enable-linger $USER"
    fi
else
    echo "✓ Linger already enabled"
fi

echo
echo "============================================================"
echo "  Installation complete"
echo "============================================================"
echo
echo "Next steps:"
echo "  1. Edit your API keys:   nano $PROJECT_DIR/.env"
echo "  2. (Optional) Telegram:  python $PROJECT_DIR/setup_telegram.py"
echo "  3. Test the bot:         cd $PROJECT_DIR && source venv/bin/activate && python main.py --test"
echo "  4. Start bot service:    systemctl --user start $SERVICE_NAME"
echo "  5. Start tracker:        systemctl --user start $TRACKER_SERVICE"
echo "  6. Watch live logs:      journalctl --user -u $SERVICE_NAME -f"
echo
