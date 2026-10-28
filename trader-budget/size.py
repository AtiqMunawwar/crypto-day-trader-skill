#!/usr/bin/env python3
"""Size a USDT-M position to a max loss. Not financial advice.

Leverage changes the margin, not the loss at the stop. The quantity is the
most that fits the risk budget, the margin cap, the order cap and the cash
buffer, stepped down to the symbol's lot size. Writes a paper bracket to
trader/orders.json. Nothing is sent.

    python3 size.py --write
    python3 size.py --plan trader/plan.json --config trader-budget/config.example.json --json
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_public as bx  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _example():
    return os.path.join(HERE, "config.example.json")


def build_order(plan, cfg, filters=None, cash=None):
    symbol = bx.normalize_symbol(plan.get("symbol") or "")
    side = str(plan.get("side") or "buy").lower()
    if side in ("long",):
        side = "buy"
    if side in ("short",):
        side = "sell"
    if side == "sell" and not cfg.get("allow_short"):
        raise bx.BinanceError("this config does not allow shorts. Set allow_short true if you mean it.")
    entry = plan["entry"]
    stop = plan["stop"]
    target = plan["target"]
    leverage = int(plan.get("leverage") or cfg.get("max_leverage") or 1)
    cap, _sym = bx.leverage_cap(symbol, cfg)
    if leverage > cap:
        raise bx.BinanceError("plan leverage %s is above the cap %s" % (leverage, cap))
    margin_mode = str(plan.get("margin_mode") or cfg.get("margin_mode") or "isolated").lower()
    if margin_mode == "cross" and not cfg.get("allow_cross"):
        raise bx.BinanceError("cross margin is off in config")
    risk_cap = float(cfg.get("max_loss_per_trade_usd") or 0)
    risk_pct = cfg.get("risk_pct_per_trade")
    budget = float(cfg.get("budget") or 0)
    if risk_pct is not None and budget:
        by_pct = budget * float(risk_pct) / 100.0
        risk_cap = by_pct if not risk_cap else min(risk_cap, by_pct)
    if risk_cap <= 0:
        raise bx.BinanceError("config needs max_loss_per_trade_usd or risk_pct_per_trade")
    if filters is None:
        filters = {
            "tick": str(plan.get("tick") or "0.01"),
            "step": str(plan.get("step") or "0.01"),
            "min_qty": str(plan.get("min_qty") or "0.01"),
            "min_notional": str(plan.get("min_notional") or "5"),
        }
    sized = bx.size_position(
        entry, stop, budget, risk_cap, leverage,
        filters["step"], filters["min_qty"], filters["min_notional"],
        cash_buffer_pct=cfg.get("cash_buffer_pct", 10),
        max_margin_pct=cfg.get("max_margin_pct", 25),
        max_order_usd=cfg.get("max_order_usd", 4000),
        min_order_usd=cfg.get("min_order_usd", 5),
        side=side, cash=cash,
    )
    warnings = list(plan.get("warnings") or [])
    bracket = bx.bracket_for(symbol, float(sized["notional"]))
    if margin_mode == "isolated":
        liq = bx.liquidation_price(entry, sized["quantity"], leverage, side, bracket["mmr"], bracket["cum"])
    else:
        wallet = float(cash if cash is not None else budget)
        liq = bx.liquidation_price_cross(entry, sized["quantity"], wallet, side, bracket["mmr"], bracket["cum"])
    stop_f = float(stop)
    if side == "buy" and not liq < stop_f:
        warnings.append("liquidation_through_stop")
    if side == "sell" and not liq > stop_f:
        warnings.append("liquidation_through_stop")
    rate = plan.get("funding_rate")
    events = int(plan.get("funding_events") or 0)
    funding_cost = None
    if rate is not None:
        funding_cost = bx.funding_payment(sized["notional"], rate, events, side)
        reward = float(sized["quantity"]) * abs(float(target) - float(entry))
        if funding_cost > reward and cfg.get("funding_cost_blocks_when_above_reward", True):
            warnings.append("funding_cost_above_reward")
    order_side = "BUY" if side == "buy" else "SELL"
    exit_side = "SELL" if side == "buy" else "BUY"
    order = {
        "symbol": symbol,
        "side": order_side,
        "positionSide": "BOTH",
        "type": "MARKET",
        "quantity": sized["quantity_str"],
        "reduceOnly": False,
        "leverage": leverage,
        "marginType": margin_mode.upper(),
        "entry_price": str(entry),
        "stopPrice": str(stop),
        "takeProfitPrice": str(target),
        "exit_side": exit_side,
        "workingType": "MARK_PRICE",
        "max_loss_usdt": bx.dec_str(sized["max_loss"]),
        "notional_usdt": bx.dec_str(sized["notional"]),
        "margin_usdt": bx.dec_str(sized["margin"]),
        "liquidation_price": bx.num(liq, 8),
        "maintenance_margin_rate": bracket["mmr"],
        "funding_rate": rate,
        "funding_events": events,
        "funding_cost_usdt": None if funding_cost is None else round(float(funding_cost), 6),
        "hold_hours": plan.get("hold_hours"),
        "binding_limit": sized["binding_limit"],
        "leverage_raises_size": sized["leverage_raises_size"],
        "warnings": warnings,
        "paper": True,
    }
    order["fingerprint"] = bx.fingerprint(order)
    return order, sized


def describe(order, cfg):
    print("PAPER size. Nothing is sent.")
    print("%s %s %s at about %s. Stop %s. Target %s." % (
        order["side"], order["quantity"], order["symbol"], order["entry_price"],
        order["stopPrice"], order["takeProfitPrice"]))
    print("Notional %s USDT. Isolated-or-stated margin %s USDT at %sx %s." % (
        order["notional_usdt"], order["margin_usdt"], order["leverage"], order["marginType"]))
    print("Loss if the stop fills at the stop: %s USDT. The limit that bound the size: %s." % (
        order["max_loss_usdt"], order["binding_limit"]))
    if order["binding_limit"] == "max_loss":
        print("Leverage did not raise the quantity. The stop already spends the risk budget. It only reduces margin.")
    elif order["leverage_raises_size"]:
        print("Margin or cash bound the size. A higher leverage would allow more contracts until the risk budget binds, and it moves liquidation closer.")
    else:
        print("The size was bound by %s, not by the risk budget." % order["binding_limit"])
    print("Liquidation estimate %s (maintenance rate %s)." % (
        order["liquidation_price"], order["maintenance_margin_rate"]))
    if order.get("funding_cost_usdt") is not None:
        print("Funding over the hold, if the rate stays %s: %s USDT (positive means this side pays)." % (
            order.get("funding_rate"), order["funding_cost_usdt"]))
    if order["warnings"]:
        print("Warnings: %s" % ", ".join(order["warnings"]))
    print("Budget %s USDT. Max loss per trade %s. Paper is %s." % (
        cfg.get("budget"), cfg.get("max_loss_per_trade_usd"), cfg.get("paper", True)))
    print(bx.disclaimer())


def main(argv=None):
    p = argparse.ArgumentParser(prog="size.py", description="Position size. Not financial advice.",
                                epilog=bx.disclaimer())
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--filters")
    p.add_argument("--write", nargs="?", const="trader/orders.json")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        plan = bx.read_json(a.plan)
        cfg = bx.load_config(a.config, _example())
        if not cfg:
            raise bx.BinanceError("no config at %s and no example beside this skill" % a.config)
        cash = None
        if os.path.exists(a.portfolio):
            port = bx.read_json(a.portfolio)
            if isinstance(port, dict) and port.get("cash") is not None:
                cash = port["cash"]
        filters = None
        if a.filters:
            filters = bx.symbol_filters(bx.read_json(a.filters), bx.normalize_symbol(plan.get("symbol")))
            if not filters:
                raise bx.BinanceError("no filters for %s" % plan.get("symbol"))
        order, _sized = build_order(plan, cfg, filters=filters, cash=cash)
        doc = {
            "paper": True,
            "exchange": "binance-usdt-m",
            "orders": [order],
        }
        if a.write:
            path = a.write if isinstance(a.write, str) else "trader/orders.json"
            bx.write_json(path, doc)
            print("Wrote %s" % path)
        describe(order, cfg)
        if a.json:
            print(bx.json.dumps(doc, indent=2, sort_keys=True))
        if "liquidation_through_stop" in order["warnings"] or "funding_cost_above_reward" in order["warnings"]:
            return 1
        return 0
    except bx.BinanceError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
