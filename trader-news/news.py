#!/usr/bin/env python3
"""Binance public announcements. Not financial advice.

Reads the public CMS catalog (listings, news, delistings). No API key.
A headline is not a reason to trade. This tool never places an order.

Stock-only sources from the original pack (SEC EDGAR, Yahoo headlines,
earnings filings) are not here.

    python3 news.py --symbols SOL,BTC --offline tests/fixtures/binance/announcements.json
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_public as bx  # noqa: E402

# Catalog ids on the public Binance announcement board, as of the fixture.
CATALOGS = {
    48: ("listing", "high"),
    49: ("news", "info"),
    161: ("delisting", "high"),
    157: ("maintenance", "info"),
}


def articles_from(doc):
    """The CMS payload, or a trimmed fixture of the same shape."""
    found = []
    catalogs = []
    if isinstance(doc, dict) and "catalogs" in doc and isinstance(doc["catalogs"], dict):
        # fixture: {"catalogs": {"48": <api doc>}}
        for key, payload in doc["catalogs"].items():
            catalogs.append((int(key), payload))
    elif isinstance(doc, dict) and "data" in doc:
        catalogs.append((None, doc))
    elif isinstance(doc, list):
        return doc
    for hinted, payload in catalogs:
        data = payload.get("data") if isinstance(payload, dict) else None
        for cat in (data or {}).get("catalogs") or []:
            cid = cat.get("catalogId") or hinted
            name = cat.get("catalogName") or ""
            for art in cat.get("articles") or []:
                found.append({
                    "id": art.get("id"),
                    "code": art.get("code"),
                    "title": art.get("title") or "",
                    "releaseDate": art.get("releaseDate"),
                    "catalogId": cid,
                    "catalogName": name,
                })
    return found


def _bases(symbols):
    bases = []
    for raw in symbols:
        sym = raw.strip().upper()
        if not sym:
            continue
        if sym.endswith("USDT") and len(sym) > 4:
            bases.append(sym[:-4])
        else:
            bases.append(sym)
    return bases


def mentions(title, bases):
    """Whole-token match so SOL does not match ISOLATED. SOLUSDT counts as SOL."""
    hits = []
    upper = title.upper()
    for base in bases:
        if re.search(r"(?<![A-Z0-9])%s(?![A-Z0-9])" % re.escape(base), upper):
            hits.append(base)
            continue
        if re.search(r"(?<![A-Z0-9])%sUSDT(?![A-Z0-9])" % re.escape(base), upper):
            hits.append(base)
    return hits


def select(items, bases, now, hours, catalogs):
    cutoff = bx.utc_ms(now) - int(float(hours) * 3600 * 1000)
    out = []
    for item in items:
        cid = item.get("catalogId")
        if catalogs and cid not in catalogs:
            continue
        released = item.get("releaseDate")
        if released is not None and int(released) < cutoff:
            continue
        tags = mentions(item.get("title") or "", bases) if bases else []
        if bases and not tags:
            continue
        kind, severity = CATALOGS.get(cid, ("other", "info"))
        if cid == 161:
            severity = "high"
        out.append(dict(item, bases=tags, kind=kind, severity=severity))
    out.sort(key=lambda r: r.get("releaseDate") or 0, reverse=True)
    return out


def describe(rows, errors):
    if errors:
        for err in errors:
            print("Source error: %s" % err)
    if not rows:
        print("No matching announcements in the window.")
    for row in rows:
        when = ""
        if row.get("releaseDate"):
            when = bx.ms_to_utc(int(row["releaseDate"])).strftime("%Y-%m-%d %H:%M")
        print("[%s] %s  %s" % (row.get("severity") or "info", when, row.get("title")))
        if row.get("bases"):
            print("  mentions %s  (%s)" % (", ".join(row["bases"]), row.get("kind")))
    print("An announcement can move a contract and can also be noise. Read it. This is not a trade.")
    print(bx.disclaimer())


def fetch_live(client, catalogs, page_size):
    items = []
    errors = []
    for cid in catalogs:
        try:
            doc = bx.fetch_announcements(client, cid, page_size)
        except bx.BinanceError as e:
            errors.append(str(e))
            continue
        items.extend(articles_from(doc))
    return items, errors


def main(argv=None):
    p = argparse.ArgumentParser(prog="news.py", description="Binance announcements. Not financial advice.",
                                epilog=bx.disclaimer())
    p.add_argument("--symbols", default="", help="SOL,BTC or SOLUSDT,BTCUSDT")
    p.add_argument("--hours", type=float, default=72)
    p.add_argument("--catalogs", default="48,49,161", help="CMS catalog ids")
    p.add_argument("--offline")
    p.add_argument("--now")
    p.add_argument("--page-size", type=int, default=20)
    p.add_argument("--pause", type=float, default=bx.PAUSE)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        catalogs = [int(x) for x in a.catalogs.split(",") if x.strip()]
        bases = _bases(a.symbols.split(",")) if a.symbols else []
        now = bx.parse_now(a.now)
        errors = []
        if a.offline:
            items = articles_from(bx.read_json(a.offline))
        else:
            items, errors = fetch_live(bx.Client(pause=a.pause), catalogs, a.page_size)
        rows = select(items, bases, now, a.hours, set(catalogs))
        describe(rows, errors)
        if a.json:
            print(bx.json.dumps({"items": rows}, indent=2, sort_keys=True))
        return 0
    except bx.BinanceError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
