"""Merge raw holdings + ticker mapping + market data into data/holdings.json,
then inject the JSON into index.html between the HOLDINGS_DATA markers so the
dashboard works as a plain static page (no fetch()).

Run from repo root: python scripts/build_data.py
"""
import json, re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def cusip_of(h):
    c = h.get("cusip") or ""
    if not c and h.get("isin", "").startswith("US"):
        c = h["isin"][2:11]
    return c

def main():
    cfg = json.loads((ROOT / "config" / "funds.json").read_text())
    raw = json.loads((ROOT / "data" / "raw_holdings.json").read_text())
    tickers = json.loads((ROOT / "data" / "tickers.json").read_text())
    market = json.loads((ROOT / "data" / "market.json").read_text())
    min_w = cfg["params"]["min_weight_pct"]
    min_f = cfg["params"]["min_funds"]

    funds_out, stocks = [], {}
    for fund in cfg["funds"]:
        fid = fund["id"]
        rec = raw.get(fid)
        if not rec:
            continue
        funds_out.append({
            "id": fid, "name": fund["name"], "short": fund["short"],
            "type": fund["type"], "category": fund["category"],
            "report": rec["report_date"], "filed": rec.get("filed_date"),
            "aum": rec.get("total_usd"),
            "ytd": market.get("fundYtd", {}).get(fund.get("quoteTicker", ""), None),
        })
        prev_w = {}
        for h in rec.get("prev_holdings", []):
            c = cusip_of(h)
            if c in tickers:
                t = tickers[c]["ticker"]
                prev_w[t] = prev_w.get(t, 0) + h["weight"]
        cur_seen = {}
        for h in rec["holdings"]:
            c = cusip_of(h)
            if c not in tickers:
                continue
            t = tickers[c]["ticker"]
            cur_seen[t] = cur_seen.get(t, 0) + h["weight"]
            s = stocks.setdefault(t, {"ticker": t, "name": h["name"].title(),
                                      "positions": {}, "prevAgg": 0.0})
        for t, w in cur_seen.items():
            if w < min_w:
                continue
            pw = prev_w.get(t)
            flag = "new" if pw is None else ("up" if w > pw * 1.10 else
                                             "down" if w < pw * 0.90 else "")
            stocks[t]["positions"][fid] = {"w": round(w, 2), "prev": round(pw, 2) if pw else None,
                                           "flag": flag}
        for t, w in prev_w.items():
            if t in stocks:
                stocks[t]["prevAgg"] += w

    out_stocks = []
    for t, s in stocks.items():
        if len(s["positions"]) < min_f:
            continue
        m = market["stocks"].get(t, {})
        agg = sum(p["w"] for p in s["positions"].values())
        prev_agg = round(s.pop("prevAgg"), 1)
        new_count = sum(1 for p in s["positions"].values() if p["flag"] == "new")
        out_stocks.append({**s,
            "count": len(s["positions"]), "agg": round(agg, 1), "prevAgg": prev_agg,
            "isNew": new_count == len(s["positions"]),
            "mktCap": m.get("mktCap"), "sp500": m.get("sp500"),
            "ret1m": m.get("ret1m"), "ret3m": m.get("ret3m"), "ret1y": m.get("ret1y"),
            "ret3y": m.get("ret3y"), "ret5y": m.get("ret5y"),
            "val": m.get("val"), "valLabel": m.get("valLabel"),
        })
    out_stocks.sort(key=lambda s: (-s["count"], -s["agg"]))

    data = {
        "generated": datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M UTC"),
        "params": cfg["params"],
        "funds": funds_out,
        "stocks": out_stocks,
    }
    dest = ROOT / "data" / "holdings.json"
    dest.write_text(json.dumps(data))
    print(f"wrote {dest}: {len(out_stocks)} overlap stocks, {len(funds_out)} funds")

    # inject into index.html so the page needs no fetch()
    page = ROOT / "index.html"
    html = page.read_text(encoding="utf-8")
    payload = ("<!--HOLDINGS_DATA_START--><script>window.HOLDINGS_DATA = "
               + json.dumps(data) + ";</script><!--HOLDINGS_DATA_END-->")
    new_html, n = re.subn(r"<!--HOLDINGS_DATA_START-->.*?<!--HOLDINGS_DATA_END-->",
                          lambda _: payload, html, flags=re.S)
    if n != 1:
        raise SystemExit("HOLDINGS_DATA markers not found in index.html")
    page.write_text(new_html, encoding="utf-8")
    print("injected data into index.html")

if __name__ == "__main__":
    main()
