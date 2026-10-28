---
name: trader-funding
description: >-
  Funding rate, open interest and squeeze context for Binance USDT-M
  perpetuals. Reads premiumIndex, open interest, the long/short account
  ratio and the 24h ticker. Flags crowded longs or shorts, squeeze risk,
  open-interest buildup, and stale funding prints. Use when the user says
  funding, open interest, crowded, squeeze, or before an entry on BTC, ETH,
  SOL, XRP, BNB or a small-cap.
argument-hint: "[SYMBOL | --scan --symbols ...]"
---

# trader-funding

Funding is a positioning fact. A positive rate means longs pay shorts every
eight hours, at 00:00, 08:00 and 16:00 UTC. It is not by itself a reason to
short. Crowded trades can keep paying and still be right. Not financial
advice. This skill never places an order and never asks for a key.

`${CLAUDE_SKILL_DIR}` is this folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/context.py" SOLUSDT
python3 "${CLAUDE_SKILL_DIR}/context.py" SOL --offline tests/fixtures/binance --now "2026-10-26 23:59"
python3 "${CLAUDE_SKILL_DIR}/context.py" --scan --symbols BTC,ETH,SOL,XRP,BNB
```

Rules, all of them visible in the output:

| flag | rule |
| --- | --- |
| `crowded_longs` | funding at least 0.05% per 8h, or 0.01% and a long/short account ratio at least 2.2 |
| `extreme_long_funding` | funding at least 0.10% per 8h |
| `crowded_shorts` / `extreme_short_funding` | the same on the short side |
| `long_squeeze_risk` | crowded longs and the 24h change is -3% or worse |
| `short_squeeze_risk` | crowded shorts and the 24h change is +3% or more |
| `oi_buildup` / `oi_flush` | open interest up or down 15% across the history window |
| `stale_funding` | the funding print is more than 24 hours old |

A dust-sized positive rate plus a 2:1 account split is normal. The ratio
only confirms a rate that is already elevated. Annualized funding is
`rate * 3 * 365`, shown so a "small" 0.05% print is about 55% a year if it
persisted. It usually does not persist.

Live calls, all public:

- `GET /fapi/v1/premiumIndex`
- `GET /fapi/v1/openInterest`
- `GET /futures/data/openInterestHist`
- `GET /futures/data/globalLongShortAccountRatio`
- `GET /fapi/v1/ticker/24hr`

`--offline DIR` reads the recorded JSON names this repo stores under
`tests/fixtures/binance/`. If the futures host returns HTTP 451, say so and
use the offline files. Do not invent a funding rate.

Pump and dump warnings from the ticker are printed too, using the same rules
as the scanner. A small-cap warning belongs in the sentence you hand to
trader-entry.

## Steps

1. Run it on the symbol before the entry timer, and again if the plan is hours old.
2. If the print is stale, say the timestamp. Do not treat September's rate as today's.
3. Hand the flags to the user as context. Then `/trader-entry` if they still want the setup.

## Safety

Not financial advice. No order, no key, no "this will squeeze". The user
types yes only later, in trader-broker, for one exact order, with paper on
unless they turned it off.
