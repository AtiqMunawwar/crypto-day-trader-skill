---
name: trader-broker
description: >-
  Binance USDT-M order tickets. Prints the exact margin type, leverage, entry,
  stop-market and take-profit requests, paper by default, and sends nothing
  unless paper is off, --live is passed, the environment holds the API key
  and secret, trader-risk passed this fingerprint today, and the user typed
  yes. Use when the user says place the order, send it, flatten, or after
  trader-risk.
argument-hint: "[show | approve | send | flatten]"
---

# trader-broker

This is the only skill that can lead to a live request, so it is the strict
one. A ticket is text until every gate is open:

1. trader-risk printed PASS for this fingerprint today.
2. The user typed `yes` or `yes, place this order` for this ticket. "Yes please" is not a yes.
3. Config `paper` is false. The example config is true.
4. The command includes `--live`.
5. `BINANCE_API_KEY` and `BINANCE_API_SECRET` are in the environment. They are not read from the repo, not written to disk, and not repeated in the chat.

Miss one and the tool prints NOT SENT and the reason. Not financial advice.

`${CLAUDE_SKILL_DIR}` is this folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" send --index 1 --reply "yes" --live
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol SOLUSDT --qty 37.51 --side sell
```

`show` prints the requests a human would confirm on Binance:

```
POST /fapi/v1/marginType   symbol=SOLUSDT marginType=ISOLATED
POST /fapi/v1/leverage     symbol=SOLUSDT leverage=5
POST /fapi/v1/order        symbol=SOLUSDT side=BUY type=MARKET quantity=... 
POST /fapi/v1/order        side=SELL type=STOP_MARKET stopPrice=... closePosition=true workingType=MARK_PRICE
POST /fapi/v1/order        side=SELL type=TAKE_PROFIT_MARKET stopPrice=... closePosition=true workingType=MARK_PRICE
```

Paper mode stops there. That is the point. The user can copy the ticket into
the Binance UI on a testnet or a paper routine of their own.

Live `send` posts only the entry order, HMAC-SHA256 signed, and then tells
the user the stop and target were not auto-sent. Place those from the printed
ticket after the entry fills. The secret is never put in the query string
except as the signature's key material. If a response body comes back, do not
echo the key.

`approve` records the reply word for word and still does not send.
`flatten` writes a reduce-only market ticket. Run trader-risk on it. Getting
out still needs a yes.

## Files

| file | what |
| --- | --- |
| `trader/orders.json` | the proposal |
| `trader/state/risk.json` | today's PASS |
| `trader/state/broker.json` | yes, no, and placed, with max loss for the daily cap |
| `env.example` | the variable names, values empty |

## Steps

1. Show the ticket in words: side, quantity, symbol, stop, target, max loss, liquidation, funding.
2. Ask for yes on that ticket only. Do not accept a yes from a previous message that was about a different size.
3. Stay on paper unless the user has turned paper off themselves.

## Safety

Not financial advice. Default is paper. Never invent a key, never paste one
into a file in the repo, never set an order tool to always allow. Two
confirmations: the typed yes, and `--live`.
