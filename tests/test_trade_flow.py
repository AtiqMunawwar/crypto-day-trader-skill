"""Entry, exit, size, risk, tickets, time machine. Recorded SOLUSDT bars."""

import json
import os
import shutil
import tempfile
import unittest
import urllib.request

from _load import FIX, ROOT, load

entry = load("trader-entry", "entry")
exitp = load("trader-exit", "exit")
size = load("trader-budget", "size")
risk = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")
tm = load("trader-timemachine", "timemachine")

KLINES = os.path.join(FIX, "SOLUSDT-5m.json")
FILTERS = os.path.join(FIX, "exchangeInfo.json")
PREMIUM = os.path.join(FIX, "SOLUSDT-premiumIndex.json")
CONFIG = os.path.join(ROOT, "trader-budget", "config.example.json")


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.entry = os.path.join(self.tmp, "entry.json")
        self.plan = os.path.join(self.tmp, "plan.json")
        self.orders = os.path.join(self.tmp, "orders.json")
        self.risk = os.path.join(self.tmp, "risk.json")

    def test_sol_scan_to_ticket_and_time_machine(self):
        code = entry.main([
            "check", "SOL", "--setup", "utc-orb", "--side", "buy",
            "--offline", KLINES, "--now", "2026-10-21 00:20", "--date", "2026-10-21",
            "--write", self.entry,
        ])
        self.assertEqual(code, 0)
        got = json.load(open(self.entry))
        self.assertEqual(got["status"], "triggered")
        self.assertEqual(got["side"], "buy")
        self.assertEqual(got["at"], "00:20")
        self.assertAlmostEqual(got["price"], 106.63, places=2)

        code = exitp.main([
            "plan", "--from", self.entry, "--stop", "or-mid", "--target", "1R",
            "--leverage", "5", "--filters", FILTERS, "--premium", PREMIUM,
            "--now", "2026-10-21 00:20", "--config", CONFIG, "--write", self.plan,
        ])
        self.assertEqual(code, 0)
        plan = json.load(open(self.plan))
        self.assertEqual(plan["entry"], "106.63")
        self.assertEqual(plan["stop"], "106.41")
        self.assertEqual(plan["target"], "106.85")
        self.assertEqual(plan["leverage"], 5)
        self.assertEqual(plan["margin_mode"], "isolated")
        self.assertIn("stale_funding", plan["warnings"])
        self.assertLess(float(plan["liquidation_price"]), float(plan["stop"]))

        code = size.main([
            "--plan", self.plan, "--config", CONFIG, "--filters", FILTERS, "--write", self.orders,
        ])
        self.assertEqual(code, 0)
        orders = json.load(open(self.orders))
        order = orders["orders"][0]
        self.assertTrue(orders["paper"])
        self.assertEqual(order["quantity"], "37.51")
        self.assertEqual(order["stopPrice"], "106.41")
        self.assertEqual(order["side"], "BUY")
        self.assertEqual(order["binding_limit"], "max_order")
        self.assertFalse(order["leverage_raises_size"])
        self.assertLess(float(order["liquidation_price"]), float(order["stopPrice"]))

        code = risk.main([
            "--orders", self.orders, "--config", CONFIG, "--now", "2026-10-21 00:20",
            "--out", self.risk,
        ])
        self.assertEqual(code, 0)
        verdict = json.load(open(self.risk))["results"][0]
        self.assertEqual(verdict["status"], "PASS")
        self.assertEqual(verdict["fingerprint"], order["fingerprint"])

        code = ticket.main([
            "show", "--orders", self.orders, "--risk", self.risk, "--now", "2026-10-21 00:20",
        ])
        self.assertEqual(code, 0)

        doc = tm.run_machine(
            tm.bx.load_klines(KLINES)["SOLUSDT"],
            tm.normalize_plan(plan),
            replays=10000, seed=19, window=2,
        )
        self.assertEqual(doc["monte_carlo"]["p_target"], 0.5526)
        self.assertEqual(doc["monte_carlo"]["p_stop"], 0.4474)
        self.assertEqual(doc["history"]["target"], 4)
        self.assertEqual(doc["history"]["stop"], 4)
        self.assertIn("thin sample", " ".join(doc["notes"]))
        again = tm.run_machine(
            tm.bx.load_klines(KLINES)["SOLUSDT"],
            tm.normalize_plan(plan),
            replays=200, seed=19, window=2,
        )
        twice = tm.run_machine(
            tm.bx.load_klines(KLINES)["SOLUSDT"],
            tm.normalize_plan(plan),
            replays=200, seed=19, window=2,
        )
        self.assertEqual(again["monte_carlo"]["p_target"], twice["monte_carlo"]["p_target"])
        self.assertEqual(again["verdict"], twice["verdict"])

    def test_offline_entry_does_not_touch_the_network(self):
        def boom(*_a, **_k):
            raise AssertionError("live network")

        orig = urllib.request.urlopen
        urllib.request.urlopen = boom
        try:
            code = entry.main([
                "check", "SOLUSDT", "--offline", KLINES, "--now", "2026-10-21 00:20",
                "--date", "2026-10-21", "--side", "buy",
            ])
        finally:
            urllib.request.urlopen = orig
        self.assertEqual(code, 0)

    def test_short_is_blocked_and_leverage_cap_blocks(self):
        plan = {
            "symbol": "SOLUSDT", "side": "sell", "entry": "100", "stop": "102", "target": "96",
            "leverage": 5, "margin_mode": "isolated", "funding_rate": None, "funding_events": 0,
            "hold_hours": 8, "tick": "0.01", "step": "0.01", "min_qty": "0.01", "min_notional": "5",
            "warnings": [],
        }
        path = os.path.join(self.tmp, "short.json")
        with open(path, "w") as fh:
            json.dump(plan, fh)
        code = size.main(["--plan", path, "--config", CONFIG])
        self.assertEqual(code, 1)

    def test_liquidation_through_the_stop_blocks(self):
        # 50x on a 2% stop puts the estimate through the stop.
        order = {
            "symbol": "SOLUSDT", "side": "BUY", "type": "MARKET", "quantity": "1",
            "reduceOnly": False, "leverage": 50, "marginType": "ISOLATED",
            "entry_price": "100", "stopPrice": "98", "takeProfitPrice": "104",
            "max_loss_usdt": "2", "notional_usdt": "100", "margin_usdt": "2",
            "liquidation_price": "98.5", "hold_hours": 8, "warnings": [], "paper": True,
        }
        doc = {"orders": [order]}
        path = os.path.join(self.tmp, "hot.json")
        cfg = os.path.join(self.tmp, "cfg.json")
        with open(path, "w") as fh:
            json.dump(doc, fh)
        base = json.load(open(CONFIG))
        base["max_leverage"] = 50
        base["symbol_max_leverage"]["SOLUSDT"] = 50
        with open(cfg, "w") as fh:
            json.dump(base, fh)
        results = risk.check_all(doc, base, risk.bx.parse_now("2026-10-21 00:20"), {})
        self.assertEqual(results[0]["status"], "BLOCK")
        self.assertTrue(any("liquidation" in r for r in results[0]["reasons"]))

    def test_pump_blocks_without_allow_volatile(self):
        order = {
            "symbol": "WIFUSDT", "side": "BUY", "type": "MARKET", "quantity": "10",
            "reduceOnly": False, "leverage": 3, "marginType": "ISOLATED",
            "entry_price": "0.4", "stopPrice": "0.36", "takeProfitPrice": "0.48",
            "max_loss_usdt": "0.4", "notional_usdt": "4", "margin_usdt": "1.4",
            "liquidation_price": "0.2", "hold_hours": 4, "warnings": ["pump", "small_cap_volatile"],
            "paper": True,
        }
        # notional under min is not the point of this test; pump is.
        base = json.load(open(CONFIG))
        base["min_order_usd"] = 1
        results = risk.check_all({"orders": [order]}, base, risk.bx.parse_now("2026-10-21 01:00"), {})
        self.assertEqual(results[0]["status"], "BLOCK")
        self.assertTrue(any("pump" in r for r in results[0]["reasons"]))


