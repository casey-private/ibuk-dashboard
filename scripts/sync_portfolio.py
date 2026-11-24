import os
import csv
import json
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# 1. 從 GitHub Secrets 讀取加密環境變數
IBKR_TOKEN = os.environ.get("IBKR_TOKEN", "").strip()
IBKR_QUERY_ID = os.environ.get("IBKR_QUERY_ID", "").strip()

# 2. 倫敦交易所 (LSE) ETF 元數據與單位設定
# 注意：IGLN.L 與 CRUD.L 在倫敦交易所報價本身已為英鎊 (GBP)，無需再除以 GBP/USD 匯率
LSE_ASSETS = [
    {"ticker": "CSH2", "yahoo": "CSH2.L", "is_pence": True, "is_usd": False},
    {"ticker": "VNRT", "yahoo": "VNRT.L", "is_pence": False, "is_usd": False},
    {"ticker": "MIDD", "yahoo": "MIDD.L", "is_pence": True, "is_usd": False},
    {"ticker": "VERX", "yahoo": "VERX.L", "is_pence": False, "is_usd": False},
    {"ticker": "SJPA", "yahoo": "SJPA.L", "is_pence": True, "is_usd": False},
    {"ticker": "SAUS", "yahoo": "SAUS.L", "is_pence": True, "is_usd": False},
    {"ticker": "EXCS", "yahoo": "EXCS.L", "is_pence": False, "is_usd": False},
    {"ticker": "IGLN", "yahoo": "IGLN.L", "is_pence": False, "is_usd": False},
    {"ticker": "LQQ3", "yahoo": "LQQ3.L", "is_pence": True, "is_usd": False},
    {"ticker": "CRUD", "yahoo": "CRUD.L", "is_pence": False, "is_usd": False},
]

