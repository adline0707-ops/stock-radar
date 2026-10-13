import os
import time
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
from flask import Flask

app = Flask(__name__)

cached_html = None
last_update_time = 0
CACHE_DURATION = 180

def get_tw_time():
    # 轉換為台灣時區 (UTC+8)
    tz_tw = timezone(timedelta(hours=8))
    return datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M:%S")

def build_dashboard():
    now_str = get_tw_time()
    intraday_candidates = [
        "2330.TW", "2454.TW", "2382.TW", "2317.TW",
        "3017.TW", "3324.TWO", "3231.TW", "6669.TW",
        "6442.TW", "3163.TWO", "4979.TWO",
        "NVDA", "TSLA", "AMD", "PLTR"
    ]
    etf_pool = ["0050.TW", "006208.TW", "00713.TW", "00878.TW", "VOO", "QQQ"]
    potential_pool = [
        "2330.TW", "2454.TW", "2303.TW", "2382.TW", "2308.TW", "2379.TW",
        "2603.TW", "2881.TW", "2882.TW", "3008.TW", "3711.TW",
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "AMD", "QCOM", "AVGO"
    ]

    all_syms = list(set(intraday_candidates + etf_pool + potential_pool))
    try:
        data = yf.download(all_syms, period="3mo", group_by="ticker", auto_adjust=True, threads=True)
    except Exception:
        data = None

    intraday_results = []
    if data is not None:
        for sym in intraday_candidates:
            try:
                df = data[sym].dropna()
                if len(df) < 5:
                    continue
                today_row = df.iloc[-1]
                prev_row = df.iloc[-2]
                cur = today_row["Close"]
                op = today_row["Open"]
                prev_c = prev_row["Close"]
                amp_5d = (((df["High"] - df["Low"]) / df["Close"]) * 100).rolling(5).mean().iloc[-1]
                vwap = (today_row["High"] + today_row["Low"] + today_row["Close"]) / 3

                if (cur >= op) and (cur >= prev_c) and (cur >= vwap):
                    intraday_results.append({
                        "標的代號": sym,
                        "現價": round(cur, 2),
                        "停損(-1%)": round(cur * 0.99, 2),
                        "目標(+2%)": round(cur * 1.02, 2),
                        "5日振幅": f"{amp_5d:.1f}%",
                        "訊號": "✅ 帶量強勢"
                    })
            except Exception:
                continue

    potential_results = []
    if data is not None:
        for sym in potential_pool:
            try:
                df = data[sym].dropna()
                if len(df) < 25:
                    continue
                cur = df["Close"].iloc[-1]
                ma5 = df["Close"].rolling(5).mean().iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]
                bias_ma20 = ((cur - ma20) / ma20) * 100

                if cur >= ma20 and ma5 >= ma20 and 0 <= bias_ma20 <= 8.0:
                    score = (cur / ma20) * 100
                    potential_results.append({
                        "代號": sym,
                        "現價": round(cur, 2),
                        "月線(20MA)": round(ma20, 2),
                        "月線乖離": f"+{bias_ma20:.1f}%",
                        "型態評估": "🔥 多頭排列·回檔有撐",
                        "_score": score
                    })
            except Exception:
                continue

    etf_results = []
    if data is not None:
        for sym in etf_pool:
            try:
                df = data[sym].dropna()
                if len(df) < 15:
                    continue
                cur = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20, min_periods=5).mean().iloc[-1]
                signal = "定期定額正常扣款" if cur >= ma20 else "⚡ 月線回檔加碼"
                etf_results.append({
                    "代號": sym,
                    "現價": round(cur, 2),
                    "月線(20MA)": round(ma20, 2),
                    "狀態": signal
                })
            except Exception:
                continue

    df_intra = pd.DataFrame(intraday_results)
    df_etf = pd.DataFrame(etf_results)
    if potential_results:
        df_pot = pd.DataFrame(potential_results).sort_values(by="_score", ascending=False).drop(columns=["_score"])
    else:
        df_pot = pd.DataFrame()

    table_intra = df_intra.to_html(index=False) if not df_intra.empty else '<div class="empty">🛡️ 目前市場弱勢，所有觀察標的皆開低走弱，無符合做多訊號（嚴禁進場）。</div>'
    table_pot = df_pot.to_html(index=False) if not df_pot.empty else '<div class="empty">盤勢偏弱，目前無符合低風險起漲點之潛力標的。</div>'
    table_etf = df_etf.to_html(index=False) if not df_etf.empty else '<div class="empty">暫無數據。</div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta http-equiv="refresh" content="180">
    <title>選股決策雷達</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 16px; }}
        .wrap {{ max-width: 960px; margin: auto; }}
        .header-box {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; }}
        h1 {{ color: #38bdf8; font-size: 20px; margin: 0 0 4px 0; }}
        .sub {{ color: #94a3b8; font-size: 12px; }}
        .timer-box {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 6px 10px; font-size: 11px; color: #38bdf8; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 14px; margin-bottom: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.4); }}
        .card-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 10px; }}
        .title {{ font-size: 15px; font-weight: bold; }}
        .badge {{ font-size: 11px; padding: 3px 6px; border-radius: 4px; font-weight: 600; }}
        .badge-blue {{ background: #3b82f6; color: #fff; }}
        .badge-green {{ background: #10b981; color: #fff; }}
        .badge-amber {{ background: #f59e0b; color: #1e293b; }}
        .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid #334155; white-space: nowrap; }}
        th {{ color: #94a3b8; font-weight: 500; background: #1a2234; }}
        tr:hover {{ background: #2d3748; }}
        .empty {{ color: #f59e0b; padding: 12px 6px; font-size: 13px; line-height: 1.5; }}
    </style>
</head>
<body>
    <div class="wrap">
        <div class="header-box">
            <div>
                <h1>📊 選股決策雷達 (雲端極速版)</h1>
                <div class="sub">更新時間 (台北)：{now_str}</div>
            </div>
            <div class="timer-box">🔄 3分鐘自動刷新</div>
        </div>
        <div class="card">
            <div class="card-header">
                <span class="title">⚡ 1. 今日當沖可做多標的</span>
                <span class="badge badge-green">僅顯示進場訊號</span>
            </div>
            <div class="table-wrap">{table_intra}</div>
        </div>
        <div class="card">
            <div class="card-header">
                <span class="title">🎯 2. AI 潛力股（波段起漲·回檔有撐）</span>
                <span class="badge badge-amber">MA5&gt;MA20 · 乖離&lt;8%</span>
            </div>
            <div class="table-wrap">{table_pot}</div>
        </div>
        <div class="card">
            <div class="card-header">
                <span class="title">💎 3. 長期持有 / 零股 (ETF 回檔監控)</span>
                <span class="badge badge-blue">逢回加碼</span>
            </div>
            <div class="table-wrap">{table_etf}</div>
        </div>
    </div>
</body>
</html>"""
    return html

@app.route("/")
def index():
    global cached_html, last_update_time
    now = time.time()
    if cached_html is None or (now - last_update_time > CACHE_DURATION):
        cached_html = build_dashboard()
        last_update_time = now
    return cached_html

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
