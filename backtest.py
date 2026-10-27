#!/usr/bin/env python3
"""
Standalone backtest — convenience wrapper around `main.py --backtest`.

Usage:
    python backtest.py --bars 5000
"""
import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

from main import run_backtest  # noqa: E402

def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest the XAU/USD strategy")
    parser.add_argument("--bars", type=int, default=5000, help="Number of 1-min bars (default 5000)")
    args = parser.parse_args()
    return run_backtest(bars=args.bars)

if __name__ == "__main__":
    raise SystemExit(main())
