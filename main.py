import os
import re
import threading
import requests
from flask import Flask, render_template_string
import telebot
from telebot import types
import yfinance as yf

# 1. 初始化 Flask Web App
app = Flask(__name__)

# 2. 初始化 Telegram 機器人
TG_TOKEN = os.environ.get("TG_TOKEN", "8681385226:AAEz20o9b9c_Jl04Z0ukUa8IUcFqRai1jyk")
bot = telebot.TeleBot(TG_TOKEN)

# 3. 官方全台股代碼名稱對照快取字典
STOCK_NAME_CACHE = {
    # 常用 ETF
    "0050": "元大台灣50", "006208": "富邦台50", "00692": "富邦公司治理",
    "00878": "國泰永續高股息", "00919": "群益台灣精選高息", "00713": "元大台灣高息低波",
    "00631L": "元大台灣50正2", "00670L": "富邦NASDAQ正2", "00830": "國泰費城半導體",
    "00935": "野村臺灣新科技50", "0052": "富邦科技",
    # 熱門焦點
    "2330": "台積電", "2454": "聯發科", "2308": "台達電", "2317": "鴻海",
    "2382": "廣達", "6669": "緯穎", "3231": "緯創", "2357": "華碩",
    "6770": "力積電", "3624": "光頡", "2305": "全友", "2383": "台光電",
    "3450": "聯鈞", "3017": "奇鋐", "3324": "雙鴻", "6274": "台燿",
    "3131": "弘塑", "3583": "辛耘", "3661": "世芯-KY", "3081": "聯亞",
    "4979": "華星光", "3163": "波若威", "6442": "光聖", "1519": "華城"
}

def load_official_tw_stock_names():
    """從證交所 ISIN 官方清冊下載全台股上市/上櫃代號中文表"""
    urls = [
        "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", # 上市
        "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"  # 上櫃
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=5)
            r.encoding = 'big5'
            # 抓取格式如： 2330 台積電
            items = re.findall(r'(\b[0-9]{4,6}[A-Z]?\b)[\u3000\s]+([\u4e00-\u9fa5A-Za-z0-9\-]+)', r.text)
            for code, name in items:
                STOCK_NAME_CACHE[code] = name.strip()
        except Exception:
            continue

# 在背景載入全市場名稱清單
threading.Thread(target=load_official_tw_stock_names, daemon=True).start()

def get_stock_name(symbol):
    clean = symbol.replace(".TW", "").replace(".TWO", "").strip()
    if clean in STOCK_NAME_CACHE:
        return STOCK_NAME_CACHE[clean]
    
    # 即時查詢 Yahoo 網頁標題或證交所備用
    try:
        url = f"https://tw.stock.yahoo.com/quote/{symbol}"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=2)
        if res.status_code == 200:
            match = re.search(r'<title>([^<]+)\s*\(([0-9]{4,6})\)', res.text)
            if match:
                name = match.group(1).replace("個股走勢", "").replace("-", "").strip()
                STOCK_NAME_CACHE[clean] = name
                return name
    except Exception:
        pass
    return clean

# 4. 黑名單：隔日沖倒貨與牛皮股
BLACKLIST_SYMBOLS = ["2609", "2615", "2603", "2002"]

# 5. 標的池
LONG_ETF_POOL = ["0050.TW", "006208.TW", "00692.TW", "00878.TW", "00919.TW", "00713.TW"]
LONG_STOCK_POOL = ["2330.TW", "2454.TW", "2308.TW", "2317.TW", "2881.TW", "2891.TW"]
SHORT_ETF_POOL = ["00631L.TW", "00670L.TW", "00830.TW", "00935.TW", "0052.TW"]