def http_get_text(url, retries=4, delay=3):
    """帶重試與瀏覽器 User-Agent 的 HTTP GET 工具"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise e

def parse_float(val):
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if not s or s == "-" or s.upper() == "N/A":
        return None
    try:
        return float(s)
    except ValueError:
        return None

def fetch_ibkr_flex_positions():
    """從 IBKR Flex Web Service 抓取最新持倉股數、成本及 Cash Report (Base Currency Summary)"""
    if not IBKR_TOKEN or not IBKR_QUERY_ID:
        print("⚠️ 未偵測到 IBKR_TOKEN 或 IBKR_QUERY_ID，跳過 IBKR 持倉同步。")
        return {}, None

    send_url = (
        f"https://gdcdyn.interactivebrokers.com/Universal/servlet/"
        f"FlexStatementService.SendRequest?t={urllib.parse.quote(IBKR_TOKEN)}"
        f"&q={urllib.parse.quote(IBKR_QUERY_ID)}&v=3"
    )

    ref_code = None
    for _ in range(6):
        xml_txt = http_get_text(send_url, retries=4, delay=4)
        root = ET.fromstring(xml_txt)
        ref_elem = root.find(".//ReferenceCode")
        if ref_elem is not None and ref_elem.text:
            ref_code = ref_elem.text.strip()
            break
        time.sleep(4)

    if not ref_code:
        print("❌ IBKR Flex SendRequest 未能取得 ReferenceCode")
        return {}, None

    time.sleep(4)
    get_url = (
        f"https://gdcdyn.interactivebrokers.com/Universal/servlet/"
        f"FlexStatementService.GetStatement?q={urllib.parse.quote(ref_code)}"
        f"&t={urllib.parse.quote(IBKR_TOKEN)}&v=3"
    )

    csv_txt = ""
    for _ in range(8):
        txt = http_get_text(get_url, retries=4, delay=4)
        if "Symbol" in txt or "Quantity" in txt or "EndingCash" in txt:
            csv_txt = txt
            break
        time.sleep(4)

    if not csv_txt:
        print("❌ IBKR Flex GetStatement 逾時或無效回應")
        return {}, None

    positions = {}
    free_cash = None
    found_base_summary = False

    pos_headers = None
    cash_headers = None
    active_section = None  # 'POS' or 'CASH'

    reader = csv.reader(csv_txt.splitlines())
    for raw_row in reader:
        row = [c.strip() for c in raw_row]
        if not row or not any(row):
            continue

        first = row[0].upper()
        # 跳過 IBKR 報表封裝標記與警告列
        if first in ("BOF", "EOF", "BOA", "EOA", "BOS", "EOS", "MSG"):
            continue

        # 情況 A：IBKR 多區塊標準格式 ("HEADER","POST",... / "DATA","POST",...)
        if first == "HEADER" and len(row) >= 3:
            sec_code = row[1].upper()
            cols = row[2:]
            if "Symbol" in cols and "Quantity" in cols:
                pos_headers = cols
                print(f"📋 偵測到持倉區塊 ({sec_code}): {cols}")
            elif "EndingCash" in cols or "EndingSettledCash" in cols:
                cash_headers = cols
                print(f"💰 偵測到現金區塊 ({sec_code}): {cols}")
            continue

        if first == "DATA" and len(row) >= 3:
            sec_code = row[1].upper()
            vals = row[2:]
            if pos_headers and len(vals) >= 2 and ("Symbol" in pos_headers):
                # 判斷是否屬於持倉資料列
                row_dict = dict(zip(pos_headers, vals))
                if row_dict.get("Symbol") and parse_float(row_dict.get("Quantity")) is not None:
                    _extract_position(row_dict, positions)
                    continue
            if cash_headers and ("EndingCash" in cash_headers or "EndingSettledCash" in cash_headers):
                row_dict = dict(zip(cash_headers, vals))
                cash_val, is_base = _extract_cash(row_dict)
                if cash_val is not None and (is_base or not found_base_summary):
                    free_cash = cash_val
                    if is_base:
                        found_base_summary = True
            continue

        # 情況 B：無 HEADER/DATA 前綴的連續表格格式
        if "Symbol" in row and "Quantity" in row:
            pos_headers = row
            active_section = "POS"
            print(f"📋 偵測到持倉標題列: {row}")
            continue

        if "EndingCash" in row or "EndingSettledCash" in row:
            cash_headers = row
            active_section = "CASH"
            print(f"💰 偵測到現金標題列: {row}")
            continue

        if active_section == "POS" and pos_headers:
            row_dict = dict(zip(pos_headers, row))
            _extract_position(row_dict, positions)
        elif active_section == "CASH" and cash_headers:
            row_dict = dict(zip(cash_headers, row))
            cash_val, is_base = _extract_cash(row_dict)
            if cash_val is not None and (is_base or not found_base_summary):
                free_cash = cash_val
                if is_base:
                    found_base_summary = True

    print(f"✅ 成功解析 IBKR 持倉：{len(positions)} 隻標的 | 自由現金 (freeCash): {free_cash}")
    return positions, free_cash

def _extract_position(row_dict, positions):
    sym = row_dict.get("Symbol", "").strip().upper()
    if not sym:
        return
    clean_sym = sym[:-1] if sym.endswith("L") and len(sym) >= 4 else sym
    qty = parse_float(row_dict.get("Quantity"))
    if qty is None:
        return

    avg_price = parse_float(row_dict.get("CostBasisPrice"))
    cost_basis = parse_float(row_dict.get("CostBasisMoney"))

    if cost_basis is None and avg_price is not None:
        cost_basis = qty * avg_price
    elif avg_price is None and cost_basis is not None and qty > 0:
        avg_price = cost_basis / qty

    positions[clean_sym] = {
        "position": qty,
        "avgPrice": round(avg_price, 4) if avg_price is not None else None,
        "costBasis": round(cost_basis, 2) if cost_basis is not None else None,
    }

def _extract_cash(row_dict):
    curr = (
        row_dict.get("CurrencyPrimary")
        or row_dict.get("Currency")
        or ""
    ).strip().upper()

    is_base = curr in ("BASE_SUMMARY", "BASE", "TOTAL")
    if curr and not is_base and curr != "GBP":
        return None, False

    val = parse_float(row_dict.get("EndingCash"))
    if val is None:
        val = parse_float(row_dict.get("EndingSettledCash"))

    if val is not None:
        return round(val, 2), is_base
    return None, False

def compute_wilder_indicators(closes):
    """100% 還原 Excel Data_XXX 的 Wilder 平滑 RSI(2), RSI(14) 與 MA4, MA30"""
    if not closes or len(closes) < 16:
        return None

    ma4 = sum(closes[-4:]) / min(4, len(closes))
    ma30 = sum(closes[-30:]) / min(30, len(closes))

    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(diff if diff > 0 else 0.0)
        losses.append(abs(diff) if diff < 0 else 0.0)

    # Wilder RSI(2)
    avg_g2 = (gains[0] + gains[1]) / 2.0
    avg_l2 = (losses[0] + losses[1]) / 2.0
    for i in range(2, len(gains)):
        avg_g2 = (avg_g2 * 1.0 + gains[i]) / 2.0
        avg_l2 = (avg_l2 * 1.0 + losses[i]) / 2.0
    rsi2 = 100.0 if avg_l2 == 0 else 100.0 - (100.0 / (1.0 + (avg_g2 / avg_l2)))

    # Wilder RSI(14)
    avg_g14 = sum(gains[:14]) / 14.0
    avg_l14 = sum(losses[:14]) / 14.0
    for i in range(14, len(gains)):
        avg_g14 = (avg_g14 * 13.0 + gains[i]) / 14.0
        avg_l14 = (avg_l14 * 13.0 + losses[i]) / 14.0
    rsi14 = 100.0 if avg_l14 == 0 else 100.0 - (100.0 / (1.0 + (avg_g14 / avg_l14)))

    return {
        "latestRaw": closes[-1],
        "rsi2": round(rsi2, 2),
        "rsi14": round(rsi14, 2),
        "ma4": round(ma4, 3),
        "ma30": round(ma30, 3),
    }

def fetch_yahoo_lse_series(symbol):
    """直接從伺服器抓取 Yahoo Finance LSE 3 個月日線（無任何 CORS 限制）"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=3mo&interval=1d"
    raw = http_get_text(url)
    data = json.loads(raw)
    result = data["chart"]["result"][0]
    raw_closes = result["indicators"]["quote"][0]["close"]
    closes = [float(c) for c in raw_closes if c is not None]
    meta_price = result.get("meta", {}).get("regularMarketPrice")
    if meta_price is not None and closes:
        closes[-1] = float(meta_price)
    return closes

