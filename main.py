import os
import time
import json
import threading
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
    if price < 10: return 0.01
    elif price < 50: return 0.05
    elif price < 100: return 0.1
    elif price < 500: return 0.5
    elif price < 1000: return 1.0
    else: return 5.0

def calculate_scalp_plan(price, is_tw=True):
    if not is_tw:
        target1 = round(price * 1.015, 2)
        target2 = round(price * 1.035, 2)
        stop = round(price * 0.99, 2)
        return {"target1": target1, "pnl1": "+1.5%", "ticks1": 0, "target2": target2, "pnl2": "+3.5%", "stop": stop}

    tick = get_tick_size(price)
    shares = 1000
    buy_amount = price * shares
    fee_rate = 0.000855
    tax_rate = 0.0015

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

    target2_raw = price * 1.032
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

def send_telegram_msg(msg, target_chat_id=None, show_keyboard=True):
    try:
        cid = target_chat_id or TG_CHAT_ID
        params = {
            "chat_id": cid,
            "text": msg,
            "parse_mode": "HTML"
        }
        if show_keyboard:
            reply_markup = {
                "keyboard": [
                    [{"text": "⚡ 當沖可以買什麼"}, {"text": "📈 今天可以買什麼"}],
                    [{"text": "🌙 明天可以買什麼"}, {"text": "🇺🇸 美股可以買什麼"}]
                ],
                "resize_keyboard": True
            }
            params["reply_markup"] = json.dumps(reply_markup)

        payload = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=payload)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"TG 推播失敗: {e}")

def get_tw_now():
    tz_tw = timezone(timedelta(hours=8))
    return datetime.now(tz_tw)

def scan_market_data():
    tw_intraday = [
        "2330.TW", "2454.TW", "2382.TW", "2317.TW",
        "3017.TW", "3324.TWO", "3231.TW", "6669.TW",
        "6442.TW", "3163.TWO", "4979.TWO"
    ]
    tw_etf = ["0050.TW", "006208.TW", "00713.TW", "00878.TW"]
    tw_potential = ["2330.TW", "2454.TW", "2303.TW", "2382.TW", "2308.TW", "2379.TW", "2603.TW", "2881.TW", "2882.TW", "3008.TW", "3711.TW"]
    us_stocks = ["NVDA", "TSLA", "AMD", "PLTR", "AAPL", "MSFT", "GOOGL", "AMZN", "QCOM", "AVGO", "VOO", "QQQ"]
    indices = ["^TWII", "^GSPC"]

    all_syms = list(set(tw_intraday + tw_etf + tw_potential + us_stocks + indices))
    try:
        data = yf.download(all_syms, period="3mo", group_by="ticker", auto_adjust=True, threads=True)
    except Exception:
        data = None
    return data, tw_intraday, tw_etf, tw_potential, us_stocks