def get_dynamic_market_leaders():
    """動態取得上市與上櫃熱門成交排行"""
    dynamic_pool = []
    headers = {"User-Agent": "Mozilla/5.0"}
    
    # 上市熱門
    try:
        res = requests.get("https://tw.stock.yahoo.com/class-quote?sectorId=all&exchange=TAI&order=volume_amount&sort=desc", headers=headers, timeout=4)
        if res.status_code == 200:
            for sym in re.findall(r'href="\/quote\/([0-9]{4})\.TW"', res.text):
                if sym not in BLACKLIST_SYMBOLS and f"{sym}.TW" not in dynamic_pool:
                    dynamic_pool.append(f"{sym}.TW")
                if len(dynamic_pool) >= 15:
                    break
    except Exception:
        pass

    # 上櫃熱門（如光頡 3624、聯亞等）
    try:
        res_otc = requests.get("https://tw.stock.yahoo.com/class-quote?sectorId=all&exchange=TWO&order=volume_amount&sort=desc", headers=headers, timeout=4)
        if res_otc.status_code == 200:
            for sym in re.findall(r'href="\/quote\/([0-9]{4})\.TWO"', res_otc.text):
                if sym not in BLACKLIST_SYMBOLS and f"{sym}.TWO" not in dynamic_pool:
                    dynamic_pool.append(f"{sym}.TWO")
                if len(dynamic_pool) >= 30:
                    break
    except Exception:
        pass

    if not dynamic_pool:
        dynamic_pool = ["6770.TW", "3624.TWO", "2305.TW", "3450.TW", "3017.TW", "3324.TW", "2383.TW", "6274.TW", "3131.TW", "3583.TW"]
        
    return dynamic_pool

def fetch_data(symbols, strat_type):
    cards = []
    target_symbols = get_dynamic_market_leaders() if strat_type == "short_stock" else symbols

    for sym in target_symbols:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="3mo")
            if hist.empty or len(hist) < 15:
                continue
            closes = hist["Close"]
            cur_price = round(float(closes.iloc[-1]), 2)
            prev_close = round(float(closes.iloc[-2]), 2)
            change_pct = round(((cur_price - prev_close) / prev_close) * 100, 2)
            
            ma5 = closes.tail(5).mean()
            ma20 = closes.tail(20).mean()
            ma60 = closes.tail(60).mean() if len(closes) >= 60 else ma20
            clean_sym = sym.replace(".TW", "").replace(".TWO", "")
            name = get_stock_name(sym)
            
            # 若仍未取得中文，強制以備用字典覆蓋
            if name == clean_sym and clean_sym in STOCK_NAME_CACHE:
                name = STOCK_NAME_CACHE[clean_sym]
            
            if strat_type == "long_etf":
                buy_zone = f"{round(cur_price * 0.98, 2)} ~ {cur_price}"
                target = "長期複利持有 (領息/正乖離>15%調節)"
                defense = f"{round(ma60, 2)} (季線支撐)"
                timing = "回測均線穩健支撐區，適合定期定額或零股分批低接。"
                cards.append({"name": name, "sym": clean_sym, "price": cur_price, "chg": change_pct, "timing": timing, "buy_zone": buy_zone, "target": target, "defense": defense, "tip": "長線ETF分批逢低進場"})
                
            elif strat_type == "long_stock":
                buy_zone = f"{round(cur_price * 0.98, 2)} ~ {round(cur_price * 1.01, 2)}"
                target = f"{round(cur_price * 1.25, 1)} (+25% 產業多頭波段)"
                defense = f"{round(ma60 * 0.97, 1)} (跌破季線轉弱防守)"
                timing = "季線向上多頭格局，具產業護城河，適合零股中長線抱牢。"
                if cur_price >= ma20:
                    cards.append({"name": name, "sym": clean_sym, "price": cur_price, "chg": change_pct, "timing": timing, "buy_zone": buy_zone, "target": target, "defense": defense, "tip": "零股分批佈局，耐震抱長"})
                    
            elif strat_type == "short_etf":
                if ma5 >= ma20 and (-1.5 <= change_pct <= 4.0):
                    buy_zone = f"{round(cur_price * 0.99, 2)} ~ {cur_price}"
                    target = f"{round(cur_price * 1.08, 2)} (+8% 短線停利)"
                    defense = f"{round(ma20 * 0.97, 2)} (跌破月線 -3% 停損)"
                    timing = "短均線黃金交叉，動能增溫剛起跑，適合 1~2 週波段操作。"
                    cards.append({"name": name, "sym": clean_sym, "price": cur_price, "chg": change_pct, "timing": timing, "buy_zone": buy_zone, "target": target, "defense": defense, "tip": "短波段持有，獲利達標入袋"})
                    
            elif strat_type == "short_stock":
                # 放寬動能門檻至 +5.8%，納入熱門強勢股
                if cur_price >= ma20 and (-1.0 <= change_pct <= 5.8):
                    buy_zone = f"{round(cur_price * 0.99, 1)} ~ {round(cur_price * 1.01, 1)}"
                    target = f"{round(cur_price * 1.12, 1)} (+12% 波段獲利點)"
                    defense = f"{round(min(ma20 * 0.97, cur_price * 0.95), 1)} (跌破月線果斷砍單)"
                    timing = "🔥 上市櫃熱門成交榜動能股，多頭均線向上，適合零股短波段。"
                    cards.append({"name": name, "sym": clean_sym, "price": cur_price, "chg": change_pct, "timing": timing, "buy_zone": buy_zone, "target": target, "defense": defense, "tip": "動態熱錢榜強勢股，分批進出"})
                    
            if len(cards) >= 4:
                break
        except Exception:
            continue
    return cards

