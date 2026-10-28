#!/usr/bin/env python3
"""Price and time alerts against Binance marks. Not financial advice.

An alert only prints. It never sells, never moves a stop, and never places
an order. Each rule rings once.

    python3 alerts.py add --symbol SOLUSDT --below 90
    python3 alerts.py add --from-plan
    python3 alerts.py check --offline ticker.json --now "2026-10-26 12:00"
    python3 alerts.py quote SOLUSDT --offline ticker.json --write
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_public as bx  # noqa: E402


def _load_rules(path):
    if not os.path.exists(path):
        return {"rules": []}
    doc = bx.read_json(path)
    if isinstance(doc, list):
        return {"rules": doc}
    doc.setdefault("rules", [])
    return doc


def _save_rules(path, doc):
    bx.write_json(path, doc)


def _state(path):
    if os.path.exists(path):
        doc = bx.read_json(path)
        if isinstance(doc, dict):
            doc.setdefault("fired", {})
            return doc
    return {"fired": {}}


def _next_id(rules):
    used = {r.get("id") for r in rules}
    n = 1
    while "a%d" % n in used:
        n += 1
    return "a%d" % n


def add_rule(doc, rule):
    rule = dict(rule)
    rule.setdefault("id", _next_id(doc["rules"]))
    doc["rules"].append(rule)
    return rule


def from_plan(plan):
    symbol = plan["symbol"]
    side = str(plan.get("side") or "buy")
    rules = [
        {"symbol": symbol, "type": "stop", "price": float(plan["stop"]), "side": side,
         "note": "plan stop"},
        {"symbol": symbol, "type": "target", "price": float(plan["target"]), "side": side,
         "note": "plan target"},
    ]
    if plan.get("as_of") and plan.get("hold_hours"):
        start = bx.parse_now(plan["as_of"])
        end = start + datetime.timedelta(hours=float(plan["hold_hours"]))
        rules.append({"symbol": symbol, "type": "time", "at": bx.iso_z(end),
                      "note": "hold window ends"})
    return rules


def fired(rule, price, now):
    kind = rule.get("type")
    if kind == "time":
        return now >= bx.parse_now(rule.get("at"))
    if price is None:
        return False
    level = float(rule.get("price"))
    side = str(rule.get("side") or "buy")
    long = side in ("buy", "long", "BUY")
    if kind == "above":
        return price >= level
    if kind == "below":
        return price <= level
    if kind == "stop":
        return price <= level if long else price >= level
    if kind == "target":
        return price >= level if long else price <= level
    if kind == "pct_move":
        ref = float(rule.get("ref"))
        pct = float(rule.get("pct"))
        if ref <= 0:
            return False
        return abs(price / ref - 1.0) * 100.0 >= pct
    return False


def price_of(symbol, ticker_doc):
    rows = bx.load_ticker_list(ticker_doc)
    for row in rows:
        if row.get("symbol") == symbol:
            return float(row.get("lastPrice") or row.get("markPrice") or row.get("price"))
    # a bare {"SOLUSDT": 100} is not a ticker. Allow {"prices": {"SOLUSDT": 1}}.
    if isinstance(ticker_doc, dict):
        prices = ticker_doc.get("prices") or {}
        if symbol in prices:
            row = prices[symbol]
            if isinstance(row, dict):
                return float(row.get("price"))
            return float(row)
    raise bx.BinanceError("no price for %s" % symbol)


def check_rules(rules, prices, now, state):
    """prices: {symbol: float}. Returns the rules that ring this pass."""
    rung = []
    for rule in rules:
        ident = rule.get("id")
        if not ident or ident in state["fired"]:
            continue
        symbol = rule.get("symbol")
        px = prices.get(symbol) if symbol else None
        if rule.get("type") != "time" and px is None:
            continue
        if fired(rule, px, now):
            state["fired"][ident] = bx.iso_z(now)
            rung.append(dict(rule, price_now=px))
    return rung


def cmd_add(a):
    doc = _load_rules(a.file)
    made = []
    if a.from_plan:
        plan = bx.read_json(a.plan)
        for rule in from_plan(plan):
            made.append(add_rule(doc, rule))
    if a.above is not None:
        made.append(add_rule(doc, {"symbol": bx.normalize_symbol(a.symbol), "type": "above", "price": a.above}))
    if a.below is not None:
        made.append(add_rule(doc, {"symbol": bx.normalize_symbol(a.symbol), "type": "below", "price": a.below}))
    if a.pct is not None:
        if a.ref is None:
            raise bx.BinanceError("a percent alert needs --ref, the price you measure from")
        made.append(add_rule(doc, {"symbol": bx.normalize_symbol(a.symbol), "type": "pct_move",
                                   "pct": a.pct, "ref": a.ref}))
    if a.at:
        made.append(add_rule(doc, {"symbol": bx.normalize_symbol(a.symbol) if a.symbol else None,
                                   "type": "time", "at": a.at, "note": a.label or "time"}))
    if not made:
        raise bx.BinanceError("nothing to add. Use --from-plan, --above, --below, --pct or --at.")
    _save_rules(a.file, doc)
    for rule in made:
        print("Added %s %s" % (rule["id"], rule["type"]))
    print(bx.disclaimer())
    return 0


def _prices_from(a, symbols):
    if a.offline:
        doc = bx.read_json(a.offline)
        return {sym: price_of(sym, doc) for sym in symbols}
    client = bx.Client(pause=a.pause)
    out = {}
    for sym in symbols:
        row = bx.fetch_ticker_24hr(client, sym)
        if isinstance(row, list):
            row = row[0]
        out[sym] = float(row["lastPrice"])
    return out


def cmd_check(a):
    doc = _load_rules(a.file)
    now = bx.parse_now(a.now)
    symbols = sorted({r.get("symbol") for r in doc["rules"] if r.get("symbol")})
    prices = _prices_from(a, symbols) if symbols else {}
    state = _state(a.state)
    rung = check_rules(doc["rules"], prices, now, state)
    if not a.no_write:
        bx.write_json(a.state, state)
    if not rung:
        print("Quiet. Nothing new at %s UTC." % bx.iso_z(now))
    for rule in rung:
        print("RING %s %s %s at %s" % (rule["id"], rule.get("symbol") or "", rule["type"], bx.num(rule.get("price_now"))))
        if rule.get("note"):
            print("  %s" % rule["note"])
    print(bx.disclaimer())
    if a.json:
        print(bx.json.dumps({"rung": rung, "prices": prices}, indent=2, sort_keys=True, default=str))
    return 0


def cmd_quote(a):
    symbol = bx.normalize_symbol(a.symbol)
    prices = _prices_from(a, [symbol])
    doc = {"as_of": bx.iso_z(bx.parse_now(a.now)), "prices": {symbol: {"price": prices[symbol], "source": "offline" if a.offline else "binance"}}}
    print("%s %s" % (symbol, bx.num(prices[symbol])))
    if a.write:
        path = a.write if isinstance(a.write, str) else "trader/prices.json"
        bx.write_json(path, doc)
        print("Wrote %s" % path)
    print(bx.disclaimer())
    return 0


def cmd_list(a):
    doc = _load_rules(a.file)
    state = _state(a.state)
    if not doc["rules"]:
        print("No alerts yet.")
    for rule in doc["rules"]:
        mark = "fired" if rule.get("id") in state["fired"] else "armed"
        print("%s  %s  %s  %s" % (rule.get("id"), mark, rule.get("type"), rule.get("symbol") or ""))
    print(bx.disclaimer())
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="alerts.py", description="Price alerts. Not financial advice.",
                                epilog=bx.disclaimer())
    sub = p.add_subparsers(dest="cmd")
    ad = sub.add_parser("add")
    ad.add_argument("--file", default="trader/alerts.json")
    ad.add_argument("--plan", default="trader/plan.json")
    ad.add_argument("--from-plan", action="store_true")
    ad.add_argument("--symbol")
    ad.add_argument("--above", type=float)
    ad.add_argument("--below", type=float)
    ad.add_argument("--pct", type=float)
    ad.add_argument("--ref", type=float)
    ad.add_argument("--at", help="UTC time the alert should ring")
    ad.add_argument("--label")
    ch = sub.add_parser("check")
    ch.add_argument("--file", default="trader/alerts.json")
    ch.add_argument("--state", default="trader/state/alerts.json")
    ch.add_argument("--offline")
    ch.add_argument("--now")
    ch.add_argument("--pause", type=float, default=bx.PAUSE)
    ch.add_argument("--no-write", action="store_true")
    ch.add_argument("--json", action="store_true")
    q = sub.add_parser("quote")
    q.add_argument("symbol")
    q.add_argument("--offline")
    q.add_argument("--now")
    q.add_argument("--pause", type=float, default=bx.PAUSE)
    q.add_argument("--write", nargs="?", const="trader/prices.json")
    ls = sub.add_parser("list")
    ls.add_argument("--file", default="trader/alerts.json")
    ls.add_argument("--state", default="trader/state/alerts.json")
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    try:
        if a.cmd == "add":
            return cmd_add(a)
        if a.cmd == "check":
            return cmd_check(a)
        if a.cmd == "quote":
            return cmd_quote(a)
        if a.cmd == "list":
            return cmd_list(a)
    except bx.BinanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
