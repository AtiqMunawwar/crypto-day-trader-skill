#!/usr/bin/env python3
"""Record offline fixtures from Binance's public data.binance.vision archive.

fapi.binance.com answers HTTP 451 from some regions, so the committed fixtures
come from the public futures archive (klines, mark price, index price, metrics,
funding) and from the public announcement catalog when it answers. Re-run this
only to refresh fixtures. Tests never call it and never touch the network.

    python3 scripts/record_fixtures.py
"""

import csv
import datetime
import io
import json
import os
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "fixtures", "binance")
UA = {"User-Agent": "crypto-trader-skill fixture recorder"}
BASE = "https://data.binance.vision/data/futures/um/"


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def unzip_csv(data):
    zf = zipfile.ZipFile(io.BytesIO(data))
    name = zf.namelist()[0]
    text = zf.read(name).decode("utf-8")
    return list(csv.reader(io.StringIO(text)))


def kline_to_api(rows):
    start = 1 if rows and rows[0] and rows[0][0] == "open_time" else 0
    out = []
    for r in rows[start:]:
        if len(r) < 11:
            continue
        out.append([
            int(float(r[0])), r[1], r[2], r[3], r[4], r[5],
            int(float(r[6])), r[7], int(float(r[8])), r[9], r[10],
            r[11] if len(r) > 11 else "0",
        ])
    return out


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    days = [datetime.date(2026, 10, 13) + datetime.timedelta(days=i) for i in range(14)]
    sol = []
    for day in days:
        url = BASE + "daily/klines/SOLUSDT/5m/SOLUSDT-5m-%s.zip" % day.isoformat()
        print("5m", day.isoformat())
        sol.extend(kline_to_api(unzip_csv(fetch(url))))
    with open(os.path.join(OUT, "SOLUSDT-5m.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "symbol": "SOLUSDT",
            "interval": "5m",
            "source": "data.binance.vision futures/um daily klines 2026-10-13 through 2026-10-26",
            "klines": sol,
        }, fh)
        fh.write("\n")

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "WIFUSDT"]
    daily = {}
    for sym in symbols:
        rows = []
        for month in ("2026-08", "2026-09"):
            url = BASE + "monthly/klines/%s/1d/%s-1d-%s.zip" % (sym, sym, month)
            print("1d", sym, month)
            rows.extend(kline_to_api(unzip_csv(fetch(url))))
        for i in range(1, 27):
            day = datetime.date(2026, 10, i)
            url = BASE + "daily/klines/%s/1d/%s-1d-%s.zip" % (sym, sym, day.isoformat())
            rows.extend(kline_to_api(unzip_csv(fetch(url))))
        by = {}
        for row in rows:
            by[row[0]] = row
        daily[sym] = [by[k] for k in sorted(by)]
        print(" ", sym, len(daily[sym]), "last", daily[sym][-1][4])
    with open(os.path.join(OUT, "daily.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "interval": "1d",
            "source": "data.binance.vision futures/um klines 2026-08-01 through 2026-10-26",
            "klines": daily,
        }, fh)
        fh.write("\n")

    met = unzip_csv(fetch(BASE + "daily/metrics/SOLUSDT/SOLUSDT-metrics-2026-10-26.zip"))
    with open(os.path.join(OUT, "SOLUSDT-metrics-2026-10-26.json"), "w", encoding="utf-8") as fh:
        json.dump({"header": met[0], "rows": met[1:],
                   "source": "data.binance.vision futures/um daily metrics 2026-10-26"}, fh)
        fh.write("\n")

    fund = unzip_csv(fetch(BASE + "monthly/fundingRate/SOLUSDT/SOLUSDT-fundingRate-2026-09.zip"))
    with open(os.path.join(OUT, "SOLUSDT-fundingRate-2026-09.json"), "w", encoding="utf-8") as fh:
        json.dump({"header": fund[0], "rows": fund[1:],
                   "source": "data.binance.vision futures/um monthly fundingRate 2026-09"}, fh)
        fh.write("\n")

    def last_row(kind):
        url = BASE + "daily/%s/SOLUSDT/5m/SOLUSDT-5m-2026-10-26.zip" % kind
        rows = unzip_csv(fetch(url))
        return rows[-1]

    mark = last_row("markPriceKlines")
    index = last_row("indexPriceKlines")
    last_fund = fund[-1]
    premium = {
        "symbol": "SOLUSDT",
        "markPrice": mark[4],
        "indexPrice": index[4],
        "estimatedSettlePrice": index[4],
        "lastFundingRate": last_fund[2],
        "interestRate": "0.00010000",
        "nextFundingTime": 1790812800000,
        "time": int(float(mark[0])) + 5 * 60 * 1000 - 1,
        "_fundingTime": int(float(last_fund[0])),
        "_note": ("markPrice and indexPrice are the last 5m closes on 2026-10-26 from "
                  "data.binance.vision. lastFundingRate is the final print in the 2026-09 "
                  "funding archive (the 2026-10 file was not published, and fapi.binance.com "
                  "returned HTTP 451 while these fixtures were recorded)."),
    }
    with open(os.path.join(OUT, "SOLUSDT-premiumIndex.json"), "w", encoding="utf-8") as fh:
        json.dump(premium, fh, indent=2)
        fh.write("\n")

    oi_hist = []
    for row in met[1:]:
        t = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        ms = int(calendar_ms(t))
        oi_hist.append({
            "symbol": "SOLUSDT",
            "sumOpenInterest": row[2],
            "sumOpenInterestValue": row[3],
            "timestamp": ms,
            "countLongShortRatio": row[6],
            "sumToptraderLongShortRatio": row[5],
        })
    with open(os.path.join(OUT, "SOLUSDT-openInterestHist.json"), "w", encoding="utf-8") as fh:
        json.dump(oi_hist, fh)
        fh.write("\n")
    with open(os.path.join(OUT, "SOLUSDT-openInterest.json"), "w", encoding="utf-8") as fh:
        json.dump({"symbol": "SOLUSDT", "openInterest": met[-1][2], "time": oi_hist[-1]["timestamp"]}, fh, indent=2)
        fh.write("\n")
    longs = []
    for row in oi_hist:
        ratio = float(row["countLongShortRatio"])
        longs.append({
            "symbol": "SOLUSDT",
            "longShortRatio": row["countLongShortRatio"],
            "longAccount": "%.8f" % (ratio / (1.0 + ratio)),
            "shortAccount": "%.8f" % (1.0 / (1.0 + ratio)),
            "timestamp": row["timestamp"],
        })
    with open(os.path.join(OUT, "SOLUSDT-longShort.json"), "w", encoding="utf-8") as fh:
        json.dump(longs, fh)
        fh.write("\n")

    # 24h ticker for SOL built from the last 288 five-minute bars (one UTC day).
    last_day = [r for r in sol if r[0] >= 1792972800000]
    o = float(last_day[0][1])
    h = max(float(r[2]) for r in last_day)
    l = min(float(r[3]) for r in last_day)
    c = float(last_day[-1][4])
    vol = sum(float(r[5]) for r in last_day)
    qv = sum(float(r[7]) for r in last_day)
    ticker = {
        "symbol": "SOLUSDT",
        "priceChange": "%.4f" % (c - o),
        "priceChangePercent": "%.3f" % ((c / o - 1.0) * 100.0),
        "lastPrice": last_day[-1][4],
        "openPrice": last_day[0][1],
        "highPrice": "%.4f" % h,
        "lowPrice": "%.4f" % l,
        "volume": "%.2f" % vol,
        "quoteVolume": "%.4f" % qv,
        "openTime": last_day[0][0],
        "closeTime": last_day[-1][6],
        "count": sum(int(r[8]) for r in last_day),
        "_note": "Aggregated from SOLUSDT 5m futures klines on 2026-10-26. Same fields as /fapi/v1/ticker/24hr.",
    }
    with open(os.path.join(OUT, "SOLUSDT-ticker-24hr.json"), "w", encoding="utf-8") as fh:
        json.dump(ticker, fh, indent=2)
        fh.write("\n")
    print("wrote fixtures to", OUT)
    return 0


def calendar_ms(dt):
    import calendar
    return calendar.timegm(dt.timetuple()) * 1000


if __name__ == "__main__":
    sys.exit(main())
