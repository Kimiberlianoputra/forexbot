# XAU/USD Signal Bot

A Python bot that monitors **XAU/USD (Gold Spot)** on the **1-minute** timeframe and sends a **Telegram alert** when a specific scalping signal fires. Built for **Fedora Linux** and runs as a **systemd user service** (auto-starts on boot, survives logout).

---

## Strategy

| Condition | Direction |
|-----------|-----------|
| Price **above EMA 100** AND RSI(3) **< 20** AND ADX(5) **> 30** AND current bar is **green** with a **normal body size** | **BUY** |
| Price **below EMA 100** AND RSI(3) **> 80** AND ADX(5) **> 30** AND current bar is **red** with a **normal body size** | **SELL** |

**"Normal body size"** = current body (|close − open|) is between **0.5×** and **1.5×** the previous bar's body.

Alerts fire **once per closed bar only** — no repeats while the condition stays true.

---

## Prerequisites

- Fedora (or any systemd-based Linux)
- Python 3.10+
- A free **Twelve Data** API key (for XAU/USD data)
- A **Telegram** account

---

## Step 1 — Get a Twelve Data API key

1. Go to <https://twelvedata.com/register> and create a free account.
2. Open your dashboard → **API Keys**.
3. Copy the key (looks like `abc123...`).

