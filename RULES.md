# IBUK Quantitative Investment Portfolio Dashboard — Project Rules

## 1. Project Architecture
- **Repo**: `casey-private/ibuk-dashboard` (Public repo, deployed via GitHub Pages on `main` branch)
- **Live URL**: `https://casey-private.github.io/ibuk-dashboard/`
- **Core Files**:
  - `index.html`: Single-file frontend dashboard (HTML / React / Tailwind CSS). Automatically reads `data.json` on load with LocalStorage fallback.
  - `scripts/sync_portfolio.py`: Automated Python backend that pulls IBKR Flex Query CSV + Yahoo Finance LSE (`.L`) historical daily closes, computes Wilder's Smoothing indicators, and outputs `data.json`.
  - `.github/workflows/daily_sync.yml`: Automated GitHub Actions cron running Mon–Fri at 17:30 UTC (London close) and 21:30 UTC (US close), committing updated `data.json` via `github-actions[bot]`.
  - `data.json`: Auto-generated portfolio snapshot.

## 2. Security & Git Workflow Rules (CRITICAL)
- **NEVER hardcode secrets**: `IBKR_TOKEN`, `IBKR_QUERY_ID`, and any API keys must NEVER be committed to code or `RULES.md`. They live in GitHub Repository Secrets. For local Python testing, use a local `.env` file and ensure `.env` is listed in `.gitignore`.
- **Always `git pull` before editing or pushing**: `github-actions[bot]` commits `data.json` twice daily. Always pull latest `main` first to avoid merge conflicts.
- **Anti-wipe protection in `sync_portfolio.py`**: IBKR Flex servers occasionally time out. Never overwrite `ibkrPositions` or `freeCash` in `data.json` with empty/null values; always inherit the previous valid `data.json` values if IBKR fails.

## 3. Quantitative Investment Rules (v2.0)
- **Account**: Interactive Brokers UK (IBUK), base currency GBP.
- **Currency & Indicators**:
  - LSE tickers quoted in pence (GBp, e.g., CSH2, MIDD, LQQ3) must be divided by 100 to display prices, MA4, and MA30 consistently in GBP (£).
  - Technical indicators (RSI(2), RSI(14), MA4, MA30) must use **Wilder's Smoothing** based on daily closing prices.

### Core Portfolio (95% Target Allocation)
- **CSH2** (GBP Overnight Money Market): **20.0%** (House deposit defense line)
- **VNRT** (US Large Cap): **25.0%**
- **MIDD** (UK FTSE 250): **9.0%**
- **VERX** (Europe ex-UK): **9.0%**
- **SJPA** (Japan): **9.0%**
- **SAUS** (Australia): **9.0%**
- **EXCS** (EM ex-China): **9.0%**
- **IGLN** (Physical Gold ETC): **5.0%**
- *(Note: CRUD is retired and excluded from active targets.)*

### Core Trading Signals
- **Minimum Rebalance Threshold**: **£300**. Even if oversold and underweight, do NOT trigger a Buy signal if the funding gap is under £300 (show watch/hold status instead).
- **Buy Signal**: Daily `RSI(2) < 15` AND `Difference < 0` AND gap `>= £300`.
- **Take-Profit Signal**: Daily `RSI(2) > 85` AND `Difference > 0` (applies to IGLN as well).
- **CSH2 20% Defense Formula**:
  `True CSH2 % = CSH2 Market Value / (Total NAV - Reserved Cash for Pending LQQ3 Limit Orders)`
  Must remain `>= 20.0%`. Trigger alert if below 20%.

### Satellite Portfolio — LQQ3 (3x NASDAQ 100, 5% Target)
- **Strictly Isolated**: NEVER include LQQ3 in Core Rebalancing calculations.
- **3-Tranche GTC Limit Buy Grid (Max 21 shares)**:
  - Tranche 1: 6 shares @ 21,900 GBp (£1,314)
  - Tranche 2: 6 shares @ 20,700 GBp (£1,242)
  - Tranche 3: 9 shares @ 18,900 GBp (£1,701)
  - Never use CSH2 20% defense funds for a 4th tranche.
- **Exit Signals**:
  - **Take Profit**: LQQ3 return `>= +18%` OR `VNRT daily RSI(2) > 85` (sell half, move stop to breakeven, sweep profits to CSH2).
  - **Price Stop-Loss**: Below `16,200 GBp`.
  - **Time Stop-Loss**: Count trading days (excluding weekends) from Tranche 1 fill date; warn at 15 trading days to halve position to avoid 3x volatility decay.

## 4. UI/UX Standards
- Must stay mobile-friendly (iPhone Safari/Chrome).
- Keep the first column (Ticker / Name) frozen (`sticky left-0`) for horizontal scrolling on mobile.
- Prevent awkward line breaks (`whitespace-nowrap`) on currency gaps and status badges.
