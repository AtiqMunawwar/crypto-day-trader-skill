#!/usr/bin/env python3
"""Public Binance market data and the shared futures math for this pack.

Standard library only. No API key. Nothing here places an order.

Live reads try USDT-M futures on fapi.binance.com first (klines, 24hr
tickers, premiumIndex, funding, open interest). Spot on api.binance.com is
the fallback for klines, then the public market-data mirror
data-api.binance.vision when a location is blocked. Funding, open interest
and the mark price exist only on the futures host.

The liquidation price is an estimate for one isolated position whose wallet
equals the initial margin, fees ignored. It is not Binance's matching engine.
"""

from __future__ import print_function

import calendar
import datetime
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "crypto-trader-skill/1.0 (public market data; not financial advice)"
FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"
VISION = "https://data-api.binance.vision"
PAUSE = 0.2
BACKOFF = 5.0
WEIGHT_PAUSE_AT = 1800

MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT")

# Approximate USDT-M maintenance brackets. Binance's live table needs a signed
# request, so these are conservative published-style estimates, not a promise
# that the exchange still uses these numbers. (notional cap, mmr, cum)
BRACKETS = {
    "BTCUSDT": ((50000, 0.004, 0.0), (250000, 0.005, 50.0), (10 ** 12, 0.01, 1300.0)),
    "ETHUSDT": ((10000, 0.005, 0.0), (100000, 0.01, 50.0), (10 ** 12, 0.02, 1050.0)),
    "SOLUSDT": ((5000, 0.01, 0.0), (50000, 0.025, 75.0), (10 ** 12, 0.05, 1325.0)),
    "XRPUSDT": ((5000, 0.01, 0.0), (50000, 0.025, 75.0), (10 ** 12, 0.05, 1325.0)),
    "BNBUSDT": ((5000, 0.01, 0.0), (50000, 0.025, 75.0), (10 ** 12, 0.05, 1325.0)),
}
DEFAULT_BRACKET = ((5000, 0.015, 0.0), (25000, 0.025, 50.0), (10 ** 12, 0.05, 675.0))

SYMBOL_LEVERAGE_CAP = {
    "BTCUSDT": 20, "ETHUSDT": 20, "SOLUSDT": 10, "XRPUSDT": 10, "BNBUSDT": 10,
}
DEFAULT_ALT_CAP = 5

PUMP_PCT = 25.0
DUMP_PCT = -25.0
WIDE_RANGE = 0.40
THIN_QUOTE = 5000000.0
THIN_MOVE_PCT = 15.0
CROWDED_FUNDING = 0.0005
EXTREME_FUNDING = 0.001
CROWDED_LONG_RATIO = 2.2
CROWDED_SHORT_RATIO = 0.55
SQUEEZE_MOVE_PCT = 3.0
OI_BUILDUP_PCT = 15.0

DISCLAIMER = ("Not financial advice. Public data and arithmetic only: not a prediction, "
              "not a recommendation, and most people who trade this way lose money.")

INTERVAL_MS = {
    "1m": 60 * 1000, "3m": 3 * 60 * 1000, "5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000, "1h": 60 * 60 * 1000, "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000, "1d": 24 * 60 * 60 * 1000,
}


class BinanceError(Exception):
    """One plain reason the tool should stop."""


def disclaimer():
    return DISCLAIMER


# ---------------------------------------------------------------- time

def ms_to_utc(ms):
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=int(ms))


def utc_ms(dt):
    """Naive UTC datetime -> unix milliseconds."""
    return int(calendar.timegm(dt.timetuple()) * 1000) + int(dt.microsecond / 1000)


def utc_now():
    return datetime.datetime.utcnow().replace(microsecond=0)


def parse_now(text):
    """'YYYY-MM-DD HH:MM' or ISO-8601 Zulu -> naive UTC datetime."""
    if text is None:
        return utc_now()
    raw = str(text).strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    if raw.endswith("+00:00"):
        raw = raw[:-6]
    if "+" in raw or raw.endswith("-00:00"):
        raise BinanceError("pass --now in UTC, like 2026-10-26 00:20 (got a timezone offset)")
    raw = raw.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise BinanceError("could not read the time %r (use 2026-10-26 00:20 UTC)" % text)


