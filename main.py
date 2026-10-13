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

TG_TOKEN = "8681385226:AAG_1drAsJU3RDmF__jZ0Eblx8LI1eBvrOw"
TG_CHAT_ID = "8865457240"

notified_today = set()
last_notified_date = ""
daily_report_sent_date = ""

cached_html = None
last_update_time = 0
CACHE_DURATION = 180

# 擴充之中英文代號庫
NAME_MAP = {
    "3013.TW": "晟銘電", "6117.TW": "迎廣", "3450.TW": "聯鈞", "3163.TWO": "波若威",
    "4979.TWO": "華星光", "3234.TWO": "光環", "2359.TW": "所羅門", "6188.TWO": "廣明",
    "1513.TW": "中興電", "1514.TW": "亞力", "2449.TW": "京元電", "3231.TW": "緯創",
    "2603.TW": "長榮", "2609.TW": "陽明", "2615.TW": "萬海", "2356.TW": "英業達",
    "2376.TW": "技嘉", "2357.TW": "華碩", "2301.TW": "光寶科", "2383.TW": "台光電",
    "3037.TW": "欣興", "2368.TW": "金像電", "8039.TW": "台虹", "2374.TW": "佳能",
    "4583.TW": "台灣精銳", "2486.TW": "一詮", "3376.TW": "新日興", "6442.TW": "光聖",
    "8054.TWO": "安國", "3661.TW": "世芯-KY", "3443.TW": "創意", "3017.TW": "奇鋐",
    "2330.TW": "台積電", "2454.TW": "聯發科", "2382.TW": "廣達", "2317.TW": "鴻海",
    "0050.TW": "元大台灣50", "006208.TW": "富邦台50", "00713.TW": "元大高息低波",
    "00878.TW": "國泰永續高股息", "VOO": "Vanguard標普500", "QQQ": "Invesco那斯達克",
    "NVDA": "輝達 NVIDIA", "TSLA": "特斯拉 Tesla", "AMD": "超微 AMD", "PLTR": "Palantir",
    "AAPL": "蘋果 Apple", "MSFT": "微軟 Microsoft", "GOOGL": "谷歌 Alphabet"
}

# 基礎大池（涵蓋近半年前 50 名高人氣週轉標的）
UNIVERSE_CANDIDATES = [
    "3013.TW", "6117.TW", "3450.TW", "3163.TWO", "4979.TWO", "3234.TWO",
    "2359.TW", "6188.TWO", "1513.TW", "1514.TW", "2449.TW", "3231.TW",
    "2603.TW", "2609.TW", "2615.TW", "2356.TW", "2376.TW", "2301.TW",
    "3037.TW", "2368.TW", "8039.TW", "2374.TW", "2486.TW", "3376.TW",
    "8054.TWO"
]

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

    for t in range(2, 8):
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
    tw_etf = ["0050.TW", "006208.TW", "00713.TW", "00878.TW"]
    tw_potential = ["2330.TW", "2454.TW", "2382.TW", "2317.TW", "3017.TW", "2603.TW", "2881.TW", "2882.TW"]
    us_stocks = ["NVDA", "TSLA", "AMD", "PLTR", "AAPL", "MSFT", "GOOGL", "VOO", "QQQ"]
    indices = ["^TWII", "^GSPC"]

    all_syms = list(set(UNIVERSE_CANDIDATES + tw_etf + tw_potential + us_stocks + indices))
    try:
        data = yf.download(all_syms, period="3mo", group_by="ticker", auto_adjust=True, threads=True)
    except Exception:
        data = None

    # 動態成交金額篩選：只抓 30元 <= 現價 <= 200元 (一張20萬以下) 且依成交額排序
    dynamic_intraday = []
    if data is not None:
        ranked_list = []
        for sym in UNIVERSE_CANDIDATES:
            try:
                df = data[sym].dropna()
                if len(df) < 2: continue
                c = df["Close"].iloc[-1]
                vol = df["Volume"].iloc[-1]
                # 嚴格價格濾網：一張 20 萬以內 (股價 <= 200) 且不低於 30 元
                if 30.0 <= c <= 200.0:
                    turnover = c * vol # 當日成交金額概算
                    ranked_list.append((sym, turnover))
            except Exception: pass
        # 取成交金額前 15 名作為今日黃金當沖池
        ranked_list.sort(key=lambda x: x[1], reverse=True)
        dynamic_intraday = [item[0] for item in ranked_list[:15]]

    if not dynamic_intraday:
        dynamic_intraday = UNIVERSE_CANDIDATES[:12]

    return data, dynamic_intraday, tw_etf, tw_potential, us_stocks

