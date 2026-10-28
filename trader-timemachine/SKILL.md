---
name: trader-timemachine
description: >-
  Time machine for a USDT-M plan. Replays the hold with a seeded Monte Carlo
  drawn from the contract's own 5-minute bars, and re-runs the same UTC
  opening-range rule on earlier days. Prints target-first, stop-first, and
  neither, plus an honest verdict. Use when the user says replay this trade,
  what are the odds, monte carlo, backtest this setup, or after trader-exit
  and before trader-budget.
argument-hint: "[--plan trader/plan.json]"
---

# trader-timemachine

This is a simulation built from recent bars, not a forecast. The next eight
hours can do something those days never did: a liquidation cascade, a halt
in the index, a funding print that flips. Not financial advice. Running it
does not place an order. The user still has to type yes, later, for one
exact ticket.

`${CLAUDE_SKILL_DIR}` is this folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --plan trader/plan.json --write
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --offline sol-5m.json --replays 10000 --seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart
```

A UTC day is 288 five-minute bars, midnight to midnight, including Saturday
and Sunday. The pool is every full day before the plan date. For each bar
of the day the pool stores that bar's open, high, low and close divided by
the previous close. A replay starts at the planned entry and walks
`hold_hours * 12` bars. Each step draws a nearby bar slot (the `--window`,
default 2) and scales a historical bar onto the current price. If the bar
touches the stop and the target, it counts as the stop. Same seed, same
bars, same numbers.

The history leg re-applies the UTC opening-range rule: first close outside
the first N minutes, same stop style, same target R, same hold. Setups that
are not `utc-orb` skip the history leg and say so.

The verdict compares the target's share of the replays that hit a side with
the share that breaks even at that R (50% at 1R, about 33% at 2R). Under 5
points is a coin flip. 5 to 15 leans. 15 or more is strong. A pool under 20
UTC days is labeled a thin sample. You need at least 8 full days or the run
refuses.

## Files

| file | what |
| --- | --- |
| `trader/plan.json` | entry, stop, target, entry bar, hold |
| `trader/timemachine.json` | written with `--write` |

`--offline` reads Binance kline JSON. Live reads page `/fapi/v1/klines`
5m, with the spot fallback if the futures host returns HTTP 451.

## Steps

1. Run it before sizing. Read the verdict and the history counts, both.
2. If the history and the Monte Carlo disagree, say both. Do not average them into a story.
3. A lean is not permission. Hand the plan to `/trader-budget` only if the user still wants the trade.

## Safety

Not financial advice. Paper trading stays the default. No yes, no order.
The time machine cannot see a move that is larger than the bars it was given.
