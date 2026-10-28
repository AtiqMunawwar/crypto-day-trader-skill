---
name: trader-risk
description: >-
  Risk manager for a USDT-M order. Blocks a trade that breaks the stop rule,
  the max loss, the daily loss, the leverage cap, isolated-only margin,
  liquidation-before-stop, reward to risk, funding that exceeds the target,
  or a small-cap pump warning. Prints PASS or BLOCK and records it so the
  broker can require that exact order. Use before trader-broker, always,
  when the user says check this, is this inside my rules, or can I take it.
argument-hint: "[--orders trader/orders.json]"
---

# trader-risk

A BLOCK stops the order. It does not go to the user for a yes, and it does
not go to the exchange. These limits keep one loss and one day small. They
do not make a trade win. Not financial advice.

Getting out is different. A `reduceOnly` order is not stopped by the daily
trade count or the pump rule. Closing a bad position should not be held
hostage by the rule that limited opening it. It still has to be the order
the user confirms.

`${CLAUDE_SKILL_DIR}` is this folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-21 00:20" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write
```

Checks on an opening order:

| rule | meaning |
| --- | --- |
| stop | required, and on the correct side of the entry |
| reward:risk | at least `min_reward_risk` (default 1) |
| max loss | quantity times the stop distance, capped in USDT and by `risk_pct_per_trade` |
| daily loss | today's planned losses plus this one, from the broker log |
| trades today | opening orders only, UTC day, 24/7 |
| leverage | at most the config cap and the symbol cap |
| margin | isolated, unless `allow_cross` |
| shorts | blocked unless `allow_short` |
| liquidation | must sit beyond the stop by `liq_buffer_pct` |
| funding | a `funding_cost_above_reward` warning blocks |
| pump | `pump`, `dump`, `thin_move`, `small_cap_volatile` block unless `allow_volatile` |
| paper | an order marked live is blocked while config paper is true |

The verdict is stored in `trader/state/risk.json` with the order fingerprint.
trader-broker treats a PASS from a different UTC day, or a different
fingerprint, as not ready.

## Files

| file | what |
| --- | --- |
| `trader/orders.json` | the proposal |
| `trader/config.json` | the limits |
| `trader/state/broker.json` | what was already placed today |
| `trader/state/risk.json` | PASS or BLOCK |

## Steps

1. Run the check after every change to size or price. A stale PASS is not a PASS.
2. Read every BLOCK reason. Do not round it off.
3. Only a PASS goes to `/trader-broker`, and even then the user must type yes.

## Safety

Not financial advice. Paper by default. No options. No cross margin and no
shorts unless the config says so in writing. Keys are never part of this
check. The user types yes in the broker step, not here.
