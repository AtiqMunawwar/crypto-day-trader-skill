"""Pure math and the HTTP client. No live network."""

import io
import os
import unittest
import urllib.error

from _load import FIX
import binance_public as bx


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "nope", {"Retry-After": "0"}, io.BytesIO(b""))


class Math(unittest.TestCase):
    def test_liquidation_isolated_long_and_short(self):
        long = bx.liquidation_price(100, 1, 10, "buy", 0.004, 0)
        short = bx.liquidation_price(100, 1, 10, "sell", 0.004, 0)
        self.assertAlmostEqual(long, 100 * 0.9 / 0.996, places=8)
        self.assertAlmostEqual(short, 100 * 1.1 / 1.004, places=8)
        self.assertLess(long, 100)
        self.assertGreater(short, 100)

    def test_size_risk_binds_before_leverage(self):
        sized = bx.size_position(100, 98, 5000, 50, 5, "0.01", "0.01", "5")
        self.assertEqual(sized["quantity_str"], "25")
        self.assertEqual(sized["binding_limit"], "max_loss")
        self.assertFalse(sized["leverage_raises_size"])
        self.assertEqual(bx.dec_str(sized["max_loss"]), "50")
        self.assertEqual(bx.dec_str(sized["margin"]), "500")

    def test_size_refuses_a_stop_that_cannot_be_funded(self):
        with self.assertRaises(bx.BinanceError):
            bx.size_position(100, 50, 100, 1, 2, "0.01", "0.01", "5")

    def test_funding_payment_and_schedule(self):
        # 00:20 UTC plus 8 hours includes the 08:00 print and not 16:00.
        start = bx.utc_ms(bx.parse_now("2026-10-21 00:20"))
        times = bx.funding_times(start, 8)
        self.assertEqual(len(times), 1)
        self.assertEqual(bx.ms_to_utc(times[0]).strftime("%H:%M"), "08:00")
        self.assertAlmostEqual(bx.funding_payment(1000, 0.0005, 1, "buy"), 0.5)
        self.assertAlmostEqual(bx.funding_payment(1000, 0.0005, 1, "sell"), -0.5)

    def test_funding_flags_and_pump(self):
        self.assertIn("extreme_long_funding", bx.funding_flags(0.0012))
        self.assertIn("crowded_longs", bx.funding_flags(0.0006))
        flags = bx.funding_flags(0.0008, change_pct=-4, oi_change_pct=20)
        self.assertIn("long_squeeze_risk", flags)
        self.assertIn("oi_buildup", flags)
        flags = bx.funding_flags(-0.0006, change_pct=4)
        self.assertIn("short_squeeze_risk", flags)
        # a dust rate plus a high account ratio is not a crowd by itself
        self.assertEqual(bx.funding_flags(0.00000074, long_short_ratio=3.0), [])
        warnings = bx.pump_dump("WIFUSDT", 48, 0.45, 0.27, 9_000_000)
        self.assertEqual(warnings, ["pump", "wide_range", "small_cap_volatile"])
        major = bx.pump_dump("BTCUSDT", 48, 100, 90, 1e9)
        self.assertIn("pump", major)
        self.assertNotIn("small_cap_volatile", major)

    def test_daily_setup_breakout(self):
        closes = [float(i) for i in range(1, 31)]
        tags, _nums = bx.find_setups(closes)
        self.assertIn("breakout_20d", tags)


class Universe(unittest.TestCase):
    def test_filter(self):
        info = bx.read_json(os.path.join(FIX, "exchangeInfo.json"))
        syms = [s["symbol"] for s in bx.perpetuals(info)]
        self.assertEqual(syms, ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "WIFUSDT", "XRPUSDT"])
        sol = bx.symbol_filters(info, "SOLUSDT")
        self.assertEqual(sol["tick"], "0.0100")
        self.assertEqual(sol["step"], "0.01")
        cap, _ = bx.leverage_cap("SOLUSDT", {"max_leverage": 5, "symbol_max_leverage": {"SOLUSDT": 10}})
        self.assertEqual(cap, 5)
        self.assertEqual(bx.normalize_symbol("sol"), "SOLUSDT")


class Http(unittest.TestCase):
    def test_429_retries_once(self):
        calls = {"n": 0}

        def opener(req, timeout=20):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _http_error(req.full_url, 429)
            body = "[]"
            return _Resp(body)

        client = bx.Client(opener=opener, sleep=lambda _s: None, pause=0)
        self.assertEqual(client.get_json("https://fapi.binance.com/fapi/v1/ping"), [])
        self.assertEqual(calls["n"], 2)

    def test_418_stops(self):
        def opener(req, timeout=20):
            raise _http_error(req.full_url, 418)

        client = bx.Client(opener=opener, sleep=lambda _s: None, pause=0)
        with self.assertRaises(bx.BinanceError) as ctx:
            client.get_text("https://fapi.binance.com/fapi/v1/ping")
        self.assertIn("418", str(ctx.exception))

    def test_451_falls_through_kline_hosts(self):
        calls = {"n": 0}

        def opener(req, timeout=20):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _http_error(req.full_url, 451)
            row = [0, "1", "2", "0.5", "1.5", "10", 299999, "15", 1, "1", "1", "0"]
            return _Resp(bx.json.dumps([row]))

        client = bx.Client(opener=opener, sleep=lambda _s: None, pause=0)
        bars, source = bx.fetch_klines(client, "SOLUSDT", "5m", limit=1)
        self.assertEqual(source, "binance-vision-spot")
        self.assertEqual(bars[0]["close"], 1.5)
        self.assertEqual(calls["n"], 3)


class _Resp(object):
    def __init__(self, body):
        self._body = body.encode("utf-8")
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


if __name__ == "__main__":
    unittest.main()
