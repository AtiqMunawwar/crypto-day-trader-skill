"""Scanner against recorded fixtures. No live network."""

import os
import unittest

from _load import FIX, load

scan = load("trader-scanner", "scan")


class Scanner(unittest.TestCase):
    def test_universe(self):
        code = scan.main(["universe", "--offline", os.path.join(FIX, "exchangeInfo.json"), "--no-write", "--json"])
        self.assertEqual(code, 0)

    def test_daily_recorded(self):
        code = scan.main([
            "daily", "--offline", os.path.join(FIX, "daily.json"),
            "--now", "2026-10-27 12:00", "--no-write", "--json",
        ])
        self.assertEqual(code, 0)

    def test_sol_utc_orb(self):
        code = scan.main([
            "orb", "--offline", os.path.join(FIX, "SOLUSDT-5m.json"),
            "--symbols", "SOL", "--date", "2026-10-21", "--at", "00:20",
            "--now", "2026-10-21 00:20", "--side", "buy", "--no-write",
        ])
        self.assertEqual(code, 0)

    def test_movers_flag_a_small_cap_pump(self):
        code = scan.main([
            "movers", "--offline", os.path.join(FIX, "movers.json"),
            "--min-quote-volume", "0", "--top", "5", "--no-write",
        ])
        self.assertEqual(code, 0)

    def test_roll_runs_offline(self):
        code = scan.main([
            "roll", "--offline", os.path.join(FIX, "SOLUSDT-5m.json"),
            "--symbols", "SOLUSDT", "--window-minutes", "60",
            "--now", "2026-10-21 04:00", "--no-write",
        ])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
