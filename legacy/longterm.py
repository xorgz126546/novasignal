import pandas as pd
import requests

def long_term_trend(symbol):

    url = f"https://api.binance.com/api/v3/klines?symbol={symbol.upper()}USDT&interval=1d&limit=200"
    data = requests.get(url).json()

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "c1","c2","c3","c4","c5","c6"
    ])

    df["close"] = df["close"].astype(float)

    ema200 = df["close"].ewm(span=200).mean().iloc[-1]
    current_price = df["close"].iloc[-1]

    if current_price > ema200:
        return "Bullish"
    else:
        return "Bearish"