**Free tier limits:** 8 API credits/min, 800/day. The bot uses **WebSocket mode** by default, which consumes only **1 WebSocket credit** (not REST credits). If you switch to REST mode, read the [Data mode](#data-mode) section.

---

## Step 2 — Create your Telegram bot (from scratch)

### 2a. Create the bot

1. Open Telegram and search for **@BotFather**.
2. Start a chat and send `/newbot`.
3. Give it a **name** (e.g. `Gold Signal Bot`).
4. Give it a **username** ending in `bot` (e.g. `my_gold_signal_bot`).
5. BotFather replies with a **token** like `123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`. Copy it.

### 2b. Get your chat ID

1. Open a chat with **your new bot** in Telegram.
2. Send it any message (e.g. `hello`).
3. Run this command (replace `YOUR_TOKEN`):

```bash
curl -s "https://api.telegram.org/botYOUR_TOKEN/getUpdates" | python3 -m json.tool
```

4. Look for `"chat": {"id": 123456789}` — that number is your **chat ID**.

> If the output is empty, make sure you actually sent a message to the bot first, then re-run.

### 2c. Verify the bot can message you

```
curl -s -X POST "https://api.telegram.org/botYOUR_TOKEN/sendMessage" \
  -d "chat_id=YOUR_CHAT_ID" \
  -d "text=Test from my signal bot"
```

You should receive the message in Telegram.

---

## Step 3 — Install the bot

```
cd xauusd-signal-bot
chmod +x install.sh
./install.sh
```

The installer will:

- Install `python3`, `pip`, `python3-devel` if missing
- Create a virtual environment in `venv/`
- Install Python dependencies
- Create `.env` from `.env.example`
- Install and enable the systemd user service
- Enable **linger** so the service starts on boot and keeps running after logout

---

## Step 4 — Configure

Edit `.env` with your keys:

```
nano .env
```

```
TWELVE_DATA_API_KEY=your_twelve_data_key
TELEGRAM_BOT_TOKEN=123456789:ABC-DEF...
TELEGRAM_CHAT_ID=987654321

# Data mode: websocket (recommended) or rest
DATA_MODE=websocket

# REST mode only: poll interval in seconds (default 60)
REST_POLL_SECONDS=60

# REST mode only: only poll during these UTC hours (e.g. "8-21")
REST_POLL_HOURS_UTC=0-23
```

---

## Step 5 — Test manually

Before starting the service, run a quick test:

```
source venv/bin/activate
python main.py --test
```

This will:

1. Verify your Twelve Data API key by fetching a live XAU/USD quote.
2. Send a test Telegram message.
3. Print the current indicator values (EMA 100, RSI 3, ADX 5).

If both succeed, you're ready.

---

## Step 6 — Start the service

```
systemctl --user start xauusd-signal-bot
```

Check it's running:

```
systemctl --user status xauusd-signal-bot
```

Watch live logs:

```
journalctl --user -u xauusd-signal-bot -f
```

Stop / restart:

```
systemctl --user stop xauusd-signal-bot
systemctl --user restart xauusd-signal-bot
```

---

## Data mode

### WebSocket (default, recommended)

Streams real-time XAU/USD ticks and builds 1-minute candles locally. Uses **1 WebSocket credit** (free tier includes a small WebSocket trial allotment of 8 concurrent credits). No REST credit consumption, so it runs 24/5 without hitting the 800/day limit.

### REST (fallback)

Polls the `time_series` endpoint every `REST_POLL_SECONDS`. **Each request costs 1 credit.** At 60-second intervals, 24/7 usage = 1,440 requests/day, which **exceeds the 800/day free limit**.

To stay within the limit in REST mode:

- Set `REST_POLL_HOURS_UTC=8-21` (London + NY sessions = 13 hours → ~780 requests).
- Or increase `REST_POLL_SECONDS` to `120` (2-minute candles → still 720 requests over 24h).

Use REST mode only if WebSocket doesn't work on your plan.

---

## Backtesting

Verify the strategy on historical data before trusting it live:

```
source venv/bin/activate
python backtest.py --bars 5000
```

This fetches the last 5,000 1-minute bars, computes indicators, and prints every signal with its timestamp and price. Use it to see how often signals actually occur and whether the thresholds suit your style.

> **Cost:** 1 API credit per backtest run.

---

## Project structure

```
xauusd-signal-bot/
├── README.md
├── requirements.txt
├── .env.example
├── config.py                 # Loads .env and defines strategy parameters
├── indicators.py             # EMA, RSI, ADX calculations (pure pandas)
├── data_feed.py              # WebSocket + REST feeds, candle builder
├── strategy.py               # Signal logic
├── notifier.py               # Telegram + desktop notification
├── main.py                   # Entry point (--test, --backtest flags)
├── backtest.py               # Historical signal tester
├── setup_telegram.py         # Interactive Telegram setup helper
├── install.sh                # One-shot installer
├── uninstall.sh              # Removes service + venv
└── systemd/
    └── xauusd-signal-bot.service
```

---

## Troubleshooting

**Bot not sending messages**

- Run `python main.py --test` and check the output.
- Verify the token with: `curl "https://api.telegram.org/botYOUR_TOKEN/getMe"`
- Verify the chat ID is correct (no trailing spaces).

**"chat not found"**

- Send a message to the bot first, then re-fetch `getUpdates`.
- Group chat IDs are negative (e.g. `-1001234567890`).

**Twelve Data errors**

- `"API key is invalid"` → re-copy the key from the dashboard.
- `"You have reached the API credits limit"` → switch to WebSocket mode, or reduce REST polling.

**Service won't start**

- Check logs: `journalctl --user -u xauusd-signal-bot -n 50 --no-pager`
- Make sure `.env` exists and is filled in.
- Make sure the venv is at `xauusd-signal-bot/venv/`.

**Service stops when I log out**

- Run `sudo loginctl enable-linger $USER` (the installer does this, but verify).

**No signals for a long time**

- That's normal — the strategy is deliberately strict. Run `backtest.py` to see historical frequency.
- Check `journalctl` to confirm the bot is receiving ticks.

---

## Strategy tuning

All parameters live in `config.py` (overridable via `.env`):

| Parameter | Default | Meaning |
|---|---|---|
| `EMA_PERIOD` | 100 | Trend filter |
| `RSI_PERIOD` | 3 | RSI lookback |
| `ADX_PERIOD` | 5 | ADX lookback |
| `RSI_OVERSOLD` | 20 | Buy RSI threshold |
| `RSI_OVERBOUGHT` | 80 | Sell RSI threshold |
| `ADX_THRESHOLD` | 30 | Minimum trend strength |
| `BODY_RATIO_MIN` | 0.5 | Min body vs previous |
| `BODY_RATIO_MAX` | 1.5 | Max body vs previous |
| `CANDLE_INTERVAL_SECONDS` | 60 | 1-minute candles |

---

## License

MIT — use at your own risk. Not financial advice.

</BDS:create_file>

<BDS:create_file fileName="xauusd-signal-bot/requirements.txt">

```txt
requests>=2.31.0
pandas>=2.0.0
numpy>=1.24.0
websocket-client>=1.7.0
python-dotenv>=1.0.0

