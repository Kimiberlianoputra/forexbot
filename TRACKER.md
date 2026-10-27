# Outcome Tracker

Measures whether the bot's signals actually **work** — not just whether
they fire.

The bot logs each signal to `signals.csv`. The tracker reads that file,
fetches the following 1-minute bars from Twelve Data, simulates the trade
(spread-adjusted, TP/SL, 30-min timeout), and writes the result to
`outcomes.csv`. `report.py` turns that CSV into a summary.

---

## What it measures

**Trade outcomes** — for every signal:

| Outcome | Meaning |
|---------|---------|
| `win` | TP ($7.50 favorable) hit before SL |
| `loss` | SL ($5.00 adverse) hit before TP |
| `ambiguous` |
