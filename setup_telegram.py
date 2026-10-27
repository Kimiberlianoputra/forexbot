#!/usr/bin/env python3
"""
Interactive Telegram setup helper.

Walks you through creating a bot and finding your chat ID, then writes
the values to .env (preserving any existing entries).

Usage:
    python setup_telegram.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import requests

ENV_PATH = Path(__file__).resolve().parent / ".env"
ENV_TEMPLATE = Path(__file__).resolve().parent / ".env.example"

def _load_env_lines() -> list[str]:
    if ENV_PATH.exists():
        return ENV_PATH.read_text().splitlines()
    if ENV_TEMPLATE.exists():
        return ENV_TEMPLATE.read_text().splitlines()
    return []

def _set_env_value(lines: list[str], key: str, value: str) -> list[str]:
    """Replace or append KEY=value in the .env line list."""
    pattern = re.compile(rf"^{re.escape(key)}=")
    replaced = False
    out: list[str] = []
    for line in lines:
        if pattern.match(line):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return out

def _write_env(lines: list[str]) -> None:
    ENV_PATH.write_text("\n".join(lines) + "\n")

def _print_banner() -> None:
    print()
    print("=" * 62)
    print("  Telegram Bot Setup Helper")
    print("=" * 62)
    print()

def _step_create_bot() -> str:
    print("STEP 1 — Create your bot")
    print("-" * 62)
    print("  1. Open Telegram and search for @BotFather")
    print("  2. Start a chat and send: /newbot")
    print("  3. Give the bot a name (e.g. 'Gold Signal Bot')")
    print("  4. Give it a username ending in 'bot' (e.g. 'my_gold_bot')")
    print("  5. BotFather replies with a token like:")
    print("     123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    print()
    token = input("Paste your bot token here: ").strip()
    if not token or ":" not in token:
        print("That doesn't look like a valid token (must contain ':').")
        sys.exit(1)
    return token

def _step_get_chat_id(token: str) -> str:
    print()
    print("STEP 2 — Get your chat ID")
    print("-" * 62)
    print("  1. Open a chat with YOUR new bot in Telegram")
    print("  2. Send it any message (e.g. 'hello')")
    print("  3. Press Enter here when you've done that.")
    print()
    input("Press Enter to fetch your chat ID… ")

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        resp = requests.get(url, timeout=15)
        data = resp.json()
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        sys.exit(1)

    if not data.get("ok"):
        print(f"Telegram error: {data}")
        sys.exit(1)

    results = data.get("result", [])
    if not results:
        print()
        print("No messages found. Make sure you sent a message to the bot first,")
        print("then re-run this script.")
        sys.exit(1)

    # Take the most recent chat
    chat = results[-1].get("message", {}).get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        print("Could not find a chat ID in the response.")
        sys.exit(1)

    print(f"Found chat ID: {chat_id}  (name: {chat.get('first_name', '?')})")
    return str(chat_id)

def _step_verify(token: str, chat_id: str) -> None:
    print()
    print("STEP 3 — Verify")
    print("-" * 62)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "✅ XAU/USD Signal Bot — Telegram is wired up correctly.",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        sys.exit(1)

    if data.get("ok"):
        print("Test message sent — check your Telegram.")
    else:
        print(f"Telegram error: {data}")
        sys.exit(1)

def main() -> int:
    _print_banner()

    lines = _load_env_lines()

    token = _step_create_bot()
    lines = _set_env_value(lines, "TELEGRAM_BOT_TOKEN", token)

    chat_id = _step_get_chat_id(token)
    lines = _set_env_value(lines, "TELEGRAM_CHAT_ID", chat_id)

    _write_env(lines)

    _step_verify(token, chat_id)

    print()
    print("=" * 62)
    print(f"Done! Wrote TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to {ENV_PATH}")
    print("=" * 62)
    print()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
