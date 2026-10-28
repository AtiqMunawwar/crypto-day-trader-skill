---
name: trader-alerts
description: >-
  Price and time alerts for Binance USDT-M. Rings once when mark or last
  price crosses a stop, a target, an above or below level, or a percent move,
  and when a hold window ends. Builds the three plan alerts in one step.
  Never sells and never places an order. Use when the user says alert me,
  watch my stop, tell me if SOL hits, or after trader-exit.
argument-hint: "[add | check | quote | list]"
---

# trader-alerts

An alert is a sentence, not an order. When the stop alert rings, nothing is
sold by this skill. If a stop-market is already working at the exchange, the
alert only tells the user what the last price did. Not financial advice.
There is no opening bell. Time alerts are UTC.

`${CLAUDE_SKILL_DIR}` is this folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol SOLUSDT --below 100
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol SOLUSDT --above 120
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol SOLUSDT --pct 3 --ref 106.63
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check --now "2026-10-21 04:00"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote SOLUSDT --write
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
```

`--from-plan` writes three rules: the stop (a long rings at or under the stop,
a short at or over it), the target, and a time alert at entry plus
`hold_hours`. Each id rings once. The state file remembers that, so a check
every minute does not spam.

`quote` reads `/fapi/v1/ticker/24hr` for the last price, or `--offline` JSON
with `lastPrice`. `--write` stores `trader/prices.json`.

## Files

| file | what |
| --- | --- |
| `trader/alerts.json` | the rules. See `alerts.example.json` |
| `trader/state/alerts.json` | ids that already rang |
| `trader/plan.json` | read by `--from-plan` |
| `trader/prices.json` | optional quote cache |

## Steps

1. After a plan exists, add the three alerts before the user walks away.
2. On a ring, say the price and which rule. Do not invent a fill.
3. A stop ring means "go look". It does not mean the exchange filled you.

## Safety

Not financial advice. Alerts never call the order endpoint. Paper remains the
default for anything that could. The user types yes only in trader-broker,
for one order, and live mode is a separate flag.

A percent alert needs `--ref`, the price you are measuring from. Without it
the tool refuses, instead of guessing yesterday's close. There is no cash
session and no previous close that every contract shares. Pick the ref on
purpose: the plan entry, or the UTC day's open.
