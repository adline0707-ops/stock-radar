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
    "AMZN": "亞商 Amazon", "QCOM": "高通 Qualcomm", "AVGO": "博通 Broadcom"
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

def send_telegram_msg(msg, target_chat_id=None):
    try:
        cid = target_chat_id or TG_CHAT_ID
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": cid,
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
    url = f"https://tw.stock.yahoo.com/quote/{clean_sym}" if (".TW" in sym or ".TWO" in sym) else f"https://finance.yahoo.com/quote/{sym}"
    return f'<a href="{url}" target="_blank" style="color:#38bdf8;text-decoration:none;font-weight:600;">{clean_sym} <span style="font-size:11px;color:#94a3b8;">({name})</span> ↗</a>'

def scan_market_data():
    tw_intraday = [
        "2330.TW", "2454.TW", "2382.TW", "2317.TW",
        "3017.TW", "3324.TWO", "3231.TW", "6669.TW",
        "6442.TW", "3163.TWO", "4979.TWO"
    ]
    tw_etf = ["0050.TW", "006208.TW", "00713.TW", "00878.TW"]
    tw_potential = ["2330.TW", "2454.TW", "2303.TW", "2382.TW", "2308.TW", "2379.TW", "2603.TW", "2881.TW", "2882.TW", "3008.TW", "3711.TW"]
    
    us_stocks = ["NVDA", "TSLA", "AMD", "PLTR", "AAPL", "MSFT", "GOOGL", "AMZN", "QCOM", "AVGO", "VOO", "QQQ"]
    indices = ["^TWII", "^GSPC"] # 台股加權 & 美股標普500

    all_syms = list(set(tw_intraday + tw_etf + tw_potential + us_stocks + indices))
    try:
        data = yf.download(all_syms, period="3mo", group_by="ticker", auto_adjust=True, threads=True)
    except Exception:
        data = None
    return data, tw_intraday, tw_etf, tw_potential, us_stocks

