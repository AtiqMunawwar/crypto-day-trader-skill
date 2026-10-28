#!/usr/bin/env python3
"""Entry timer for a USDT-M setup. Not financial advice.

Waits for a completed bar. A wick through the level is not an entry. A close
through it is. Writes trader/entry.json. Never places an order.

    python3 entry.py check SOLUSDT --setup utc-orb --now "2026-10-26 00:20" --offline FILE
    python3 entry.py check SOLUSDT --setup level --level 102.5 --side buy
    python3 entry.py watch SOLUSDT --setup utc-orb --offline FILE --now "2026-10-26 02:00"
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_public as bx  # noqa: E402


def _hhmm(text):
    parts = str(text).strip().split(":")
    if len(parts) != 2:
        raise bx.BinanceError("time wants HH:MM")
    return int(parts[0]), int(parts[1])


def _cutoff(a):
    now = bx.parse_now(a.now)
    if a.at:
        hh, mm = _hhmm(a.at)
        day = datetime.datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else now.date()
        cutoff = datetime.datetime(day.year, day.month, day.day, hh, mm)
    else:
        cutoff = now.replace(second=0, microsecond=0)
    return cutoff


def _bars(a, symbol):
    if a.offline:
        data = bx.load_klines(a.offline)
        if symbol not in data:
            raise bx.BinanceError("no %s bars in %s" % (symbol, a.offline))
        return data[symbol]
    client = bx.Client(pause=a.pause)
    bars, src = bx.fetch_klines(client, symbol, a.interval, limit=a.limit)
    return bars


def evaluate(bars, cutoff, setup, side, or_minutes, level, interval, valid_hours):
    """Pure. Returns a status dict: waiting, triggered, not_ready, expired."""
    interval_min = 1 if interval == "1m" else 5
    step = interval_min * 60 * 1000
    cutoff_ms = bx.utc_ms(cutoff)
    done = bx.complete_bars(bars, cutoff_ms)
    day = cutoff.date()
    day_bars = bx.bars_on_utc_day(done, day)
    base = {
        "date": day.isoformat(),
        "interval": interval,
        "setup": setup,
        "as_of": bx.iso_z(cutoff),
    }
    if setup == "level":
        if level is None:
            raise bx.BinanceError("a level setup needs --level")
        if not day_bars:
            base.update({"status": "not_ready", "reason": "no completed bars yet this UTC day"})
            return base
        for i, b in enumerate(day_bars):
            up = b["close"] > level and side in ("buy", "both")
            down = b["close"] < level and side in ("sell", "both")
            if up or down:
                which = "buy" if up else "sell"
                close_dt = bx.ms_to_utc(b["close_ms"] + 1)
                base.update({
                    "status": "triggered", "side": which, "price": b["close"],
                    "level": level, "bar_index": i, "at": close_dt.strftime("%H:%M"),
                    "open_ms": b["open_ms"],
                })
                return base
        last = day_bars[-1]
        base.update({
            "status": "waiting", "side": side, "level": level, "price": last["close"],
            "distance_pct": (last["close"] / float(level) - 1.0) * 100.0,
        })
        return base

    opening = bx.utc_opening_range(day_bars, or_minutes, interval_min)
    if opening is None:
        base.update({"status": "not_ready",
                     "reason": "the %d-minute UTC opening range is not complete" % or_minutes})
        return base
    valid_until = day_bars[0]["open_ms"] + (opening["bars"] * step) + int(valid_hours * 3600 * 1000)
    hit = bx.first_break(day_bars, opening, side=side)
    if hit:
        base.update({
            "status": "triggered", "side": hit["side"], "price": hit["price"], "at": hit["at"],
            "bar_index": hit["index"], "or_high": hit["or_high"], "or_low": hit["or_low"],
            "or_minutes": or_minutes, "pct_over": round(hit["pct_over"], 3),
            "open_ms": hit["open_ms"],
        })
        return base
    if cutoff_ms > valid_until:
        base.update({
            "status": "expired", "side": side, "or_high": opening["high"], "or_low": opening["low"],
            "or_minutes": or_minutes,
            "reason": "no close outside the range within %g hours" % valid_hours,
        })
        return base
    last = day_bars[-1]
    base.update({
        "status": "waiting", "side": side, "price": last["close"],
        "or_high": opening["high"], "or_low": opening["low"], "or_minutes": or_minutes,
        "above_high_pct": (last["close"] / opening["high"] - 1.0) * 100.0,
        "below_low_pct": (last["close"] / opening["low"] - 1.0) * 100.0,
    })
    return base


def _say(result):
    status = result["status"]
    if status == "triggered":
        print("TRIGGERED %s %s at %s UTC, close %s." % (
            result.get("side"), result.get("setup"), result.get("at"), bx.num(result.get("price"))))
        if result.get("or_high") is not None:
            print("Opening range %s to %s. Close is %+.3f%% beyond the level." % (
                bx.num(result["or_low"]), bx.num(result["or_high"]), result.get("pct_over") or 0))
    elif status == "waiting":
        print("WAITING. Last close %s. The bar has not closed through the level." % bx.num(result.get("price")))
        if result.get("or_high") is not None:
            print("Range %s to %s." % (bx.num(result["or_low"]), bx.num(result["or_high"])))
    elif status == "expired":
        print("EXPIRED. %s" % result.get("reason"))
    else:
        print("NOT READY. %s" % result.get("reason"))
    print(bx.disclaimer())


def cmd_check(a):
    symbol = bx.normalize_symbol(a.symbol)
    cutoff = _cutoff(a)
    bars = _bars(a, symbol)
    side = "buy" if a.side in ("buy", "long") else "sell" if a.side in ("sell", "short") else a.side
    result = evaluate(bars, cutoff, a.setup, side if a.side != "both" else "both",
                      a.or_minutes, a.level, a.interval, a.valid_hours)
    result["symbol"] = symbol
    if a.side == "both" and result.get("status") == "triggered":
        pass
    elif a.side == "both":
        result["side"] = "both"
    if a.write:
        path = a.write if isinstance(a.write, str) else "trader/entry.json"
        # argparse nargs=? with const gives True-like const. We use const path.
        bx.write_json(path, result)
        print("Wrote %s" % path)
    _say(result)
    if a.json:
        print(bx.json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "triggered" else 0


def cmd_watch(a):
    """Replay completed bars up to --now when offline. Live mode sleeps --every seconds."""
    symbol = bx.normalize_symbol(a.symbol)
    end = _cutoff(a)
    bars = _bars(a, symbol)
    if not (a.offline or a.now):
        raise bx.BinanceError("live watch is a loop: pass --now or --offline to replay, "
                              "or run check on a schedule. This process will not spin without a bound.")
    # Replay from the start of the UTC day, one bar at a time.
    day = end.date()
    day_bars = [b for b in bars if bx.ms_to_utc(b["open_ms"]).date() == day]
    if not day_bars:
        raise bx.BinanceError("no bars on %s" % day.isoformat())
    last = None
    for b in day_bars:
        close_at = bx.ms_to_utc(b["close_ms"] + 1)
        if close_at > end:
            break
        last = evaluate(bars, close_at, a.setup, a.side if a.side != "both" else "both",
                        a.or_minutes, a.level, a.interval, a.valid_hours)
        print("%s  %s  close %s" % (close_at.strftime("%H:%M"), last["status"], bx.num(b["close"])))
        if last["status"] in ("triggered", "expired"):
            last["symbol"] = symbol
            if a.write:
                bx.write_json(a.write if isinstance(a.write, str) else "trader/entry.json", last)
            _say(last)
            if a.json:
                print(bx.json.dumps(last, indent=2, sort_keys=True))
            return 0
    if last:
        last["symbol"] = symbol
        _say(last)
    print(bx.disclaimer())
    return 0


def cmd_watchlist(a):
    doc = bx.read_json(a.watchlist)
    hits = doc.get("hits") or []
    if not hits:
        raise bx.BinanceError("no hits in %s" % a.watchlist)
    code = 0
    for hit in hits:
        a.symbol = hit["symbol"]
        if hit.get("side") in ("buy", "sell"):
            a.side = hit["side"]
        print("--- %s ---" % hit["symbol"])
        cmd_check(a)
    return code


def main(argv=None):
    p = argparse.ArgumentParser(prog="entry.py", description="Entry timer. Not financial advice.",
                                epilog=bx.disclaimer())
    sub = p.add_subparsers(dest="cmd")

    def add(sp, symbol_required=True):
        if symbol_required:
            sp.add_argument("symbol")
        sp.add_argument("--setup", choices=["utc-orb", "level"], default="utc-orb")
        sp.add_argument("--side", choices=["buy", "sell", "long", "short", "both"], default="both")
        sp.add_argument("--level", type=float)
        sp.add_argument("--or-minutes", type=int, default=15)
        sp.add_argument("--interval", choices=["5m", "1m"], default="5m")
        sp.add_argument("--valid-hours", type=float, default=4)
        sp.add_argument("--offline")
        sp.add_argument("--now")
        sp.add_argument("--at", help="cutoff HH:MM UTC")
        sp.add_argument("--date")
        sp.add_argument("--limit", type=int, default=500)
        sp.add_argument("--pause", type=float, default=bx.PAUSE)
        sp.add_argument("--write", nargs="?", const="trader/entry.json")
        sp.add_argument("--json", action="store_true")

    c = sub.add_parser("check")
    add(c)
    w = sub.add_parser("watch")
    add(w)
    wl = sub.add_parser("watchlist")
    add(wl, symbol_required=False)
    wl.add_argument("--watchlist", default="trader/watchlist.json")

    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    try:
        if a.cmd == "check":
            return cmd_check(a)
        if a.cmd == "watch":
            return cmd_watch(a)
        if a.cmd == "watchlist":
            return cmd_watchlist(a)
    except bx.BinanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
