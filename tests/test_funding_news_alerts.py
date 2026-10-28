"""Funding context, announcements, and alerts. Fixtures only."""

import json
import os
import shutil
import tempfile
import unittest

from _load import FIX, load

funding = load("trader-funding", "context")
news = load("trader-news", "news")
alerts = load("trader-alerts", "alerts")


class Funding(unittest.TestCase):
    def test_recorded_sol_is_stale_not_crowded(self):
        ctx = funding.build_context(
            "SOLUSDT",
            funding.bx.read_json(os.path.join(FIX, "SOLUSDT-premiumIndex.json")),
            funding.bx.read_json(os.path.join(FIX, "SOLUSDT-openInterest.json")),
            funding.bx.read_json(os.path.join(FIX, "SOLUSDT-openInterestHist.json")),
            funding.bx.read_json(os.path.join(FIX, "SOLUSDT-longShort.json")),
            funding.bx.read_json(os.path.join(FIX, "SOLUSDT-ticker-24hr.json")),
            funding.bx.parse_now("2026-10-26 23:59"),
        )
        self.assertTrue(ctx["stale_funding"])
        self.assertNotIn("crowded_longs", ctx["flags"])
        self.assertAlmostEqual(ctx["funding_rate"], 7.4e-7, places=10)

    def test_cli(self):
        code = funding.main(["SOL", "--offline", FIX, "--now", "2026-10-26 23:59"])
        self.assertEqual(code, 0)


class News(unittest.TestCase):
    def test_sol_does_not_match_isolated(self):
        title = "Binance to Adjust Leverage on Select Isolated Margin Trading Pairs"
        self.assertEqual(news.mentions(title, ["SOL"]), [])
        self.assertEqual(news.mentions("Delist PTBUSDT perpetual", ["PTB"]), ["PTB"])

    def test_recorded_delisting(self):
        items = news.articles_from(news.bx.read_json(os.path.join(FIX, "announcements.json")))
        rows = news.select(items, ["PTB"], news.bx.parse_now("2026-10-27 18:00"), 240, {48, 49, 161})
        self.assertTrue(rows)
        self.assertTrue(any("PTBUSDT" in row["title"] for row in rows))
        self.assertTrue(any(row["severity"] == "high" for row in rows))


class Alerts(unittest.TestCase):
    def test_level_rings_once(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        rules = os.path.join(tmp, "alerts.json")
        state = os.path.join(tmp, "state.json")
        prices = os.path.join(tmp, "prices.json")
        with open(prices, "w") as fh:
            json.dump({"symbol": "SOLUSDT", "lastPrice": "90"}, fh)
        code = alerts.main(["add", "--file", rules, "--symbol", "SOLUSDT", "--below", "95"])
        self.assertEqual(code, 0)
        code = alerts.main(["check", "--file", rules, "--state", state, "--offline", prices,
                            "--now", "2026-10-21 01:00"])
        self.assertEqual(code, 0)
        code = alerts.main(["check", "--file", rules, "--state", state, "--offline", prices,
                            "--now", "2026-10-21 01:05"])
        self.assertEqual(code, 0)
        fired = json.load(open(state))["fired"]
        self.assertEqual(len(fired), 1)

    def test_from_plan_stop(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        plan = os.path.join(tmp, "plan.json")
        rules = os.path.join(tmp, "alerts.json")
        with open(plan, "w") as fh:
            json.dump({
                "symbol": "SOLUSDT", "side": "buy", "stop": "106.41", "target": "106.85",
                "as_of": "2026-10-21T00:20:00Z", "hold_hours": 8,
            }, fh)
        alerts.main(["add", "--file", rules, "--from-plan", "--plan", plan])
        doc = json.load(open(rules))
        kinds = sorted(r["type"] for r in doc["rules"])
        self.assertEqual(kinds, ["stop", "target", "time"])


if __name__ == "__main__":
    unittest.main()