def format_tg_card(item):
    sign = "+" if item["chg"] > 0 else ""
    return (
        f"📌 <b>{item['name']} ({item['sym']})</b>\n"
        f"現價：<b>{item['price']}</b> 元 ({sign}{item['chg']}%)\n"
        f"🚦 <b>進場時機訊號</b>：{item['timing']}\n"
        f"📥 <b>建議進場區間</b>：{item['buy_zone']} 元\n"
        f"🎯 <b>出場目標價</b>：{item['target']}\n"
        f"🛡️ <b>防守停損價</b>：{item['defense']}\n"
        f"💡 <b>操作指引</b>：{item['tip']}"
    )

# ----------------- Web App 儀表板 HTML 頁面 -----------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HanStockRadar 量化戰情室</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #0b0f19; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; padding-bottom: 50px; }
        .card-custom { background: #161e2e; border: 1px solid #2d3748; border-radius: 14px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.4); }
        .stock-title { color: #ffffff !important; font-size: 1.25rem; font-weight: 800; }
        .stock-code { color: #94a3b8 !important; font-size: 0.95rem; font-weight: normal; margin-left: 4px; }
        .badge-long { background: #059669; color: #ffffff; font-weight: 600; padding: 6px 10px; border-radius: 6px; }
        .badge-short { background: #2563eb; color: #ffffff; font-weight: 600; padding: 6px 10px; border-radius: 6px; }
        .section-title { border-left: 5px solid #38bdf8; padding-left: 12px; margin-top: 35px; margin-bottom: 20px; font-weight: 700; color: #f1f5f9; }
        .price-up { color: #ff3b5c; font-weight: 800; }
        .price-down { color: #10b981; font-weight: 800; }
        .info-label { color: #e2e8f0; font-weight: 600; }
        .info-value { color: #cbd5e1; }
    </style>
</head>
<body class="container py-4">
    <div class="text-center mb-4">
        <h2 class="fw-bold text-white">🚀 HanStockRadar 投資策略儀表板</h2>
        <p class="text-info">長線穩健存股 + 上市上櫃動態熱錢榜 | 進出場時機指引</p>
    </div>

    <!-- 1. 長線 ETF -->
    <h4 class="section-title">🏛️ 長線可抱 ETF（定期定額 / 逢低存股）</h4>
    <div class="row">
        {% for item in long_etf %}
        <div class="col-md-4">
            <div class="card card-custom p-3">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <span class="stock-title">{{ item.name }}<span class="stock-code">({{ item.sym }})</span></span>
                    <span class="badge badge-long">長線ETF</span>
                </div>
                <div class="fs-4 mb-2 {{ 'price-up' if item.chg > 0 else 'price-down' }}">{{ item.price }} 元 ({{ '+' if item.chg > 0 else '' }}{{ item.chg }}%)</div>
                <p class="small mb-1"><span class="info-label">🚦 進場時機：</span><span class="info-value">{{ item.timing }}</span></p>
                <p class="small mb-1"><span class="info-label">📥 建議進場：</span><span class="info-value">{{ item.buy_zone }} 元</span></p>
                <p class="small mb-1"><span class="info-label">🎯 出場目標：</span><span class="info-value">{{ item.target }}</span></p>
                <p class="small mb-0 text-danger">🛡️ <b>防守支撐：</b>{{ item.defense }}</p>
            </div>
        </div>
        {% endfor %}
    </div>

    <!-- 2. 長線 個股 -->
    <h4 class="section-title">💎 長線可抱 個股（產業龍頭 / 核心資產）</h4>
    <div class="row">
        {% for item in long_stock %}
        <div class="col-md-4">
            <div class="card card-custom p-3">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <span class="stock-title">{{ item.name }}<span class="stock-code">({{ item.sym }})</span></span>
                    <span class="badge badge-long">長線個股</span>
                </div>
                <div class="fs-4 mb-2 {{ 'price-up' if item.chg > 0 else 'price-down' }}">{{ item.price }} 元 ({{ '+' if item.chg > 0 else '' }}{{ item.chg }}%)</div>
                <p class="small mb-1"><span class="info-label">🚦 進場時機：</span><span class="info-value">{{ item.timing }}</span></p>
                <p class="small mb-1"><span class="info-label">📥 建議進場：</span><span class="info-value">{{ item.buy_zone }} 元</span></p>
                <p class="small mb-1"><span class="info-label">🎯 出場目標：</span><span class="info-value">{{ item.target }}</span></p>
                <p class="small mb-0 text-danger">🛡️ <b>防守支撐：</b>{{ item.defense }}</p>
            </div>
        </div>
        {% endfor %}
    </div>

    <!-- 3. 短線 ETF -->
    <h4 class="section-title">⚡ 短線可抱 ETF（趨勢動能波段）</h4>
    <div class="row">
        {% for item in short_etf %}
        <div class="col-md-4">
            <div class="card card-custom p-3">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <span class="stock-title">{{ item.name }}<span class="stock-code">({{ item.sym }})</span></span>
                    <span class="badge badge-short">短線ETF</span>
                </div>
                <div class="fs-4 mb-2 {{ 'price-up' if item.chg > 0 else 'price-down' }}">{{ item.price }} 元 ({{ '+' if item.chg > 0 else '' }}{{ item.chg }}%)</div>
                <p class="small mb-1"><span class="info-label">🚦 進場時機：</span><span class="info-value">{{ item.timing }}</span></p>
                <p class="small mb-1"><span class="info-label">📥 建議進場：</span><span class="info-value">{{ item.buy_zone }} 元</span></p>
                <p class="small mb-1"><span class="info-label">🎯 出場目標：</span><span class="info-value">{{ item.target }}</span></p>
                <p class="small mb-0 text-danger">🛡️ <b>防守停損：</b>{{ item.defense }}</p>
            </div>
        </div>
        {% endfor %}
    </div>

    <!-- 4. 短線 個股（上市櫃動態熱錢榜） -->
    <h4 class="section-title">🚀 短線可抱 個股（🔥 上市/上櫃動態熱錢榜過濾）</h4>
    <div class="row">
        {% for item in short_stock %}
        <div class="col-md-4">
            <div class="card card-custom p-3">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <span class="stock-title">{{ item.name }}<span class="stock-code">({{ item.sym }})</span></span>
                    <span class="badge badge-short">熱門動態榜</span>
                </div>
                <div class="fs-4 mb-2 {{ 'price-up' if item.chg > 0 else 'price-down' }}">{{ item.price }} 元 ({{ '+' if item.chg > 0 else '' }}{{ item.chg }}%)</div>
                <p class="small mb-1"><span class="info-label">🚦 進場時機：</span><span class="info-value">{{ item.timing }}</span></p>
                <p class="small mb-1"><span class="info-label">📥 建議進場：</span><span class="info-value">{{ item.buy_zone }} 元</span></p>
                <p class="small mb-1"><span class="info-label">🎯 出場目標：</span><span class="info-value">{{ item.target }}</span></p>
                <p class="small mb-0 text-danger">🛡️ <b>防守停損：</b>{{ item.defense }}</p>
            </div>
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    l_etf = fetch_data(LONG_ETF_POOL, "long_etf")
    l_stk = fetch_data(LONG_STOCK_POOL, "long_stock")
    s_etf = fetch_data(SHORT_ETF_POOL, "short_etf")
    s_stk = fetch_data([], "short_stock")
    return render_template_string(HTML_TEMPLATE, long_etf=l_etf, long_stock=l_stk, short_etf=s_etf, short_stock=s_stk)

# ----------------- Telegram 機器人事件處理 -----------------
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(types.KeyboardButton("🏛️ 長線可抱ETF"), types.KeyboardButton("💎 長線可抱個股"))
    keyboard.row(types.KeyboardButton("⚡ 短線可抱ETF"), types.KeyboardButton("🚀 短線可抱個股"))
    return keyboard

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "👋 歡迎使用 <b>HanStockRadar</b>！\n請點擊下方按鈕查詢最新的長/短線進出場配置：",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )

@bot.message_handler(func=lambda msg: True)
def handle_text(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    if text == "🏛️ 長線可抱ETF":
        bot.send_message(chat_id, "🔍 正在篩選適合小資穩健滾存的【長線優質ETF】...", parse_mode="HTML")
        cards = fetch_data(LONG_ETF_POOL, "long_etf")
        msg = "🏛️ <b>【長線可抱 ETF 推薦】</b>\n<i>以逢低分批存股、領息為核心：</i>\n\n" + "\n\n".join([format_tg_card(c) for c in cards]) if cards else "目前未達低接門檻，建議耐心等待拉回！"
        bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    elif text == "💎 長線可抱個股":
        bot.send_message(chat_id, "🔍 正在篩選具產業護城河的【長線龍頭個股】...", parse_mode="HTML")
        cards = fetch_data(LONG_STOCK_POOL, "long_stock")
        msg = "💎 <b>【長線可抱 個股推薦】</b>\n<i>鎖定龍頭競爭優勢，適合零股定期長抱：</i>\n\n" + "\n\n".join([format_tg_card(c) for c in cards]) if cards else "目前多數標的評價偏高，請等待回測季線！"
        bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    elif text == "⚡ 短線可抱ETF":
        bot.send_message(chat_id, "🔍 正在計算動能增溫、適合短期波段的【槓桿/主題型ETF】...", parse_mode="HTML")
        cards = fetch_data(SHORT_ETF_POOL, "short_etf")
        msg = "⚡ <b>【短線可抱 ETF 波段】</b>\n<i>短線均線轉強，吃波段獲利即走：</i>\n\n" + "\n\n".join([format_tg_card(c) for c in cards]) if cards else "目前短線動能不足，暫無推薦標的。"
        bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    elif text == "🚀 短線可抱個股":
        bot.send_message(chat_id, "🔍 正在動態抓取【上市/上櫃今日成交榜前30大熱門股】，過濾動能未暴衝標的...", parse_mode="HTML")
        cards = fetch_data([], "short_stock")
        msg = "🚀 <b>【短線可抱 個股（🔥 上市櫃動態熱錢榜）】</b>\n<i>涵蓋上市櫃人氣主流，波段進出：</i>\n\n" + "\n\n".join([format_tg_card(c) for c in cards]) if cards else "目前市場動態榜標的普遍漲幅過大或回落，無安全進場點。"
        bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_main_keyboard())
    else:
        bot.send_message(chat_id, "請點選下方選單按鈕查詢股票策略！", reply_markup=get_main_keyboard())

def start_bot():
    print("Telegram Bot polling started...")
    bot.infinity_polling()

t = threading.Thread(target=start_bot)
t.daemon = True
t.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