def check_single_stock(query):
    query = query.upper().strip()
    target_sym = None
    for sym, name in NAME_MAP.items():
        clean_code = sym.split('.')[0]
        if query == clean_code or query == name or query in name:
            target_sym = sym
            break

    if not target_sym:
        test_sym = query if ("." in query or len(query) < 5) else f"{query}.TW"
        target_sym = test_sym

    try:
        df = yf.download(target_sym, period="3mo", auto_adjust=True)
        if df.empty or len(df) < 5:
            return f"❌ 找不到代號 <b>{query}</b> 的即時行情，請確認代號是否正確（例如：3013、聯鈞、NVDA）。"

        cur = df["Close"].iloc[-1].item() if hasattr(df["Close"].iloc[-1], "item") else float(df["Close"].iloc[-1])
        op = df["Open"].iloc[-1].item() if hasattr(df["Open"].iloc[-1], "item") else float(df["Open"].iloc[-1])
        prev_c = df["Close"].iloc[-2].item() if hasattr(df["Close"].iloc[-2], "item") else float(df["Close"].iloc[-2])
        hi = df["High"].iloc[-1].item() if hasattr(df["High"].iloc[-1], "item") else float(df["High"].iloc[-1])
        lo = df["Low"].iloc[-1].item() if hasattr(df["Low"].iloc[-1], "item") else float(df["Low"].iloc[-1])
        
        ma5 = float(df["Close"].rolling(5).mean().iloc[-1])
        ma20 = float(df["Close"].rolling(20, min_periods=5).mean().iloc[-1])
        bias20 = ((cur - ma20) / ma20) * 100
        chg = ((cur - prev_c) / prev_c) * 100
        vwap = (hi + lo + cur) / 3

        is_tw = (".TW" in target_sym or ".TWO" in target_sym)
        plan = calculate_scalp_plan(cur, is_tw=is_tw)
        sname = NAME_MAP.get(target_sym, target_sym.split('.')[0])
        cost_wan = f"每張約 {int(cur * 1000 / 10000)}萬" if is_tw else f"約 ${cur:.2f}"

        status_tag = "🔥 多方強勢攻" if (cur >= op and cur >= prev_c and cur >= vwap) else "⚠️ 破線震盪整理"

        msg = (
            f"🔎 <b>【個股即時診斷】{sname} ({target_sym})</b>\n"
            f"現價：<b>{cur:.2f}</b> ({chg:+.2f}%，{cost_wan})\n"
            f"技術位階：{status_tag}\n\n"
            f"📊 <b>關鍵均線與支撐：</b>\n"
            f"• 5日均線：{ma5:.2f}\n"
            f"• 20日月線：{ma20:.2f} (乖離 {bias20:+.1f}%)\n"
            f"• 當日均價(VWAP)：{vwap:.2f}\n\n"
            f"🎯 <b>當沖/短線作戰指引：</b>\n"
            f"• 🎯 第一目標 (保底)：<b>{plan['target1']}</b> ({plan['pnl1']})\n"
            f"• 🚀 第二目標 (主力)：<b>{plan['target2']}</b> ({plan['pnl2']})\n"
            f"• 🛑 嚴守停損點：<b>{plan['stop']}</b>\n\n"
            f"💡 <i>若目前股價高於當日均價線且乖離適中，偏多操作勝率較高！</i>"
        )
        return msg
    except Exception:
        return f"⚠️ 查詢 {query} 時發生錯誤，請稍候重試。"

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

    if query_type == "scalp":
        msg = f"⚡ <b>【自動熱門當沖雷達 (一張20萬以下)】</b> ({now_str})\n🧭 <b>大盤氛圍：</b> {twii_text}\n\n"
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

                # 價格嚴格壓在 200 元以下 (一張20萬內)，且符合突破動能
                if (cur <= 200.0) and (cur >= op) and (cur >= prev_c) and (cur >= vwap) and (chg < 8.5) and (pullback <= 1.5):
                    name = NAME_MAP.get(sym, sym)
                    plan = calculate_scalp_plan(cur, is_tw=True)
                    clean_code = sym.split('.')[0]
                    cost_info = f"每張約 {int(cur * 1000 / 10000)}萬"
                    hits.append(
                        f"🔹 <b>{name} ({clean_code})</b> 現價 <b>{cur:.2f}</b> ({chg:+.2f}%，{cost_info})\n"
                        f"   🎯 保底小賺：<b>{plan['target1']}</b> ({plan['pnl1']}，跳{plan['ticks1']}檔)\n"
                        f"   🚀 主力拉大：<b>{plan['target2']}</b> ｜ 🛑 停損：{plan['stop']}\n"
                    )
            except Exception: pass
        if hits:
            msg += "🎯 <b>【動態量能熱門前線標的】</b>\n\n" + "\n".join(hits[:3])
            msg += "\n💡 <i>資金門檻全數壓在 20 萬以內，吃 2~3 個檔位即獲利落袋！</i>"
        else:
            msg += "🛡️ <b>熱門池中目前無符合突破條件之標的。</b>\n（無量或高點拉回過大，請耐心空手觀望，切忌盲目追高！）"
        return msg

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

    elif query_type == "tomorrow":
        msg = f"🌙 <b>【明日開盤動能觀察清單 (20萬以內)】</b> ({now_str})\n🧭 <b>大盤總結：</b> {twii_text}\n\n"
        picks = []
        for sym in tw_intraday[:8]:
            try:
                df = data[sym].dropna()
                if len(df) < 20: continue
                c = df["Close"].iloc[-1]
                ma5 = df["Close"].rolling(5).mean().iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]
                bias = ((c - ma20) / ma20) * 100
                if (c <= 200.0) and (c >= ma20) and (ma5 >= ma20) and (0.5 <= bias <= 7.0):
                    name = NAME_MAP.get(sym, sym)
                    cost_info = f"一張約 {int(c * 1000 / 10000)}萬"
                    picks.append(f"🔥 <b>{name} ({sym.split('.')[0]})</b> 收盤 {c:.2f} ({cost_info})\n   月線支撐 {ma20:.2f} (乖離 +{bias:.1f}%)\n   👉 <b>作戰策略：</b> 50~200元熱門成交前線股，明日開高可偏多布局。")
            except Exception: pass

        msg += "📋 <b>【明日動能熱門標的】</b>\n\n" + ("\n\n".join(picks[:3]) if picks else "盤面多數回檔，明日開盤先觀察不急著進場。\n")
        msg += "\n\n💡 <i>明天 09:05 開盤後，點擊【當沖可以買什麼】即可獲取即時點位！</i>"
        return msg

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

