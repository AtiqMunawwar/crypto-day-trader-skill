#!/usr/bin/env python3
"""Binance USDT-M order tickets. Paper by default. Not financial advice.

Prints the exact requests. Sends nothing unless every gate is open: config
paper is false, you passed --live, BINANCE_API_KEY and BINANCE_API_SECRET are
in the environment (never in a file in this repo), trader-risk passed this
exact order today, and you typed yes for it.

    python3 ticket.py show --orders trader/orders.json --risk trader/state/risk.json
    python3 ticket.py approve --index 1 --reply yes
    python3 ticket.py send --index 1 --reply yes --live
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_public as bx  # noqa: E402

FAPI = "https://fapi.binance.com"


def ticket_lines(order):
    """Human and machine form of one bracket. Paper text, not a sent request."""
    symbol = order["symbol"]
    margin = order.get("marginType") or "ISOLATED"
    leverage = order.get("leverage") or 1
    lines = []
    lines.append("POST /fapi/v1/marginType  symbol=%s marginType=%s" % (symbol, margin))
    lines.append("POST /fapi/v1/leverage  symbol=%s leverage=%s" % (symbol, leverage))
    lines.append("POST /fapi/v1/order  symbol=%s side=%s type=%s quantity=%s newOrderRespType=RESULT" % (
        symbol, order.get("side"), order.get("type") or "MARKET", order.get("quantity")))
    if order.get("stopPrice") and not order.get("reduceOnly"):
        lines.append(
            "POST /fapi/v1/order  symbol=%s side=%s type=STOP_MARKET stopPrice=%s closePosition=true workingType=%s"
            % (symbol, order.get("exit_side") or "SELL", order.get("stopPrice"), order.get("workingType") or "MARK_PRICE"))
        lines.append(
            "POST /fapi/v1/order  symbol=%s side=%s type=TAKE_PROFIT_MARKET stopPrice=%s closePosition=true workingType=%s"
            % (symbol, order.get("exit_side") or "SELL", order.get("takeProfitPrice"), order.get("workingType") or "MARK_PRICE"))
    return lines


def describe(order, ready):
    print("---")
    print("%s %s %s" % (order.get("side"), order.get("quantity"), order.get("symbol")))
    print("Status: %s" % ("READY for a yes" if ready else "NOT READY"))
    for line in ticket_lines(order):
        print("  " + line)
    if order.get("max_loss_usdt") is not None:
        print("Max loss if the stop fills at %s: %s USDT." % (order.get("stopPrice"), order.get("max_loss_usdt")))
    if order.get("liquidation_price"):
        print("Liquidation estimate: %s. Margin %s at %sx." % (
            order.get("liquidation_price"), order.get("margin_usdt"), order.get("leverage")))
    if order.get("funding_cost_usdt") is not None:
        print("Funding over the hold if the rate holds: %s USDT (positive means you pay)." % order.get("funding_cost_usdt"))
    print("Fingerprint %s" % (order.get("fingerprint") or bx.fingerprint(order)))
    print("Paper default: this text is the order. It has not been sent.")


def _passed(risk_doc, fingerprint, day):
    if not risk_doc:
        return False
    if risk_doc.get("day") not in (None, day):
        return False
    for row in risk_doc.get("results") or []:
        if row.get("fingerprint") == fingerprint and row.get("status") == "PASS" and row.get("day") in (None, day):
            return True
    return False


def show(orders, risk_doc, day):
    ready_any = False
    for i, order in enumerate(orders, start=1):
        fp = order.get("fingerprint") or bx.fingerprint(order)
        ready = _passed(risk_doc, fp, day)
        ready_any = ready_any or ready
        print("Ticket %d" % i)
        describe(order, ready)
    print(bx.disclaimer())
    return ready_any


def live_blockers(cfg, reply, env, risk_ok, live_flag):
    reasons = []
    if not live_flag:
        reasons.append("pass --live as well as a yes. The flag is the extra confirmation.")
    if cfg.get("paper", True):
        reasons.append("config paper is true. Refusing to send.")
    key = env.get("BINANCE_API_KEY") or ""
    secret = env.get("BINANCE_API_SECRET") or ""
    if not key or not secret:
        reasons.append("set BINANCE_API_KEY and BINANCE_API_SECRET in the environment. They are not read from the repo.")
    if not bx.yes_reply(reply):
        reasons.append("the reply is not an explicit yes for this order.")
    if not risk_ok:
        reasons.append("trader-risk has not passed this exact order today.")
    return reasons


def sign_query(params, secret):
    """HMAC-SHA256 of the urlencoded query. The secret is not part of the query."""
    query = urllib.parse.urlencode(list(params.items()))
    digest = hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
    return query + "&signature=" + digest


def entry_params(order, timestamp):
    return {
        "symbol": order["symbol"],
        "side": order["side"],
        "type": order.get("type") or "MARKET",
        "quantity": str(order["quantity"]),
        "newOrderRespType": "RESULT",
        "recvWindow": "5000",
        "timestamp": str(int(timestamp)),
    }


def post_signed(path, params, key, secret, opener=None):
    opener = opener or urllib.request.urlopen
    query = sign_query(params, secret)
    if secret and secret in query.replace("signature=", ""):
        # urlencoding could theoretically embed the secret if a param held it. Refuse.
        raise bx.BinanceError("refusing to send: the secret would appear outside the signature")
    url = FAPI + path + "?" + query
    req = urllib.request.Request(url, data=b"", method="POST", headers={"X-MBX-APIKEY": key})
    try:
        with opener(req, timeout=20) as resp:
            body = resp.read()
            if isinstance(body, bytes):
                body = body.decode("utf-8", "replace")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        raise bx.BinanceError("Binance answered HTTP %s. %s" % (e.code, detail))


def _load_state(path):
    if os.path.exists(path):
        doc = bx.read_json(path)
        if isinstance(doc, dict):
            doc.setdefault("approvals", [])
            doc.setdefault("placed", [])
            return doc
    return {"approvals": [], "placed": []}


def cmd_show(a):
    doc = bx.read_json(a.orders)
    risk = bx.read_json(a.risk) if os.path.exists(a.risk) else {}
    now = bx.parse_now(a.now)
    show(doc.get("orders") or [], risk, now.date().isoformat())
    return 0


def _order_at(doc, index):
    orders = doc.get("orders") or []
    if index < 1 or index > len(orders):
        raise bx.BinanceError("no ticket %s" % index)
    return orders[index - 1]


def cmd_approve(a):
    doc = bx.read_json(a.orders)
    order = _order_at(doc, a.index)
    now = bx.parse_now(a.now)
    fp = order.get("fingerprint") or bx.fingerprint(order)
    state = _load_state(a.state)
    if not bx.yes_reply(a.reply):
        state["approvals"].append({"fingerprint": fp, "reply": a.reply, "accepted": False, "at": bx.iso_z(now)})
        bx.write_json(a.state, state)
        print("Not a yes. Nothing was approved.")
        print(bx.disclaimer())
        return 1
    state["approvals"].append({
        "fingerprint": fp, "reply": a.reply, "accepted": True, "at": bx.iso_z(now),
        "day": now.date().isoformat(),
    })
    bx.write_json(a.state, state)
    print("Recorded yes for %s. Paper mode still does not send it." % fp)
    print("To send live you also need paper false, --live, the two environment variables, and a PASS today.")
    print(bx.disclaimer())
    return 0


def cmd_send(a):
    doc = bx.read_json(a.orders)
    order = _order_at(doc, a.index)
    cfg = bx.load_config(a.config)
    now = bx.parse_now(a.now)
    day = now.date().isoformat()
    risk = bx.read_json(a.risk) if os.path.exists(a.risk) else {}
    fp = order.get("fingerprint") or bx.fingerprint(order)
    reasons = live_blockers(cfg, a.reply, os.environ, _passed(risk, fp, day), a.live)
    if reasons:
        print("NOT SENT.")
        for reason in reasons:
            print("  - %s" % reason)
        print(bx.disclaimer())
        return 1
    # All gates open. Still confirm the user understands this hits the live endpoint.
    key = os.environ["BINANCE_API_KEY"]
    secret = os.environ["BINANCE_API_SECRET"]
    stamp = int(time.time() * 1000)
    params = entry_params(order, stamp)
    result = post_signed("/fapi/v1/order", params, key, secret)
    state = _load_state(a.state)
    state["placed"].append({
        "fingerprint": fp, "day": day, "reduceOnly": bool(order.get("reduceOnly")),
        "max_loss_usdt": order.get("max_loss_usdt"), "result": result, "at": bx.iso_z(now),
    })
    bx.write_json(a.state, state)
    print("Sent the entry order. Response order id: %s" % (result.get("orderId"),))
    print("Stop and target are NOT sent automatically in this build after a live entry. Place them from the ticket if the entry filled.")
    print(bx.disclaimer())
    return 0


def cmd_flatten(a):
    symbol = bx.normalize_symbol(a.symbol)
    side = "SELL" if a.side in ("sell", "long-exit") else "BUY" if a.side in ("buy", "short-exit") else a.side.upper()
    order = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": bx.dec_str(a.qty),
        "reduceOnly": True,
        "leverage": 1,
        "marginType": "ISOLATED",
        "paper": True,
        "warnings": [],
    }
    order["fingerprint"] = bx.fingerprint(order)
    doc = {"paper": True, "exchange": "binance-usdt-m", "orders": [order]}
    if a.write:
        bx.write_json(a.write if isinstance(a.write, str) else "trader/orders.json", doc)
    describe(order, False)
    print("Flatten ticket written. Run trader-risk, then approve. Getting out still needs your yes.")
    print(bx.disclaimer())
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="ticket.py", description="Order tickets. Not financial advice.",
                                epilog=bx.disclaimer())
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("show")
    s.add_argument("--orders", default="trader/orders.json")
    s.add_argument("--risk", default="trader/state/risk.json")
    s.add_argument("--now")
    ap = sub.add_parser("approve")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--index", type=int, default=1)
    ap.add_argument("--reply", required=True)
    ap.add_argument("--state", default="trader/state/broker.json")
    ap.add_argument("--now")
    sd = sub.add_parser("send")
    sd.add_argument("--orders", default="trader/orders.json")
    sd.add_argument("--index", type=int, default=1)
    sd.add_argument("--reply", required=True)
    sd.add_argument("--live", action="store_true")
    sd.add_argument("--config", default="trader/config.json")
    sd.add_argument("--risk", default="trader/state/risk.json")
    sd.add_argument("--state", default="trader/state/broker.json")
    sd.add_argument("--now")
    fl = sub.add_parser("flatten")
    fl.add_argument("--symbol", required=True)
    fl.add_argument("--qty", required=True)
    fl.add_argument("--side", default="sell", help="sell closes a long; buy closes a short")
    fl.add_argument("--write", nargs="?", const="trader/orders.json")
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    try:
        if a.cmd == "show":
            return cmd_show(a)
        if a.cmd == "approve":
            return cmd_approve(a)
        if a.cmd == "send":
            return cmd_send(a)
        if a.cmd == "flatten":
            return cmd_flatten(a)
    except bx.BinanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
