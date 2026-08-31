import datetime

def fetch_market_data():
    print("[Collector] Fetching US market close data...")
    return {"TNX": {"close": 4.62, "change_pct": 3.45}, "VIX": {"close": 19.2, "change_pct": -0.8}, "NASDAQ": {"close": 17820.5, "change_pct": -0.4}}