def handle_query(query_type):
    now_dt = get_tw_now()
    now_str = now_dt.strftime("%H:%M:%S")
    data, tw_intraday, tw_etf, tw_potential, us_stocks = scan_market_data()
    if data is None:
        return "⚠️ 目前連線市場數據庫逾時，請稍後 30 秒再試一次！"

    twii_text = "加權指數監控中"
    if "^TWII" in data.columns.levels[0]:
        try:
            df_tw = data["^TWII"].dropna()
            c = df_tw["Close"].iloc[-1]
            ma5 = df_tw["Close"].rolling(5).mean().iloc[-1]
            diff = c - df_tw["Close"].iloc[-2]
            twii_text = f"加權指數 {c:.0f} ({diff:+.0f}) ｜ " + ("多方控盤" if c >= ma5 else "偏弱震盪")
        except Exception: pass

    # 1. 當沖可以買什麼
    if query_type == "scalp":
        msg = f"⚡ <b>【台股當沖量化雷達】</b> ({now_str})\n🧭 <b>大盤氛圍：</b> {twii_text}\n\n"
        hits = []
        for sym in tw_intraday:
            try:
                df = data[sym].dropna()
                if len(df) < 5: continue
                cur = df["Close"].iloc[-1]
                op = df["Open"].iloc[-1]
                hi = df["High"].iloc[-1]
                prev_c = df["Close"].iloc[-2]
                vwap = (df["High"].iloc[-1] + df["Low"].iloc[-1] + cur) / 3
                pullback = ((hi - cur) / hi) * 100
                chg = ((cur - prev_c) / prev_c) * 100

                if (cur >= op) and (cur >= prev_c) and (cur >= vwap) and (chg < 8.5) and (pullback <= 1.5):
                    name = NAME_MAP.get(sym, sym)
                    plan = calculate_scalp_plan(cur, is_tw=True)
                    hits.append(
                        f"🔹 <b>{name} ({sym.split('.')[0]})</b> 現價 <b>{cur:.2f}</b> ({chg:+.2f}%)\n"
                        f"   🎯 保底小賺：<b>{plan['target1']}</b> ({plan['pnl1']}，跳{plan['ticks1']}檔)\n"
                        f"   🚀 主力拉大：<b>{plan['target2']}</b> ｜ 🛑 停損：{plan['stop']}\n"
                    )
            except Exception: pass
        if hits:
            msg += "🎯 <b>【當前符合爆量起漲標的】</b>\n\n" + "\n".join(hits[:3])
            msg += "\n💡 <i>達標第一目標即可平倉保底 1~2 千，或移至保本讓利潤奔馳！</i>"
        else:
            msg += "🛡️ <b>目前盤面無符合嚴格當沖條件標的。</b>\n（無量或回檔過大，請耐心空手觀望，切忌盲目追高！）"
        return msg

    # 2. 今天可以買什麼
    elif query_type == "today":
        msg = f"📈 <b>【今日台股精選（波段起漲 / ETF 扣款）】</b> ({now_str})\n🧭 <b>大盤氛圍：</b> {twii_text}\n\n"
        pot_hits = []
        for sym in tw_potential:
            try:
                df = data[sym].dropna()
                if len(df) < 25: continue
                c = df["Close"].iloc[-1]
                ma5 = df["Close"].rolling(5).mean().iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]
                bias = ((c - ma20) / ma20) * 100
                if c >= ma20 and ma5 >= ma20 and 0.5 <= bias <= 8.0:
                    name = NAME_MAP.get(sym, sym)
                    pot_hits.append(f"• <b>{name} ({sym.split('.')[0]})</b>：現價 {c:.2f} (月線支撐 {ma20:.2f}，乖離 +{bias:.1f}%)")
            except Exception: pass

        msg += "🎯 <b>【低風險波段買點標的】</b>\n" + ("\n".join(pot_hits[:3]) if pot_hits else "目前盤面震盪，無低基期起漲標的。\n")
        
        etf_hits = []
        for sym in tw_etf:
            try:
                df = data[sym].dropna()
                c = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20, min_periods=5).mean().iloc[-1]
                bias = ((c - ma20) / ma20) * 100
                name = NAME_MAP.get(sym, sym)
                sig = "⚡ 破月線超值加碼區！" if c < ma20 else "正常定期扣款"
                etf_hits.append(f"• <b>{name}</b> ({c:.2f})：乖離 {bias:+.1f}% ｜ {sig}")
            except Exception: pass
        msg += "\n\n💎 <b>【ETF 扣款戰略】</b>\n" + "\n".join(etf_hits)
        return msg

    # 3. 明天可以買什麼
    elif query_type == "tomorrow":
        msg = f"🌙 <b>【明日開盤獵股觀察清單】</b> ({now_str})\n🧭 <b>大盤總結：</b> {twii_text}\n\n"
        picks = []
        for sym in tw_potential:
            try:
                df = data[sym].dropna()
                if len(df) < 25: continue
                c = df["Close"].iloc[-1]
                ma5 = df["Close"].rolling(5).mean().iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]
                bias = ((c - ma20) / ma20) * 100
                if c >= ma20 and ma5 >= ma20 and 0.5 <= bias <= 7.0:
                    name = NAME_MAP.get(sym, sym)
                    picks.append(f"🔥 <b>{name} ({sym.split('.')[0]})</b> 收盤 {c:.2f}\n   月線支撐 {ma20:.2f} (乖離 +{bias:.1f}%)\n   👉 <b>作戰策略：</b> 明日開盤若開在均價線上，可偏多布局。")
            except Exception: pass

        msg += "📋 <b>【明日多方觀察標的】</b>\n\n" + ("\n\n".join(picks[:3]) if picks else "盤面多數回檔，明日開盤先觀察不急著進場。\n")
        msg += "\n\n💡 <i>明天 09:05 開盤後，點擊【當沖可以買什麼】即可獲取即時點位！</i>"
        return msg

    # 4. 美股可以買什麼
    elif query_type == "us":
        sp_text = "美股大盤監控中"
        if "^GSPC" in data.columns.levels[0]:
            try:
                df_sp = data["^GSPC"].dropna()
                sp_c = df_sp["Close"].iloc[-1]
                sp_ma5 = df_sp["Close"].rolling(5).mean().iloc[-1]
                sp_text = f"標普500指數 {sp_c:.0f} (" + ("多方格局" if sp_c >= sp_ma5 else "震盪偏弱") + ")"
            except Exception: pass

        msg = f"🇺🇸 <b>【美股戰情室 · 標的推薦】</b> ({now_str})\n🧭 <b>大盤氛圍：</b> {sp_text}\n\n"
        us_hits = []
        for sym in us_stocks:
            try:
                df = data[sym].dropna()
                if len(df) < 25: continue
                c = df["Close"].iloc[-1]
                ma5 = df["Close"].rolling(5).mean().iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]
                bias = ((c - ma20) / ma20) * 100
                name = NAME_MAP.get(sym, sym)

                if c >= ma20 and ma5 >= ma20 and 0.5 <= bias <= 7.0:
                    t1 = round(c * 1.02, 2)
                    t2 = round(c * 1.05, 2)
                    stop = round(c * 0.985, 2)
                    us_hits.append(
                        f"🔹 <b>{name} ({sym})</b> 現價 <b>${c:.2f}</b> (乖離 +{bias:.1f}%)\n"
                        f"   🎯 短線目標：${t1} (+2%) ｜ 🚀 波段目標：${t2} (+5%)\n"
                        f"   🛑 停損防守：${stop} (-1.5%)\n"
                    )
            except Exception: pass

        msg += "🔥 <b>【美股多頭起漲標的】</b>\n\n" + ("\n".join(us_hits[:3]) if us_hits else "目前美股多數整理中，無追高標的。\n\n")
        msg += "💎 <b>【美股大盤 ETF】</b>\n"
        for etf in ["VOO", "QQQ"]:
            try:
                df = data[etf].dropna()
                c = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20, min_periods=5).mean().iloc[-1]
                name = NAME_MAP.get(etf, etf)
                sig = "破月線加碼！" if c < ma20 else "正常扣款"
                msg += f"• <b>{name}</b> (${c:.2f}) ｜ {sig}\n"
            except Exception: pass
        return msg

