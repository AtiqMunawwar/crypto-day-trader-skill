---
name: trader-budget
description: >-
  Budget and position size for a USDT-M perpetual. Sizes quantity from the
  max loss at the stop and from a percent of the account, then caps it by
  margin, order size and cash buffer. Leverage changes margin, not the loss
  at the stop, unless margin was the binding limit. Writes one paper bracket
  to trader/orders.json. Use when the user says how many contracts, size this,
  I have 5000 USDT, risk 1 percent, or after trader-timemachine.
argument-hint: "[--plan trader/plan.json]"
---

# trader-budget

The stop decides the pain. Quantity is `risk budget / distance to the stop`,
then rounded down to the lot step. Leverage does not increase that quantity
when the risk budget is the tightest limit. It only reduces the margin. If
margin or cash is tighter, leverage would allow more contracts, and it pulls
liquidation closer to the entry. The tool says which limit bound the size.
Not financial advice. It writes a proposal. It does not send it. The user
types yes later, or nothing happens.

`${CLAUDE_SKILL_DIR}` is this folder.

```bash
cp "${CLAUDE_SKILL_DIR}/config.example.json" trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
python3 "${CLAUDE_SKILL_DIR}/size.py" --plan trader/plan.json --filters exchangeInfo.json --json
```

Copy the example config once and change the numbers in plain language:

- **budget**: money that can be lost. Not rent.
- **risk_pct_per_trade** and **max_loss_per_trade_usd**: the size uses the smaller of the two.
- **cash_buffer_pct**: stays untouched (default 10).
- **max_margin_pct**: the most of the budget this position may post as margin (default 25).
- **max_leverage**: default 5. Per-symbol caps are tighter for alts.
- **margin_mode**: `isolated`. Cross stays off unless `allow_cross` is true.
- **allow_short**: false until the user says they want shorts.
- **paper**: true. Leave it true.

A stop so far away that the size rounds under the minimum quantity is a
refusal, not a quiet smaller order.

The bracket is a market entry plus a STOP_MARKET and a TAKE_PROFIT_MARKET,
both `closePosition=true` and `workingType=MARK_PRICE`. Quantity, stop and
target are strings Binance can accept. Liquidation is recomputed for this
quantity and this maintenance bracket. The brackets in the code are
conservative estimates, because the live leverage bracket endpoint wants a
signed request.

Funding cost is `notional * rate * funding events`. Positive means this side
pays. If that payment is larger than the profit at the target, the order is
tagged `funding_cost_above_reward` and the command exits 1.

## Files

| file | what |
| --- | --- |
| `trader/config.json` | the rules |
| `trader/plan.json` | from trader-exit |
| `trader/portfolio.json` | optional cash cap |
| `trader/orders.json` | the one paper bracket |

## Steps

1. If config is missing, copy the example and ask for the budget and the max loss.
2. Run `size.py`. Read the binding limit in a sentence.
3. Read liquidation versus the stop. If liquidation is closer, do not continue.
4. Hand the file to `/trader-risk`.

## Safety

Not financial advice. Paper is the default. A PASS later is a rules check,
not a recommendation. The user must type yes for the exact order before any
send, and live mode also needs `--live` plus environment keys.