def iso_z(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def day_start_ms(day):
    return utc_ms(datetime.datetime(day.year, day.month, day.day))


# ---------------------------------------------------------------- http

class Client(object):
    """Polite JSON client. Inject opener and sleep in tests; nothing is sent otherwise."""

    def __init__(self, opener=None, sleep=None, pause=PAUSE, timeout=20):
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep or time.sleep
        self.pause = pause
        self.timeout = timeout
        self.weight = 0
        self.calls = 0

    def get_text(self, url, attempts=2):
        last = "no answer"
        for attempt in range(attempts):
            if self.weight >= WEIGHT_PAUSE_AT:
                self.sleep(BACKOFF)
                self.weight = 0
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            try:
                with self.opener(req, timeout=self.timeout) as resp:
                    self.calls += 1
                    headers = getattr(resp, "headers", {}) or {}
                    raw_w = headers.get("X-MBX-USED-WEIGHT-1M") or headers.get("x-mbx-used-weight-1m")
                    if raw_w:
                        try:
                            self.weight = int(raw_w)
                        except (TypeError, ValueError):
                            pass
                    body = resp.read()
                    if isinstance(body, bytes):
                        body = body.decode("utf-8", "replace")
                if self.pause:
                    self.sleep(self.pause)
                return body
            except urllib.error.HTTPError as e:
                code = e.code
                retry_after = 0
                try:
                    hdrs = e.headers or {}
                    retry_after = float(hdrs.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    retry_after = 0
                try:
                    e.close()
                except Exception:
                    pass
                if code == 429 and attempt + 1 < attempts:
                    self.sleep(retry_after or BACKOFF)
                    last = "HTTP 429"
                    continue
                if code == 418:
                    raise BinanceError(
                        "Binance banned this address for a while (HTTP 418). Stop requesting and wait.")
                if code == 451:
                    raise BinanceError(
                        "Binance refused this location (HTTP 451) for %s. Futures data is on "
                        "fapi.binance.com; klines can fall back to spot. Or pass --offline."
                        % url.split("?")[0])
                if code >= 500 and attempt + 1 < attempts:
                    self.sleep(1.0)
                    last = "HTTP %s" % code
                    continue
                raise BinanceError("Binance answered HTTP %s for %s" % (code, url.split("?")[0]))
            except urllib.error.URLError as e:
                last = "could not connect: %s" % (e.reason,)
                if attempt + 1 < attempts:
                    self.sleep(1.0)
                    continue
                break
            except OSError as e:
                last = "network error: %s" % (e,)
                break
        raise BinanceError("Binance did not answer (%s): wait a minute and try again" % last)

    def get_json(self, url):
        text = self.get_text(url)
        try:
            return json.loads(text)
        except ValueError:
            raise BinanceError("Binance did not return JSON from %s" % url.split("?")[0])


def _query(path, params):
    items = [(k, params[k]) for k in params if params[k] is not None]
    if not items:
        return path
    return path + "?" + urllib.parse.urlencode(items)


# ---------------------------------------------------------------- klines and tickers

def parse_kline_row(row):
    if not isinstance(row, (list, tuple)) or len(row) < 7:
        raise BinanceError("a kline row needs open time, ohlc, volume and close time")
    try:
        return {
            "open_ms": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_ms": int(row[6]),
            "quote_volume": float(row[7]) if len(row) > 7 and row[7] not in (None, "") else 0.0,
            "trades": int(float(row[8])) if len(row) > 8 and row[8] not in (None, "") else 0,
        }
    except (TypeError, ValueError):
        raise BinanceError("a kline row had a non-numeric field")


def kline_params(symbol, interval, limit=500, start=None, end=None):
    if interval not in INTERVAL_MS:
        raise BinanceError("unknown interval %s" % interval)
    if limit < 1 or limit > 1500:
        raise BinanceError("klines limit must be from 1 to 1500")
    params = {"symbol": symbol, "interval": interval, "limit": str(int(limit))}
    if start is not None:
        params["startTime"] = str(int(start))
    if end is not None:
        params["endTime"] = str(int(end))
    return params


def fetch_klines(client, symbol, interval, limit=500, start=None, end=None, market="futures"):
    """Return (bars, source). Futures first. Spot, then the vision mirror, on HTTP 451."""
    params = kline_params(symbol, interval, limit, start, end)
    hosts = []
    if market != "spot":
        hosts.append((FAPI, "/fapi/v1/klines", "binance-usdt-m"))
    hosts.append((SPOT, "/api/v3/klines", "binance-spot"))
    hosts.append((VISION, "/api/v3/klines", "binance-vision-spot"))
    errors = []
    for host, path, label in hosts:
        url = host + _query(path, params)
        try:
            doc = client.get_json(url)
        except BinanceError as e:
            errors.append("%s: %s" % (label, e))
            # a hard 418 should not be retried on another host forever, but 451 should
            if "HTTP 418" in str(e):
                raise
            continue
        if isinstance(doc, dict) and doc.get("code"):
            errors.append("%s: %s" % (label, doc.get("msg") or doc.get("code")))
            continue
        if not isinstance(doc, list):
            errors.append("%s: not a kline list" % label)
            continue
        return [parse_kline_row(r) for r in doc], label
    raise BinanceError("no klines for %s (%s)" % (symbol, "; ".join(errors)))


def fetch_klines_range(client, symbol, interval, start_ms, end_ms, market="futures"):
    """Page through klines. Binance returns at most 1500 a call."""
    step = INTERVAL_MS[interval]
    out = []
    cursor = int(start_ms)
    guard = 0
    source = None
    while cursor < end_ms and guard < 40:
        guard += 1
        bars, source = fetch_klines(client, symbol, interval, limit=1500, start=cursor, end=end_ms, market=market)
        if not bars:
            break
        # drop overlap
        if out:
            bars = [b for b in bars if b["open_ms"] > out[-1]["open_ms"]]
        if not bars:
            break
        out.extend(bars)
        nxt = bars[-1]["open_ms"] + step
        if nxt <= cursor:
            break
        cursor = nxt
        if len(bars) < 1500:
            break
    return out, source


def fetch_ticker_24hr(client, symbol=None):
    params = {"symbol": symbol} if symbol else {}
    doc = client.get_json(FAPI + _query("/fapi/v1/ticker/24hr", params))
    return doc


def fetch_premium_index(client, symbol=None):
    params = {"symbol": symbol} if symbol else {}
    return client.get_json(FAPI + _query("/fapi/v1/premiumIndex", params))


def fetch_open_interest(client, symbol):
    return client.get_json(FAPI + _query("/fapi/v1/openInterest", {"symbol": symbol}))


def fetch_funding_history(client, symbol, limit=100):
    return client.get_json(FAPI + _query("/fapi/v1/fundingRate", {"symbol": symbol, "limit": str(limit)}))


def fetch_oi_hist(client, symbol, period="1h", limit=48):
    return client.get_json(FAPI + _query("/futures/data/openInterestHist",
                                         {"symbol": symbol, "period": period, "limit": str(limit)}))


def fetch_long_short(client, symbol, period="1h", limit=30):
    return client.get_json(FAPI + _query("/futures/data/globalLongShortAccountRatio",
                                         {"symbol": symbol, "period": period, "limit": str(limit)}))


def fetch_exchange_info(client):
    return client.get_json(FAPI + "/fapi/v1/exchangeInfo")


def fetch_announcements(client, catalog_id, page_size=20):
    """Binance public CMS. No key. Titles only are enough for the news skill."""
    url = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId=%s&pageNo=1&pageSize=%s"
           % (int(catalog_id), int(page_size)))
    return client.get_json(url)


# ---------------------------------------------------------------- symbols and filters

def normalize_symbol(text, quote="USDT"):
    s = "".join(str(text or "").strip().upper().split())
    s = s.replace("-", "").replace("/", "").replace("_", "")
    if not s:
        raise BinanceError("missing symbol")
    if s.endswith(quote) and len(s) > len(quote):
        pass
    elif s.isalnum() and not s.endswith(quote):
        s = s + quote
    else:
        raise BinanceError("not a symbol: %r" % text)
    if not s.isalnum() or len(s) < 5 or len(s) > 20:
        raise BinanceError("not a USDT perpetual symbol: %r" % text)
    return s


def perpetuals(exchange_info, quote="USDT"):
    """TRADING quote-margined perpetuals. Drops dated futures and other quotes."""
    symbols = exchange_info.get("symbols") if isinstance(exchange_info, dict) else exchange_info
    if not isinstance(symbols, list):
        raise BinanceError("exchangeInfo has no symbols list")
    out = []
    for s in symbols:
        if not isinstance(s, dict):
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != quote:
            continue
        if s.get("status") != "TRADING":
            continue
        sym = s.get("symbol") or ""
        if "_" in sym:
            continue
        out.append(s)
    out.sort(key=lambda s: s.get("symbol") or "")
    return out


def symbol_filters(exchange_info, symbol):
    symbols = exchange_info.get("symbols") if isinstance(exchange_info, dict) else []
    for s in symbols:
        if s.get("symbol") != symbol:
            continue
        found = {}
        for f in s.get("filters") or []:
            found[f.get("filterType")] = f
        price = found.get("PRICE_FILTER") or {}
        lot = found.get("LOT_SIZE") or {}
        market = found.get("MARKET_LOT_SIZE") or {}
        notion = found.get("MIN_NOTIONAL") or {}
        step = lot.get("stepSize") or market.get("stepSize") or "0.001"
        min_qty = lot.get("minQty") or market.get("minQty") or step
        # market orders sometimes have a coarser step; use the coarser of the two
        mstep = market.get("stepSize")
        if mstep and _dec(mstep) > _dec(step):
            step = mstep
        return {
            "tick": str(price.get("tickSize") or "0.0001"),
            "step": str(step),
            "min_qty": str(min_qty),
            "min_notional": str(notion.get("notional") or "5"),
            "base": s.get("baseAsset") or symbol.replace("USDT", ""),
            "quote": s.get("quoteAsset") or "USDT",
        }
    return None


def leverage_cap(symbol, config=None):
    """The tightest cap: config max, per-symbol override, then the built-in cap."""
    config = config or {}
    cap = int(config.get("max_leverage") or 5)
    table = dict(SYMBOL_LEVERAGE_CAP)
    table.update(config.get("symbol_max_leverage") or {})
    alt = int(config.get("default_alt_max_leverage") or DEFAULT_ALT_CAP)
    sym_cap = int(table.get(symbol) or alt)
    return min(cap, sym_cap), sym_cap


def bracket_for(symbol, notional):
    rows = BRACKETS.get(symbol) or DEFAULT_BRACKET
    notion = float(notional)
    for cap, mmr, cum in rows:
        if notion <= cap:
            return {"mmr": mmr, "cum": cum, "notional_cap": cap}
    last = rows[-1]
    return {"mmr": last[1], "cum": last[2], "notional_cap": last[0]}


# ---------------------------------------------------------------- decimals

def _dec(x):
    from decimal import Decimal
    return Decimal(str(x))


def floor_step(value, step):
    from decimal import Decimal, ROUND_DOWN
    v, s = _dec(value), _dec(step)
    if s <= 0:
        raise BinanceError("step size must be above 0")
    return (v / s).to_integral_value(rounding=ROUND_DOWN) * s


def ceil_step(value, step):
    from decimal import Decimal, ROUND_UP
    v, s = _dec(value), _dec(step)
    if s <= 0:
        raise BinanceError("step size must be above 0")
    return (v / s).to_integral_value(rounding=ROUND_UP) * s


def round_step(value, step):
    from decimal import ROUND_HALF_UP
    v, s = _dec(value), _dec(step)
    if s <= 0:
        raise BinanceError("step size must be above 0")
    return (v / s).to_integral_value(rounding=ROUND_HALF_UP) * s


def dec_str(value):
    s = format(_dec(value), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def num(x, places=8):
    """A short decimal for humans."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if v == 0:
        return "0"
    s = ("%." + str(places) + "f") % v
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


# ---------------------------------------------------------------- liquidation, funding, sizing

def liquidation_price(entry, qty, leverage, side, mmr, cum=0.0):
    """Isolated, one position, wallet = initial margin, fees ignored.

    Long:  (entry * (1 - 1/lev) - cum/qty) / (1 - mmr)
    Short: (entry * (1 + 1/lev) + cum/qty) / (1 + mmr)
    """
    entry = float(entry)
    qty = float(qty)
    leverage = float(leverage)
    mmr = float(mmr)
    cum = float(cum or 0)
    if leverage <= 0:
        raise BinanceError("leverage must be above 0")
    if not 0 <= mmr < 1:
        raise BinanceError("maintenance margin rate must be from 0 up to but not including 1")
    if qty <= 0 or entry <= 0:
        raise BinanceError("entry and quantity must be above 0")
    extra = cum / qty
    long = side in ("buy", "long", "BUY")
    if long:
        return (entry * (1.0 - 1.0 / leverage) - extra) / (1.0 - mmr)
    return (entry * (1.0 + 1.0 / leverage) + extra) / (1.0 + mmr)


def liquidation_price_cross(entry, qty, wallet, side, mmr, cum=0.0):
    """Cross estimate using the whole wallet as the balance. Fees ignored."""
    entry = float(entry)
    qty = float(qty)
    wallet = float(wallet)
    mmr = float(mmr)
    cum = float(cum or 0)
    if qty <= 0 or entry <= 0 or wallet <= 0:
        raise BinanceError("entry, quantity and wallet must be above 0")
    extra = cum / qty
    long = side in ("buy", "long", "BUY")
    if long:
        return (entry - wallet / qty - extra) / (1.0 - mmr)
    return (entry + wallet / qty + extra) / (1.0 + mmr)


def funding_times(start_ms, hold_hours, interval_hours=8):
    """Funding timestamps strictly after start_ms and at or before the hold end."""
    start_ms = int(start_ms)
    end_ms = start_ms + int(round(float(hold_hours) * 3600 * 1000))
    step = int(interval_hours) * 3600 * 1000
    if start_ms % step == 0:
        t = start_ms + step
    else:
        t = (start_ms // step + 1) * step
    out = []
    while t <= end_ms and len(out) < 10000:
        out.append(t)
        t += step
    return out


def funding_payment(notional, rate, events, side):
    """Positive means this side pays. Positive funding: longs pay shorts."""
    gross = float(notional) * float(rate) * int(events)
    if side in ("buy", "long", "BUY"):
        return gross
    return -gross


def size_position(entry, stop, budget, risk_usd, leverage, step, min_qty, min_notional,
                  cash_buffer_pct=10, max_margin_pct=25, max_order_usd=4000, min_order_usd=5,
                  side="buy", cash=None):
    """Largest step-aligned quantity inside the risk, margin, order and cash caps.

    The risk cap is qty * |entry-stop|. Leverage does not raise that cap. It only
    changes how much margin the same quantity needs, so it matters when margin
    or cash is the binding limit.
    """
    entry_d = _dec(entry)
    stop_d = _dec(stop)
    budget_d = _dec(budget)
    risk_d = _dec(risk_usd)
    lev = _dec(leverage)
    distance = abs(entry_d - stop_d)
    if entry_d <= 0 or stop_d <= 0:
        raise BinanceError("entry and stop must be above 0")
    if distance <= 0:
        raise BinanceError("the stop is on the entry: there is no risk to size against")
    if budget_d <= 0 or risk_d <= 0 or lev < 1:
        raise BinanceError("budget, max loss and leverage must be usable numbers (leverage at least 1)")
    long = side in ("buy", "long", "BUY")
    if long and not stop_d < entry_d:
        raise BinanceError("a long stop goes under the entry")
    if not long and not stop_d > entry_d:
        raise BinanceError("a short stop goes above the entry")
    spendable_budget = budget_d * (_dec(100) - _dec(cash_buffer_pct)) / _dec(100)
    spendable = spendable_budget if cash is None else min(spendable_budget, _dec(cash))
    if spendable <= 0:
        raise BinanceError("no spendable margin after the cash buffer")
    hundred = _dec(100)
    raw = {
        "max_loss": risk_d / distance,
        "margin": (budget_d * _dec(max_margin_pct) / hundred) * lev / entry_d,
        "max_order": _dec(max_order_usd) / entry_d,
        "cash": spendable * lev / entry_d,
    }
    binding = min(raw, key=lambda k: raw[k])
    qty = floor_step(raw[binding], step)
    minq = _dec(min_qty)
    if qty < minq:
        raise BinanceError(
            "the stop is too far for this account: the size rounds to %s, under the minimum quantity %s. "
            "A tighter stop or a larger max loss is required. Refusing to shrink the risk rule in secret."
            % (dec_str(qty), dec_str(minq)))
    notional = qty * entry_d
    if notional < _dec(min_notional):
        raise BinanceError(
            "notional %s USDT is under the minimum %s. The risk budget cannot fund the smallest order."
            % (dec_str(notional), min_notional))
    if notional < _dec(min_order_usd):
        raise BinanceError("notional %s USDT is under min_order_usd %s" % (dec_str(notional), min_order_usd))
    margin = notional / lev
    max_loss = qty * distance
    if max_loss - risk_d > _dec("0.0000001"):
        raise BinanceError("internal: sized loss %s exceeds the risk budget" % dec_str(max_loss))
    return {
        "quantity": qty,
        "quantity_str": dec_str(qty),
        "notional": notional,
        "margin": margin,
        "max_loss": max_loss,
        "binding_limit": binding,
        "raw_qty": {k: dec_str(v) for k, v in raw.items()},
        "distance": distance,
        "leverage_raises_size": binding in ("margin", "cash"),
    }


# ---------------------------------------------------------------- indicators and setups

def rsi(closes, n=14):
    """Wilder RSI. None when there are not enough closes."""
    if len(closes) < n + 1:
        return None
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag = sum(gains[:n]) / float(n)
    al = sum(losses[:n]) / float(n)
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / float(n)
        al = (al * (n - 1) + losses[i]) / float(n)
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def atr(bars, n=14):
    """Simple average of the last n true ranges. Returns (atr, bars_used)."""
    if not bars:
        raise BinanceError("ATR needs bars")
    trs = []
    prev = None
    for b in bars:
        tr = b["high"] - b["low"]
        if prev is not None:
            tr = max(tr, abs(b["high"] - prev), abs(b["low"] - prev))
        trs.append(tr)
        prev = b["close"]
    use = trs[-n:]
    return sum(use) / float(len(use)), len(use)


def find_setups(closes):
    """Daily-close setups. closes oldest first, at least 30 values.

    Long cousins of the original pack, plus the short-side mirrors a perp can use.
    """
    if len(closes) < 30:
        raise BinanceError("need 30 daily closes")
    c = [float(x) for x in closes]
    last = c[-1]
    hi20 = max(c[-21:-1])
    lo20 = min(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1.0
    drop = c[-16] / c[-6] - 1.0 if c[-6] else 0.0
    rng5 = (max(c[-5:]) - min(c[-5:])) / last if last else 0.0
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if last < lo20:
        tags.append("breakdown_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if drop > 0.10 and rng5 < 0.05:
        tags.append("bear_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if c[-2] > ma20_prev and last < ma20:
        tags.append("lost_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r is not None and r > 70:
        tags.append("overbought")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    if r_prev is not None and r_prev > 70 and last < c[-2]:
        tags.append("overbought_fade")
    return tags, {
        "rsi": r, "rsi_prev": r_prev, "high20": hi20, "low20": lo20, "ma20": ma20,
        "run_pct": run * 100.0, "range5_pct": rng5 * 100.0,
    }


def pump_dump(symbol, change_pct, high, low, quote_volume, majors=None):
    """Warnings for a 24h ticker. Small-caps get an extra flag."""
    majors = set(majors or MAJORS)
    warnings = []
    change_pct = float(change_pct)
    high = float(high)
    low = float(low)
    quote_volume = float(quote_volume or 0)
    span = (high - low) / low if low else 0.0
    if change_pct >= PUMP_PCT:
        warnings.append("pump")
    if change_pct <= DUMP_PCT:
        warnings.append("dump")
    if span >= WIDE_RANGE:
        warnings.append("wide_range")
    if symbol not in majors and abs(change_pct) >= THIN_MOVE_PCT and quote_volume < THIN_QUOTE:
        warnings.append("thin_move")
    if symbol not in majors and any(w in warnings for w in ("pump", "dump", "wide_range", "thin_move")):
        warnings.append("small_cap_volatile")
    return warnings


def funding_flags(rate, long_short_ratio=None, change_pct=None, oi_change_pct=None):
    """Crowded positioning, squeeze risk, open-interest buildup or flush."""
    rate = float(rate)
    flags = []
    if rate >= EXTREME_FUNDING:
        flags.append("extreme_long_funding")
    elif rate >= CROWDED_FUNDING:
        flags.append("crowded_longs")
    if rate <= -EXTREME_FUNDING:
        flags.append("extreme_short_funding")
    elif rate <= -CROWDED_FUNDING:
        flags.append("crowded_shorts")
    if long_short_ratio is not None:
        ratio = float(long_short_ratio)
        # The account ratio only confirms a rate that is already elevated.
        # A dust-sized positive rate plus a 2:1 account split is normal, not a crowd.
        if ratio >= CROWDED_LONG_RATIO and rate >= 0.0001 and "crowded_longs" not in flags and "extreme_long_funding" not in flags:
            flags.append("crowded_longs")
        if ratio <= CROWDED_SHORT_RATIO and rate <= -0.0001 and "crowded_shorts" not in flags and "extreme_short_funding" not in flags:
            flags.append("crowded_shorts")
    crowded_long = any(f in flags for f in ("crowded_longs", "extreme_long_funding"))
    crowded_short = any(f in flags for f in ("crowded_shorts", "extreme_short_funding"))
    if change_pct is not None and crowded_long and float(change_pct) <= -SQUEEZE_MOVE_PCT:
        flags.append("long_squeeze_risk")
    if change_pct is not None and crowded_short and float(change_pct) >= SQUEEZE_MOVE_PCT:
        flags.append("short_squeeze_risk")
    if oi_change_pct is not None:
        if float(oi_change_pct) >= OI_BUILDUP_PCT:
            flags.append("oi_buildup")
        elif float(oi_change_pct) <= -OI_BUILDUP_PCT:
            flags.append("oi_flush")
    return flags


def annualized_funding(rate, intervals_per_day=3):
    return float(rate) * intervals_per_day * 365.0


# ---------------------------------------------------------------- bar geometry (UTC, 24/7)

def complete_bars(bars, now_ms):
    return [b for b in bars if b["close_ms"] < int(now_ms)]


def bars_on_utc_day(bars, day):
    start = day_start_ms(day)
    end = start + 24 * 60 * 60 * 1000
    return [b for b in bars if start <= b["open_ms"] < end]


def contiguous(bars, interval_ms):
    if not bars:
        return False
    start = bars[0]["open_ms"]
    for i, b in enumerate(bars):
        if b["open_ms"] != start + i * interval_ms:
            return False
    return True


def utc_opening_range(day_bars, or_minutes, interval_min=5):
    """First or_minutes of the UTC day. Requires those bars to be present and contiguous."""
    n = or_minutes // interval_min
    if or_minutes % interval_min or n < 1:
        raise BinanceError("opening range must be a positive multiple of the bar size")
    if len(day_bars) < n:
        return None
    rng = day_bars[:n]
    step = interval_min * 60 * 1000
    if not contiguous(rng, step):
        return None
    # they must start at midnight
    midnight = rng[0]["open_ms"] - (rng[0]["open_ms"] % (24 * 60 * 60 * 1000))
    if rng[0]["open_ms"] != midnight:
        return None
    return {
        "high": max(b["high"] for b in rng),
        "low": min(b["low"] for b in rng),
        "bars": n,
        "minutes": or_minutes,
    }


def first_break(day_bars, opening, side="both"):
    """First completed bar after the range that closes outside it.

    side 'both' returns whichever comes first. 'buy' waits for a close above
    the high. 'sell' waits for a close under the low.
    """
    n = opening["bars"]
    oh, ol = opening["high"], opening["low"]
    for i in range(n, len(day_bars)):
        b = day_bars[i]
        up = b["close"] > oh
        down = b["close"] < ol
        if side in ("buy", "long") and up:
            return _break_hit(b, i, "buy", oh, ol)
        if side in ("sell", "short") and down:
            return _break_hit(b, i, "sell", oh, ol)
        if side == "both" and (up or down):
            which = "buy" if up else "sell"
            return _break_hit(b, i, which, oh, ol)
    return None


def _break_hit(bar, index, side, oh, ol):
    level = oh if side == "buy" else ol
    pct = (bar["close"] / level - 1.0) * 100.0
    close_dt = ms_to_utc(bar["close_ms"] + 1)  # close_ms is inclusive of the last millisecond
    return {
        "side": side,
        "index": index,
        "price": bar["close"],
        "open_ms": bar["open_ms"],
        "close_ms": bar["close_ms"],
        "at": close_dt.strftime("%H:%M"),
        "or_high": oh,
        "or_low": ol,
        "pct_over": pct,
        "volume": bar["volume"],
        "quote_volume": bar["quote_volume"],
    }


def rolling_break(bars, window_minutes, interval_min=5, side="both"):
    """Latest completed bar closes outside the prior window's high or low."""
    n = window_minutes // interval_min
    if n < 1 or window_minutes % interval_min:
        raise BinanceError("window must be a positive multiple of the bar size")
    if len(bars) < n + 1:
        return None
    last = bars[-1]
    window = bars[-(n + 1):-1]
    oh = max(b["high"] for b in window)
    ol = min(b["low"] for b in window)
    up = last["close"] > oh
    down = last["close"] < ol
    if side in ("buy", "long"):
        down = False
    if side in ("sell", "short"):
        up = False
    if not up and not down:
        return None
    which = "buy" if up else "sell"
    hit = _break_hit(last, len(bars) - 1, which, oh, ol)
    hit["window_minutes"] = window_minutes
    return hit


# ---------------------------------------------------------------- files

def read_json(path):
    if not path or not os.path.exists(path):
        raise BinanceError("no file at %s" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise BinanceError("%s is not valid JSON (%s)" % (path, e))


def write_json(path, doc):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def kline_map(doc):
    """A fixture or a raw download -> {SYMBOL: [bar, ...]}."""
    if isinstance(doc, dict) and isinstance(doc.get("klines"), dict):
        return {sym: [parse_kline_row(r) for r in rows] for sym, rows in doc["klines"].items()}
    if isinstance(doc, dict) and isinstance(doc.get("klines"), list):
        sym = doc.get("symbol")
        if not sym:
            raise BinanceError("kline file with a list needs a symbol")
        return {sym: [parse_kline_row(r) for r in doc["klines"]]}
    if isinstance(doc, dict):
        out = {}
        for sym, rows in doc.items():
            if sym.startswith("_") or sym in ("source", "interval", "symbol"):
                continue
            if isinstance(rows, list) and rows and isinstance(rows[0], list):
                out[sym] = [parse_kline_row(r) for r in rows]
        if out:
            return out
    raise BinanceError("not a kline file (expected {symbol: [rows]} or {klines: ...})")


def load_klines(path):
    if os.path.isdir(path):
        out = {}
        for name in sorted(os.listdir(path)):
            if not name.endswith(".json"):
                continue
            out.update(kline_map(read_json(os.path.join(path, name))))
        if not out:
            raise BinanceError("no JSON klines in %s" % path)
        return out
    return kline_map(read_json(path))


def load_ticker_list(doc):
    if isinstance(doc, dict) and "tickers" in doc:
        doc = doc["tickers"]
    if isinstance(doc, dict) and "symbol" in doc and "lastPrice" in doc:
        return [doc]
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        rows = []
        for sym, row in doc.items():
            if isinstance(row, dict) and "lastPrice" in row:
                row = dict(row)
                row.setdefault("symbol", sym)
                rows.append(row)
        if rows:
            return rows
    raise BinanceError("not a 24hr ticker file")


def ticker_change(row):
    return float(row.get("priceChangePercent") or 0)


def fingerprint(order):
    import hashlib
    keys = ("symbol", "side", "type", "quantity", "stopPrice", "takeProfitPrice",
            "leverage", "marginType", "reduceOnly")
    core = {}
    for k in keys:
        if k in order and order[k] is not None:
            core[k] = order[k]
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def load_config(path, example=None):
    if path and os.path.exists(path):
        doc = read_json(path)
        if not isinstance(doc, dict):
            raise BinanceError("%s is not a config object" % path)
        return doc
    if example and os.path.exists(example):
        return read_json(example)
    return {}


def yes_reply(text):
    t = " ".join(str(text or "").strip().lower().split())
    return t in ("yes", "yes, place this order")