def main():
    # 先讀取上一份 data.json 作為防洗白後備（避免 IBKR 伺服器偶發逾時覆蓋成空值）
    prev_data = {}
    if os.path.exists("data.json"):
        try:
            with open("data.json", "r", encoding="utf-8") as f:
                prev_data = json.load(f)
        except Exception as e:
            print(f"⚠️ 讀取舊 data.json 失敗: {e}")

    ibkr_pos, ibkr_cash = fetch_ibkr_flex_positions()

    ibkr_sync_status = "LIVE"
    if not ibkr_pos and prev_data.get("ibkrPositions"):
        ibkr_pos = prev_data["ibkrPositions"]
        ibkr_sync_status = "CACHED_FALLBACK"
        print("🛡️ IBKR 持倉本次未有回傳，已自動保留上一次成功同步之持倉數據。")

    if ibkr_cash is None and prev_data.get("freeCash") is not None:
        ibkr_cash = prev_data["freeCash"]
        print(f"🛡️ IBKR 現金本次未有回傳，已自動保留上一次自由現金: {ibkr_cash}")

    gbp_usd = prev_data.get("gbpUsdRate", 1.318)
    try:
        fx_closes = fetch_yahoo_lse_series("GBPUSD=X")
        if fx_closes:
            gbp_usd = round(fx_closes[-1], 4)
    except Exception as e:
        print(f"⚠️ GBP/USD 匯率抓取失敗，沿用預設值: {e}")

    market_data = dict(prev_data.get("marketData", {}))
    for asset in LSE_ASSETS:
        t = asset["ticker"]
        try:
            closes = fetch_yahoo_lse_series(asset["yahoo"])
            ind = compute_wilder_indicators(closes)
            if ind:
                price_gbp = ind["latestRaw"]
                ma4_gbp = ind["ma4"]
                ma30_gbp = ind["ma30"]
                if asset["is_pence"]:
                    price_gbp /= 100.0
                    ma4_gbp /= 100.0
                    ma30_gbp /= 100.0
                if asset["is_usd"]:
                    price_gbp /= gbp_usd
                    ma4_gbp /= gbp_usd
                    ma30_gbp /= gbp_usd

                market_data[t] = {
                    "priceGbp": round(price_gbp, 4),
                    "rsi2": ind["rsi2"],
                    "rsi14": ind["rsi14"],
                    "ma4": round(ma4_gbp, 2),
                    "ma30": round(ma30_gbp, 2),
                }
                print(f"✅ {asset['yahoo']}: £{price_gbp:.4f} | RSI(2)={ind['rsi2']} | MA4=£{ma4_gbp:.2f}")
        except Exception as e:
            print(f"❌ {asset['yahoo']} 抓取失敗（保留舊值）: {e}")

    output = {
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "ibkrSyncStatus": ibkr_sync_status,
        "gbpUsdRate": gbp_usd,
        "freeCash": ibkr_cash,
        "ibkrPositions": ibkr_pos,
        "marketData": market_data,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("🎉 data.json 更新完成！")

if __name__ == "__main__":
    main()
