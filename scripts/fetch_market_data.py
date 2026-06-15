"""Resolve CUSIPs to tickers and fetch market data for all overlap stocks.

Inputs : data/raw_holdings.json, config/manual_overrides.json
Outputs: data/tickers.json   (cusip -> {ticker, name})
         data/market.json    (ticker -> mktCap, returns, valuation, sp500 flag)

Ticker resolution order:
  1. config/manual_overrides.json
  2. SEC fails-to-deliver files (free bulk CUSIP->SYMBOL source)
  3. OpenFIGI API (no key needed at low volume; US ISINs also carry the CUSIP)

Run from repo root: python scripts/fetch_market_data.py
"""
import io, json, time, zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "SmartMoneyDashboard adeokunyade@gmail.com"}

def collect_cusips():
    raw = json.loads((ROOT / "data" / "raw_holdings.json").read_text())
    cusips = {}
    for rec in raw.values():
        for key in ("holdings", "prev_holdings"):
            for h in rec.get(key, []):
                c = h.get("cusip") or ""
                if not c and h.get("isin", "").startswith("US"):
                    c = h["isin"][2:11]          # US ISIN embeds the CUSIP
                if c:
                    cusips.setdefault(c, h["name"])
    return cusips

def load_overrides():
    p = ROOT / "config" / "manual_overrides.json"
    return json.loads(p.read_text()) if p.exists() else {}

def ftd_mapping(months_back=3):
    """CUSIP->SYMBOL from SEC fails-to-deliver files (two half-month files per month)."""
    mapping = {}
    d = date.today()
    tried = 0
    while tried < months_back * 2 + 2 and len(mapping) < 1000:
        ym = f"{d.year}{d.month:02d}"
        for half in ("b", "a"):
            url = f"https://www.sec.gov/files/data/fails-deliver-data/cnsfails{ym}{half}.zip"
            try:
                r = requests.get(url, headers=UA, timeout=120)
                if r.status_code != 200:
                    continue
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    txt = z.read(z.namelist()[0]).decode("utf-8", "replace")
                for line in txt.splitlines()[1:]:
                    parts = line.split("|")
                    if len(parts) >= 3 and len(parts[1]) == 9 and parts[2]:
                        mapping.setdefault(parts[1].upper(), parts[2].upper())
                print(f"  FTD {ym}{half}: mapping now {len(mapping)} cusips")
            except Exception as e:
                print(f"  FTD {ym}{half} skipped: {e}")
            tried += 1
        d = (d.replace(day=1) - timedelta(days=1))
    return mapping

def openfigi_lookup(cusips):
    """Batch CUSIP->ticker via OpenFIGI (25 req/min keyless, 10 jobs per req)."""
    out = {}
    items = list(cusips)
    for i in range(0, len(items), 10):
        batch = items[i:i+10]
        jobs = [{"idType": "ID_CUSIP", "idValue": c} for c in batch]
        try:
            r = requests.post("https://api.openfigi.com/v3/mapping", json=jobs,
                              headers={"Content-Type": "application/json"}, timeout=60)
            if r.status_code == 429:
                time.sleep(20)
                r = requests.post("https://api.openfigi.com/v3/mapping", json=jobs,
                                  headers={"Content-Type": "application/json"}, timeout=60)
            if r.status_code == 200:
                for c, res in zip(batch, r.json()):
                    data = res.get("data") or []
                    if data and data[0].get("ticker"):
                        out[c] = data[0]["ticker"].replace("/", "-")
        except Exception as e:
            print(f"  openfigi batch failed: {e}")
        time.sleep(2.6)   # stay under keyless rate limit
    return out

def sp500_tickers():
    try:
        import pandas as pd
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                              storage_options=UA)
        return set(tables[0]["Symbol"].str.replace(".", "-", regex=False).str.upper())
    except Exception as e:
        print(f"  S&P500 list unavailable ({e}) — flag skipped")
        return set()

