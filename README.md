# Crypto trader skills

Ten Claude skills for Binance USDT-M perpetual futures. Scan public charts,
read funding and announcements, time an entry, plan the stop with leverage
and liquidation in view, replay the hold, size to a loss limit, and print
the exact order. Paper by default. Nothing is sent until you type yes.

This is a crypto fork of [Jake Schincariol's day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
(MIT). The original pack scans US stocks with Yahoo Finance, Nasdaq symbol
lists, SEC EDGAR and House Clerk disclosures. That license is unchanged:
see `LICENSE`, copyright 2026 Jake Schincariol. The futures tools in this
fork are built to the same idea: free public data, standard-library Python,
no key required to analyze, no order without an explicit yes.

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the next
candle. Most people who trade perpetual futures lose money. You can lose
more than the margin you posted if you use cross margin or if a stop fills
through. Decide for yourself.

## Install

Python 3.8 or newer. Nothing to pip install.

```bash
git clone <this repo>
cd <this repo>
python3 -m unittest discover -s tests -v
```

As Claude skills, copy each `trader-*` folder. Each folder carries its own
`binance_public.py`, so one skill still runs on its own:

```bash
cp -r trader-* ~/.claude/skills/
```

Project-local: copy the same folders into `.claude/skills/`. Or paste one
`SKILL.md` at the top of a chat. You lose the Python tools, but the method
is the same.

There is no API key for analysis. Live order send is off unless you edit
`paper` yourself. If you do, the key and secret belong in the environment
(`trader-broker/env.example`), never in a file you commit.

## The ten

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart scanner | USDT-M perpetuals: daily breakouts, flags, RSI, UTC opening-range breaks, rolling-window breaks, 24h gainers and losers, pump/dump warnings. |
| `/trader-funding` | Funding and OI | Funding rate, mark versus index, open interest, long/short ratio, crowded positioning, squeeze risk. |
| `/trader-news` | Announcements | Binance public listings, news and delistings. No SEC, no Yahoo. |
| `/trader-entry` | Entry timer | First completed bar that closes through the UTC opening range or a level. |
| `/trader-exit` | Exit planner | Stop, target, ATR or range, leverage cap, isolated liquidation estimate, funding over the hold. |
| `/trader-timemachine` | Time machine | Seeded Monte Carlo of the hold, plus the same rule on earlier UTC days. |
| `/trader-budget` | Position size | Quantity from the max loss. Leverage changes margin, not that loss, unless margin was the binding cap. |
| `/trader-risk` | Risk manager | PASS or BLOCK: stop, loss, leverage, liquidation beyond the stop, funding, pump warnings, daily loss. |
| `/trader-broker` | Order tickets | Prints the exact Binance USDT-M requests. Paper unless you opt into live. |
| `/trader-alerts` | Alerts | Stop, target, level and hold-end alerts against Binance prices. Rings once. |

Removed from the original pack, because they are stock-only:

| original | why it is gone |
| --- | --- |
| `/trader-pelosi` | House Clerk PTR filings. Not a crypto data source. |
| `/trader-analyst` | SEC company facts and earnings XBRL. Not a crypto data source. |
| `/trader-news` (old) | Yahoo headlines and SEC 8-Ks. Replaced by Binance announcements. |

## How a trade moves

```
scanner -> funding -> news -> entry -> exit -> timemachine -> budget -> risk -> broker
alerts: stop, target, end of the hold
```

Markets are open all weekend. Times are UTC. There is no opening bell. The
"opening range" is the first 15 minutes of the UTC day, and the default hold
is 8 hours (one funding cycle), not a cash close.

### Worked example: SOLUSDT, 21 October 2026, 00:20 UTC

Recorded Binance USDT-M 5-minute bars (public `data.binance.vision` archive,
13 through 26 October 2026). The commands below use `--offline` and `--now`,
so they reproduce without a network. Live runs omit `--offline` and call
`fapi.binance.com`.

1. **Scanner.** At 00:20 UTC the 15-minute range was 106.22 to 106.61. The
   00:15 bar closed at 106.63, a buy, 0.019% through the high. That is a
   thin break of a tight range.

```bash
python3 trader-scanner/scan.py orb \
  --offline tests/fixtures/binance/SOLUSDT-5m.json \
  --symbols SOLUSDT --date 2026-10-21 --at 00:20 --now "2026-10-21 00:20" --side buy --no-write
```

2. **Entry timer.** Same bar, status TRIGGERED, close 106.63 at 00:20 UTC.

```bash
python3 trader-entry/entry.py check SOLUSDT --setup utc-orb --side buy \
  --offline tests/fixtures/binance/SOLUSDT-5m.json \
  --now "2026-10-21 00:20" --date 2026-10-21 --write trader/entry.json
```

3. **Exit plan, 5x isolated.** Stop at the range midpoint, snapped to the
   0.01 tick: 106.41. Risk 0.22 USDT per unit. Target 1R at 106.85. The
   funding file is the last print in the September 2026 archive
   (`7.4e-7` at 30 September 16:00 UTC). October's monthly file was not
   published when the fixtures were recorded, and `fapi.binance.com`
   returned HTTP 451 from that network, so the plan is flagged
   `stale_funding`. Do not treat that rate as live.

```bash
python3 trader-exit/exit.py plan --from trader/entry.json \
  --stop or-mid --target 1R --leverage 5 \
  --filters tests/fixtures/binance/exchangeInfo.json \
  --premium tests/fixtures/binance/SOLUSDT-premiumIndex.json \
  --now "2026-10-21 00:20" \
  --config trader-budget/config.example.json \
  --write trader/plan.json
```

4. **Time machine.** 10,000 replays, seed 19, 8-hour hold, pool of the 8
   earlier full UTC days (a thin sample, and the tool says so). Target
   first 55.3%, stop first 44.7%, neither 0%, average R +0.105. Verdict:
   leans toward the target, barely. History of the same rule on those 8
   days: it fired 8 times, target 4, stop 4, average R 0. A coin flip on
   the real days. That disagreement is the point of looking before you
   size.

```bash
python3 trader-timemachine/timemachine.py run \
  --plan trader/plan.json \
  --offline tests/fixtures/binance/SOLUSDT-5m.json \
  --replays 10000 --seed 19
```

5. **Size.** Account 5,000 USDT, max loss 50 USDT, max leverage 5, isolated.
   The risk budget would have allowed about 227 units, but `max_order_usd`
   of 4,000 binds first: **37.51 SOL**. Notional 3,999.69 USDT, margin
   799.94 USDT, loss at the stop **8.25 USDT**. Leverage did not raise the
   quantity. Liquidation estimate after sizing: **86.17** (maintenance
   1% on this notional), which is beyond the stop at 106.41. Funding at
   the stale rate for one interval is about 0.003 USDT.

```bash
python3 trader-budget/size.py \
  --plan trader/plan.json \
  --config trader-budget/config.example.json \
  --filters tests/fixtures/binance/exchangeInfo.json \
  --write trader/orders.json
```

6. **Risk.** PASS for that fingerprint. A 50x plan with liquidation through
   the stop would BLOCK. A small-cap pump warning BLOCKs unless
   `allow_volatile` is true. Shorts BLOCK while `allow_short` is false.

```bash
python3 trader-risk/check.py \
  --orders trader/orders.json \
  --config trader-budget/config.example.json \
  --now "2026-10-21 00:20"
```

7. **Ticket.** Paper. The tool prints the five requests and stops. Typing
   yes records the word. It still does not send. Live send also needs
   `"paper": false`, `--live`, and `BINANCE_API_KEY` / `BINANCE_API_SECRET`
   in the environment.

```bash
python3 trader-broker/ticket.py show --now "2026-10-21 00:20"
```

```
POST /fapi/v1/marginType  symbol=SOLUSDT marginType=ISOLATED
POST /fapi/v1/leverage    symbol=SOLUSDT leverage=5
POST /fapi/v1/order       symbol=SOLUSDT side=BUY type=MARKET quantity=37.51 newOrderRespType=RESULT
POST /fapi/v1/order       symbol=SOLUSDT side=SELL type=STOP_MARKET stopPrice=106.41 closePosition=true workingType=MARK_PRICE
POST /fapi/v1/order       symbol=SOLUSDT side=SELL type=TAKE_PROFIT_MARKET stopPrice=106.85 closePosition=true workingType=MARK_PRICE
```

8. **Alerts** on the stop, the target, and 08:20 UTC when the 8-hour hold ends.

```bash
python3 trader-alerts/alerts.py add --from-plan
python3 trader-alerts/alerts.py check
```

On the same recorded daily bars, a daily scan as of 27 October 2026 tags
ETHUSDT and SOLUSDT `breakdown_20d` and BNBUSDT `reclaim_ma20`. Funding
context for SOL that week is a stale, near-zero rate, open interest about
8.92 million contracts, long/short account ratio about 3.08. None of the
crowded-funding rules fire, because a dust-sized rate is not a crowd.

## The tools

```bash
python3 trader-scanner/scan.py universe
python3 trader-scanner/scan.py daily --symbols BTC,ETH,SOL,XRP,BNB,WIF
python3 trader-scanner/scan.py orb --symbols SOLUSDT --at 00:20
python3 trader-scanner/scan.py roll --symbols SOLUSDT --window-minutes 60
python3 trader-scanner/scan.py movers --top 15
python3 trader-funding/context.py SOLUSDT
python3 trader-news/news.py --symbols SOL,BTC --hours 72
python3 trader-entry/entry.py check SOLUSDT --setup utc-orb
python3 trader-exit/exit.py plan --stop or-mid --target 1R --leverage 5 --write
python3 trader-timemachine/timemachine.py run --write
python3 trader-budget/size.py --write
python3 trader-risk/check.py
python3 trader-broker/ticket.py show
python3 trader-alerts/alerts.py add --from-plan
python3 -m unittest discover -s tests -v
```

Tests are offline. They load recorded Binance JSON under `tests/fixtures/binance/`:
futures klines, metrics, funding, a 24h ticker aggregated from those klines,
announcement titles, and an exchangeInfo-shaped filter file. Nothing in the
test suite opens a socket.

`scripts/record_fixtures.py` refreshes the vision archives. It is not part of
the test run.

## Data, leverage, and the limits of the math

- **Futures first.** Klines, 24h tickers, `premiumIndex`, funding history and
  open interest are requested from `https://fapi.binance.com`. The client
  pauses between calls, retries once on HTTP 429, and stops on HTTP 418.
- **Spot fallback, klines only.** If the futures host answers HTTP 451, klines
  are tried on `https://api.binance.com` and then on
  `https://data-api.binance.vision`. Funding, open interest and the mark
  price are not on those hosts. The tool says which source it used.
- **This environment.** While the fixtures were recorded, `fapi.binance.com`
  returned HTTP 451 (restricted location). Klines, mark price, index price,
  metrics and the September funding file came from Binance's public
  `data.binance.vision` futures archive instead. The October 2026 monthly
  funding file was not published yet.
- **Tick sizes** in `tests/fixtures/binance/exchangeInfo.json` match the
  price grid of those klines. They are not a live `exchangeInfo` dump.
  Confirm filters on Binance before you rely on a quantity.
- **Liquidation** is an estimate for one isolated position, wallet equal to
  initial margin, fees ignored:

  long: `(entry * (1 - 1/leverage) - cum/qty) / (1 - mmr)`

  short: `(entry * (1 + 1/leverage) + cum/qty) / (1 + mmr)`

  Maintenance brackets in the code are conservative stand-ins. Binance's
  live bracket table requires a signed request, so it can differ. Cross
  margin uses the whole wallet and can liquidate other positions. It is
  off unless you set `allow_cross`.
- **Leverage caps.** Example config: max 5x. Built-in ceilings before that
  cap: BTC and ETH 20, SOL, XRP and BNB 10, other alts 5. A pump warning
  on a small-cap blocks the trade unless `allow_volatile` is true, and then
  `volatile_max_leverage` (3) still applies.
- **A stop is a plan.** In a fast market a stop-market can fill worse than
  its price. The max loss shown is the loss at the stop price, not the
  worst fill, and not the funding you may still pay.
- **The time machine is not a prediction.** Same plan, same bars, same
  `--seed` give the same numbers. They are still a replay.

## Fine print

**Not financial advice.** Education and tooling. A PASS is a rules check.

**No trade goes through until you say yes.** Only `/trader-broker` can send,
and only when trader-risk passed that exact order today, you typed yes for
it, config paper is false, and you also passed `--live`. The default is to
print the ticket and stop.

**Paper first.** `"paper": true` ships in `trader-budget/config.example.json`.

**Keys.** `BINANCE_API_KEY` and `BINANCE_API_SECRET` are environment variables.
`trader-broker/env.example` has the names and empty values. Do not paste keys
into the chat or the repo.

**24/7.** Risk counts trades on the UTC day. There is no pattern-day-trader
rule and no cash session in this fork.

**Funding.** Longs pay when the rate is positive. Shorts pay when it is
negative. The cost the tools show assumes the last rate holds for every
print inside the hold. It will not.

## Files

```
binance_public.py     shared client and futures math (also copied into each skill)
trader-scanner/       SKILL.md, scan.py
trader-funding/       SKILL.md, context.py
trader-news/          SKILL.md, news.py
trader-entry/         SKILL.md, entry.py
trader-exit/          SKILL.md, exit.py
trader-timemachine/   SKILL.md, timemachine.py
trader-budget/        SKILL.md, size.py, config.example.json
trader-risk/          SKILL.md, check.py
trader-broker/        SKILL.md, ticket.py, env.example
trader-alerts/        SKILL.md, alerts.py, alerts.example.json
tests/                unittest suite, recorded Binance fixtures
scripts/record_fixtures.py
```

MIT. Original pack by [Jake Schincariol](https://opusjake.ai).
This fork adapts it to Binance USDT-M perpetual futures.
