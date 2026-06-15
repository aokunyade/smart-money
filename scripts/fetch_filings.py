"""Fetch latest (and previous) holdings for every fund in config/funds.json.

13F funds:  via https://data.sec.gov/submissions/  ->  13F-HR information table XML
NPORT funds: via series-scoped EDGAR browse  ->  NPORT-P primary_doc.xml

Output: data/raw_holdings.json
  { fundId: { "report_date", "filed_date", "total_usd",
              "holdings": [{name, cusip, value, weight}],          # latest
              "prev_holdings": [...], "prev_report_date": ... } }  # one quarter back

Run from repo root:  python scripts/fetch_filings.py
"""
import json, re, sys, time, html
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "SmartMoneyDashboard adeokunyade@gmail.com"}
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

_last_req = [0.0]
def get(url, as_text=True):
    """Throttled, cached GET (EDGAR etiquette: <10 req/s, identify yourself)."""
    key = re.sub(r"[^A-Za-z0-9]+", "_", url)[-150:]
    cached = CACHE / key
    if cached.exists():
        data = cached.read_bytes()
    else:
        wait = 0.15 - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            try:
                r = requests.get(url, headers=UA, timeout=60)
                _last_req[0] = time.time()
                if r.status_code == 200:
                    data = r.content
                    cached.write_bytes(data)
                    break
                print(f"  HTTP {r.status_code} {url} (attempt {attempt+1})")
            except requests.RequestException as e:
                print(f"  retry {attempt+1}: {e}")
            time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"failed to fetch {url}")
    return data.decode("utf-8", "replace") if as_text else data

def strip_ns(tag):
    return tag.rsplit("}", 1)[-1]

# ---------------------------------------------------------------- 13F ----
def latest_13f_accessions(cik, n=2):
    sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
    rec = sub["filings"]["recent"]
    out = []
    for form, acc, fdate, rdate in zip(rec["form"], rec["accessionNumber"],
                                       rec["filingDate"], rec["reportDate"]):
        if form == "13F-HR":  # originals only; amendments (13F-HR/A) skipped for v1
            out.append({"accession": acc, "filed": fdate, "report": rdate})
        if len(out) == n:
            break
    return out

def fetch_13f_holdings(cik, accession):
    cik_i, acc = int(cik), accession.replace("-", "")
    idx = json.loads(get(f"https://www.sec.gov/Archives/edgar/data/{cik_i}/{acc}/index.json"))
    xml_files = [f["name"] for f in idx["directory"]["item"] if f["name"].lower().endswith(".xml")]
    table_xml = None
    for name in xml_files:
        if "primary_doc" in name.lower():
            continue
        body = get(f"https://www.sec.gov/Archives/edgar/data/{cik_i}/{acc}/{name}")
        if "informationtable" in body.lower() or "<infotable" in body.lower():
            table_xml = body
            break
    if table_xml is None:  # some filers put the table inside primary_doc
        for name in xml_files:
            body = get(f"https://www.sec.gov/Archives/edgar/data/{cik_i}/{acc}/{name}")
            if "<infotable" in body.lower():
                table_xml = body
                break
    if table_xml is None:
        raise RuntimeError(f"no information table found for CIK {cik} {accession}")
    return parse_13f_table(table_xml)

def parse_13f_table(xml_text):
    root = ET.fromstring(xml_text.encode())
    holdings = {}
    for el in root.iter():
        if strip_ns(el.tag).lower() != "infotable":
            continue
        row = {strip_ns(c.tag).lower(): c for c in el}
        def txt(k, sub=None):
            node = row.get(k)
            if node is None:
                return ""
            if sub:
                for c in node:
                    if strip_ns(c.tag).lower() == sub:
                        return (c.text or "").strip()
                return ""
            return (node.text or "").strip()
        if txt("putcall"):          # skip options
            continue
        cusip = txt("cusip").upper().replace(" ", "")
        value = float(txt("value") or 0)
        name = html.unescape(txt("nameofissuer"))
        if not cusip or value <= 0:
            continue
        h = holdings.setdefault(cusip, {"name": name, "cusip": cusip, "value": 0.0})
        h["value"] += value          # merge multiple lots of the same security
    # 13F values are whole dollars since 2023-01; sanity-checked in verify step
    return list(holdings.values())