def fetch_market(tickers):
    import yfinance as yf
    import pandas as pd
    out = {}
    hist = yf.download(tickers, period="5y", interval="1d", auto_adjust=True,
                       progress=False, group_by="ticker", threads=True)
    for t in tickers:
        try:
            px = hist[t]["Close"].dropna() if isinstance(hist.columns, pd.MultiIndex) \
                 else hist["Close"].dropna()
            if px.empty:
                continue
            last = float(px.iloc[-1])
            def ret(days):
                target = px.index[-1] - pd.Timedelta(days=days)
                older = px[px.index <= target]
                return round(100 * (last / float(older.iloc[-1]) - 1), 1) if len(older) else None
            rec = {"ret1m": ret(30), "ret3m": ret(91), "ret1y": ret(365),
                   "ret3y": ret(365*3), "ret5y": ret(365*5)}
            tk = yf.Ticker(t)
            info = tk.info or {}
            rec["mktCap"] = info.get("marketCap")
            sector = (info.get("sector") or "").lower()
            pe = info.get("trailingPE")
            pb = info.get("priceToBook")
            ps = info.get("priceToSalesTrailing12Months")
            if "financial" in sector and pb:
                rec["val"], rec["valLabel"] = round(pb, 1), "P/B"
            elif pe and pe > 0:
                rec["val"], rec["valLabel"] = round(pe, 1), "P/E"
            elif ps:
                rec["val"], rec["valLabel"] = round(ps, 1), "P/S"
            ytd_start = px[px.index >= datetime(px.index[-1].year, 1, 1).strftime("%Y-%m-%d")]
            if len(ytd_start) > 1:
                rec["retYtd"] = round(100 * (last / float(ytd_start.iloc[0]) - 1), 1)
            out[t] = rec
        except Exception as e:
            print(f"  {t}: market data failed ({e})")
    return out

def fund_quotes(quote_tickers):
    """YTD return for mutual fund tickers (FDGRX etc.)."""
    import yfinance as yf
    out = {}
    for t in quote_tickers:
        try:
            px = yf.Ticker(t).history(period="ytd")["Close"].dropna()
            if len(px) > 1:
                out[t] = round(100 * (float(px.iloc[-1]) / float(px.iloc[0]) - 1), 1)
        except Exception:
            pass
    return out

def main():
    cusips = collect_cusips()
    print(f"{len(cusips)} unique cusips across all funds")
    overrides = load_overrides()
    mapping = {}
    ftd = ftd_mapping()
    for c in cusips:
        if c in overrides:
            mapping[c] = overrides[c]
        elif c in ftd:
            mapping[c] = ftd[c]
    missing = [c for c in cusips if c not in mapping]
    print(f"{len(mapping)} mapped via overrides/FTD, {len(missing)} -> OpenFIGI")
    if missing:
        mapping.update(openfigi_lookup(missing))
    unmapped = [c for c in cusips if c not in mapping]
    if unmapped:
        print(f"UNMAPPED ({len(unmapped)}): add to config/manual_overrides.json if relevant:")
        for c in unmapped[:40]:
            print(f'    "{c}": "TICKER",   // {cusips[c]}')
    (ROOT / "data" / "tickers.json").write_text(json.dumps(
        {c: {"ticker": t, "name": cusips.get(c, "")} for c, t in mapping.items()}))

    tickers = sorted(set(mapping.values()))
    print(f"fetching market data for {len(tickers)} tickers ...")
    market = fetch_market(tickers)
    spx = sp500_tickers()
    for t, rec in market.items():
        rec["sp500"] = t in spx if spx else None

    cfg = json.loads((ROOT / "config" / "funds.json").read_text())
    quotes = fund_quotes([f["quoteTicker"] for f in cfg["funds"] if f.get("quoteTicker")])
    (ROOT / "data" / "market.json").write_text(json.dumps({"stocks": market, "fundYtd": quotes}))
    print(f"wrote market.json ({len(market)} tickers, {len(quotes)} fund quotes)")

if __name__ == "__main__":
    main()
