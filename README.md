# Smart Money Overlap Dashboard

A dashboard showing which stocks are held by multiple top hedge funds and mutual funds,
built from SEC EDGAR filings (13F-HR for hedge funds, NPORT-P for mutual funds).
Live site: **https://aokunyade.github.io/smart-money/**

Data refreshes automatically on the 1st of each month via GitHub Actions.
Note: 13F filings are quarterly (~45 days after quarter end), so holdings change
four times a year — the monthly run keeps prices/returns current and picks up
new filings as they land.

## One-time GitHub setup (about 10 minutes)

You already have a GitHub account (`aokunyade`). Do these steps in your browser:

**1. Create the repository**
- Go to https://github.com/new
- Repository name: `smart-money`
- Leave it **Public** (required for free GitHub Pages)
- Do NOT check "Add a README" — leave everything unchecked
- Click **Create repository**

**2. Upload this folder's files**
- On the new repo page, click the **"uploading an existing file"** link
- Open this `smart-money` folder on your computer in File Explorer
- Select ALL files and folders inside it (Ctrl+A) and drag them onto the GitHub upload page
  - Important: the `.github` folder must be included. If drag-and-drop skips it,
    use "choose your files" and select everything, or upload it separately.
- Commit message: `initial upload` → click **Commit changes**

**3. Enable GitHub Pages**
- In the repo, go to **Settings → Pages** (left sidebar)
- Under "Build and deployment" → **Source**, choose **GitHub Actions**

**4. Run the first build**
- Go to the **Actions** tab. If asked, click "I understand my workflows, enable them"
- Click **"Refresh data and deploy"** in the left list → **Run workflow** → green **Run workflow** button
- The run takes 10–20 minutes (it downloads ~60 SEC filings and market data for ~150 stocks)
- When it shows a green check, visit **https://aokunyade.github.io/smart-money/**

That's it. The site now refreshes itself on the 1st of every month. To refresh manually
anytime, repeat step 4.

## Changing the fund list

Edit `config/funds.json` (you can do this directly on GitHub: open the file → pencil icon → commit).
Each fund needs:
- 13F funds: `id`, `name`, `short`, `type: "13F"`, `category`, `cik` (find it by searching the
  manager at https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany)
- Mutual funds: same plus `seriesId` (S000…) and `quoteTicker`

Committing the change triggers a rebuild automatically.

You can also change the overlap rules in the `params` block (`min_weight_pct`, `min_funds`).

## If some stocks show "—" everywhere or are missing

A few CUSIPs may fail to map to tickers (the Actions log prints `UNMAPPED` lines).
Add them to `config/manual_overrides.json` like:

```json
{ "G00894108": "ARX" }
```

## Running locally (optional)

```
pip install -r requirements.txt
python scripts/fetch_filings.py
python scripts/fetch_market_data.py
python scripts/build_data.py
```

Then open `index.html` in a browser. Holdings data is embedded into the page,
so no local server is needed.

## How it works

- `scripts/fetch_filings.py` — pulls the latest + previous quarter's holdings for every fund
  from SEC EDGAR (throttled, cached, with a proper User-Agent per SEC policy)
- `scripts/fetch_market_data.py` — maps CUSIPs→tickers (SEC fails-to-deliver data, then the
  OpenFIGI API), then pulls prices/returns/valuations via yfinance and the S&P 500 list
- `scripts/build_data.py` — applies the overlap rules (≥0.25% position, held by ≥2 funds),
  computes quarter-over-quarter flags (▲ added >10%, ▼ trimmed >10%, ★ new position),
  writes `data/holdings.json`, and embeds it into `index.html`
- `.github/workflows/refresh.yml` — monthly schedule + manual button + deploy to Pages

Data notes: 13F filings only cover US-listed long positions (no shorts, no foreign-listed
shares, no bonds). Mutual fund weights come from NPORT-P `pctVal` (% of net assets).
Aggregate-weight change (ΔQ) compares against the previous quarter's filings only.