# -------------------------------------------------------------- NPORT ----
def latest_nport_accessions(series_id, n=2):
    atom = get("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
               f"&CIK={series_id}&type=NPORT-P&dateb=&owner=include&count={n+2}&output=atom")
    accs = re.findall(r"<accession-number>([\d-]+)</accession-number>", atom)
    hrefs = re.findall(r"Archives/edgar/data/(\d+)/", atom)
    types = re.findall(r"<filing-type>([^<]+)</filing-type>", atom)
    out = []
    for acc, cik_num, ftype in zip(accs, hrefs, types):
        if ftype != "NPORT-P":
            continue
        out.append({"accession": acc, "cik": cik_num})
        if len(out) == n:
            break
    return out

def fetch_nport_holdings(cik_num, accession):
    acc = accession.replace("-", "")
    body = get(f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc}/primary_doc.xml")
    root = ET.fromstring(body.encode())
    info = {"holdings": [], "total": None, "report": None}
    for el in root.iter():
        t = strip_ns(el.tag).lower()
        if t == "totassets" and info["total"] is None:
            info["total"] = float(el.text)
        elif t == "reppddate":
            info["report"] = el.text
        elif t == "invstorsec":
            row = {strip_ns(c.tag).lower(): c for c in el}
            def txt(k):
                node = row.get(k)
                return (node.text or "").strip() if node is not None and node.text else ""
            cat = txt("assetcat")
            if cat and cat not in ("EC",):   # equities only (EC = equity-common)
                continue
            pct = txt("pctval")
            cusip = txt("cusip").upper().replace(" ", "")
            if cusip in ("", "N/A", "000000000"):
                cusip = ""
            isin = ""
            ids = row.get("identifiers")
            if ids is not None:
                for c in ids:
                    if strip_ns(c.tag).lower() == "isin":
                        isin = c.attrib.get("value", "")
            if not pct:
                continue
            info["holdings"].append({
                "name": html.unescape(txt("name")),
                "cusip": cusip, "isin": isin,
                "value": float(txt("valusd") or 0),
                "weight": float(pct),       # pctVal is % of net assets
            })
    return info

# --------------------------------------------------------------- main ----
def main():
    cfg = json.loads((ROOT / "config" / "funds.json").read_text())
    out = {}
    for fund in cfg["funds"]:
        fid = fund["id"]
        print(f"[{fid}] {fund['name']} ({fund['type']})")
        try:
            if fund["type"] == "13F":
                filings = latest_13f_accessions(fund["cik"], 2)
                if not filings:
                    raise RuntimeError("no 13F-HR filings found")
                cur = fetch_13f_holdings(fund["cik"], filings[0]["accession"])
                total = sum(h["value"] for h in cur)
                for h in cur:
                    h["weight"] = round(100 * h["value"] / total, 4)
                rec = {"report_date": filings[0]["report"], "filed_date": filings[0]["filed"],
                       "total_usd": total, "holdings": cur}
                if len(filings) > 1:
                    prev = fetch_13f_holdings(fund["cik"], filings[1]["accession"])
                    ptotal = sum(h["value"] for h in prev) or 1
                    for h in prev:
                        h["weight"] = round(100 * h["value"] / ptotal, 4)
                    rec["prev_holdings"] = prev
                    rec["prev_report_date"] = filings[1]["report"]
            else:
                filings = latest_nport_accessions(fund["seriesId"], 2)
                if not filings:
                    raise RuntimeError("no NPORT-P filings found")
                cur = fetch_nport_holdings(filings[0]["cik"], filings[0]["accession"])
                rec = {"report_date": cur["report"], "filed_date": None,
                       "total_usd": cur["total"], "holdings": cur["holdings"]}
                if len(filings) > 1:
                    prev = fetch_nport_holdings(filings[1]["cik"], filings[1]["accession"])
                    rec["prev_holdings"] = prev["holdings"]
                    rec["prev_report_date"] = prev["report"]
            n = len(rec["holdings"])
            top = max(rec["holdings"], key=lambda h: h["weight"]) if n else None
            print(f"   {n} positions, report {rec['report_date']}"
                  + (f", top: {top['name']} {top['weight']:.1f}%" if top else ""))
            out[fid] = rec
        except Exception as e:
            print(f"   !! FAILED: {e}", file=sys.stderr)
    dest = ROOT / "data" / "raw_holdings.json"
    dest.write_text(json.dumps(out))
    print(f"\nwrote {dest} ({len(out)}/{len(cfg['funds'])} funds)")
    if len(out) < len(cfg["funds"]) * 0.8:
        sys.exit("too many fund failures — aborting so stale data isn't published")

if __name__ == "__main__":
    main()
