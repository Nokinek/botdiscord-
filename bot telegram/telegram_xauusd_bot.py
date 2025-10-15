"""
Telegram Bot for XAUUSD (Gold) trading signals — MetaTrader5 version
Stable 24/7 with auto-retry and async job scheduling
"""

import time
import logging
import asyncio
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    JobQueue,
)

# ---------------- CONFIG ----------------
CONFIG = {
    "TELEGRAM_TOKEN": "8482055566:AAGtjzWEn_68ME3cqt8vKMb12b4H2WysDnQ",  # Twój bot token
    "CHAT_ID": 5399344439,  # Twój chat ID
    "SYMBOL": "XAUUSD",
    "INTERVAL_MINUTES": 60,
    "CHECK_EVERY_SECONDS": 900,  # co 15 minut
    "EMA_FAST": 12,
    "EMA_SLOW": 26,
    "EMA_SIGNAL": 9,
    "EMA_TREND": 200,
    "RSI_PERIOD": 14,
    "RSI_OVERSOLD": 30,
    "RSI_OVERBOUGHT": 70,
    "DEBOUNCE_SECONDS": 3600,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("xauusd_bot")

LAST_SIGNAL = {"type": None, "timestamp": 0}
job_lock = asyncio.Lock()

# ---------------- FETCH DATA FROM MT5 ----------------
def fetch_ohlcv_mt5(interval_minutes: int):
    if not mt5.initialize():
        logger.error(f"MT5 initialize failed, error code = {mt5.last_error()}")
        return None

    timeframe_map = {
        1: mt5.TIMEFRAME_M1,
        5: mt5.TIMEFRAME_M5,
        15: mt5.TIMEFRAME_M15,
        30: mt5.TIMEFRAME_M30,
        60: mt5.TIMEFRAME_H1,
        240: mt5.TIMEFRAME_H4,
        1440: mt5.TIMEFRAME_D1,
    }

    timeframe = timeframe_map.get(interval_minutes, mt5.TIMEFRAME_H1)
    rates = mt5.copy_rates_from_pos(CONFIG["SYMBOL"], timeframe, 0, 500)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        logger.warning("MT5 returned no data")
        return None

    df = pd.DataFrame(rates)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("datetime", inplace=True)
    return df

# ---------------- INDICATORS ----------------
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.rolling(window=period).mean()
    ma_down = down.rolling(window=period).mean()
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist}, index=series.index)

# ---------------- STRATEGY ----------------
def generate_signal(df):
    cfg = CONFIG
    close = df["close"]
    if len(close) < 2:
        return "HOLD", {"price": close.iloc[-1], "rsi": np.nan}

    macd_df = macd(close, cfg["EMA_FAST"], cfg["EMA_SLOW"], cfg["EMA_SIGNAL"])
    rsi_v = rsi(close, cfg["RSI_PERIOD"])
    ema_trend = ema(close, cfg["EMA_TREND"])

    last, prev = len(close) - 1, len(close) - 2
    macd_now, macd_prev = macd_df.iloc[last], macd_df.iloc[prev]
    rsi_now = rsi_v.iloc[last]
    price = close.iloc[last]

    macd_up = macd_prev.macd < macd_prev.signal and macd_now.macd > macd_now.signal
    macd_down = macd_prev.macd > macd_prev.signal and macd_now.macd < macd_now.signal

    if macd_up and price > ema_trend.iloc[last] and rsi_now < cfg["RSI_OVERBOUGHT"]:
        return "BUY", {"price": price, "rsi": rsi_now}
    elif macd_down and price < ema_trend.iloc[last] and rsi_now > cfg["RSI_OVERSOLD"]:
        return "SELL", {"price": price, "rsi": rsi_now}
    else:
        return "HOLD", {"price": price, "rsi": rsi_now}

# ---------------- BOT ----------------
async def send_message(bot, text):
    try:
        await bot.send_message(chat_id=CONFIG["CHAT_ID"], text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Send message failed: {e}")

async def check_and_send(context: ContextTypes.DEFAULT_TYPE):
    async with job_lock:
        global LAST_SIGNAL
        df = fetch_ohlcv_mt5(CONFIG["INTERVAL_MINUTES"])
        if df is None or df.empty:
            logger.warning("No MT5 data fetched.")
            return

        sig, meta = generate_signal(df)
        now = time.time()

        if sig != LAST_SIGNAL["type"] or now - LAST_SIGNAL["timestamp"] > CONFIG["DEBOUNCE_SECONDS"]:
            LAST_SIGNAL.update({"type": sig, "timestamp": now})
            msg = f"<b>XAUUSD signal:</b> {sig}\n💰 Price: {meta['price']:.2f}\n📊 RSI: {meta['rsi']:.1f}\n<i>Not financial advice</i>"
            await send_message(context.bot, msg)
            logger.info(f"Sent signal: {sig} at {meta['price']:.2f}")

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"✅ Bot działa 24/7.\nSygnały dla: {CONFIG['SYMBOL']}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sig = LAST_SIGNAL.get("type") or "brak"
    await update.message.reply_text(f"Ostatni sygnał: {sig}")

# ---------------- MAIN ----------------
def main():
    token = CONFIG["TELEGRAM_TOKEN"]
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))

    job_queue = app.job_queue
    job_queue.run_repeating(check_and_send, interval=CONFIG["CHECK_EVERY_SECONDS"], first=5)

    logger.info("🚀 Bot XAUUSD uruchomiony 24/7...")
    app.run_polling()  # <-- działa sam, bez asyncio.run()

def run_forever():
    while True:
        try:
            main()
        except KeyboardInterrupt:
            logger.info("🛑 Bot zatrzymany przez użytkownika.")
            break
        except Exception as e:
            logger.error(f"⚠️ Bot crashed: {e}. Restarting in 10 sekund...")
            time.sleep(10)

if __name__ == "__main__":
    run_forever()
