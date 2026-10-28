#!/usr/bin/env python3
"""Time Machine for a USDT-M plan. Not financial advice.

Replays the hold from the coin's own 5-minute bars: a seeded Monte Carlo, plus
the same UTC opening-range rule on earlier days. A simulation is not a prediction.
Crypto trades all weekend. A UTC day is 288 five-minute bars. The default hold
is the plan's hold_hours, not a closing bell.

    python3 timemachine.py run --offline FILE --plan trader/plan.json --write
    python3 timemachine.py run --offline FILE --symbol SOLUSDT --date 2026-10-26 \\
        --entry 100 --stop 98 --target 104 --entry-time 00:20 --hold-hours 8
"""

import argparse
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_public as bx  # noqa: E402

SESSION_BARS = 288
STEP_MS = 5 * 60 * 1000
MIN_SESSIONS = 8
SAMPLE_PATHS = 40
BAND_PATHS = 400
BAND_Q = (0.05, 0.25, 0.5, 0.75, 0.95)
HIST_BINS = 24
COIN_FLIP = 0.05
STRONG = 0.15
MANY_NEITHER = 0.40


class TMError(bx.BinanceError):
    pass


def split_sessions(bars):
    """Full UTC days of 288 contiguous 5-minute bars, oldest first."""
    by = {}
    for b in bars:
        day = bx.ms_to_utc(b["open_ms"]).date().isoformat()
        by.setdefault(day, []).append(b)
    full = []
    for day in sorted(by):
        rows = sorted(by[day], key=lambda r: r["open_ms"])
        if len(rows) != SESSION_BARS:
            continue
        if not bx.contiguous(rows, STEP_MS):
            continue
        if rows[0]["open_ms"] != bx.day_start_ms(bx.ms_to_utc(rows[0]["open_ms"]).date()):
            continue
        full.append((day, rows))
    return full


def build_pool(sessions):
    """pool[i] holds (o, h, l, c) / previous close for bar i of the UTC day."""
    pool = [[] for _ in range(SESSION_BARS)]
    prev_close = None
    for _day, rows in sessions:
        if prev_close:
            b = rows[0]
            pool[0].append((b["open"] / prev_close, b["high"] / prev_close,
                            b["low"] / prev_close, b["close"] / prev_close))
        for i in range(1, SESSION_BARS):
            pc = rows[i - 1]["close"]
            if pc <= 0:
                continue
            b = rows[i]
            pool[i].append((b["open"] / pc, b["high"] / pc, b["low"] / pc, b["close"] / pc))
        prev_close = rows[-1]["close"]
    return pool


