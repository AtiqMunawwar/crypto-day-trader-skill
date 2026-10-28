#!/usr/bin/env python3
"""Risk gate for a USDT-M order. Not financial advice.

Prints PASS or BLOCK. A BLOCK does not go to the broker. Getting out
(reduceOnly) is not stopped by the daily trade cap. Nothing is sent.

    python3 check.py --orders trader/orders.json --config trader-budget/config.example.json
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_public as bx  # noqa: E402


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _day(now):
    return now.date().isoformat()


def _entries_today(state, day):
    count = 0
    loss = 0.0
    for rec in (state or {}).get("placed") or []:
        if str(rec.get("day") or "") != day:
            continue
        if rec.get("reduceOnly"):
            continue
        count += 1
        loss += float(rec.get("max_loss_usdt") or 0)
    return count, loss


def check_order(order, cfg, now, state, seen):
    reasons = []
    symbol = str(order.get("symbol") or "")
    try:
        norm = bx.normalize_symbol(symbol)
    except bx.BinanceError as e:
        return ["BLOCK"], [str(e)]
    if norm != symbol:
        reasons.append("symbol should be the full perpetual, %s" % norm)
    if symbol in (cfg.get("blocked") or []):
        reasons.append("%s is on the blocked list" % symbol)
    reduce_only = bool(order.get("reduceOnly"))
    side = str(order.get("side") or "").upper()
    if side not in ("BUY", "SELL"):
        reasons.append("side must be BUY or SELL")
    opening_short = side == "SELL" and not reduce_only
    if opening_short and not cfg.get("allow_short"):
        reasons.append("shorts are off (allow_short is false)")
    if not reduce_only and cfg.get("require_stop", True) and not order.get("stopPrice"):
        reasons.append("a stop is required")
    entry = _f(order.get("entry_price"))
    stop = _f(order.get("stopPrice"))
    target = _f(order.get("takeProfitPrice"))
    qty = _f(order.get("quantity"))
    leverage = _f(order.get("leverage")) or 1
    if not reduce_only:
        if entry is None or stop is None or qty is None or entry <= 0 or qty <= 0:
            reasons.append("entry, stop and quantity must be numbers above 0")
        else:
            long = side == "BUY"
            if long and not stop < entry:
                reasons.append("a long stop goes under the entry")
            if not long and stop and not stop > entry:
                reasons.append("a short stop goes above the entry")
            if target is not None:
                reward = (target - entry) if long else (entry - target)
                risk = abs(entry - stop)
                if reward <= 0 or risk <= 0:
                    reasons.append("target is on the wrong side")
                else:
                    rr = reward / risk
                    if rr + 1e-9 < float(cfg.get("min_reward_risk") or 1):
                        reasons.append("reward:risk %s is under %s" % (round(rr, 3), cfg.get("min_reward_risk")))
            loss = qty * abs(entry - stop)
            cap = float(cfg.get("max_loss_per_trade_usd") or 0)
            pct = cfg.get("risk_pct_per_trade")
            budget = float(cfg.get("budget") or 0)
            if pct is not None and budget:
                cap = min(cap, budget * float(pct) / 100.0) if cap else budget * float(pct) / 100.0
            if cap and loss > cap * 1.001:
                reasons.append("loss at the stop is %s USDT, over the %s cap" % (round(loss, 4), cap))
            lev_cap, _sym = bx.leverage_cap(symbol, cfg)
            if leverage > lev_cap + 1e-9:
                reasons.append("leverage %s is above the cap %s" % (leverage, lev_cap))
            if leverage < 1:
                reasons.append("leverage must be at least 1")
            mode = str(order.get("marginType") or "ISOLATED").upper()
            if mode == "CROSS" and not cfg.get("allow_cross"):
                reasons.append("cross margin is off")
            liq = _f(order.get("liquidation_price"))
            buffer = float(cfg.get("liq_buffer_pct") or 0) / 100.0
            if liq is not None and stop is not None:
                if long and not liq < stop * (1 - buffer):
                    reasons.append("liquidation estimate %s is not beyond the stop %s" % (liq, stop))
                if not long and not liq > stop * (1 + buffer):
                    reasons.append("liquidation estimate %s is not beyond the stop %s" % (liq, stop))
            warnings = order.get("warnings") or []
            volatile = any(w in warnings for w in ("pump", "dump", "small_cap_volatile", "thin_move"))
            if volatile and not cfg.get("allow_volatile"):
                reasons.append("pump/dump warning on a small-cap. Set allow_volatile only if you accept that.")
            if "funding_cost_above_reward" in warnings and cfg.get("funding_cost_blocks_when_above_reward", True):
                reasons.append("estimated funding you pay is larger than the target")
            if leverage > float(cfg.get("volatile_max_leverage") or leverage) and volatile:
                reasons.append("leverage is above volatile_max_leverage")
            margin = _f(order.get("margin_usdt"))
            spendable = budget * (1 - float(cfg.get("cash_buffer_pct") or 0) / 100.0)
            max_margin = budget * float(cfg.get("max_margin_pct") or 100) / 100.0
            if margin is not None and margin > max_margin * 1.001:
                reasons.append("margin %s is over max_margin_pct" % margin)
            if margin is not None and margin > spendable * 1.001:
                reasons.append("margin %s is over the cash buffer" % margin)
            hold = _f(order.get("hold_hours"))
            max_hold = _f(cfg.get("max_hold_hours"))
            if hold is not None and max_hold is not None and hold > max_hold + 1e-9:
                reasons.append("hold %sh is over max_hold_hours %s" % (hold, max_hold))
    fp = order.get("fingerprint") or bx.fingerprint(order)
    if fp in seen:
        reasons.append("duplicate order %s" % fp)
    seen.add(fp)
    day = _day(now)
    count, loss_today = _entries_today(state, day)
    if not reduce_only:
        if count >= int(cfg.get("max_trades_per_day") or 1000):
            reasons.append("already at max_trades_per_day")
        this_loss = _f(order.get("max_loss_usdt")) or 0
        daily_cap = float(cfg.get("max_daily_loss_usd") or 0)
        if daily_cap and loss_today + this_loss > daily_cap * 1.001:
            reasons.append("this trade would put today's planned loss over %s" % daily_cap)
    if cfg.get("paper", True) and order.get("paper") is False:
        reasons.append("config is paper, this order says live")
    status = "BLOCK" if reasons else "PASS"
    return [status], reasons


def check_all(doc, cfg, now, state=None):
    orders = doc.get("orders") if isinstance(doc, dict) else doc
    if not isinstance(orders, list):
        raise bx.BinanceError("orders file needs an orders list")
    state = state or {}
    seen = set()
    results = []
    for order in orders:
        status, reasons = check_order(order, cfg, now, state, seen)
        results.append({
            "symbol": order.get("symbol"),
            "fingerprint": order.get("fingerprint") or bx.fingerprint(order),
            "status": status[0],
            "reasons": reasons,
            "day": _day(now),
            "reduceOnly": bool(order.get("reduceOnly")),
        })
    return results


def report(results):
    for row in results:
        if row["status"] == "PASS":
            print("PASS %s %s" % (row["symbol"], row["fingerprint"]))
        else:
            print("BLOCK %s %s" % (row["symbol"], row["fingerprint"]))
            for reason in row["reasons"]:
                print("  - %s" % reason)
    print(bx.disclaimer())


def main(argv=None):
    p = argparse.ArgumentParser(prog="check.py", description="Risk check. Not financial advice.",
                                epilog=bx.disclaimer())
    p.add_argument("--orders", default="trader/orders.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p.add_argument("--out", default="trader/state/risk.json")
    p.add_argument("--now")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        doc = bx.read_json(a.orders)
        example = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "trader-budget", "config.example.json")
        cfg = bx.load_config(a.config, example if os.path.exists(example) else None)
        if not cfg:
            raise bx.BinanceError("no config")
        state = bx.read_json(a.state) if os.path.exists(a.state) else {}
        now = bx.parse_now(a.now)
        results = check_all(doc, cfg, now, state)
        out = {"as_of": bx.iso_z(now), "day": now.date().isoformat(), "results": results}
        if not a.no_write:
            bx.write_json(a.out, out)
        report(results)
        if a.json:
            print(bx.json.dumps(out, indent=2, sort_keys=True))
        return 0 if all(r["status"] == "PASS" for r in results) else 1
    except bx.BinanceError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
