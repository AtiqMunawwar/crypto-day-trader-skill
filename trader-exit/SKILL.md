---
name: trader-exit
description: >-
  Exit planner for a USDT-M perpetual. Sets the stop and the target before
  entry, from the UTC opening range, a percent, a price, or ATR, then
  estimates isolated liquidation, the leverage cap, and funding paid over
  the hold. Writes trader/plan.json. Use when the user says plan the exit,
  where is the stop, ATR stop, liquidation, funding cost, or after
  trader-entry and before trader-timemachine.
argument-hint: "[--stop or-mid|atr:K|pct:X] [--leverage N]"
---

# trader-exit

The plan is written before the size. A stop is a price you chose, not a
guarantee: a fast market can fill past it, and on a perp the liquidation
engine can close you if the stop never trades. The liquidation number here
is an estimate for one isolated position whose margin is the initial margin,
fees ignored. It is not Binance's matching engine. Not financial advice.
No order is placed. The user still has to type yes later.

`${CLAUDE_SKILL_DIR}` is this folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --from trader/entry.json --stop or-mid --target 1R --leverage 5 --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol SOLUSDT --entry 106.63 --side buy --stop atr:1.5 --target 2R \
    --or-high 106.61 --or-low 106.22 --offline klines.json
python3 "${CLAUDE_SKILL_DIR}/exit.py" show
```

Stop rules: `or-mid`, `or-low` (longs), `or-high` (shorts), `price:X`, `pct:X`,
`atr:K`. Targets: `1R`, `2R`, `price:X`. Prices are snapped to the symbol's
tick. The stop is rounded away from the entry. A stop closer than 0.05% of
price is refused.

Leverage is capped by `max_leverage` in config and by a per-symbol cap
(BTC and ETH 20, SOL, XRP and BNB 10, other alts 5, and the config cap wins
when it is tighter). The example config caps everything at 5x. Cross margin
is refused unless `allow_cross` is true. The default hold is 8 hours, one
funding interval, from `max_hold_hours`.

Funding uses `lastFundingRate` from premiumIndex. Positive funding means
longs pay. The cost is `notional * rate * number of 00:00, 08:00, 16:00 UTC
prints inside the hold`. A print older than 24 hours is flagged
`stale_funding`. If the futures host is blocked, pass `--premium FILE`.

The liquidation printed here uses a guess quantity from the risk budget.
`/trader-budget` recomputes it for the quantity it actually sizes. If the
guess already sits through the stop, the command exits 1.

## Files

| file | what |
| --- | --- |
| `trader/entry.json` | the triggered entry |
| `trader/config.json` | leverage cap, hold, margin mode. Start from trader-budget's example |
| `trader/plan.json` | entry, stop, target, leverage, liquidation, funding, warnings |

## Steps

1. Read the entry. If it is not triggered, stop and say so.
2. Prefer a stop that is beyond normal noise. A 15-minute range can be very tight.
3. Tell the user the risk per unit, the reward:risk, the leverage, the margin mode, the liquidation estimate, and the funding.
4. Name every warning, especially `stale_funding` and `liquidation_through_stop`.
5. Hand the plan to `/trader-timemachine` before anyone talks about size.

## Safety

Not financial advice. Paper by default. Isolated is the default so one
position cannot spend the whole wallet. The user must type an explicit yes
before trader-broker will even consider a live send, and the config must
have paper turned off on purpose.
