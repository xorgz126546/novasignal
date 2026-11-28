import requests

BASE_URL = "https://api.coingecko.com/api/v3"

# cache لتجنب الطلب كل مرة
COIN_LIST = {}

def load_coin_list():
    global COIN_LIST
    if COIN_LIST:
        return

    coins = requests.get(f"{BASE_URL}/coins/list").json()

    for coin in coins:
        COIN_LIST[coin["symbol"].upper()] = coin["id"]


def get_coin_data(symbol: str):

    load_coin_list()

    symbol = symbol.upper()

    if symbol not in COIN_LIST:
        return None

    coin_id = COIN_LIST[symbol]

    url = f"{BASE_URL}/coins/{coin_id}?localization=false&tickers=false&market_data=true&community_data=true&developer_data=true"
    data = requests.get(url, timeout=15).json()

    market = data.get("market_data", {})

    return {
        "name": data.get("name"),
        "rank": market.get("market_cap_rank"),
        "market_cap": market.get("market_cap", {}).get("usd"),
        "volume": market.get("total_volume", {}).get("usd"),
        "circulating_supply": market.get("circulating_supply"),
        "total_supply": market.get("total_supply"),
        "max_supply": market.get("max_supply"),
        "community_score": data.get("community_score"),
        "developer_score": data.get("developer_score"),
        "liquidity_score": data.get("liquidity_score")
    }
