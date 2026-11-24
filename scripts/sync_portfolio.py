import os
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
LSE_ASSETS = [
    {"ticker": "CSH2", "yahoo": "CSH2.L", "is_pence": True, "is_usd": False},
    {"ticker": "VNRT", "yahoo": "VNRT.L", "is_pence": False, "is_usd": False},
    {"ticker": "MIDD", "yahoo": "MIDD.L", "is_pence": True, "is_usd": False},
    {"ticker": "VERX", "yahoo": "VERX.L", "is_pence": False, "is_usd": False},
    {"ticker": "SJPA", "yahoo": "SJPA.L", "is_pence": True, "is_usd": False},
    {"ticker": "SAUS", "yahoo": "SAUS.L", "is_pence": True, "is_usd": False},
    {"ticker": "EXCS", "yahoo": "EXCS.L", "is_pence": False, "is_usd": False},
    {"ticker": "IGLN", "yahoo": "IGLN.L", "is_pence": False, "is_usd": True},
    {"ticker": "LQQ3", "yahoo": "LQQ3.L", "is_pence": True, "is_usd": False},
    {"ticker": "CRUD", "yahoo": "CRUD.L", "is_pence": False, "is_usd": True},
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

def fetch_ibkr_flex_positions():
    """從 IBKR Flex Web Service 抓取最新持倉股數與成本/現金"""
    if not IBKR_TOKEN or not IBKR_QUERY_ID:
        print("⚠️ 未偵測到 IBKR_TOKEN 或 IBKR_QUERY_ID，跳過 IBKR 持倉同步。")
        return {}, None

    send_url = (
        f"https://gdcdyn.interactivebrokers.com/Universal/servlet/"
        f"FlexStatementService.SendRequest?t={urllib.parse.quote(IBKR_TOKEN)}"
        f"&q={urllib.parse.quote(IBKR_QUERY_ID)}&v=3"
    )

    ref_code = None
    for _ in range(4):
        xml_txt = http_get_text(send_url)
        root = ET.fromstring(xml_txt)
        ref_elem = root.find(".//ReferenceCode")
        if ref_elem is not None and ref_elem.text:
            ref_code = ref_elem.text.strip()
            break
        time.sleep(3)

    if not ref_code:
        print("❌ IBKR Flex SendRequest 未能取得 ReferenceCode")
        return {}, None

    # 等待 3 秒讓 IBKR 伺服器生成報表
    time.sleep(3)
    get_url = (
        f"https://gdcdyn.interactivebrokers.com/Universal/servlet/"
        f"FlexStatementService.GetStatement?q={urllib.parse.quote(ref_code)}"
        f"&t={urllib.parse.quote(IBKR_TOKEN)}&v=3"
    )

    csv_txt = ""
    for _ in range(4):
        txt = http_get_text(get_url)
        if "Symbol" in txt or "Quantity" in txt:
            csv_txt = txt
            break
        time.sleep(3)

    if not csv_txt:
        print("❌ IBKR Flex GetStatement 逾時或無效回應")
        return {}, None

    positions = {}
    free_cash = None

    for raw_line in csv_txt.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('"MSG"') or line.startswith("MSG"):
            continue
        cols = [c.strip().strip('"') for c in line.split(",")]
        if len(cols) < 2 or cols[0] == "Symbol":
            continue

        sym = cols[0].upper()
        clean_sym = sym[:-1] if sym.endswith("L") and len(sym) >= 4 else sym

        # 偵測現金列
        if clean_sym in ("GBP", "CASH", "BASE_SUMMARY"):
            for val_str in reversed(cols[1:]):
                try:
                    free_cash = float(val_str)
                    break
                except ValueError:
                    continue
            continue

        # 解析持倉股數與均價/成本
        try:
            qty = float(cols[1])
            avg_price = None
            cost_basis = None
            if len(cols) >= 3 and cols[2]:
                val2 = float(cols[2])
                if qty > 0 and val2 > qty * 2:
                    cost_basis = val2
                    avg_price = val2 / qty
                else:
                    avg_price = val2
                    cost_basis = qty * val2
            positions[clean_sym] = {
                "position": qty,
                "avgPrice": round(avg_price, 4) if avg_price is not None else None,
                "costBasis": round(cost_basis, 2) if cost_basis is not None else None,
            }
        except ValueError:
            continue

    print(f"✅ 成功解析 IBKR 持倉：{len(positions)} 隻標的")
    return positions, free_cash

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
    ibkr_pos, ibkr_cash = fetch_ibkr_flex_positions()

    # 抓取 GBP/USD 匯率供 IGLN / CRUD 換算英鎊
    gbp_usd = 1.318
    try:
        fx_closes = fetch_yahoo_lse_series("GBPUSD=X")
        if fx_closes:
            gbp_usd = round(fx_closes[-1], 4)
    except Exception as e:
        print(f"⚠️ GBP/USD 匯率抓取失敗，沿用預設值: {e}")

    market_data = {}
    for asset in LSE_ASSETS:
        t = asset["ticker"]
        try:
            closes = fetch_yahoo_lse_series(asset["yahoo"])
            ind = compute_wilder_indicators(closes)
            if ind:
                price_gbp = ind["latestRaw"]
                if asset["is_pence"]:
                    price_gbp /= 100.0
                if asset["is_usd"]:
                    price_gbp /= gbp_usd

                market_data[t] = {
                    "priceGbp": round(price_gbp, 4),
                    "rsi2": ind["rsi2"],
                    "rsi14": ind["rsi14"],
                    "ma4": ind["ma4"],
                    "ma30": ind["ma30"],
                }
                print(f"✅ {asset['yahoo']}: £{price_gbp:.4f} | RSI(2)={ind['rsi2']}")
        except Exception as e:
            print(f"❌ {asset['yahoo']} 抓取失敗: {e}")

    output = {
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
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
