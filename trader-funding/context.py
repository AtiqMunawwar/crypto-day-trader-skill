#!/usr/bin/env python3
"""Funding, open interest and squeeze context. Not financial advice.

Reads Binance public futures data: premiumIndex, open interest, the long/short
account ratio and the 24h ticker. A crowded funding print is a positioning
fact, not a signal to fade it. This tool never places an order.

    python3 context.py SOLUSDT --offline tests/fixtures/binance
    python3 context.py scan --symbols BTCUSDT,ETHUSDT,SOLUSDT --offline DIR
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_public as bx  # noqa: E402


def _pick(doc, symbol):
    if isinstance(doc, list):
        rows = [r for r in doc if r.get("symbol") in (None, symbol)]
        return rows[-1] if rows else None
    if isinstance(doc, dict) and doc.get("symbol") in (None, symbol):
        return doc
    return None


def _read_offline(folder, symbol):
    """Best-effort load of the fixture names this pack records."""
    def one(*names):
        for name in names:
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return bx.read_json(path)
        return None
    premium = one("%s-premiumIndex.json" % symbol, "premium.json")
    oi = one("%s-openInterest.json" % symbol, "openInterest.json")
    hist = one("%s-openInterestHist.json" % symbol, "openInterestHist.json")
    ratio = one("%s-longShort.json" % symbol, "longShort.json")
    ticker = one("%s-ticker-24hr.json" % symbol, "ticker.json")
    return premium, oi, hist, ratio, ticker


def oi_change_pct(hist):
    if not isinstance(hist, list) or len(hist) < 2:
        return None
    try:
        first = float(hist[0].get("sumOpenInterest") or hist[0].get("openInterest"))
        last = float(hist[-1].get("sumOpenInterest") or hist[-1].get("openInterest"))
    except (TypeError, ValueError):
        return None
    if first == 0:
        return None
    return (last / first - 1.0) * 100.0


def build_context(symbol, premium, oi, hist, ratio, ticker, now):
    symbol = bx.normalize_symbol(symbol)
    prem = _pick(premium, symbol) or {}
    try:
        rate = float(prem.get("lastFundingRate"))
    except (TypeError, ValueError):
        raise bx.BinanceError("no funding rate for %s" % symbol)
    mark = prem.get("markPrice")
    index = prem.get("indexPrice")
    basis = None
    if mark is not None and index not in (None, "", "0"):
        try:
            basis = (float(mark) / float(index) - 1.0) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            basis = None
    ls = None
    if isinstance(ratio, list) and ratio:
        try:
            ls = float(ratio[-1].get("longShortRatio"))
        except (TypeError, ValueError):
            ls = None
    elif isinstance(ratio, dict) and ratio.get("longShortRatio") is not None:
        ls = float(ratio["longShortRatio"])
    change = None
    high = low = quote = None
    if isinstance(ticker, dict) and ticker.get("priceChangePercent") is not None:
        change = float(ticker["priceChangePercent"])
        high = float(ticker.get("highPrice") or 0)
        low = float(ticker.get("lowPrice") or 0)
        quote = float(ticker.get("quoteVolume") or 0)
    oi_pct = oi_change_pct(hist if isinstance(hist, list) else None)
    flags = bx.funding_flags(rate, ls, change, oi_pct)
    warnings = []
    if change is not None and high and low:
        warnings = bx.pump_dump(symbol, change, high, low, quote or 0)
    funding_time = prem.get("_fundingTime") or prem.get("fundingTime")
    stale = False
    if funding_time is not None and bx.utc_ms(now) - int(funding_time) > 24 * 3600 * 1000:
        stale = True
        flags = flags + ["stale_funding"]
    oi_now = None
    if isinstance(oi, dict):
        oi_now = oi.get("openInterest")
    return {
        "symbol": symbol,
        "funding_rate": rate,
        "funding_annualized": bx.annualized_funding(rate),
        "funding_time": funding_time,
        "stale_funding": stale,
        "mark_price": None if mark is None else float(mark),
        "index_price": None if index is None else float(index),
        "basis_pct": None if basis is None else round(basis, 4),
        "open_interest": oi_now,
        "oi_change_pct": None if oi_pct is None else round(oi_pct, 2),
        "long_short_ratio": ls,
        "change_pct_24h": change,
        "flags": flags,
        "warnings": warnings,
        "as_of": bx.iso_z(now),
    }


def describe(ctx):
    print("%s funding %s per 8h (about %s annualized if it stayed here)." % (
        ctx["symbol"], bx.num(ctx["funding_rate"], 8),
        "%.1f%%" % (100 * ctx["funding_annualized"])))
    if ctx["stale_funding"]:
        print("That funding print is more than 24 hours old. Do not treat it as the live rate.")
    if ctx["basis_pct"] is not None:
        print("Mark %s vs index %s, basis %+.4f%%." % (
            bx.num(ctx["mark_price"]), bx.num(ctx["index_price"]), ctx["basis_pct"]))
    if ctx["open_interest"] is not None:
        print("Open interest %s contracts. Change over the history window: %s%%." % (
            ctx["open_interest"], ctx["oi_change_pct"]))
    if ctx["long_short_ratio"] is not None:
        print("Long/short account ratio %s (above 1 means more long accounts)." % bx.num(ctx["long_short_ratio"], 4))
    if ctx["change_pct_24h"] is not None:
        print("24h change %+.2f%%." % ctx["change_pct_24h"])
    if ctx["flags"]:
        print("Flags: %s." % ", ".join(ctx["flags"]))
    else:
        print("Flags: none of the crowded, squeeze or open-interest rules fired.")
    if ctx["warnings"]:
        print("Ticker warnings: %s." % ", ".join(ctx["warnings"]))
    print("Crowded longs can keep paying funding and still be right. This is context, not a fade.")
    print(bx.disclaimer())


def _live(symbol, client):
    premium = bx.fetch_premium_index(client, symbol)
    try:
        oi = bx.fetch_open_interest(client, symbol)
    except bx.BinanceError:
        oi = None
    try:
        hist = bx.fetch_oi_hist(client, symbol)
    except bx.BinanceError:
        hist = None
    try:
        ratio = bx.fetch_long_short(client, symbol)
    except bx.BinanceError:
        ratio = None
    try:
        ticker = bx.fetch_ticker_24hr(client, symbol)
    except bx.BinanceError:
        ticker = None
    return premium, oi, hist, ratio, ticker


def cmd_one(a, symbol):
    now = bx.parse_now(a.now)
    if a.offline:
        premium, oi, hist, ratio, ticker = _read_offline(a.offline, symbol)
        if premium is None:
            raise bx.BinanceError("no premium file for %s in %s" % (symbol, a.offline))
    else:
        premium, oi, hist, ratio, ticker = _live(symbol, bx.Client(pause=a.pause))
    ctx = build_context(symbol, premium, oi, hist, ratio, ticker, now)
    if a.write:
        bx.write_json(a.write if isinstance(a.write, str) else "trader/funding.json", ctx)
    describe(ctx)
    if a.json:
        print(bx.json.dumps(ctx, indent=2, sort_keys=True))
    return ctx


def main(argv=None):
    p = argparse.ArgumentParser(prog="context.py", description="Funding and open interest. Not financial advice.",
                                epilog=bx.disclaimer())
    p.add_argument("symbol", nargs="?", help="SOL or SOLUSDT. Omit with scan.")
    p.add_argument("--scan", action="store_true")
    p.add_argument("--symbols", help="comma list used with --scan")
    p.add_argument("--offline", help="folder of recorded JSON")
    p.add_argument("--now")
    p.add_argument("--pause", type=float, default=bx.PAUSE)
    p.add_argument("--write", nargs="?", const="trader/funding.json")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.scan:
            symbols = [bx.normalize_symbol(s) for s in (a.symbols or "").split(",") if s.strip()]
            if not symbols and a.offline:
                symbols = sorted({os.path.basename(p).split("-")[0]
                                  for p in glob.glob(os.path.join(a.offline, "*-premiumIndex.json"))})
            if not symbols:
                raise bx.BinanceError("scan needs --symbols")
            for symbol in symbols:
                print("==== %s" % symbol)
                cmd_one(a, symbol)
            return 0
        if not a.symbol:
            p.print_help()
            return 2
        cmd_one(a, bx.normalize_symbol(a.symbol))
        return 0
    except bx.BinanceError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
