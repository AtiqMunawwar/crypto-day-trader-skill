#!/usr/bin/env python3
"""Exit plan for a USDT-M perp: stop, target, leverage, liquidation, funding.

Not financial advice. A stop is a plan, not a guaranteed fill. The liquidation
price is an estimate for one isolated position. This tool never places an order.

    python3 exit.py plan --from trader/entry.json --stop or-mid --target 1R --leverage 5 --write
    python3 exit.py plan --symbol SOLUSDT --entry 100 --stop pct:1.5 --target 2R --side buy
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_public as bx  # noqa: E402

MIN_RISK_PCT = 0.05


def parse_stop(text):
    t = str(text).strip().lower().replace("_", "-")
    if t in ("or-mid", "mid"):
        return "or_mid", None
    if t in ("or-low", "low"):
        return "or_low", None
    if t in ("or-high", "high"):
        return "or_high", None
    if ":" not in t:
        raise bx.BinanceError("--stop is or-mid, or-low, or-high, price:X, pct:X or atr:K")
    kind, raw = t.split(":", 1)
    kind = kind.strip()
    if kind not in ("price", "pct", "atr"):
        raise bx.BinanceError("--stop is or-mid, or-low, or-high, price:X, pct:X or atr:K")
    try:
        val = float(raw)
    except ValueError:
        raise bx.BinanceError("--stop %s needs a number" % kind)
    if val <= 0:
        raise bx.BinanceError("--stop %s needs a number above 0" % kind)
    if kind == "pct" and val >= 100:
        raise bx.BinanceError("--stop pct needs a percent under 100")
    return kind, val


def parse_target(text):
    t = str(text).strip().lower()
    if t.endswith("r"):
        try:
            r = float(t[:-1])
        except ValueError:
            raise bx.BinanceError("--target is 1R, 2R or price:X")
        if r <= 0:
            raise bx.BinanceError("--target R must be above 0")
        return "r", r
    if t.startswith("price:"):
        return "price", float(t.split(":", 1)[1])
    raise bx.BinanceError("--target is 1R, 2R or price:X")


def _side(text):
    t = str(text or "buy").lower()
    if t in ("buy", "long"):
        return "buy"
    if t in ("sell", "short"):
        return "sell"
    raise bx.BinanceError("side must be buy or sell")


def compute_prices(entry, side, stop_rule, stop_param, target_rule, target_param,
                   or_high=None, or_low=None, atr_value=None, atr_bars=None, tick="0.01"):
    entry_d = bx._dec(entry)
    if entry_d <= 0:
        raise bx.BinanceError("entry must be above 0")
    long = side == "buy"
    if stop_rule == "or_mid":
        if or_high is None or or_low is None:
            raise bx.BinanceError("an or-mid stop needs the opening range")
        raw = (bx._dec(or_high) + bx._dec(or_low)) / 2
        label = "opening-range midpoint"
    elif stop_rule == "or_low":
        if or_low is None:
            raise bx.BinanceError("an or-low stop needs the opening-range low")
        if not long:
            raise bx.BinanceError("a short uses or-high (the far side), not or-low")
        raw = bx._dec(or_low)
        label = "opening-range low"
    elif stop_rule == "or_high":
        if or_high is None:
            raise bx.BinanceError("an or-high stop needs the opening-range high")
        if long:
            raise bx.BinanceError("a long uses or-low (the far side), not or-high")
        raw = bx._dec(or_high)
        label = "opening-range high"
    elif stop_rule == "price":
        raw = bx._dec(stop_param)
        label = "your price"
    elif stop_rule == "pct":
        pct = bx._dec(stop_param) / bx._dec(100)
        raw = entry_d * (1 - pct) if long else entry_d * (1 + pct)
        label = "%s%% %s the entry" % (bx.num(stop_param), "under" if long else "over")
    elif stop_rule == "atr":
        if atr_value is None:
            raise bx.BinanceError("an atr stop needs bars")
        raw = entry_d - bx._dec(stop_param) * bx._dec(atr_value) if long else entry_d + bx._dec(stop_param) * bx._dec(atr_value)
        label = "%s x ATR(%s) %s the entry" % (bx.num(stop_param), atr_bars, "under" if long else "over")
    else:
        raise bx.BinanceError("unknown stop rule")
    stop = bx.floor_step(raw, tick) if long else bx.ceil_step(raw, tick)
    if long and stop >= entry_d:
        raise bx.BinanceError("the stop %s is not under the entry %s" % (bx.dec_str(stop), bx.dec_str(entry_d)))
    if not long and stop <= entry_d:
        raise bx.BinanceError("the stop %s is not above the entry %s" % (bx.dec_str(stop), bx.dec_str(entry_d)))
    if stop <= 0:
        raise bx.BinanceError("the stop must stay above 0")
    risk = abs(entry_d - stop)
    floor_risk = entry_d * bx._dec(MIN_RISK_PCT) / bx._dec(100)
    if risk < floor_risk:
        raise bx.BinanceError(
            "the stop is %s away, under %s%% of price. Widen it so a normal tick does not stop you out."
            % (bx.dec_str(risk), MIN_RISK_PCT))
    if target_rule == "r":
        raw_t = entry_d + bx._dec(target_param) * risk if long else entry_d - bx._dec(target_param) * risk
        target = bx.ceil_step(raw_t, tick) if long else bx.floor_step(raw_t, tick)
        t_label = "%sR" % bx.num(target_param)
    else:
        target = bx.round_step(target_param, tick)
        t_label = "your price"
    if long and target <= entry_d:
        raise bx.BinanceError("a long target goes above the entry")
    if not long and target >= entry_d:
        raise bx.BinanceError("a short target goes under the entry")
    reward = abs(target - entry_d)
    rr = reward / risk
    return {
        "entry": entry_d, "stop": stop, "target": target, "risk": risk, "reward": reward,
        "reward_risk": rr, "stop_label": label, "target_label": t_label,
    }


def _load_entry(path):
    doc = bx.read_json(path)
    if doc.get("status") not in (None, "triggered"):
        raise bx.BinanceError("entry file status is %s. Plan from a triggered entry, or pass --entry." % doc.get("status"))
    return doc


def _bars_for(a, symbol):
    if not a.offline:
        return None
    data = bx.load_klines(a.offline)
    return data.get(symbol) or []


def _premium(a):
    if a.premium:
        doc = bx.read_json(a.premium)
        if isinstance(doc, list):
            doc = doc[0] if doc else {}
        return doc
    return None


def build_plan(a):
    entry_doc = _load_entry(a.from_path) if a.from_path and os.path.exists(a.from_path) and not a.symbol else {}
    symbol = bx.normalize_symbol(a.symbol or entry_doc.get("symbol") or "")
    side = _side(a.side or entry_doc.get("side") or "buy")
    now = bx.parse_now(a.now)
    filters = {"tick": a.tick, "step": a.step, "min_qty": a.min_qty, "min_notional": a.min_notional}
    if a.filters:
        found = bx.symbol_filters(bx.read_json(a.filters), symbol)
        if not found:
            raise bx.BinanceError("no %s filters in %s" % (symbol, a.filters))
        filters = found
    if a.entry is not None:
        entry = a.entry
    elif entry_doc.get("price") is not None:
        entry = entry_doc["price"]
    else:
        raise bx.BinanceError("pass --entry or --from a triggered entry.json")
    entry = bx.dec_str(bx.round_step(entry, filters["tick"]))
    or_high = a.or_high if a.or_high is not None else entry_doc.get("or_high")
    or_low = a.or_low if a.or_low is not None else entry_doc.get("or_low")
    stop_rule, stop_param = parse_stop(a.stop)
    target_rule, target_param = parse_target(a.target)
    atr_value = None
    atr_bars = None
    bars = _bars_for(a, symbol)
    if stop_rule == "atr":
        if not bars:
            if a.offline:
                raise bx.BinanceError("ATR needs bars for %s in %s" % (symbol, a.offline))
            client = bx.Client(pause=a.pause)
            bars, _src = bx.fetch_klines(client, symbol, "5m", limit=100)
        done = bx.complete_bars(bars, bx.utc_ms(now))
        atr_value, atr_bars = bx.atr(done[-50:] if done else bars[-50:], 14)
    prices = compute_prices(entry, side, stop_rule, stop_param, target_rule, target_param,
                            or_high, or_low, atr_value, atr_bars, filters["tick"])
    cfg = bx.load_config(a.config)
    requested = int(a.leverage if a.leverage is not None else cfg.get("max_leverage") or 5)
    cap, sym_cap = bx.leverage_cap(symbol, cfg)
    if requested > cap:
        raise bx.BinanceError(
            "leverage %s is above the cap %s for %s (symbol cap %s, config max_leverage %s)."
            % (requested, cap, symbol, sym_cap, cfg.get("max_leverage", 5)))
    if requested < 1:
        raise bx.BinanceError("leverage must be at least 1")
    margin_mode = (a.margin or cfg.get("margin_mode") or "isolated").lower()
    if margin_mode not in ("isolated", "cross"):
        raise bx.BinanceError("margin mode is isolated or cross")
    if margin_mode == "cross" and not (cfg.get("allow_cross") or a.allow_cross):
        raise bx.BinanceError("cross margin is off. Set allow_cross in config, or stay isolated.")
    hold = float(a.hold_hours if a.hold_hours is not None else cfg.get("max_hold_hours") or 8)
    # A 1-unit notional is enough to pick the bracket; budget resizes later.
    notion_guess = float(prices["entry"])
    bracket = bx.bracket_for(symbol, notion_guess)
    # Recompute with a more realistic notional once we know nothing about qty.
    # Use entry * (risk budget / distance) when config has a max loss, so the
    # bracket matches the trade the budget skill will size. Fall back to 1 unit.
    risk_usd = float(cfg.get("max_loss_per_trade_usd") or 50)
    qty_guess = risk_usd / float(prices["risk"])
    notion_guess = qty_guess * float(prices["entry"])
    bracket = bx.bracket_for(symbol, notion_guess)
    liq = bx.liquidation_price(prices["entry"], max(qty_guess, float(filters["min_qty"])), requested, side,
                               bracket["mmr"], bracket["cum"])
    if margin_mode == "cross":
        wallet = float(cfg.get("budget") or risk_usd)
        liq = bx.liquidation_price_cross(prices["entry"], max(qty_guess, float(filters["min_qty"])), wallet, side,
                                         bracket["mmr"], bracket["cum"])
    prem = _premium(a)
    warnings = []
    funding_rate = None
    funding_time = None
    if prem:
        try:
            funding_rate = float(prem.get("lastFundingRate"))
        except (TypeError, ValueError):
            funding_rate = None
        funding_time = prem.get("_fundingTime") or prem.get("fundingTime") or prem.get("time")
        if funding_time is not None and bx.utc_ms(now) - int(funding_time) > 24 * 3600 * 1000:
            warnings.append("stale_funding")
    else:
        warnings.append("funding_unavailable")
    start_ms = bx.utc_ms(now)
    events = bx.funding_times(start_ms, hold)
    pay = None
    if funding_rate is not None:
        pay = bx.funding_payment(notion_guess, funding_rate, len(events), side)
    # liquidation must sit beyond the stop
    stop_f = float(prices["stop"])
    if side == "buy" and not liq < stop_f:
        warnings.append("liquidation_through_stop")
    if side == "sell" and not liq > stop_f:
        warnings.append("liquidation_through_stop")
    entry_time = a.time or entry_doc.get("at")
    plan = {
        "symbol": symbol,
        "side": side,
        "date": (a.date or entry_doc.get("date") or now.date().isoformat()),
        "setup": entry_doc.get("setup") or a.setup or "level",
        "interval": "5m",
        "entry": bx.dec_str(prices["entry"]),
        "stop": bx.dec_str(prices["stop"]),
        "target": bx.dec_str(prices["target"]),
        "risk_per_unit": bx.dec_str(prices["risk"]),
        "reward_per_unit": bx.dec_str(prices["reward"]),
        "reward_risk": float(round(float(prices["reward_risk"]), 4)),
        "stop_rule": stop_rule,
        "stop_label": prices["stop_label"],
        "target_label": prices["target_label"],
        "entry_time": entry_time,
        "entry_bar": entry_doc.get("bar_index"),
        "opening_range": {"minutes": entry_doc.get("or_minutes") or a.or_minutes,
                          "high": or_high, "low": or_low},
        "leverage": requested,
        "margin_mode": margin_mode,
        "maintenance_margin_rate": bracket["mmr"],
        "maintenance_cum": bracket["cum"],
        "liquidation_price": bx.num(liq, 8),
        "liquidation_note": "estimate for one %s position, fees ignored" % margin_mode,
        "hold_hours": hold,
        "funding_rate": funding_rate,
        "funding_time": funding_time,
        "funding_events": len(events),
        "funding_cost_usdt_at_guess_qty": None if pay is None else round(pay, 6),
        "guess_qty": qty_guess,
        "atr": None if atr_value is None else atr_value,
        "warnings": warnings,
        "tick": filters["tick"],
        "step": filters["step"],
        "min_qty": filters["min_qty"],
        "min_notional": filters["min_notional"],
        "as_of": bx.iso_z(now),
        "paper": True,
    }
    return plan


def describe(plan):
    print("%s %s  entry %s  stop %s (%s)  target %s (%s)" % (
        plan["side"], plan["symbol"], plan["entry"], plan["stop"], plan["stop_label"],
        plan["target"], plan["target_label"]))
    print("Risk %s USDT per unit. Reward:risk %s. Leverage %sx %s." % (
        plan["risk_per_unit"], plan["reward_risk"], plan["leverage"], plan["margin_mode"]))
    print("Liquidation estimate %s before sizing. %s. trader-budget recomputes it for the real quantity." % (
        plan["liquidation_price"], plan["liquidation_note"]))
    if plan.get("funding_rate") is None:
        print("Funding rate: unknown.")
    else:
        print("Funding rate %s. About %s events in %sh. Cost at the guess size: %s USDT (positive means you pay)." % (
            plan["funding_rate"], plan["funding_events"], bx.num(plan["hold_hours"]),
            plan["funding_cost_usdt_at_guess_qty"]))
    if plan["warnings"]:
        print("Warnings: %s" % ", ".join(plan["warnings"]))
    print("A stop can fill past its price in a fast market. The liquidation figure is an estimate.")
    print(bx.disclaimer())


def cmd_plan(a):
    plan = build_plan(a)
    if a.write:
        path = a.write if isinstance(a.write, str) else "trader/plan.json"
        bx.write_json(path, plan)
        print("Wrote %s" % path)
    describe(plan)
    if a.json:
        print(bx.json.dumps(plan, indent=2, sort_keys=True))
    if "liquidation_through_stop" in plan["warnings"]:
        print("The liquidation estimate is through the stop. Lower the leverage or widen nothing: move the stop first.",
              file=sys.stderr)
        return 1
    return 0


def cmd_show(a):
    plan = bx.read_json(a.plan)
    describe(plan)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="exit.py", description="Exit planner. Not financial advice.",
                                epilog=bx.disclaimer())
    sub = p.add_subparsers(dest="cmd")
    pl = sub.add_parser("plan")
    pl.add_argument("--from", dest="from_path", default="trader/entry.json")
    pl.add_argument("--symbol")
    pl.add_argument("--entry", type=float)
    pl.add_argument("--side", default=None)
    pl.add_argument("--stop", default="or-mid")
    pl.add_argument("--target", default="1R")
    pl.add_argument("--or-high", type=float)
    pl.add_argument("--or-low", type=float)
    pl.add_argument("--or-minutes", type=int, default=15)
    pl.add_argument("--setup", default=None)
    pl.add_argument("--date")
    pl.add_argument("--time", help="entry close HH:MM UTC")
    pl.add_argument("--leverage", type=int)
    pl.add_argument("--margin", choices=["isolated", "cross"])
    pl.add_argument("--allow-cross", action="store_true")
    pl.add_argument("--hold-hours", type=float)
    pl.add_argument("--tick", default="0.01")
    pl.add_argument("--step", default="0.01")
    pl.add_argument("--min-qty", default="0.01")
    pl.add_argument("--min-notional", default="5")
    pl.add_argument("--filters", help="exchangeInfo JSON")
    pl.add_argument("--offline", help="klines, required for an ATR stop offline")
    pl.add_argument("--premium", help="premiumIndex JSON")
    pl.add_argument("--config", default="trader/config.json")
    pl.add_argument("--now")
    pl.add_argument("--pause", type=float, default=bx.PAUSE)
    pl.add_argument("--write", nargs="?", const="trader/plan.json")
    pl.add_argument("--json", action="store_true")
    sh = sub.add_parser("show")
    sh.add_argument("--plan", default="trader/plan.json")
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    try:
        if a.cmd == "plan":
            return cmd_plan(a)
        if a.cmd == "show":
            return cmd_show(a)
    except bx.BinanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
