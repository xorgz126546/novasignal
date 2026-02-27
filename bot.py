import asyncio
import sys

# Fix for Windows event loop issue (MUST BE AT THE TOP)
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import requests
import pandas as pd
import numpy as np
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "8055993847:AAGB5XNWSMoVavoJo-3aX9JjWfXy6dSAoH4"

# ================= INDICATORS =================

def EMA(series, period):
    return series.ewm(span=period, adjust=False).mean()

def RSI(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ================= BINANCE DATA =================

def get_klines(symbol, interval="5m", limit=200):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}"
    response = requests.get(url, timeout=10)
    if response.status_code != 200:
        raise Exception("Invalid symbol or Binance unavailable")
    data = response.json()
    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tbbav", "tbqav", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df

# ================= LONG TERM =================

def long_term_rating(symbol):
    try:
        df = get_klines(symbol, "1d", 200)
        close = df["close"]
        price = close.iloc[-1]
        ema200 = EMA(close, 200).iloc[-1]
        score = 0
        # Trend
        if price > ema200:
            trend = "Bullish"
            score += 3
        else:
            trend = "Bearish"
            score += 1
        # Stability
        volatility = close.pct_change().std()
        if volatility < 0.04:
            score += 2
        elif volatility < 0.08:
            score += 1
        # Volume growth
        recent_vol = df["volume"].tail(30).mean()
        old_vol = df["volume"].head(30).mean()
        if recent_vol > old_vol:
            score += 2
        # Rating
        if score >= 6:
            rating = "A"
            decision = "Blue-chip crypto asset (long-term holding suitable)"
        elif score >= 4:
            rating = "B"
            decision = "Good project — can be accumulated on dips"
        elif score >= 3:
            rating = "C"
            decision = "Speculative / trading only"
        else:
            rating = "D"
            decision = "Avoid"
        return trend, rating, decision
    except Exception as e:
        print(f"Error in long_term_rating: {e}")
        return "Unknown", "Unknown", "No data available"

# ================= SIGNAL ANALYSIS =================

def analyze(symbol):
    try:
        df = get_klines(symbol)
        close = df["close"]
        price = close.iloc[-1]
        ema20 = EMA(close, 20).iloc[-1]
        ema50 = EMA(close, 50).iloc[-1]
        rsi = RSI(close).iloc[-1]
        # SIGNAL
        signal = "NO TRADE"
        if ema20 > ema50 and rsi > 52:
            signal = "BUY"
        elif ema20 < ema50 and rsi < 48:
            signal = "SELL"
        # NO TRADE OUTPUT
        if signal == "NO TRADE":
            return f"""
🪙 NovaSignals Report — {symbol}

🚫 No Trade Opportunity
Market sideways / weak momentum

📈 Indicators:
EMA20: {ema20:.4f}
EMA50: {ema50:.4f}
RSI: {rsi:.2f}
"""
        # TARGETS
        risk = price * 0.01
        if signal == "BUY":
            tp1 = price + risk
            tp2 = price + (risk * 2)
            tp3 = price + (risk * 3)
            sl = price - risk
        else:
            tp1 = price - risk
            tp2 = price - (risk * 2)
            tp3 = price - (risk * 3)
            sl = price + risk
        # LONG TERM
        trend, rating, decision = long_term_rating(symbol)
        return f"""
🪙 NovaSignals Report — {symbol}

📊 Short Term Signal:
Signal: {signal}
Entry: {price:.4f}

TP1: {tp1:.4f}
TP2: {tp2:.4f}
TP3: {tp3:.4f}

Stop Loss: {sl:.4f}

📈 Indicators:
EMA20: {ema20:.4f}
EMA50: {ema50:.4f}
RSI: {rsi:.2f}

==============================
🧠 LONG TERM INVESTMENT ANALYSIS
Trend (EMA200): {trend}
Investment Rating: {rating}
Decision: {decision}
==============================
"""
    except Exception as e:
        print(f"Error analyzing {symbol}: {e}")
        return f"❌ Symbol not found or API error: {symbol}"

# ================= TELEGRAM HANDLERS =================

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /signal BTC")
        return
    symbol = context.args[0].upper()
    result = analyze(symbol)
    await update.message.reply_text(result)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    text = text.replace("USDT", "")
    text = text.replace("/", "")
    text = text.replace(" ", "")
    if len(text) > 10:
        return
    await update.message.reply_text("⏳ Analyzing...")
    result = analyze(text)
    await update.message.reply_text(result)

# ================= MAIN =================

async def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("signal", signal_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Bot started successfully")
    await app.run_polling()

if __name__ == "__main__":
    # Additional fix: create new event loop
    if sys.platform == 'win32':
        loop = asyncio.WindowsSelectorEventLoop()
        asyncio.set_event_loop(loop)
    
    try:
        asyncio.run(main())
    except RuntimeError:
        # Fallback if event loop issue persists
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main())
        finally:
            loop.close()