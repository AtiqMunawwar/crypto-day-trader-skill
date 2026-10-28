---
name: trader-scanner
description: >-
  Chart scanner for Binance USDT-M perpetual futures. Builds the list of
  TRADING USDT perpetuals, scores daily closes for 20-day breakouts, bull and
  bear flags, moving-average reclaims and RSI bounces, finds the first close
  outside the UTC day's opening range, finds rolling-window breaks, and ranks
  24h gainers and losers with a pump or dump warning on thin small-caps.
  Free public Binance data, no API key. Use when the user says scan perps,
  what is breaking out, opening range, top gainers, flags, oversold, or
  before trader-entry.
argument-hint: "[universe | daily | orb | roll | movers]"
---

# trader-scanner

The scanner lists charts that matched a rule. A hit is not a trade and it is
not a prediction. Most people who trade perps lose money. This skill never
places an order. Nothing in this pack is sent until the user types yes for
one exact ticket in trader-broker.

`${CLAUDE_SKILL_DIR}` is the folder that holds this file.

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols BTC,ETH,SOL,XRP,BNB
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --symbols SOLUSDT --at 00:20
python3 "${CLAUDE_SKILL_DIR}/scan.py" roll --symbols SOLUSDT --window-minutes 60
python3 "${CLAUDE_SKILL_DIR}/scan.py" movers --min-quote-volume 5000000 --top 15
```

Crypto does not have an opening bell. `orb` uses the UTC day: the first
`--or-minutes` (default 15) of 00:00 UTC are the range, and a hit is the
first completed 5-minute bar that closes outside it. `roll` ignores midnight
and uses the prior N minutes. Both run on Sunday.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | `SYMBOL\|BASE\|USDT-M perpetual`, from `universe` |
| `trader/watchlist.json` | `mode`, `as_of`, `hits[]` for the entry timer |

`daily` setups on completed UTC daily closes (today's candle is left out):

| setup | rule |
| --- | --- |
| `breakout_20d` / `breakdown_20d` | last close beyond the prior 20 closes |
| `bull_flag` / `bear_flag` | a 10% run, then 5 closes inside a 5% band |
| `reclaim_ma20` / `lost_ma20` | close crosses the 20-day average |
| `oversold` / `overbought` | Wilder RSI 14 under 30 or over 70 |
| `oversold_bounce` / `overbought_fade` | RSI was extreme, and today closed back |

`movers` reads `/fapi/v1/ticker/24hr`, drops symbols with an underscore
(dated futures) and quotes other than USDT, and adds warnings: `pump` at
+25% or more, `dump` at -25% or worse, `wide_range` when the day's high-low
is at least 40% of the low, `thin_move` on a non-major under 5 million USDT
of quote volume, and `small_cap_volatile` on alts that trip those rules.
Majors are BTC, ETH, SOL, XRP and BNB.

## Data

Futures klines and tickers come from `fapi.binance.com`. A kline request that
is refused with HTTP 451 falls back to spot on `api.binance.com`, then to
`data-api.binance.vision`. Funding and open interest have no spot equivalent.
The client pauses between calls, backs off once on HTTP 429, and stops on
HTTP 418. Pass `--offline FILE` to read a saved JSON payload. `--now` is UTC.

## Steps

1. `universe` when the contract list might have changed.
2. `daily` or `movers` for a short list. Say how many symbols came back.
3. `orb` or `roll` on that list. Say the cutoff in UTC.
4. The user picks one symbol. Hand it to `/trader-funding` and `/trader-entry`.
   Do not pick for them.

## Rules

- Say "matched a rule", never "a buy" or "a winner".
- A small-cap warning is a warning. Do not talk the user into it.
- Re-run before acting. A 5-minute close goes stale quickly.

## Safety

Not financial advice. Paper trading is the default. No order is placed unless
trader-risk passed that exact order, the user typed yes for it, and live mode
was asked for on purpose. Keys stay in the environment, never in the chat.
