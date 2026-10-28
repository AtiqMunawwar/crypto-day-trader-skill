---
name: trader-news
description: >-
  Binance announcement reader. Pulls the public CMS catalogs for new
  listings, general news and delistings, and keeps items that name the
  symbols you pass. No API key. Replaces the original pack's SEC and Yahoo
  headline tools, which do not apply to perps. Use when the user says any
  news, listings, delistings, or what did Binance announce about SOL.
argument-hint: "[--symbols SOL,BTC]"
---

# trader-news

A headline is something to read, not a signal. Listings and delistings move
perp books, and they also get faded by the second candle. This skill fetches
titles from Binance's public announcement catalog and filters them. It does
not place an order. Not financial advice.

The original day-trader pack read Yahoo headlines and SEC 8-K filings. Those
sources are stock-specific and they are not in this fork. Congress trade
disclosures are not here either.

`${CLAUDE_SKILL_DIR}` is this folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py" --symbols SOL,BTC --hours 72
python3 "${CLAUDE_SKILL_DIR}/news.py" --symbols PTB --offline tests/fixtures/binance/announcements.json --hours 240
```

Catalogs:

| id | kind | severity |
| --- | --- | --- |
| 48 | new listings | high |
| 49 | latest news | info |
| 161 | delistings | high |

The request is `GET https://www.binance.com/bapi/composite/v1/public/cms/article/list/query`
with `type=1` and a `catalogId`. No key. Titles are matched on the base
asset as its own token, so SOL does not match the word Isolated, and
SOLUSDT counts as SOL. Delistings are marked high even when the catalog
table says so already.

`--now` is UTC and `--hours` drops older items. `--offline` reads a saved
payload so tests never hit the network.

## Steps

1. Run it for the symbols on the watchlist before the entry, and for anything you might hold through a funding time.
2. Read delistings and listings out loud, with the time in UTC.
3. Do not turn a title into a trade. If the user still wants the chart, that is `/trader-entry`.

## What it will not do

It will not scrape paid news, invent a headline, or call an article bullish.
If the catalog is down, it prints the error and stops.

## Safety

Not financial advice. No keys. No orders. Paper remains the default
everywhere else in the pack. A live order still needs trader-risk PASS, the
word yes, and `--live`.
