#!/usr/bin/env python3
"""Scan USDT-M perpetual charts. Not financial advice.

Free public Binance data, standard library only. A hit matched a rule. It is
not a trade and this tool never places an order.

    python3 scan.py universe
    python3 scan.py daily --symbols BTCUSDT,ETHUSDT,SOLUSDT
    python3 scan.py orb --at 00:20 --now "2026-10-26 00:20"
    python3 scan.py roll --window-minutes 60
    python3 scan.py movers --top 10
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_public as bx  # noqa: E402

DEFAULT_MIN_QUOTE = 5000000.0


def _client(a):
    return bx.Client(pause=getattr(a, "pause", bx.PAUSE))


def _symbols_arg(a):
    if not getattr(a, "symbols", None):
        return None
    return [bx.normalize_symbol(s) for s in a.symbols.split(",") if s.strip()]


def _write(path, doc, enabled):
    if enabled and path:
        bx.write_json(path, doc)


def cmd_universe(a):
    if a.offline:
        info = bx.read_json(a.offline)
    else:
        info = bx.fetch_exchange_info(_client(a))
    rows = bx.perpetuals(info, quote="USDT")
    lines = []
    for s in rows:
        lines.append("%s|%s|USDT-M perpetual" % (s.get("symbol"), s.get("baseAsset") or ""))
    text = "\n".join(lines) + ("\n" if lines else "")
    if not a.no_write:
        folder = os.path.dirname(a.out)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print("USDT-M perpetuals trading: %d" % len(rows))
    if a.json:
        print(bx.json.dumps({"count": len(rows), "symbols": [s.get("symbol") for s in rows]}))
    else:
        for line in lines[: a.show]:
            print("  " + line)
        if len(lines) > a.show:
            print("  ... %d more in %s" % (len(lines) - a.show, a.out))
    print(bx.disclaimer())
    return 0 if rows else 1


def _daily_bars(symbol, raw_bars, now):
    """Completed daily bars only. The UTC day that contains `now` is still open."""
    today = now.date()
    by = {}
    for b in raw_bars:
        d = bx.ms_to_utc(b["open_ms"]).date()
        if d >= today:
            continue
        by[d] = b
    return [by[k] for k in sorted(by)]


def cmd_daily(a):
    now = bx.parse_now(a.now)
    if a.offline:
        data = bx.load_klines(a.offline)
        symbols = _symbols_arg(a) or sorted(data)
    else:
        symbols = _symbols_arg(a)
        if not symbols:
            raise bx.BinanceError("daily needs --symbols or --offline (one kline request per symbol)")
        data = {}
        client = _client(a)
        for sym in symbols:
            bars, _src = bx.fetch_klines(client, sym, "1d", limit=a.limit)
            data[sym] = bars
    hits = []
    scanned = 0
    short = 0
    for sym in symbols:
        bars = _daily_bars(sym, data.get(sym) or [], now)
        if len(bars) < 30:
            short += 1
            continue
        last = bars[-1]
        quote = last["quote_volume"]
        if a.min_quote_volume and quote < a.min_quote_volume:
            continue
        scanned += 1
        closes = [b["close"] for b in bars]
        tags, nums = bx.find_setups(closes)
        if not tags:
            continue
        hits.append({
            "symbol": sym,
            "setups": tags,
            "price": last["close"],
            "close_date": bx.ms_to_utc(last["open_ms"]).date().isoformat(),
            "rsi": None if nums["rsi"] is None else round(nums["rsi"], 1),
            "quote_volume": quote,
        })
    hits.sort(key=lambda h: (-(h["quote_volume"] or 0), h["symbol"]))
    doc = {
        "mode": "daily",
        "as_of": bx.iso_z(now),
        "scanned": scanned,
        "short": short,
        "hits": hits,
    }
    _write(a.out, doc, not a.no_write)
    print("Daily scan at %s UTC. %d symbols had 30 completed closes. %d matched a rule."
          % (bx.iso_z(now), scanned, len(hits)))
    if short:
        print("%d symbols were too short to score." % short)
    for h in hits[: a.show]:
        print("  %-12s %s  rsi %s  %s" % (
            h["symbol"], bx.num(h["price"]), h["rsi"], ",".join(h["setups"])))
    if a.json:
        print(bx.json.dumps(doc, indent=2, sort_keys=True))
    print(bx.disclaimer())
    return 0


def _five(symbol, a, client, cache):
    if symbol in cache:
        return cache[symbol]
    if a.offline:
        return cache.get(symbol) or []
    bars, _src = bx.fetch_klines(client, symbol, "5m", limit=a.limit)
    cache[symbol] = bars
    return bars


def cmd_orb(a):
    now = bx.parse_now(a.now)
    if a.at:
        hh, mm = _hhmm(a.at)
        day = datetime.datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else now.date()
        cutoff = datetime.datetime(day.year, day.month, day.day, hh, mm)
        if a.date is None and cutoff > now:
            raise bx.BinanceError("--at %s is later than now" % a.at)
    else:
        cutoff = now.replace(second=0, microsecond=0)
        day = cutoff.date()
    if a.or_minutes < 5 or a.or_minutes % 5 or a.or_minutes > 120:
        raise bx.BinanceError("--or-minutes wants a multiple of 5 from 5 to 120")
    cutoff_ms = bx.utc_ms(cutoff)
    if a.offline:
        cache = bx.load_klines(a.offline)
        symbols = _symbols_arg(a) or sorted(cache)
    else:
        symbols = _symbols_arg(a)
        if not symbols:
            raise bx.BinanceError("orb needs --symbols or --offline")
        cache = {}
    client = None if a.offline else _client(a)
    tickers = {}
    if a.tickers:
        for row in bx.load_ticker_list(bx.read_json(a.tickers)):
            tickers[row.get("symbol")] = row
    hits = []
    scanned = 0
    waiting = 0
    for sym in symbols:
        bars = complete_bars_for(cache.get(sym) if a.offline else _five(sym, a, client, cache), cutoff_ms)
        day_bars = bx.bars_on_utc_day(bars, day)
        opening = bx.utc_opening_range(day_bars, a.or_minutes, 5)
        if opening is None:
            continue
        # the first bar after the range must be allowed to have closed
        if cutoff_ms < day_bars[0]["open_ms"] + (opening["bars"] + 1) * 5 * 60 * 1000:
            continue
        scanned += 1
        hit = bx.first_break(day_bars, opening, side=a.side)
        if hit is None:
            waiting += 1
            continue
        # only count a break whose close is at or before the cutoff (complete_bars already did)
        warnings = []
        t = tickers.get(sym)
        if t:
            warnings = bx.pump_dump(sym, bx.ticker_change(t), t.get("highPrice") or hit["or_high"],
                                    t.get("lowPrice") or hit["or_low"], t.get("quoteVolume") or 0)
        hits.append({
            "symbol": sym,
            "setups": ["utc_orb_" + hit["side"]],
            "side": hit["side"],
            "price": hit["price"],
            "at": hit["at"],
            "bar_index": hit["index"],
            "or_high": hit["or_high"],
            "or_low": hit["or_low"],
            "pct_over": round(hit["pct_over"], 3),
            "warnings": warnings,
        })
    hits.sort(key=lambda h: (h["at"], h["symbol"]))
    doc = {
        "mode": "utc-orb",
        "as_of": bx.iso_z(cutoff),
        "date": day.isoformat(),
        "or_minutes": a.or_minutes,
        "scanned": scanned,
        "waiting": waiting,
        "hits": hits,
    }
    _write(a.out, doc, not a.no_write)
    print("UTC opening-range scan %s, first %d minutes, cutoff %s UTC."
          % (day.isoformat(), a.or_minutes, cutoff.strftime("%H:%M")))
    print("%d usable charts. %d still inside the range. %d closed outside it."
          % (scanned, waiting, len(hits)))
    for h in hits[: a.show]:
        flag = ("  warnings: " + ",".join(h["warnings"])) if h["warnings"] else ""
        print("  %s  %-12s %s  range %s to %s  %+.3f%%%s" % (
            h["at"], h["symbol"], h["side"], bx.num(h["or_low"]), bx.num(h["or_high"]), h["pct_over"], flag))
    if a.json:
        print(bx.json.dumps(doc, indent=2, sort_keys=True))
    print(bx.disclaimer())
    return 0


def complete_bars_for(bars, cutoff_ms):
    return bx.complete_bars(bars or [], cutoff_ms)


def cmd_roll(a):
    now = bx.parse_now(a.now)
    cutoff_ms = bx.utc_ms(now)
    if a.window_minutes < 5 or a.window_minutes % 5 or a.window_minutes > 24 * 60:
        raise bx.BinanceError("--window-minutes wants a multiple of 5, up to 1440")
    if a.offline:
        cache = bx.load_klines(a.offline)
        symbols = _symbols_arg(a) or sorted(cache)
    else:
        symbols = _symbols_arg(a)
        if not symbols:
            raise bx.BinanceError("roll needs --symbols or --offline")
        cache = {}
    client = None if a.offline else _client(a)
    hits = []
    for sym in symbols:
        bars = complete_bars_for(cache.get(sym) if a.offline else _five(sym, a, client, cache), cutoff_ms)
        # The tail is the latest window plus the bar that just closed.
        tail = bars[-(a.window_minutes // 5 + 1):] if bars else []
        hit = bx.rolling_break(tail, a.window_minutes, 5, a.side)
        if not hit:
            continue
        hits.append({
            "symbol": sym,
            "setups": ["roll_%dm_%s" % (a.window_minutes, hit["side"])],
            "side": hit["side"],
            "price": hit["price"],
            "at": hit["at"],
            "window_high": hit["or_high"],
            "window_low": hit["or_low"],
            "pct_over": round(hit["pct_over"], 3),
            "warnings": [],
        })
    doc = {"mode": "roll", "as_of": bx.iso_z(now), "window_minutes": a.window_minutes, "hits": hits}
    _write(a.out, doc, not a.no_write)
    print("Rolling %d-minute break at %s UTC. %d of %d symbols closed outside the prior window."
          % (a.window_minutes, bx.iso_z(now), len(hits), len(symbols)))
    for h in hits[: a.show]:
        print("  %-12s %s  %s  window %s to %s  %+.3f%%" % (
            h["symbol"], h["side"], bx.num(h["price"]), bx.num(h["window_low"]), bx.num(h["window_high"]), h["pct_over"]))
    if a.json:
        print(bx.json.dumps(doc, indent=2, sort_keys=True))
    print(bx.disclaimer())
    return 0


def cmd_movers(a):
    if a.offline:
        rows = bx.load_ticker_list(bx.read_json(a.offline))
    else:
        doc = bx.fetch_ticker_24hr(_client(a))
        rows = doc if isinstance(doc, list) else [doc]
    kept = []
    for row in rows:
        sym = row.get("symbol") or ""
        if not sym.endswith("USDT") or "_" in sym:
            continue
        try:
            qv = float(row.get("quoteVolume") or 0)
            chg = float(row.get("priceChangePercent") or 0)
            high = float(row.get("highPrice") or 0)
            low = float(row.get("lowPrice") or 0)
            last = float(row.get("lastPrice") or 0)
        except (TypeError, ValueError):
            continue
        if qv < a.min_quote_volume:
            continue
        warnings = bx.pump_dump(sym, chg, high, low, qv)
        kept.append({
            "symbol": sym, "change_pct": chg, "last": last, "quote_volume": qv,
            "high": high, "low": low, "warnings": warnings,
        })
    gainers = sorted(kept, key=lambda r: -r["change_pct"])[: a.top]
    losers = sorted(kept, key=lambda r: r["change_pct"])[: a.top]
    doc = {"mode": "movers", "gainers": gainers, "losers": losers, "universe": len(kept)}
    _write(a.out, doc, not a.no_write)
    print("24h movers, quote volume at least %s USDT. %d symbols." % (bx.num(a.min_quote_volume), len(kept)))
    print("Gainers:")
    _print_movers(gainers)
    print("Losers:")
    _print_movers(losers)
    if a.json:
        print(bx.json.dumps(doc, indent=2, sort_keys=True))
    print(bx.disclaimer())
    return 0


def _print_movers(rows):
    for r in rows:
        flag = ("  " + ",".join(r["warnings"])) if r["warnings"] else ""
        print("  %+8.2f%%  %-14s %s  quote %s%s" % (
            r["change_pct"], r["symbol"], bx.num(r["last"]), bx.num(r["quote_volume"]), flag))


def _hhmm(text):
    parts = str(text).strip().split(":")
    if len(parts) != 2:
        raise bx.BinanceError("time wants HH:MM, got %r" % text)
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise bx.BinanceError("time wants HH:MM, got %r" % text)
    return hh, mm


def _add_common(p):
    p.add_argument("--symbols", help="comma list, SOL or SOLUSDT")
    p.add_argument("--offline", help="kline JSON, or a folder of them")
    p.add_argument("--now", help="UTC time, 2026-10-26 00:20")
    p.add_argument("--out", default="trader/watchlist.json")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--show", type=int, default=20)
    p.add_argument("--pause", type=float, default=bx.PAUSE)
    p.add_argument("--limit", type=int, default=500)


def main(argv=None):
    p = argparse.ArgumentParser(prog="scan.py", description="USDT-M chart scanner. Not financial advice.",
                                epilog=bx.disclaimer())
    sub = p.add_subparsers(dest="cmd")

    u = sub.add_parser("universe", help="list TRADING USDT-M perpetuals")
    u.add_argument("--offline", help="saved exchangeInfo JSON")
    u.add_argument("--out", default="trader/universe.txt")
    u.add_argument("--no-write", action="store_true")
    u.add_argument("--json", action="store_true")
    u.add_argument("--show", type=int, default=20)
    u.add_argument("--pause", type=float, default=bx.PAUSE)

    d = sub.add_parser("daily", help="20-day breakouts, flags, RSI on daily closes")
    _add_common(d)
    d.add_argument("--min-quote-volume", type=float, default=0)

    o = sub.add_parser("orb", help="first close outside the UTC day's opening range")
    _add_common(o)
    o.add_argument("--or-minutes", type=int, default=15)
    o.add_argument("--at", help="cutoff HH:MM UTC")
    o.add_argument("--date", help="UTC date YYYY-MM-DD")
    o.add_argument("--side", choices=["both", "buy", "sell"], default="both")
    o.add_argument("--tickers", help="optional 24hr ticker JSON, for pump warnings")

    r = sub.add_parser("roll", help="close outside the prior N minutes")
    _add_common(r)
    r.add_argument("--window-minutes", type=int, default=60)
    r.add_argument("--side", choices=["both", "buy", "sell"], default="both")

    m = sub.add_parser("movers", help="top 24h gainers and losers")
    m.add_argument("--offline", help="ticker/24hr JSON")
    m.add_argument("--min-quote-volume", type=float, default=DEFAULT_MIN_QUOTE)
    m.add_argument("--top", type=int, default=15)
    m.add_argument("--out", default="trader/watchlist.json")
    m.add_argument("--no-write", action="store_true")
    m.add_argument("--json", action="store_true")
    m.add_argument("--pause", type=float, default=bx.PAUSE)

    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    try:
        if a.cmd == "universe":
            return cmd_universe(a)
        if a.cmd == "daily":
            return cmd_daily(a)
        if a.cmd == "orb":
            return cmd_orb(a)
        if a.cmd == "roll":
            return cmd_roll(a)
        if a.cmd == "movers":
            return cmd_movers(a)
    except bx.BinanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
