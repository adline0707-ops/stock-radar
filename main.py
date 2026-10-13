import os
import time
from datetime import datetime, timezone, timedelta
import urllib.parse
import urllib.request
import pandas as pd
import yfinance as yf
from flask import Flask

app = Flask(__name__)

TG_TOKEN = "8681385226:AAFvzJ-3L86hWNj_DYr1VZTa4X9dRi8SCYM"
TG_CHAT_ID = "8865457240"

notified_today = set()
last_notified_date = ""

cached_html = None
last_update_time = 0
CACHE_DURATION = 180

NAME_MAP = {
    "2330.TW": "台積電", "2454.TW": "聯發科", "2382.TW": "廣達", "2317.TW": "鴻海",
    "3017.TW": "奇鋐", "3324.TWO": "雙鴻", "3231.TW": "緯創", "6669.TW": "緯穎",
    "6442.TW": "光聖", "3163.TWO": "波若威", "4979.TWO": "華星光", "2303.TW": "聯電",
    "2308.TW": "台達電", "2379.TW": "瑞昱", "2603.TW": "長榮", "2881.TW": "富邦金",
    "2882.TW": "國泰金", "3008.TW": "大立光", "3711.TW": "日月光投控",
    "0050.TW": "元大台灣50", "006208.TW": "富邦台50", "00713.TW": "元大高息低波",
    "00878.TW": "國泰永續高股息", "VOO": "Vanguard標普500", "QQQ": "Invesco那斯達克",
    "NVDA": "輝達 NVIDIA", "TSLA": "特斯拉 Tesla", "AMD": "超微 AMD", "PLTR": "Palantir",
    "AAPL": "蘋果 Apple", "MSFT": "微軟 Microsoft", "GOOGL": "谷歌 Alphabet",
    "AMZN": "亞馬遜 Amazon", "QCOM": "高通 Qualcomm", "AVGO": "博通 Broadcom"
}

def get_tick_size(price):
    if price < 10:
        return 0.01
    elif price < 50:
        return 0.05
    elif price < 100:
        return 0.1
    elif price < 500:
        return 0.5
    elif price < 1000:
        return 1.0
    else:
        return 5.0

def calculate_scalp_plan(price, is_tw=True):
    if not is_tw:
        target1 = round(price * 1.015, 2)
        target2 = round(price * 1.035, 2)
        stop = round(price * 0.99, 2)
        return {
            "target1": target1, "pnl1": "+1.5%", "ticks1": 0,
            "target2": target2, "pnl2": "+3.5%",
            "stop": stop
        }

    tick = get_tick_size(price)
    shares = 1000
    buy_amount = price * shares
    fee_rate = 0.000855
    tax_rate = 0.0015

    # 搜尋第一目標（保底淨利 1,000 ~ 1,800 元，跳 2 ~ 6 檔）
    best_target1 = price + (tick * 3)
    best_net1 = 0
    best_ticks1 = 3

    for t in range(2, 7):
        target_p = price + (tick * t)
        sell_amount = target_p * shares
        fee_buy = max(20, buy_amount * fee_rate)
        fee_sell = max(20, sell_amount * fee_rate)
        tax = sell_amount * tax_rate
        net = sell_amount - buy_amount - (fee_buy + fee_sell + tax)
        if net >= 1000:
            best_target1 = round(target_p, 2)
            best_net1 = int(net)
            best_ticks1 = t
            break

    # 計算第二目標（波段拉大 +3.0% ~ +3.5%）
    target2_raw = price * 1.032
    # 對齊至最近的合法 tick
    tick2 = get_tick_size(target2_raw)
    target2 = round(round(target2_raw / tick2) * tick2, 2)
    sell2_amount = target2 * shares
    fee_buy = max(20, buy_amount * fee_rate)
    fee_sell2 = max(20, sell2_amount * fee_rate)
    tax2 = sell2_amount * tax_rate
    net2 = int(sell2_amount - buy_amount - (fee_buy + fee_sell2 + tax2))

    stop_loss = round(price - (tick * max(2, best_ticks1)), 2)

    return {
        "target1": best_target1,
        "pnl1": f"+{best_net1:,}元" if best_net1 > 0 else "+1.2%",
        "ticks1": best_ticks1,
        "target2": target2,
        "pnl2": f"+{net2:,}元" if net2 > 0 else "+3.2%",
        "stop": stop_loss
    }

def send_telegram_msg(msg):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": TG_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"TG 推播失敗: {e}")

def get_tw_now():
    tz_tw = timezone(timedelta(hours=8))
    return datetime.now(tz_tw)

def make_link(sym):
    name = NAME_MAP.get(sym, sym)
    clean_sym = sym.replace(".TWO", "").replace(".TW", "")
    if ".TW" in sym or ".TWO" in sym:
        url = f"https://tw.stock.yahoo.com/quote/{clean_sym}"
    else:
        url = f"https://finance.yahoo.com/quote/{sym}"
    return f'<a href="{url}" target="_blank" style="color:#38bdf8;text-decoration:none;font-weight:600;">{clean_sym} <span style="font-size:11px;color:#94a3b8;">({name})</span> ↗</a>'