def generate_smart_report(market_choice="auto", force_mode=None):
    now_dt = get_tw_now()
    now_str = now_dt.strftime("%H:%M:%S")
    time_val = now_dt.hour * 100 + now_dt.minute

    data, tw_intraday, tw_etf, tw_potential, us_stocks = scan_market_data()
    if data is None:
        return "⚠️ 目前連線市場數據庫逾時，請稍後 30 秒再試一次！"

    # 若未指定市場，依台北時間自動判斷主要市場
    if market_choice == "auto":
        # 晚上 20:30 到清晨 05:00 預設為美股時段
        if time_val >= 2030 or time_val <= 500:
            active_market = "US"
        else:
            active_market = "TW"
    else:
        active_market = market_choice.upper()

    # ---------------- 🇺🇸 美股診斷報告 ----------------
    if active_market == "US":
        sp500_str = "美股大盤監控中"
        if "^GSPC" in data.columns.levels[0]:
            try:
                df_sp = data["^GSPC"].dropna()
                sp_c = df_sp["Close"].iloc[-1]
                sp_ma5 = df_sp["Close"].rolling(5).mean().iloc[-1]
                sp500_str = f"標普500指數 {sp_c:.0f} (" + ("站穩5日線 多方格局" if sp_c >= sp_ma5 else "跌破5日線 震盪保守") + ")"
            except Exception: pass

        msg = f"🇺🇸 <b>【美股戰情室 · 即時/波段診斷】</b> ({now_str})\n"
        msg += f"🧭 <b>大盤氛圍：</b> {sp500_str}\n\n"

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

                # 美股波段買點：站穩月線、MA5向上、月線乖離不超過7%
                if c >= ma20 and ma5 >= ma20 and 0.5 <= bias <= 7.0:
                    t1 = round(c * 1.02, 2)
                    t2 = round(c * 1.05, 2)
                    stop = round(c * 0.985, 2)
                    us_hits.append(
                        f"🔹 <b>{name} ({sym})</b> 現價 <b>${c:.2f}</b> (月線乖離 +{bias:.1f}%)\n"
                        f"   🎯 短線目標：${t1} (+2%) ｜ 🚀 波段目標：${t2} (+5%)\n"
                        f"   🛑 停損防守：${stop} (-1.5%)"
                    )
            except Exception: pass

        if us_hits:
            msg += "🔥 <b>【美股強勢多頭排列標的】</b>\n" + "\n\n".join(us_hits[:4]) + "\n\n"
        else:
            msg += "目前美股多數處於乖離過大或回檔整理，建議分批觀察。\n\n"

        # 美股大盤 ETF (VOO / QQQ) 回檔評估
        msg += "💎 <b>【美股核心 ETF 扣款評估】</b>\n"
        for etf in ["VOO", "QQQ"]:
            try:
                df = data[etf].dropna()
                c = df["Close"].iloc[-1]
                ma20 = df["Close"].rolling(20).mean().iloc[-1]
                bias = ((c - ma20) / ma20) * 100
                name = NAME_MAP.get(etf, etf)
                sig = "破月線甜蜜加碼區！" if c < ma20 else "多頭正常定期扣款"
                msg += f"• <b>{name}</b> (${c:.2f})：乖離 {bias:+.1f}% ｜ {sig}\n"
            except Exception: pass
        return msg

    # ---------------- 🇹🇼 台股診斷報告 ----------------
    else:
        mode = force_mode
        if not mode:
            if 800 <= time_val < 900: mode = "premarket"
            elif 900 <= time_val <= 1335: mode = "intraday"
            else: mode = "tomorrow"

        twii_str = "加權指數監控中"
        if "^TWII" in data.columns.levels[0]:
            try:
                df_tw = data["^TWII"].dropna()
                tw_c = df_tw["Close"].iloc[-1]
                tw_ma5 = df_tw["Close"].rolling(5).mean().iloc[-1]
                diff = tw_c - df_tw["Close"].iloc[-2]
                twii_str = f"加權指數 {tw_c:.0f} ({diff:+.0f}) ｜ " + ("多方控盤" if tw_c >= tw_ma5 else "偏弱震盪")
            except Exception: pass

        if mode == "premarket":
            msg = f"🇹🇼 <b>【台股盤前備戰日報】</b> ({now_str})\n🧭 <b>大盤氛圍：</b> {twii_str}\n\n"
            msg += "🎯 <b>【今日焦點獵物】</b> (量大/振幅佳)：\n"
            for sym in tw_intraday[:5]:
                try:
                    df = data[sym].dropna()
                    c = df["Close"].iloc[-1]
                    name = NAME_MAP.get(sym, sym)
                    msg += f"• <b>{name} ({sym.split('.')[0]})</b>：昨收 {c:.1f}\n"
                except Exception: pass
            msg += "\n💡 09:00 前 5 分鐘假單多，等 09:05 均價線確立再出手！"
            return msg

        elif mode == "tomorrow":
            msg = f"🇹🇼 <b>【台股盤後籌碼 · 明日觀察名單】</b> ({now_str})\n🧭 <b>今日大盤：</b> {twii_str}\n\n"
            pot_picks = []
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
                        pot_picks.append(f"🔥 <b>{name} ({sym.split('.')[0]})</b>\n   收盤 {c:.2f} ｜ 月線支撐 {ma20:.2f} (乖離 +{bias:.1f}%)\n   👉 多頭排列回檔有撐，明日開高可偏多操作。")
                except Exception: pass
            msg += "📋 <b>【明天值得留意的潛力標的】</b>\n" + ("\n\n".join(pot_picks[:3]) if pot_picks else "盤面多數回檔，明日先觀望。\n")
            return msg

        else:
            msg = f"🇹🇼 <b>【台股盤中即時雷達】</b> ({now_str})\n🧭 <b>大盤氛圍：</b> {twii_str}\n\n"
            intraday_hits = []
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
                        intraday_hits.append({
                            "name": name, "sym": sym, "cur": cur, "chg": chg,
                            "t1": plan["target1"], "p1": plan["pnl1"], "ticks": plan["ticks1"],
                            "t2": plan["target2"], "stop": plan["stop"]
                        })
                except Exception: pass

            msg += "🎯 <b>【當沖多方即時標的】</b>\n"
            if intraday_hits:
                for h in intraday_hits[:3]:
                    msg += (
                        f"🔹 <b>{h['name']} ({h['sym'].split('.')[0]})</b> 現價 <b>{h['cur']:.2f}</b> ({h['chg']:+.2f}%)\n"
                        f"   🎯 保底小賺：<b>{h['t1']}</b> ({h['p1']}，跳{h['ticks']}檔)\n"
                        f"   🚀 主力拉大：<b>{h['t2']}</b> ｜ 🛑 停損：{h['stop']}\n\n"
                    )
            else:
                msg += "🛡️ 盤面無符合爆量起漲標的，請空手觀望，切忌追高。\n\n"
            return msg

