import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types
import yfinance as yf

# 讀取環境變數或固定 Token
TG_TOKEN = os.environ.get("TG_TOKEN", "8681385226:AAEz20o9b9c_Jl04Z0ukUa8IUcFqRai1jyk")
bot = telebot.TeleBot(TG_TOKEN)

# 標的池定義
LONG_ETF_POOL = ["0050.TW", "006208.TW", "00692.TW", "00878.TW", "00919.TW", "00713.TW"]
LONG_STOCK_POOL = ["2330.TW", "2454.TW", "2308.TW", "2317.TW", "2881.TW", "2891.TW"]
SHORT_ETF_POOL = ["00631L.TW", "00670L.TW", "00830.TW", "00935.TW", "0052.TW"]
SHORT_STOCK_POOL = ["3450.TW", "3017.TW", "3324.TW", "2383.TW", "6274.TW", "3131.TW", "3583.TW", "3661.TW"]

def format_card(name, sym, price, chg, timing, buy_zone, target, defense, tip):
    sign = "+" if chg > 0 else ""
    return (
        f"📌 <b>{name} ({sym})</b>\n"
        f"現價：<b>{price}</b> 元 ({sign}{chg}%)\n"
        f"🚦 <b>進場時機訊號</b>：{timing}\n"
        f"📥 <b>建議進場區間</b>：{buy_zone} 元\n"
        f"🎯 <b>出場目標價</b>：{target}\n"
        f"🛡️ <b>防守停損價</b>：{defense}\n"
        f"💡 <b>操作指引</b>：{tip}"
    )

def analyze_strategy(symbols, strategy_type):
    results = []
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="6mo")
            if hist.empty or len(hist) < 20:
                continue
                
            closes = hist["Close"]
            cur_price = round(float(closes.iloc[-1]), 2)
            prev_close = round(float(closes.iloc[-2]), 2)
            change_pct = round(((cur_price - prev_close) / prev_close) * 100, 2)
            
            ma5 = closes.tail(5).mean()
            ma10 = closes.tail(10).mean()
            ma20 = closes.tail(20).mean()
            ma60 = closes.tail(60).mean() if len(closes) >= 60 else ma20
            
            clean_sym = sym.replace(".TW", "")
            name = ticker.info.get("shortName", clean_sym)
            
            if strategy_type == "long_etf":
                buy_zone = f"{round(cur_price * 0.98, 2)} ~ {cur_price}"
                target = "長期複利持有 (領息/正乖離>15%調節)"
                defense = f"{round(ma60, 2)} (季線支撐)"
                timing = "回測均線穩健支撐區，適合定期定額或零股分批低接。"
                results.append(format_card(name, clean_sym, cur_price, change_pct, timing, buy_zone, target, defense, "長線ETF分批逢低進場"))
                
            elif strategy_type == "long_stock":
                buy_zone = f"{round(cur_price * 0.98, 2)} ~ {round(cur_price * 1.01, 2)}"
                target = f"{round(cur_price * 1.25, 1)} (+25% 產業多頭波段)"
                defense = f"{round(ma60 * 0.97, 1)} (跌破季線轉弱防守)"
                timing = "季線向上多頭格局，具產業護城河，適合零股中長線抱牢。"
                if cur_price >= ma20:
                    results.append(format_card(name, clean_sym, cur_price, change_pct, timing, buy_zone, target, defense, "零股分批佈局，耐震抱長"))
                    
            elif strategy_type == "short_etf":
                if ma5 >= ma20 and (-1.5 <= change_pct <= 4.0):
                    buy_zone = f"{round(cur_price * 0.99, 2)} ~ {cur_price}"
                    target = f"{round(cur_price * 1.08, 2)} (+8% 短線停利)"
                    defense = f"{round(ma20 * 0.97, 2)} (跌破月線 -3% 停損)"
                    timing = "短均線黃金交叉，動能增溫剛起跑，適合 1~2 週波段操作。"
                    results.append(format_card(name, clean_sym, cur_price, change_pct, timing, buy_zone, target, defense, "短波段持有，獲利達標入袋"))
                    
            elif strategy_type == "short_stock":
                if cur_price >= ma20 and (-0.5 <= change_pct <= 4.0):
                    buy_zone = f"{round(cur_price * 0.99, 1)} ~ {round(cur_price * 1.01, 1)}"
                    target = f"{round(cur_price * 1.12, 1)} (+12% 波段獲利點)"
                    defense = f"{round(min(ma20 * 0.97, cur_price * 0.95), 1)} (跌破月線果斷砍單)"
                    timing = "月線多頭排列、未暴衝追高，剛完成回測整理出量勾起。"
                    results.append(format_card(name, clean_sym, cur_price, change_pct, timing, buy_zone, target, defense, "零股小單進出，嚴守停利停損"))
                    
            if len(results) >= 3:
                break
        except Exception:
            continue
    return results

def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = types.KeyboardButton("🏛️ 長線可抱ETF")
    btn2 = types.KeyboardButton("💎 長線可抱個股")
    btn3 = types.KeyboardButton("⚡ 短線可抱ETF")
    btn4 = types.KeyboardButton("🚀 短線可抱個股")
    keyboard.row(btn1, btn2)
    keyboard.row(btn3, btn4)
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
        cards = analyze_strategy(LONG_ETF_POOL, "long_etf")
        msg = "🏛️ <b>【長線可抱 ETF 推薦】</b>\n<i>以逢低分批存股、領息為核心：</i>\n\n" + "\n\n".join(cards) if cards else "目前未達低接門檻，建議耐心等待拉回！"
        bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    elif text == "💎 長線可抱個股":
        bot.send_message(chat_id, "🔍 正在篩選具產業護城河的【長線龍頭個股】...", parse_mode="HTML")
        cards = analyze_strategy(LONG_STOCK_POOL, "long_stock")
        msg = "💎 <b>【長線可抱 個股推薦】</b>\n<i>鎖定龍頭競爭優勢，適合零股定期長抱：</i>\n\n" + "\n\n".join(cards) if cards else "目前多數標的評價偏高，請等待回測季線！"
        bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    elif text == "⚡ 短線可抱ETF":
        bot.send_message(chat_id, "🔍 正在計算動能增溫、適合短期波段的【槓桿/主題型ETF】...", parse_mode="HTML")
        cards = analyze_strategy(SHORT_ETF_POOL, "short_etf")
        msg = "⚡ <b>【短線可抱 ETF 波段】</b>\n<i>短線均線轉強，吃波段獲利即走：</i>\n\n" + "\n\n".join(cards) if cards else "目前短線動能不足，暫無推薦標的。"
        bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_main_keyboard())

    elif text == "🚀 短線可抱個股":
        bot.send_message(chat_id, "🔍 正在過濾未暴衝、回測月線有撐的【熱門主流波段股】...", parse_mode="HTML")
        cards = analyze_strategy(SHORT_STOCK_POOL, "short_stock")
        msg = "🚀 <b>【短線可抱 個股波段】</b>\n<i>鎖定出量未暴衝主流股，零股短打：</i>\n\n" + "\n\n".join(cards) if cards else "市場處於震盪整理，無符合安全進場點標的。"
        bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=get_main_keyboard())
    else:
        bot.send_message(chat_id, "請點選下方選單按鈕查詢股票策略！", reply_markup=get_main_keyboard())

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running smoothly!")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()
    print("Web server running, starting bot polling...")
    bot.infinity_polling()