def telegram_listener():
    global daily_report_sent_date
    try:
        del_url = f"https://api.telegram.org/bot{TG_TOKEN}/deleteWebhook?drop_pending_updates=True"
        urllib.request.urlopen(del_url, timeout=5)
    except Exception: pass

    offset = 0
    while True:
        try:
            now_dt = get_tw_now()
            today_str = now_dt.strftime("%Y-%m-%d")
            time_val = now_dt.hour * 100 + now_dt.minute
            
            if (1335 <= time_val <= 1400) and (now_dt.weekday() < 5) and (daily_report_sent_date != today_str):
                daily_report_sent_date = today_str
                daily_msg = "📢 <b>【台股 13:35 盤後自動結算戰報】</b>\n\n" + handle_query("tomorrow")
                send_telegram_msg(daily_msg, show_keyboard=True)

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
                        send_telegram_msg("🔍 正在自動挑選成交額最高、20萬以內熱門當沖股...", target_chat_id=chat_id, show_keyboard=False)
                        report = handle_query("scalp")
                        send_telegram_msg(report, target_chat_id=chat_id, show_keyboard=True)
                    elif "明天" in text:
                        send_telegram_msg("🔍 正在進行全盤量化掃描，請稍候 3 秒...", target_chat_id=chat_id, show_keyboard=False)
                        report = handle_query("tomorrow")
                        send_telegram_msg(report, target_chat_id=chat_id, show_keyboard=True)
                    elif "今天" in text or text in ["/buy", "/start"]:
                        send_telegram_msg("🔍 正在進行全盤量化掃描，請稍候 3 秒...", target_chat_id=chat_id, show_keyboard=False)
                        report = handle_query("today")
                        send_telegram_msg(report, target_chat_id=chat_id, show_keyboard=True)
                    elif "美股" in text:
                        send_telegram_msg("🔍 正在進行全盤量化掃描，請稍候 3 秒...", target_chat_id=chat_id, show_keyboard=False)
                        report = handle_query("us")
                        send_telegram_msg(report, target_chat_id=chat_id, show_keyboard=True)
                    else:
                        send_telegram_msg(f"🔎 正在為您調取 <b>{text}</b> 即時量價數據...", target_chat_id=chat_id, show_keyboard=False)
                        res_single = check_single_stock(text)
                        send_telegram_msg(res_single, target_chat_id=chat_id, show_keyboard=True)
        except Exception:
            time.sleep(2)
        time.sleep(1)

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

    data, tw_intraday, tw_etf, tw_potential, us_stocks = scan_market_data()

    market_badge = '<span class="badge badge-amber">數據更新中</span>'
    market_text = "加權指數監控中..."
    if data is not None and "^TWII" in data.columns.levels[0]:
        try:
            df_idx = data["^TWII"].dropna()
            if len(df_idx) >= 5:
                idx_cur = df_idx["Close"].iloc[-1]
                idx_prev = df_idx["Close"].iloc[-2]
                idx_ma5 = df_idx["Close"].rolling(5).mean().iloc[-1]
                diff = idx_cur - idx_prev
                diff_pct = (diff / idx_prev) * 100
                diff_str = f"{'+' if diff >= 0 else ''}{diff:.1f} ({'+' if diff >= 0 else ''}{diff_pct:.2f}%)"
                if idx_cur >= idx_ma5:
                    market_badge = '<span class="badge badge-red">多方控盤 · 站上5日線</span>'
                    market_text = f"加權指數：<b>{idx_cur:.1f}</b> (<span style='color:#ef4444;'>{diff_str}</span>) ｜ 大盤強勢，順勢操作。"
                else:
                    market_badge = '<span class="badge badge-green">空方震盪 · 跌破5日線</span>'
                    market_text = f"加權指數：<b>{idx_cur:.1f}</b> (<span style='color:#10b981;'>{diff_str}</span>) ｜ 大盤偏弱，小賺即跑，嚴控停損！"
        except Exception: pass

    long_results = []
    short_results = []
    if data is not None:
        for sym in tw_intraday:
            try:
                df = data[sym].dropna()
                if len(df) < 5: continue
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
                clean_sym = sym.split('.')[0]
                cost_wan = f"約{int(cur * 1000 / 10000)}萬"
                plan = calculate_scalp_plan(cur, is_tw=True)

                change_pct = ((cur - prev_c) / prev_c) * 100
                pullback_from_high = ((hi - cur) / hi) * 100

                target_copy_btn = f"<button class='copy-btn' onclick=\"copyVal('{plan['target1']}')\">{plan['target1']} 📋</button>"

                if (cur <= 200.0) and (cur >= op) and (cur >= prev_c) and (cur >= vwap) and (change_pct < 8.5) and (pullback_from_high <= 1.5):
                    long_results.append({
                        "標的": f"<a href='https://tw.stock.yahoo.com/quote/{clean_sym}' target='_blank' style='color:#38bdf8;text-decoration:none;font-weight:700;'>{clean_sym} <span style='font-size:11px;color:#94a3b8;'>({stock_name})</span> ↗</a>",
                        "現價(門檻)": f"<span style='color:#ef4444;font-weight:700;'>{round(cur, 2)}</span> <span style='font-size:11px;color:#94a3b8;'>({cost_wan})</span>",
                        "保底目標(1~2千)": f"{target_copy_btn} <span style='color:#ef4444;font-size:11px;'>({plan['pnl1']})</span>",
                        "主力衝高": f"<b style='color:#f59e0b;'>{plan['target2']}</b> <span style='font-size:11px;'>({plan['pnl2']})</span>",
                        "嚴守停損": f"<span style='color:#10b981;font-weight:600;'>{plan['stop']}</span>",
                        "量能": f"<span style='color:#ef4444;'>{vol_ratio:.1f}x</span>" if vol_ratio > 1.2 else "常態量",
                        "訊號": "🔥 多方帶量攻"
                    })
                    if is_golden_time and (sym not in notified_today):
                        notified_today.add(sym)
                        msg = (
                            f"⚡ <b>【熱門當沖雙目標訊號 (20萬內)】</b>\n"
                            f"標的：<b>{stock_name} ({sym})</b>\n"
                            f"現價：<b>{cur:.2f}</b> (漲幅 {change_pct:+.2f}%，每張{cost_wan})\n\n"
                            f"🎯 <b>第一目標 (保底)：{plan['target1']}</b> ({plan['pnl1']}，跳{plan['ticks1']}檔)\n"
                            f"🚀 <b>第二目標 (拉大)：{plan['target2']}</b> ({plan['pnl2']})\n"
                            f"🛑 <b>嚴守停損：{plan['stop']}</b>\n\n"
                            f"💡 <i>吃 2~3 個檔位即獲利落袋，或將停損拉至進場價保本挑戰第二目標！</i>"
                        )
                        send_telegram_msg(msg)

                elif (cur < op) and (cur < prev_c) and (cur < vwap) and (change_pct > -8.5):
                    short_results.append({
                        "標的": f"<a href='https://tw.stock.yahoo.com/quote/{clean_sym}' target='_blank' style='color:#94a3b8;text-decoration:none;font-weight:600;'>{clean_sym} ({stock_name}) ↗</a>",
                        "現價": f"<span style='color:#10b981;font-weight:700;'>{round(cur, 2)}</span>",
                        "反彈壓力(停損)": round(cur * 1.01, 2),
                        "下殺目標(-2%)": round(cur * 0.98, 2),
                        "5日振幅": f"{amp_5d:.1f}%",
                        "訊號": "⚠️ 弱勢破線走跌"
                    })
            except Exception: pass

    potential_results = []
    if data is not None:
        all_pot_candidates = tw_potential + [s for s in us_stocks if not s.startswith("^")]
        for sym in all_pot_candidates:
            try:
                df = data[sym].dropna()
                if len(df) < 25: continue
                cur = df["Close"].iloc[-1]
                ma5 = df["Close"].rolling(5).mean().iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]
                bias_ma20 = ((cur - ma20) / ma20) * 100

                if cur >= ma20 and ma5 >= ma20 and 0 <= bias_ma20 <= 8.0:
                    score = (cur / ma20) * 100
                    sname = NAME_MAP.get(sym, sym)
                    clean_s = sym.split('.')[0]
                    link_url = f"https://tw.stock.yahoo.com/quote/{clean_s}" if (".TW" in sym or ".TWO" in sym) else f"https://finance.yahoo.com/quote/{sym}"
                    potential_results.append({
                        "標的": f"<a href='{link_url}' target='_blank' style='color:#38bdf8;text-decoration:none;font-weight:600;'>{clean_s} <span style='font-size:11px;color:#94a3b8;'>({sname})</span> ↗</a>",
                        "市場": "🇺🇸 美股" if not (".TW" in sym or ".TWO" in sym) else "🇹🇼 台股",
                        "現價": f"<b>{round(cur, 2)}</b>",
                        "月線(20MA)": round(ma20, 2),
                        "月線乖離": f"<span style='color:#ef4444;'>+{bias_ma20:.1f}%</span>",
                        "型態": "💎 多頭起漲·回檔有撐",
                        "_score": score
                    })
            except Exception: pass

    etf_results = []
    if data is not None:
        all_etfs = tw_etf + ["VOO", "QQQ"]
        for sym in all_etfs:
            try:
                df = data[sym].dropna()
                if len(df) < 15: continue
                cur = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20, min_periods=5).mean().iloc[-1]
                bias_ma20 = ((cur - ma20) / ma20) * 100
                sname = NAME_MAP.get(sym, sym)
                clean_s = sym.split('.')[0]
                link_url = f"https://tw.stock.yahoo.com/quote/{clean_s}" if (".TW" in sym or ".TWO" in sym) else f"https://finance.yahoo.com/quote/{sym}"
                
                signal = "<span style='color:#94a3b8;'>定期定額正常扣款</span>" if cur >= ma20 else "<span style='color:#ef4444;font-weight:700;'>⚡ 破月線回檔加碼！</span>"
                etf_results.append({
                    "標的": f"<a href='{link_url}' target='_blank' style='color:#38bdf8;text-decoration:none;font-weight:600;'>{clean_s} <span style='font-size:11px;color:#94a3b8;'>({sname})</span> ↗</a>",
                    "現價": round(cur, 2),
                    "月線(20MA)": round(ma20, 2),
                    "乖離率": f"{bias_ma20:+.1f}%",
                    "操作指引": signal
                })
            except Exception: pass

    df_long = pd.DataFrame(long_results)
    df_short = pd.DataFrame(short_results)
    df_etf = pd.DataFrame(etf_results)
    df_pot = pd.DataFrame(potential_results).sort_values(by="_score", ascending=False).drop(columns=["_score"]) if potential_results else pd.DataFrame()

    table_long = df_long.to_html(index=False, escape=False) if not df_long.empty else '<div class="empty">🛡️ 今日熱門成交池中無符合強勢做多標的（多方觀望，嚴禁盲目追高）。</div>'
    table_short = df_short.to_html(index=False, escape=False) if not df_short.empty else '<div class="empty">盤面無明顯重挫標的。</div>'
    table_pot = df_pot.to_html(index=False, escape=False) if not df_pot.empty else '<div class="empty">目前無符合低風險起漲點之潛力標的。</div>'
    table_etf = df_etf.to_html(index=False, escape=False) if not df_etf.empty else '<div class="empty">暫無數據。</div>'

    if is_golden_time:
        timing_banner = '<div style="background:#450a0a;border:1px solid #dc2626;padding:8px 12px;border-radius:8px;font-size:12px;color:#fecaca;margin-bottom:12px;">🔥 <b>台股黃金動能時段 (09:05-10:15)</b>：市場量能充沛，20萬內熱門動能推播中！</div>'
    elif is_market_closed:
        timing_banner = '<div style="background:#1e293b;border:1px solid #334155;padding:8px 12px;border-radius:8px;font-size:12px;color:#94a3b8;margin-bottom:12px;">🌙 <b>台股已收盤</b>：13:35 已推播盤後戰報。夜間可於 TG 查詢『美股』或任意輸入代號秒查行情！</div>'
    else:
        timing_banner = '<div style="background:#78350f;border:1px solid #d97706;padding:8px 12px;border-radius:8px;font-size:12px;color:#fde68a;margin-bottom:12px;">⏳ <b>已過台股黃金動能期 (10:15後)</b>：盤勢易量縮洗刷，關閉當沖推播，建議停止新開倉！</div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta http-equiv="refresh" content="180">
    <title>選股決策雷達</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b1120; color: #f8fafc; margin: 0; padding: 14px; }}
        .wrap {{ max-width: 960px; margin: auto; }}
        .header-box {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }}
        h1 {{ color: #38bdf8; font-size: 20px; margin: 0 0 4px 0; font-weight: 800; }}
        .sub {{ color: #94a3b8; font-size: 12px; }}
        .timer-box {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 6px 10px; font-size: 11px; color: #38bdf8; text-align: right; }}
        .market-banner {{ background: #162032; border: 1px solid #334155; border-radius: 10px; padding: 10px 14px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; font-size: 13px; }}
        .card {{ background: #131d31; border: 1px solid #1e293b; border-radius: 12px; padding: 14px; margin-bottom: 16px; box-shadow: 0 4px 14px rgba(0,0,0,0.4); }}
        .card-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1e293b; padding-bottom: 8px; margin-bottom: 10px; }}
        .title {{ font-size: 15px; font-weight: 700; }}
        .badge {{ font-size: 11px; padding: 3px 6px; border-radius: 4px; font-weight: 600; }}
        .badge-red {{ background: #dc2626; color: #fff; }}
        .badge-blue {{ background: #2563eb; color: #fff; }}
        .badge-green {{ background: #059669; color: #fff; }}
        .badge-amber {{ background: #d97706; color: #fff; }}
        .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid #1e293b; white-space: nowrap; }}
        th {{ color: #94a3b8; font-weight: 600; background: #0f172a; }}
        tr:hover {{ background: #1a2744; }}
        .empty {{ color: #f59e0b; padding: 12px 6px; font-size: 13px; line-height: 1.5; }}
        .copy-btn {{ background: #1e293b; border: 1px solid #38bdf8; color: #38bdf8; border-radius: 4px; padding: 2px 6px; font-size: 12px; cursor: pointer; font-weight: 600; }}
        .copy-btn:active {{ background: #38bdf8; color: #0f172a; }}
    </style>
    <script>
        function copyVal(val) {{
            navigator.clipboard.writeText(val).then(() => {{
                alert('已複製目標價: ' + val + ' (可直接至券商貼上掛單)');
            }});
        }}
    </script>
</head>
<body>
    <div class="wrap">
        <div class="header-box">
            <div>
                <h1>📊 選股決策雷達 (熱門成交＋20萬以內動能版)</h1>
                <div class="sub">更新時間 (台北)：{now_str}</div>
            </div>
            <div class="timer-box">🔄 3分鐘自動刷新<br><span style="color:#ef4444;">熱門成交自動排行</span> ｜ <span style="color:#38bdf8;">每張&lt;20萬</span></div>
        </div>

        {timing_banner}

        <div class="market-banner">
            <div>🧭 <strong>大盤風向球：</strong> {market_text}</div>
            <div>{market_badge}</div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="title">⚡ 1. 今日台股當沖：自動抓熱門成交（一張 20 萬以下）</span>
                <span class="badge badge-red">自動排名前15大 · 跳2~3檔達標</span>
            </div>
            <div class="table-wrap">{table_long}</div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="title">⚠️ 2. 弱勢避險 / 放空參考標的</span>
                <span class="badge badge-green">破今開均價線</span>
            </div>
            <div class="table-wrap">{table_short}</div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="title">🎯 3. AI 潛力股（台股 / 美股波段回檔有撐）</span>
                <span class="badge badge-amber">MA5&gt;MA20 · 乖離&lt;8%</span>
            </div>
            <div class="table-wrap">{table_pot}</div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="title">💎 4. 長期持有 / 零股 (台美 ETF 回檔監控)</span>
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

listener_thread = threading.Thread(target=telegram_listener, daemon=True)
listener_thread.start()

try:
    send_telegram_msg("🔥 <b>【自動熱門換血 ＋ 20萬限價濾網已啟用】</b>\n1. 系統自動按『當日成交金額』即時挑選市場最熱門前線股！\n2. 嚴格限定股價 30~200 元（每張 20 萬以內），徹底排除超高價牛皮股！\n3. 跳 2~3 個檔位即達 1,000~2,000 元當沖目標！", show_keyboard=True)
except Exception: pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
