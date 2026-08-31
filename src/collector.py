import yfinance as yf

def fetch_market_data():
    print("[Collector] 미국 거시경제 지표 종가 수집 시작...")
    tickers = {"TNX": "^TNX", "VIX": "^VIX", "NASDAQ": "^IXIC"}
    data = {}
    for name, ticker in tickers.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if not hist.empty:
                close = hist['Close'].iloc[-1]
                prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else close
                change_pct = ((close - prev_close) / prev_close) * 100
                data[name] = {"close": round(float(close), 2), "change_pct": round(float(change_pct), 2)}
            else:
                data[name] = {"close": 0.0, "change_pct": 0.0}
        except Exception as e:
            print(f"[Collector] Error fetching {name}: {e}")
            data[name] = {"close": 0.0, "change_pct": 0.0}
            
    print(f"[Collector] 데이터 수집 완료: {data}")
    return data
