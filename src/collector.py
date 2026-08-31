import logging

import yfinance as yf


logger = logging.getLogger(__name__)

def fetch_market_data():
    logger.info("market_collection_started")
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
            logger.exception("market_collection_failed ticker=%s", name)
            data[name] = {"close": 0.0, "change_pct": 0.0}
            
    logger.info("market_collection_finished data=%s", data)
    return data