def _pick(pool, rng, index, window):
    j = index % SESSION_BARS
    if window:
        j = (j + rng.randint(-window, window)) % SESSION_BARS
    bucket = pool[j]
    if not bucket:
        hour = (j // 12) * 12
        bucket = [item for slot in pool[hour:hour + 12] for item in slot]
    if not bucket:
        bucket = [item for slot in pool for item in slot]
    if not bucket:
        raise TMError("the replay pool is empty")
    return rng.choice(bucket)


def pct(sorted_values, q):
    if not sorted_values:
        return None
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def simulate(pool, entry, stop, target, start_index, n_bars, replays=10000, seed=19, window=2, side="buy"):
    long = side == "buy"
    risk = (entry - stop) if long else (stop - entry)
    if risk <= 0:
        raise TMError("stop is on the wrong side of the entry")
    r_target = ((target - entry) if long else (entry - target)) / risk
    rng = random.Random(seed)
    outcomes = []
    closes = []
    paths = []
    for n in range(replays):
        price = entry
        outcome = None
        keep = n < SAMPLE_PATHS
        path = [entry]
        for step in range(1, n_bars + 1):
            _o, h, l, c = _pick(pool, rng, start_index + step, window)
            hi, lo, cl = price * h, price * l, price * c
            if outcome is None:
                if long:
                    if lo <= stop:
                        outcome = "stop"
                    elif hi >= target:
                        outcome = "target"
                else:
                    if hi >= stop:
                        outcome = "stop"
                    elif lo <= target:
                        outcome = "target"
            price = cl
            if keep:
                path.append(price)
        outcomes.append(outcome or "close")
        closes.append(price)
        if keep:
            paths.append(path)
    cnt = {name: outcomes.count(name) for name in ("target", "stop", "close")}
    green = 0
    rs = []
    for outcome, price in zip(outcomes, closes):
        if outcome == "target" or (outcome == "close" and ((price > entry) if long else (price < entry))):
            green += 1
        if outcome == "target":
            rs.append(r_target)
        elif outcome == "stop":
            rs.append(-1.0)
        else:
            rs.append(((price - entry) if long else (entry - price)) / risk)
    srt = sorted(closes)
    band_rng = random.Random(seed)
    allp = []
    band_n = min(BAND_PATHS, replays)
    for _n in range(band_n):
        price = entry
        path = [entry]
        for step in range(1, n_bars + 1):
            _o, _h, _l, c = _pick(pool, band_rng, start_index + step, window)
            price = price * c
            path.append(price)
        allp.append(path)
    bands = []
    for j in range(n_bars + 1):
        col = sorted(p[j] for p in allp)
        bands.append([round(pct(col, q), 8) for q in BAND_Q])
    lo_p, hi_p = pct(srt, 0.01), pct(srt, 0.99)
    width = (hi_p - lo_p) / HIST_BINS if hi_p is not None else 0
    bins = [0] * HIST_BINS
    if width and width > 0:
        for price in srt:
            b = int((price - lo_p) / width)
            if b == HIST_BINS:
                b = HIST_BINS - 1
            if 0 <= b < HIST_BINS:
                bins[b] += 1
    return {
        "replays": replays,
        "seed": seed,
        "window": window,
        "bars_ahead": n_bars,
        "p_target": round(cnt["target"] / float(replays), 4),
        "p_stop": round(cnt["stop"] / float(replays), 4),
        "p_neither": round(cnt["close"] / float(replays), 4),
        "p_green": round(green / float(replays), 4),
        "median_close": round(pct(srt, 0.5), 8),
        "p5_close": round(pct(srt, 0.05), 8),
        "p95_close": round(pct(srt, 0.95), 8),
        "avg_R": round(statistics.mean(rs), 3) if rs else None,
        "r_at_target": round(r_target, 4),
        "bands": bands,
        "sample_paths": [[round(x, 8) for x in path] for path in paths],
        "hist": {"lo": lo_p, "hi": hi_p, "bins": bins},
    }


def _stop_from_rule(rule, oh, ol, entry, long, tick):
    name = rule["stop_rule"]
    if name == "or_mid":
        raw = (oh + ol) / 2.0
    elif name in ("or_low", "or_high"):
        raw = ol if long else oh
    else:
        frac = rule["stop_pct"] / 100.0
        raw = entry * (1 - frac) if long else entry * (1 + frac)
    return float(bx.floor_step(raw, tick) if long else bx.ceil_step(raw, tick))


def history_replay(sessions, plan, tick):
    """Same UTC-open rule on every full day before the plan date."""
    if str(plan.get("setup") or "").lower() not in ("utc-orb", "orb"):
        return None
    long = plan["side"] == "buy"
    minutes = int((plan.get("opening_range") or {}).get("minutes") or 15)
    n = minutes // 5
    hold_bars = int(round(float(plan.get("hold_hours") or 8) * 12))
    rule = {
        "stop_rule": plan.get("stop_rule") or "pct",
        "stop_pct": float(plan["risk_per_unit"]) / float(plan["entry"]) * 100.0,
        "target_r": float(plan.get("target_r") or plan.get("reward_risk") or 1),
    }
    if rule["stop_rule"] not in ("or_mid", "or_low", "or_high", "pct"):
        rule["stop_rule"] = "pct"
    trades = []
    by = {day: rows for day, rows in sessions}
    days = [day for day, _rows in sessions if day < plan["date"]]
    for day in days:
        rows = by[day]
        opening = bx.utc_opening_range(rows, minutes, 5)
        if not opening:
            continue
        hit = bx.first_break(rows, opening, side="buy" if long else "sell")
        if hit is None:
            continue
        # only count a trigger inside the first 4 hours, matching the entry timer default
        if hit["index"] >= n + 48:
            continue
        entry = hit["price"]
        stop = _stop_from_rule(rule, opening["high"], opening["low"], entry, long, tick)
        risk = (entry - stop) if long else (stop - entry)
        if risk <= 0:
            continue
        target = entry + rule["target_r"] * risk if long else entry - rule["target_r"] * risk
        outcome = None
        price = entry
        for step in range(1, hold_bars + 1):
            idx = hit["index"] + step
            if idx < SESSION_BARS:
                bar = rows[idx]
            else:
                # next UTC day, if we have it
                nxt = _next_day(day)
                if nxt not in by:
                    break
                bar = by[nxt][idx - SESSION_BARS]
            price = bar["close"]
            if long:
                if bar["low"] <= stop:
                    outcome = "stop"
                    break
                if bar["high"] >= target:
                    outcome = "target"
                    break
            else:
                if bar["high"] >= stop:
                    outcome = "stop"
                    break
                if bar["low"] <= target:
                    outcome = "target"
                    break
        if outcome == "target":
            r = rule["target_r"]
        elif outcome == "stop":
            r = -1.0
        else:
            r = ((price - entry) if long else (entry - price)) / risk
            outcome = "close"
        trades.append({
            "date": day, "time": hit["at"], "entry": entry, "stop": stop, "target": target,
            "outcome": outcome, "R": round(r, 3),
        })
    return {
        "sessions": len(days),
        "triggered": len(trades),
        "target": sum(1 for t in trades if t["outcome"] == "target"),
        "stop": sum(1 for t in trades if t["outcome"] == "stop"),
        "close": sum(1 for t in trades if t["outcome"] == "close"),
        "green": sum(1 for t in trades if t["R"] > 0),
        "avg_R": round(statistics.mean([t["R"] for t in trades]), 3) if trades else None,
        "trades": trades,
    }


def _next_day(iso):
    import datetime
    d = datetime.date(*[int(x) for x in iso.split("-")])
    return (d + datetime.timedelta(days=1)).isoformat()


def lean(mc):
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return None
    r = mc.get("r_at_target") or 1.0
    share = pt / (pt + ps)
    even = 1.0 / (1.0 + r)
    return share - even, share, even


def verdict(mc):
    pt, ps = mc["p_target"], mc["p_stop"]
    lv = lean(mc)
    if lv is None:
        return "No replay reached the target or the stop during the hold. The price at the end of the hold decides every one."
    gap, share, even = lv
    r = mc.get("r_at_target") or 1.0
    one_r = abs(r - 1.0) < 0.02
    if abs(gap) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(gap) < STRONG:
        head = "Leans toward the target" if gap > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if gap > 0 else "Strongly toward the stop"
    odds = "target first %.1f%% vs stop first %.1f%%" % (100 * pt, 100 * ps)
    if one_r:
        return "%s: %s." % (head, odds)
    return ("%s: at %gR the target has to come first in %.0f%% of the replays that hit one side; "
            "it did in %.0f%% (%s)." % (head, round(r, 2), 100 * even, 100 * share, odds))


def normalize_plan(raw):
    symbol = bx.normalize_symbol(raw.get("symbol") or "")
    side = str(raw.get("side") or "buy").lower()
    if side in ("long",):
        side = "buy"
    if side in ("short",):
        side = "sell"
    if side not in ("buy", "sell"):
        raise TMError("side must be buy or sell")
    try:
        entry = float(raw.get("entry"))
        stop = float(raw.get("stop"))
        target = float(raw.get("target"))
    except (TypeError, ValueError):
        raise TMError("the plan needs numeric entry, stop and target")
    long = side == "buy"
    if long and not stop < entry < target:
        raise TMError("a buy needs stop < entry < target")
    if not long and not target < entry < stop:
        raise TMError("a sell needs target < entry < stop")
    if raw.get("entry_bar") is not None:
        k = int(raw["entry_bar"])
    elif raw.get("entry_time"):
        text = str(raw["entry_time"]).strip().split(" ")[0]
        hh, mm = [int(x) for x in text.split(":")]
        close_min = hh * 60 + mm
        if close_min % 5 or close_min <= 0:
            raise TMError("entry_time must be a 5-minute close, like 00:20")
        k = close_min // 5 - 1
    else:
        raise TMError("the plan needs entry_time (HH:MM UTC, the bar's close) or entry_bar")
    if not 0 <= k <= SESSION_BARS - 2:
        raise TMError("entry bar is outside the UTC day")
    hold = float(raw.get("hold_hours") or 8)
    n_bars = int(round(hold * 12))
    if n_bars < 1:
        raise TMError("hold is too short to replay")
    risk = abs(entry - stop)
    return {
        "symbol": symbol, "side": side, "date": str(raw.get("date") or ""),
        "setup": str(raw.get("setup") or "utc-orb"),
        "entry": entry, "stop": stop, "target": target,
        "entry_time": raw.get("entry_time"), "entry_bar": k,
        "risk_per_unit": float(raw.get("risk_per_unit") or risk),
        "target_r": float(raw.get("reward_risk") or raw.get("target_r") or (abs(target - entry) / risk)),
        "opening_range": raw.get("opening_range") or {"minutes": 15},
        "stop_rule": str(raw.get("stop_rule") or "pct"),
        "hold_hours": hold,
        "tick": str(raw.get("tick") or "0.01"),
        "bars_ahead": n_bars,
    }


def load_plan(a):
    if a.symbol:
        raw = {
            "symbol": a.symbol, "side": a.side, "date": a.date, "entry": a.entry,
            "stop": a.stop, "target": a.target, "entry_time": a.entry_time,
            "setup": a.setup, "hold_hours": a.hold_hours, "stop_rule": a.stop_rule,
            "opening_range": {"minutes": a.or_minutes}, "tick": a.tick,
        }
        return normalize_plan(raw)
    if not os.path.exists(a.plan):
        raise TMError("no plan at %s. Run trader-exit, or pass --symbol and the prices." % a.plan)
    return normalize_plan(bx.read_json(a.plan))


def run_machine(bars, plan, replays, seed, window):
    sessions = split_sessions(bars)
    past = [s for s in sessions if s[0] < plan["date"]]
    if len(past) < MIN_SESSIONS:
        raise TMError(
            "need %d full UTC days before %s to build a pool (found %d). "
            "Five-minute bars, 288 a day, no gaps." % (MIN_SESSIONS, plan["date"], len(past)))
    if len(past) < 20:
        thin = True
    else:
        thin = False
    pool = build_pool(past)
    mc = simulate(pool, plan["entry"], plan["stop"], plan["target"], plan["entry_bar"],
                  plan["bars_ahead"], replays=replays, seed=seed, window=window, side=plan["side"])
    hist = history_replay(sessions, plan, plan["tick"])
    notes = []
    if thin:
        notes.append("The pool is %d UTC days, under 20. Treat the odds as a thin sample." % len(past))
    if mc["p_neither"] > MANY_NEITHER:
        notes.append("%.1f%% of replays hit neither side during the hold." % (100 * mc["p_neither"]))
    return {
        "symbol": plan["symbol"],
        "date": plan["date"],
        "side": plan["side"],
        "entry": plan["entry"],
        "stop": plan["stop"],
        "target": plan["target"],
        "hold_hours": plan["hold_hours"],
        "sessions": len(past),
        "monte_carlo": mc,
        "history": hist,
        "verdict": verdict(mc),
        "notes": notes,
        "disclaimer": bx.disclaimer(),
    }


def report(doc):
    mc = doc["monte_carlo"]
    print("%s %s planned %s -> stop %s / target %s, hold %sh, %d replays, seed %s." % (
        doc["side"], doc["symbol"], doc["entry"], doc["stop"], doc["target"],
        bx.num(doc["hold_hours"]), mc["replays"], mc["seed"]))
    print("Pool: %d earlier UTC days." % doc["sessions"])
    print("Target first %.1f%%. Stop first %.1f%%. Neither %.1f%%. Average R %s." % (
        100 * mc["p_target"], 100 * mc["p_stop"], 100 * mc["p_neither"], mc["avg_R"]))
    print("Close at the end of the hold: median %s, 5th %s, 95th %s." % (
        bx.num(mc["median_close"]), bx.num(mc["p5_close"]), bx.num(mc["p95_close"])))
    hist = doc.get("history")
    if hist:
        print("History: the rule fired %d times in %d earlier days. Target %d, stop %d, neither %d. Average R %s." % (
            hist["triggered"], hist["sessions"], hist["target"], hist["stop"], hist["close"], hist["avg_R"]))
    else:
        print("History: skipped (the setup is not a UTC opening-range break).")
    print(doc["verdict"])
    for note in doc["notes"]:
        print(note)
    print(doc["disclaimer"])


def chart(doc):
    mc = doc["monte_carlo"]
    width = 40
    print("Share of %d replays" % mc["replays"])
    for label, key in (("target", "p_target"), ("stop", "p_stop"), ("neither", "p_neither")):
        n = int(round(mc[key] * width))
        print("  %-8s |%s| %5.1f%%" % (label, "#" * n + "." * (width - n), 100 * mc[key]))
    print(doc.get("verdict") or "")
    print(bx.disclaimer())


def _load_bars(a, symbol):
    if a.offline:
        data = bx.load_klines(a.offline)
        if symbol not in data:
            raise TMError("no %s bars in %s" % (symbol, a.offline))
        return data[symbol]
    now = bx.parse_now(a.now)
    start = bx.utc_ms(now) - 20 * 24 * 3600 * 1000
    client = bx.Client(pause=a.pause)
    bars, src = bx.fetch_klines_range(client, symbol, "5m", start, bx.utc_ms(now))
    print("Fetched %d bars from %s." % (len(bars), src))
    return bars


def cmd_run(a):
    if not 1 <= a.replays <= 1000000:
        raise TMError("--replays must be from 1 to 1000000")
    if not 0 <= a.window <= 10:
        raise TMError("--window must be from 0 to 10")
    plan = load_plan(a)
    if not plan["date"]:
        raise TMError("the plan needs a date")
    bars = _load_bars(a, plan["symbol"])
    doc = run_machine(bars, plan, a.replays, a.seed, a.window)
    if a.write:
        path = a.write if isinstance(a.write, str) else "trader/timemachine.json"
        bx.write_json(path, doc)
        print("Wrote %s" % path)
    if a.json:
        # bands and paths are large; human report is the default
        print(bx.json.dumps(doc, indent=2, sort_keys=True))
    else:
        report(doc)
    return 0


def cmd_chart(a):
    doc = bx.read_json(a.from_path)
    chart(doc)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="timemachine.py", description="Time Machine. Not financial advice.",
                                epilog=bx.disclaimer())
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--plan", default="trader/plan.json")
    r.add_argument("--offline")
    r.add_argument("--symbol")
    r.add_argument("--side", default="buy")
    r.add_argument("--date")
    r.add_argument("--entry", type=float)
    r.add_argument("--stop", type=float)
    r.add_argument("--target", type=float)
    r.add_argument("--entry-time")
    r.add_argument("--setup", default="utc-orb")
    r.add_argument("--stop-rule", default="pct")
    r.add_argument("--or-minutes", type=int, default=15)
    r.add_argument("--hold-hours", type=float, default=8)
    r.add_argument("--tick", default="0.01")
    r.add_argument("--replays", type=int, default=10000)
    r.add_argument("--seed", type=int, default=19)
    r.add_argument("--window", type=int, default=2)
    r.add_argument("--now")
    r.add_argument("--pause", type=float, default=bx.PAUSE)
    r.add_argument("--write", nargs="?", const="trader/timemachine.json")
    r.add_argument("--json", action="store_true")
    c = sub.add_parser("chart")
    c.add_argument("--from", dest="from_path", default="trader/timemachine.json")
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    try:
        if a.cmd == "run":
            return cmd_run(a)
        if a.cmd == "chart":
            return cmd_chart(a)
    except bx.BinanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