def telegram_listener():
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={offset}&timeout=20"
            req = urllib.request.urlopen(url, timeout=25)
            res = json.loads(req.read().decode("utf-8"))
            if res.get("ok"):
                for item in res.get("result", []):
                    offset = item["update_id"] + 1
                    msg = item.get("message", {})
                    text = msg.get("text", "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    market_choice = "auto"
                    if any(k in text for k in ["美股", "美", "/us", "US"]):
                        market_choice = "US"
                    elif any(k in text for k in ["台股", "台", "/tw", "TW"]):
                        market_choice = "TW"

                    force_mode = None
                    if any(k in text for k in ["明天", "明日", "/tomorrow", "盤後"]):
                        force_mode = "tomorrow"
                    elif any(k in text for k in ["盤前", "開盤前", "/premarket"]):
                        force_mode = "premarket"
                    elif any(k in text for k in ["今天", "當下", "盤中", "/today"]):
                        force_mode = "intraday"

                    keywords = ["買什麼", "可以買", "推薦", "買", "/buy", "選股", "明天", "盤前", "今天", "美股", "台股", "/us", "/tw"]
                    if any(k in text for k in keywords):
                        send_telegram_msg("🔍 正在進行市場量價風控掃描，請稍候 3 秒...", target_chat_id=chat_id)
                        report = generate_smart_report(market_choice=market_choice, force_mode=force_mode)
                        send_telegram_msg(report, target_chat_id=chat_id)
        except Exception: pass
        time.sleep(2)

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
                    market_badge = '<span class="badge badge-green">多方控盤 · 站上5日線</span>'
                    market_text = f"加權指數：{idx_cur:.1f} ({diff_str}) ｜ 大盤強勢，順勢操作。"
                else:
                    market_badge = '<span class="badge badge-red">空方震盪 · 跌破5日線</span>'
                    market_text = f"加權指數：{idx_cur:.1f} ({diff_str}) ｜ 大盤偏弱，小賺即跑，嚴控停損！"
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
                plan = calculate_scalp_plan(cur, is_tw=True)

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
                            f"⚡ <b>【台股當沖雙目標訊號】</b>\n"
                            f"標的：<b>{stock_name} ({sym})</b>\n"
                            f"現價：<b>{cur:.2f}</b> (漲幅 {change_pct:+.2f}%)\n\n"
                            f"🎯 <b>第一目標 (保底)：{plan['target1']}</b> ({plan['pnl1']}，跳{plan['ticks1']}檔)\n"
                            f"🚀 <b>第二目標 (拉大)：{plan['target2']}</b> ({plan['pnl2']})\n"
                            f"🛑 <b>嚴守停損：{plan['stop']}</b>\n\n"
                            f"💡 達標第一目標可平倉落袋 1~2 千，或移至成本價保本挑戰第二目標！"
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
                    potential_results.append({
                        "標的": make_link(sym),
                        "市場": "🇺🇸 美股" if not (".TW" in sym or ".TWO" in sym) else "🇹🇼 台股",
                        "現價": round(cur, 2),
                        "月線(20MA)": round(ma20, 2),
                        "月線乖離": f"+{bias_ma20:.1f}%",
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
                signal = "定期定額正常扣款" if cur >= ma20 else "⚡ 破月線回檔加碼！"
                etf_results.append({
                    "標的": make_link(sym),
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

    table_long = df_long.to_html(index=False, escape=False) if not df_long.empty else '<div class="empty">🛡️ 今日無符合強勢做多標的（多方觀望，嚴禁盲目進場追高）。</div>'
    table_short = df_short.to_html(index=False, escape=False) if not df_short.empty else '<div class="empty">盤面無明顯重挫標的。</div>'
    table_pot = df_pot.to_html(index=False, escape=False) if not df_pot.empty else '<div class="empty">目前無符合低風險起漲點之潛力標的。</div>'
    table_etf = df_etf.to_html(index=False, escape=False) if not df_etf.empty else '<div class="empty">暫無數據。</div>'

    if is_golden_time:
        timing_banner = '<div style="background:#064e3b;border:1px solid #059669;padding:8px 12px;border-radius:8px;font-size:12px;color:#a7f3d0;margin-bottom:12px;">🔥 <b>台股黃金動能時段 (09:05-10:15)</b>：市場量能充沛，雙目標推播啟動！</div>'
    elif is_market_closed:
        timing_banner = '<div style="background:#1e293b;border:1px solid #334155;padding:8px 12px;border-radius:8px;font-size:12px;color:#94a3b8;margin-bottom:12px;">🌙 <b>台股已收盤</b>：夜間時段可於 TG 查詢『美股可以買什麼』獲取美股最新戰情。</div>'
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
                <h1>📊 選股決策雷達 (台美雙棲旗艦版)</h1>
                <div class="sub">更新時間 (台北)：{now_str}</div>
            </div>
            <div class="timer-box">🔄 3分鐘自動刷新<br><span style="color:#10b981;">💬 TG 支援『台股/美股』分流查詢</span></div>
        </div>

        {timing_banner}

        <div class="market-banner">
            <div>🧭 <strong>大盤風向球：</strong> {market_text}</div>
            <div>{market_badge}</div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="title">⚡ 1. 今日台股當沖：強勢做多（保底小賺 vs 主力波段）</span>
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
    send_telegram_msg("🌐 <b>【台美雙棲選股助理已升級】</b>\n現在支援市場分流查詢：\n• <b>『美股可以買什麼？』</b>（美股大盤、科技巨頭波段、VOO/QQQ）\n• <b>『台股可以買什麼？』</b>（加權風向、當沖跳檔、波段潛力）\n• <b>『現在可以買什麼？』</b>（白天自動看台股，晚上自動看美股）")
except Exception: pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
