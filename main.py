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

# 3. 股票/ETF 基礎繁體中文名稱字典
# 完整台股熱門中文名稱庫
TW_STOCK_DICT = {
    # ETF
    "0050": "元大台灣50", "006208": "富邦台50", "00692": "富邦公司治理",
    "00878": "國泰永續高股息", "00919": "群益台灣精選高息", "00713": "元大台灣高息低波",
    "00631L": "元大台灣50正2", "00670L": "富邦NASDAQ正2", "00830": "國泰費城半導體",
    "00935": "野村臺灣新科技50", "0052": "富邦科技",
    # 權值與熱門動態股
    "2330": "台積電", "2454": "聯發科", "2308": "台達電", "2317": "鴻海",
    "2382": "廣達", "6669": "緯穎", "3231": "緯創", "2357": "華碩",
    "3450": "聯鈞", "3017": "奇鋐", "3324": "雙鴻", "2383": "台光電",
    "6274": "台燿", "3131": "弘塑", "3583": "辛耘", "3661": "世芯-KY",
    "3081": "聯亞", "4979": "華星光", "3163": "波若威", "6442": "光聖",
    "1519": "華城", "1503": "士電", "1513": "中興電", "2609": "陽明",
    "2615": "萬海", "2603": "長榮", "2881": "富邦金", "2891": "中信金"
}

def get_stock_name(symbol):
    clean = symbol.replace(".TW", "").replace(".TWO", "").strip()
    if clean in TW_STOCK_DICT:
        return TW_STOCK_DICT[clean]
    # 嘗試從證交所 API 即時查詢中文名稱
    try:
        import requests
        r = requests.get(f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{clean}.tw", timeout=2)
        data = r.json()
        if data.get("msgArray"):
            return data["msgArray"][0].get("n", clean)
    except Exception:
        pass
    return clean

# 4. 黑名單：隔日沖倒貨與大型無波段標的
BLACKLIST_SYMBOLS = ["2609", "2615", "2603", "2002", "2881", "2882"]

# 5. 長線固定核心標的池
LONG_ETF_POOL = ["0050.TW", "006208.TW", "00692.TW", "00878.TW", "00919.TW", "00713.TW"]
LONG_STOCK_POOL = ["2330.TW", "2454.TW", "2308.TW", "2317.TW", "2881.TW", "2891.TW"]
SHORT_ETF_POOL = ["00631L.TW", "00670L.TW", "00830.TW", "00935.TW", "0052.TW"]

# 6. 動態抓取台股當日成交金額前 30 大熱門排行
def get_dynamic_market_leaders():
    """從公開市場即時榜單動態取得成交金額最高的前 30 檔股票代碼"""
    dynamic_pool = []
    try:
        url = "https://tw.stock.yahoo.com/class-quote?sectorId=all&exchange=TAI&order=volume_amount&sort=desc"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            found = re.findall(r'href="\/quote\/([0-9]{4})\.TW"', res.text)
            for sym in found:
                if sym not in BLACKLIST_SYMBOLS and f"{sym}.TW" not in dynamic_pool:
                    dynamic_pool.append(f"{sym}.TW")
                if len(dynamic_pool) >= 25:
                    break
    except Exception:
        pass
    
    # 若網路爬蟲暫時阻擋，使用備用高熱度主流題材池
    if not dynamic_pool:
        fallback = ["3450.TW", "3017.TW", "3324.TW", "2383.TW", "6274.TW", "3131.TW", "3583.TW", "3661.TW", "2382.TW", "6669.TW"]
        dynamic_pool = [s for s in fallback if s.replace(".TW", "") not in BLACKLIST_SYMBOLS]
        
    return dynamic_pool

def fetch_data(symbols, strat_type):
    cards = []
    target_symbols = get_dynamic_market_leaders() if strat_type == "short_stock" else symbols

    for sym in target_symbols:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="3mo")
            if hist.empty or len(hist) < 20:
                continue
            closes = hist["Close"]
            cur_price = round(float(closes.iloc[-1]), 2)
            prev_close = round(float(closes.iloc[-2]), 2)
            change_pct = round(((cur_price - prev_close) / prev_close) * 100, 2)
            
            ma5 = closes.tail(5).mean()
            ma20 = closes.tail(20).mean()
            ma60 = closes.tail(60).mean() if len(closes) >= 60 else ma20
            clean_sym = sym.replace(".TW", "")
            
            # 取得中文名稱
            name = get_stock_name(sym)
            
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
                # 動態熱門排行短線篩選：站在月線之上、未過度暴衝 (+0.2% ~ +3.8%)、拒絕追高
                if cur_price >= ma20 and (-0.8 <= change_pct <= 3.8):
                    buy_zone = f"{round(cur_price * 0.99, 1)} ~ {round(cur_price * 1.01, 1)}"
                    target = f"{round(cur_price * 1.12, 1)} (+12% 波段獲利點)"
                    defense = f"{round(min(ma20 * 0.97, cur_price * 0.95), 1)} (跌破月線果斷砍單)"
                    timing = "🔥 熱門成交排行榜強勢股，月線向上且未暴衝，適合零股進出。"
                    cards.append({"name": name, "sym": clean_sym, "price": cur_price, "chg": change_pct, "timing": timing, "buy_zone": buy_zone, "target": target, "defense": defense, "tip": "每日動態熱錢龍頭，分批切入"})
                    
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

# ----------------- Web App 儀表板 HTML 頁面 (高對比黑底清晰樣式) -----------------
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
        /* 強制股票中文名稱呈現純白粗體，高對比不反灰 */
        .stock-title { color: #ffffff !important; font-size: 1.25rem; font-weight: 800; letter-spacing: 0.5px; }
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
        <p class="text-info">長線穩健存股 + 短線每日成交榜動態熱錢 | 進出場時機指引</p>
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

    <!-- 4. 短線 個股（每日動態成交排行） -->
    <h4 class="section-title">🚀 短線可抱 個股（🔥 每日動態成交熱錢榜過濾）</h4>
    <div class="row">
        {% for item in short_stock %}
        <div class="col-md-4">
            <div class="card card-custom p-3">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <span class="stock-title">{{ item.name }}<span class="stock-code">({{ item.sym }})</span></span>
                    <span class="badge badge-short">動態熱錢榜</span>
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
        bot.send_message(chat_id, "🔍 正在動態抓取【台股今日成交榜前30大熱門股】，過濾起漲未暴衝標的...", parse_mode="HTML")
        cards = fetch_data([], "short_stock")
        msg = "🚀 <b>【短線可抱 個股（🔥 每日動態熱錢榜）】</b>\n<i>鎖定市場最熱量能、未暴衝追高、零股短波段：</i>\n\n" + "\n\n".join([format_tg_card(c) for c in cards]) if cards else "市場成交榜標的多數已暴衝或破線，無安全進場點。"
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
