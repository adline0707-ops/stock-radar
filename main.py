import os
import threading
import time
from datetime import datetime
import pandas as pd
import yfinance as yf
from flask import Flask

app = Flask(__name__)

cached_html = "<h1>系統正在抓取數據，請稍候約 5 秒後重新整理...</h1>"

def fetch_and_generate():
    global cached_html
    while True:
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 修正上櫃股票為 .TWO
            intraday_candidates = [
                "2330.TW", "2454.TW", "2382.TW", "2317.TW",
                "3017.TW", "3324.TWO", "3231.TW", "6669.TW",
                "6442.TW", "3163.TWO", "4979.TWO",
                "NVDA", "TSLA", "AMD", "PLTR"
            ]
            etf_pool = ["0050.TW", "006208.TW", "00713.TW", "00878.TW", "VOO", "QQQ"]

            all_syms = list(set(intraday_candidates + etf_pool))
            data = yf.download(all_syms, period="3mo", group_by="ticker", auto_adjust=True, threads=True)

            # 1. 當沖候選 (僅保留綠燈做多訊號)
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

            # 2. ETF 均線監控
            etf_results = []
            if data is not None:
                for sym in etf_pool:
                    try:
                        df = data[sym].dropna()
                        if len(df) < 20:
                            continue
                        cur = df["Close"].iloc[-1]
                        ma20 = df["Close"].rolling(20).mean().iloc[-1]
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

            table_intra = df_intra.to_html(index=False) if not df_intra.empty else '<div class="empty">🛡️ 目前市場弱勢，所有觀察標的皆開低走弱，無符合做多訊號（嚴禁進場）。</div>'
            table_etf = df_etf.to_html(index=False) if not df_etf.empty else '<div class="empty">暫無數據。</div>'

            html = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta http-equiv="refresh" content="180">
    <title>選股決策雷達</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 16px; }
        .wrap { max-width: 960px; margin: auto; }
        .header-box { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; }
        h1 { color: #38bdf8; font-size: 20px; margin: 0 0 4px 0; }
        .sub { color: #94a3b8; font-size: 12px; }
        .timer-box { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 6px 10px; font-size: 11px; color: #38bdf8; }
        .card { background: #1e293b; border-radius: 12px; padding: 14px; margin-bottom: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.4); }
        .card-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 10px; }
        .title { font-size: 15px; font-weight: bold; }
        .badge { font-size: 11px; padding: 3px 6px; border-radius: 4px; font-weight: 600; }
        .badge-blue { background: #3b82f6; color: #fff; }
        .badge-green { background: #10b981; color: #fff; }
        .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #334155; white-space: nowrap; }
        th { color: #94a3b8; font-weight: 500; background: #1a2234; }
        tr:hover { background: #2d3748; }
        .empty { color: #f59e0b; padding: 12px 6px; font-size: 13px; line-height: 1.5; }
    </style>
</head>
<body>
    <div class="wrap">
        <div class="header-box">
            <div>
                <h1>📊 選股決策雷達 (雲端極速版)</h1>
                <div class="sub">更新時間：__TIME__</div>
            </div>
            <div class="timer-box">🔄 3分鐘自動刷新</div>
        </div>
        <div class="card">
            <div class="card-header">
                <span class="title">⚡ 1. 今日當沖可做多標的</span>
                <span class="badge badge-green">僅顯示進場訊號</span>
            </div>
            <div class="table-wrap">__TABLE_INTRA__</div>
        </div>
        <div class="card">
            <div class="card-header">
                <span class="title">💎 2. 長期持有 / 零股 (ETF 回檔監控)</span>
                <span class="badge badge-blue">逢回加碼</span>
            </div>
            <div class="table-wrap">__TABLE_ETF__</div>
        </div>
    </div>
</body>
</html>"""
            html = html.replace("__TIME__", now_str)
            html = html.replace("__TABLE_INTRA__", table_intra)
            html = html.replace("__TABLE_ETF__", table_etf)
            cached_html = html
        except Exception as e:
            print(f"背景更新錯誤: {e}")
        
        time.sleep(180)

@app.route("/")
def index():
    return cached_html

# 啟動後立即在背景開始更新
t = threading.Thread(target=fetch_and_generate, daemon=True)
t.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