class Broker(unittest.TestCase):
    def test_signature_does_not_contain_the_secret(self):
        secret = "unit-test-secret-value"
        query = ticket.sign_query({"symbol": "SOLUSDT", "timestamp": "1700000000000"}, secret)
        self.assertNotIn(secret, query)
        self.assertIn("signature=", query)
        self.assertEqual(len(query.split("signature=")[1]), 64)

    def test_live_send_refuses_paper_mode(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        orders = os.path.join(tmp, "orders.json")
        cfg = os.path.join(tmp, "config.json")
        with open(orders, "w") as fh:
            json.dump({"paper": True, "orders": [{
                "symbol": "SOLUSDT", "side": "BUY", "type": "MARKET", "quantity": "1",
                "reduceOnly": False, "leverage": 2, "marginType": "ISOLATED",
                "stopPrice": "1", "takeProfitPrice": "3",
            }]}, fh)
        with open(cfg, "w") as fh:
            json.dump({"paper": True}, fh)
        code = ticket.main([
            "send", "--orders", orders, "--config", cfg, "--reply", "yes", "--live",
            "--now", "2026-10-21 00:20",
        ])
        self.assertEqual(code, 1)

    def test_yes_is_literal(self):
        self.assertTrue(ticket.bx.yes_reply("yes"))
        self.assertTrue(ticket.bx.yes_reply("Yes, place this order"))
        self.assertFalse(ticket.bx.yes_reply("yes please send it"))
        reasons = ticket.live_blockers(
            {"paper": False}, "yes",
            {"BINANCE_API_KEY": "k", "BINANCE_API_SECRET": "s"}, True, True)
        self.assertEqual(reasons, [])
        blocked = ticket.live_blockers({"paper": True}, "ok", {}, False, False)
        self.assertGreaterEqual(len(blocked), 4)


if __name__ == "__main__":
    unittest.main()
