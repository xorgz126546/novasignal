from coingecko import get_coin_data

# العملات الكبرى (Blue Chips)
BLUE_CHIPS = ["BTC", "ETH", "BNB", "SOL", "XRP"]


def evaluate_project(symbol):

    symbol = symbol.upper()

    # ---------------------------------
    # 1) BLUE CHIP OVERRIDE
    # ---------------------------------
    if symbol in BLUE_CHIPS:
        return "A", "Blue-chip crypto asset (long-term holding suitable)"

    data = get_coin_data(symbol)

    if not data:
        return "Unknown", "No data available"

    score = 0

    # ---------------------------------
    # Market Rank
    # ---------------------------------
    rank = data.get("rank")
    if rank:
        if rank <= 30:
            score += 4
        elif rank <= 100:
            score += 3
        elif rank <= 300:
            score += 2

    # ---------------------------------
    # Tokenomics
    # ---------------------------------
    circ = data.get("circulating_supply")
    maxs = data.get("max_supply")

    if circ and maxs:
        ratio = circ / maxs
        if ratio > 0.7:
            score += 3
        elif ratio > 0.4:
            score += 2
        else:
            score += 1

    # ---------------------------------
    # Market Activity
    # ---------------------------------
    market_cap = data.get("market_cap")
    volume = data.get("volume")

    if market_cap and volume:
        activity = volume / market_cap
        if activity > 0.20:
            score += 3
        elif activity > 0.10:
            score += 2
        elif activity > 0.05:
            score += 1

    # ---------------------------------
    # Developer Activity
    # ---------------------------------
    dev = data.get("developer_score")
    if dev:
        if dev > 70:
            score += 3
        elif dev > 40:
            score += 2
        elif dev > 20:
            score += 1

    # ---------------------------------
    # FINAL RATING
    # ---------------------------------
    if score >= 9:
        return "A", "Strong long-term investment"
    elif score >= 6:
        return "B", "Good investment"
    elif score >= 3:
        return "C", "Speculative / trading only"
    else:
        return "D", "High risk — avoid long-term holding"