# 背景監聽線程
def telegram_listener():
    # 啟動前先清除 Webhook，避免 409 Conflict
    try:
        del_url = f"https://api.telegram.org/bot{TG_TOKEN}/deleteWebhook"
        urllib.request.urlopen(del_url, timeout=5)
    except Exception: pass

    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={offset}&timeout=15"
            req = urllib.request.urlopen(url, timeout=20)
            res = json.loads(req.read().decode("utf-8"))
            if res.get("ok"):
                for item in res.get("result", []):
                    offset = item["update_id"] + 1
                    msg = item.get("message", {})
                    text = msg.get("text", "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    if not text: continue

                    if "當沖" in text:
                        q_type = "scalp"
                    elif "明天" in text:
                        q_type = "tomorrow"
                    elif "今天" in text:
                        q_type = "today"
                    elif "美股" in text:
                        q_type = "us"
                    elif any(k in text for k in ["買什麼", "可以買", "推薦", "/buy", "/start"]):
                        q_type = "today"
                    else:
                        continue

                    send_telegram_msg("🔍 正在進行全盤量化掃描，請稍候 3 秒...", target_chat_id=chat_id, show_keyboard=False)
                    report = handle_query(q_type)
                    send_telegram_msg(report, target_chat_id=chat_id, show_keyboard=True)
        except Exception as e:
            time.sleep(2)
        time.sleep(1)

@app.route("/")
def index():
    return "<h1>Stock Radar Bot is Running!</h1>"

listener_thread = threading.Thread(target=telegram_listener, daemon=True)
listener_thread.start()

try:
    send_telegram_msg("🎯 <b>【專屬量化助理已就位】</b>\n已為你綁定專屬私密頻道，點擊下方快捷按鈕即可查詢！", show_keyboard=True)
except Exception: pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