def build_dashboard():
    global notified_today, last_notified_date
    now_dt = get_tw_now()
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    today_str = now_dt.strftime("%Y-%m-%d")
    current_time_val = now_dt.hour * 100 + now_dt.minute

    is_golden_time = (905 <= current_time_val <= 1015)
    is_market_closed = (current_time_val >= 1335 or current_time_val < 900)

    if today_str != last_notified_date:
        notified_today = set()
        last_notified_date = today_str

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
    index_sym = "^TWII"

    all_syms = list(set(intraday_candidates + etf_pool + potential_pool + [index_sym]))
    try:
        data = yf.download(all_syms, period="3mo", group_by="ticker", auto_adjust=True, threads=True)
    except Exception:
        data = None

    market_badge = '<span class="badge badge-amber">數據更新中</span>'
    market_text = "加權指數監控中..."
    if data is not None and index_sym in data.columns.levels[0]:
        try:
            df_idx = data[index_sym].dropna()
            if len(df_idx) >= 5:
                idx_cur = df_idx["Close"].iloc[-1]
                idx_prev = df_idx["Close"].iloc[-2]
                idx_ma5 = df_idx["Close"].rolling(5).mean().iloc[-1]
                diff = idx_cur - idx_prev
                diff_pct = (diff / idx_prev) * 100
                diff_str = f"{'+' if diff >= 0 else ''}{diff:.1f} ({'+' if diff >= 0 else ''}{diff_pct:.2f}%)"
                if idx_cur >= idx_ma5:
                    market_badge = '<span class="badge badge-green">多方控盤 · 站上5日線</span>'
                    market_text = f"加權指數：{idx_cur:.1f} ({diff_str}) ｜ 大盤強勢，順勢操作。"
                else:
                    market_badge = '<span class="badge badge-red">空方震盪 · 跌破5日線</span>'
                    market_text = f"加權指數：{idx_cur:.1f} ({diff_str}) ｜ 大盤偏弱，小賺即跑，嚴控停損！"
        except Exception:
            pass

    long_results = []
    short_results = []
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
                hi = today_row["High"]
                prev_c = prev_row["Close"]
                amp_5d = (((df["High"] - df["Low"]) / df["Close"]) * 100).rolling(5).mean().iloc[-1]
                vwap = (today_row["High"] + today_row["Low"] + today_row["Close"]) / 3
                vol_ratio = (today_row["Volume"] / df["Volume"].rolling(5).mean().iloc[-2]) if ("Volume" in today_row and df["Volume"].rolling(5).mean().iloc[-2] > 0) else 1.0

                stock_name = NAME_MAP.get(sym, sym)
                is_tw = (".TW" in sym or ".TWO" in sym)
                plan = calculate_scalp_plan(cur, is_tw=is_tw)

                change_pct = ((cur - prev_c) / prev_c) * 100
                pullback_from_high = ((hi - cur) / hi) * 100

                if (cur >= op) and (cur >= prev_c) and (cur >= vwap) and (change_pct < 8.5) and (pullback_from_high <= 1.5):
                    long_results.append({
                        "標的": make_link(sym),
                        "現價": round(cur, 2),
                        "第一目標(保底小賺)": f"<b>{plan['target1']}</b> ({plan['pnl1']})",
                        "第二目標(主力衝高)": f"<b style='color:#38bdf8;'>{plan['target2']}</b> ({plan['pnl2']})",
                        "嚴守停損": plan["stop"],
                        "5日振幅": f"{amp_5d:.1f}%",
                        "量能": f"{vol_ratio:.1f}x 均量" if vol_ratio > 1.2 else "常態量",
                        "訊號": "🔥 多方帶量攻"
                    })
                    if is_golden_time and (sym not in notified_today):
                        notified_today.add(sym)
                        msg = (
                            f"⚡ <b>【當沖黃金雙目標訊號】</b>\n"
                            f"標的：<b>{stock_name} ({sym})</b>\n"
                            f"現價：<b>{cur:.2f}</b> (漲幅 {change_pct:+.2f}%)\n\n"
                            f"🎯 <b>第一目標 (保底)：{plan['target1']}</b> ({plan['pnl1']}，跳{plan['ticks1']}檔)\n"
                            f"🚀 <b>第二目標 (拉大)：{plan['target2']}</b> ({plan['pnl2']})\n"
                            f"🛑 <b>嚴守停損：{plan['stop']}</b>\n\n"
                            f"💡 <b>實戰建議：</b>\n"
                            f"1. 達第一目標可先平倉落袋 1~2 千，或出半數部位。\n"
                            f"2. 剩餘部位將停損移至成本價，零風險挑戰第二目標！"
                        )
                        send_telegram_msg(msg)

                elif (cur < op) and (cur < prev_c) and (cur < vwap) and (change_pct > -8.5):
                    short_results.append({
                        "標的": make_link(sym),
                        "現價": round(cur, 2),
                        "反彈壓力(停損)": round(cur * 1.01, 2),
                        "下殺目標(-2%)": round(cur * 0.98, 2),
                        "5日振幅": f"{amp_5d:.1f}%",
                        "訊號": "⚠️ 弱勢破線走跌"
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
                        "標的": make_link(sym),
                        "現價": round(cur, 2),
                        "月線(20MA)": round(ma20, 2),
                        "月線乖離": f"+{bias_ma20:.1f}%",
                        "型態": "💎 多頭起漲·回檔有撐",
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
                bias_ma20 = ((cur - ma20) / ma20) * 100
                signal = "定期定額正常扣款" if cur >= ma20 else "⚡ 破月線回檔加碼！"
                etf_results.append({
                    "標的": make_link(sym),
                    "現價": round(cur, 2),
                    "月線(20MA)": round(ma20, 2),
                    "乖離率": f"{bias_ma20:+.1f}%",
                    "操作指引": signal
                })
            except Exception:
                continue

    df_long = pd.DataFrame(long_results)
    df_short = pd.DataFrame(short_results)
    df_etf = pd.DataFrame(etf_results)
    df_pot = pd.DataFrame(potential_results).sort_values(by="_score", ascending=False).drop(columns=["_score"]) if potential_results else pd.DataFrame()

    table_long = df_long.to_html(index=False, escape=False) if not df_long.empty else '<div class="empty">🛡️ 今日無符合強勢做多標的（多方觀望，嚴禁盲目進場追高）。</div>'
    table_short = df_short.to_html(index=False, escape=False) if not df_short.empty else '<div class="empty">盤面無明顯重挫標的。</div>'
    table_pot = df_pot.to_html(index=False, escape=False) if not df_pot.empty else '<div class="empty">目前無符合低風險起漲點之潛力標的。</div>'
    table_etf = df_etf.to_html(index=False, escape=False) if not df_etf.empty else '<div class="empty">暫無數據。</div>'

    if is_golden_time:
        timing_banner = '<div style="background:#064e3b;border:1px solid #059669;padding:8px 12px;border-radius:8px;font-size:12px;color:#a7f3d0;margin-bottom:12px;">🔥 <b>黃金動能時段 (09:05-10:15)</b>：市場量能充沛，雙目標推播啟動！</div>'
    elif is_market_closed:
        timing_banner = '<div style="background:#1e293b;border:1px solid #334155;padding:8px 12px;border-radius:8px;font-size:12px;color:#94a3b8;margin-bottom:12px;">🌙 <b>台股已收盤/休市中</b>：數據已結算，可參考波段與 ETF 扣款區。</div>'
    else:
        timing_banner = '<div style="background:#78350f;border:1px solid #d97706;padding:8px 12px;border-radius:8px;font-size:12px;color:#fde68a;margin-bottom:12px;">⏳ <b>已過黃金動能期 (10:15後)</b>：盤勢易量縮洗刷，關閉當沖推播，建議停止新開倉！</div>'

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
        .header-box {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }}
        h1 {{ color: #38bdf8; font-size: 20px; margin: 0 0 4px 0; }}
        .sub {{ color: #94a3b8; font-size: 12px; }}
        .timer-box {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 6px 10px; font-size: 11px; color: #38bdf8; text-align: right; }}
        .market-banner {{ background: #1a2234; border: 1px solid #334155; border-radius: 10px; padding: 10px 14px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; font-size: 13px; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 14px; margin-bottom: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.4); }}
        .card-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 10px; }}
        .title {{ font-size: 15px; font-weight: bold; }}
        .badge {{ font-size: 11px; padding: 3px 6px; border-radius: 4px; font-weight: 600; }}
        .badge-red {{ background: #ef4444; color: #fff; }}
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
                <h1>📊 選股決策雷達 (雙目標進階版)</h1>
                <div class="sub">更新時間 (台北)：{now_str}</div>
            </div>
            <div class="timer-box">🔄 3分鐘自動刷新<br><span style="color:#10b981;">🎯 雙目標動態試算已啟用</span></div>
        </div>

        {timing_banner}

        <div class="market-banner">
            <div>🧭 <strong>大盤風向球：</strong> {market_text}</div>
            <div>{market_badge}</div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="title">⚡ 1. 今日當沖：強勢做多（保底小賺 vs 主力波段）</span>
                <span class="badge badge-green">保底+拉大雙機制</span>
            </div>
            <div class="table-wrap">{table_long}</div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="title">⚠️ 2. 弱勢避險 / 放空參考標的</span>
                <span class="badge badge-red">破今開均價線</span>
            </div>
            <div class="table-wrap">{table_short}</div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="title">🎯 3. AI 潛力股（波段起漲·回檔有撐）</span>
                <span class="badge badge-amber">MA5&gt;MA20 · 乖離&lt;8%</span>
            </div>
            <div class="table-wrap">{table_pot}</div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="title">💎 4. 長期持有 / 零股 (ETF 回檔監控)</span>
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

try:
    send_telegram_msg("🚀 <b>【雙目標停利系統已上線】</b>\n🎯 第一目標：保底小賺 1,000~2,000 元（跳2~4檔）\n📈 第二目標：主力衝高拉大至 3,000~5,500 元（+3.2%）\n兼顧天天落袋與大行情獲利！")
except Exception:
    pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
