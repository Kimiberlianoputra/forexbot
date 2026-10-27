#!/usr/bin/env bash
#
# XAU/USD Signal Bot — one-shot installer for Fedora (and other systemd distros).
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="xauusd-signal-bot"
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

# ── 5. systemd unit ──────────────────────────────────────────
echo "→ Installing systemd user service…"
mkdir -p "$SERVICE_DIR"
sed "s|%h|$HOME|g" "$PROJECT_DIR/systemd/$SERVICE_NAME.service" \
    > "$SERVICE_DIR/$SERVICE_NAME.service"

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME.service" >/dev/null

# ── 6. Linger (start on boot, survive logout) ────────────────
if ! loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
    echo "→ Enabling linger (service will start on boot)…"
    if sudo -n loginctl enable-linger "$USER" 2>/dev/null; then
        echo "✓ Linger enabled"
    else
        echo "  ⚠  Could not enable linger without a password."
        echo "     Run this manually:  sudo loginctl enable-linger $USER"
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
echo "  4. Start the service:    systemctl --user start $SERVICE_NAME"
echo "  5. Watch logs:           journalctl --user -u $SERVICE_NAME -f"
echo
