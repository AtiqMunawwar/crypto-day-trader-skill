---
name: trader-entry
description: >-
  Entry timer for a Binance USDT-M perpetual. Watches 5-minute or 1-minute
  bars and calls the entry only when a bar closes through the UTC opening
  range or through a price level. Says waiting, triggered, expired, or not
  ready, and writes trader/entry.json for the exit planner. Use when the
  user says tell me when to get in, watch SOL, did it break the range, or
  after trader-scanner and before trader-exit.
argument-hint: "[SYMBOL] [--setup utc-orb|level]"
---

# trader-entry

A wick through the level is not an entry. A completed bar that closes through
it is. The timer writes that price and time down and stops. It never buys or
sells. Day trading and perp trading lose most people money, and a clean close
does not change that. No order exists until the user types yes later.

`${CLAUDE_SKILL_DIR}` is this folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check SOLUSDT --setup utc-orb --side buy
python3 "${CLAUDE_SKILL_DIR}/entry.py" check SOLUSDT --setup level --level 102.5 --side buy --write
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch SOLUSDT --setup utc-orb --offline FILE --now "2026-10-21 02:00"
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist
```

The UTC opening range is the first `--or-minutes` (15) after 00:00 UTC. The
default validity window is 4 hours after that range. After it, the status is
`expired` and you start over instead of chasing. `--side buy` waits for a
close above the range high. `--side sell` waits for a close under the low.
Shorts still have to be allowed later in config. The default config does not
allow them.

`watch` with `--offline` or `--now` replays the day bar by bar and does not
sleep. A live loop with no bound is refused. Run `check` on a schedule if you
want a fresh bar.

## Files

| file | what |
| --- | --- |
| `trader/watchlist.json` | scanner hits, for `watchlist` |
| `trader/entry.json` | written with `--write`: status, symbol, side, price, time, range |

`--now` is UTC, for example `2026-10-21 00:20`. `--offline` is a kline JSON
file in Binance's array shape, or a folder of them.

## Steps

1. Take the symbol the user picked. Do not substitute another.
2. Run `check`. Read the status out loud: waiting, triggered, expired, or not ready.
3. On triggered, the price is the bar's close, and the time is that bar's close in UTC.
4. Hand a triggered entry to `/trader-exit`. If it is waiting, say how the last close sits versus the range.

## Rules

- Never call a forming bar an entry.
- Say the time in UTC. There is no session close to hide behind.
- If the close is only a few tenths of a percent through a tight range, say so. Tight ranges make tight stops.

## Safety

Not financial advice. This skill does not size, check, or send an order.
Paper is the default downstream. The user must type yes before any live order,
and only after trader-risk passes that exact order.
