import unittest
from unittest.mock import MagicMock, patch

from src.market_sources import (
    FALLBACK_ORDER,
    _fetch_alphavantage,
    _fetch_fmp,
    _fetch_fred,
    _fetch_stooq,
    fetch_fallback,
)

ALL_KEYS = {"FMP_API_KEY": "k", "ALPHAVANTAGE_API_KEY": "k", "FRED_API_KEY": "k"}


def _resp(json_data=None, text=None):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = json_data
    r.text = text or ""
    return r


class FallbackOrderTest(unittest.TestCase):
    def test_every_required_indicator_has_a_fallback(self):
        for indicator in ("TNX", "VIX", "NASDAQ", "SPX", "DXY"):
            self.assertTrue(FALLBACK_ORDER.get(indicator), f"{indicator} 폴백 없음")

    def test_fmp_not_used_for_denied_indicators(self):
        # 실측상 FMP 무료 플랜은 ^TNX / DX-Y.NYB 가 ACCESS DENIED 다
        self.assertNotIn("fmp", FALLBACK_ORDER["TNX"])
        self.assertNotIn("fmp", FALLBACK_ORDER["DXY"])

    def test_alphavantage_only_used_for_treasury(self):
        # 무료 플랜은 인덱스 데이터 미제공, 국채금리만 가능
        for indicator, chain in FALLBACK_ORDER.items():
            if indicator != "TNX":
                self.assertNotIn("alphavantage", chain, f"{indicator} 에 alphavantage 부적절")


class FetcherTest(unittest.TestCase):
    @patch.dict("os.environ", ALL_KEYS, clear=True)
    @patch("src.market_sources.requests.get")
    def test_fmp_parses_price_and_previous_close(self, get):
        get.return_value = _resp([{"price": 15.85, "previousClose": 14.92}])

        result = _fetch_fmp("VIX")

        self.assertEqual(result["close"], 15.85)
        self.assertEqual(result["change_pct"], 6.23)
        self.assertEqual(result["source"], "fmp")

    @patch.dict("os.environ", {}, clear=True)
    def test_fmp_without_key_returns_none(self):
        self.assertIsNone(_fetch_fmp("VIX"))

    @patch.dict("os.environ", ALL_KEYS, clear=True)
    def test_fmp_rejects_unsupported_indicator(self):
        self.assertIsNone(_fetch_fmp("TNX"))

    @patch.dict("os.environ", ALL_KEYS, clear=True)
    @patch("src.market_sources.requests.get")
    def test_alphavantage_treasury_parsing(self, get):
        get.return_value = _resp({"data": [
            {"date": "2026-08-28", "value": "4.73"},
            {"date": "2026-08-27", "value": "4.67"},
        ]})

        result = _fetch_alphavantage("TNX")

        self.assertEqual(result["close"], 4.73)
        self.assertEqual(result["source"], "alphavantage")

    @patch.dict("os.environ", ALL_KEYS, clear=True)
    @patch("src.market_sources.requests.get")
    def test_alphavantage_skips_placeholder_values(self, get):
        get.return_value = _resp({"data": [
            {"date": "2026-08-29", "value": "."},
            {"date": "2026-08-28", "value": "4.73"},
            {"date": "2026-08-27", "value": "4.67"},
        ]})

        result = _fetch_alphavantage("TNX")

        self.assertEqual(result["close"], 4.73)

    @patch.dict("os.environ", ALL_KEYS, clear=True)
    @patch("src.market_sources.requests.get")
    def test_fred_parsing_and_placeholder_skip(self, get):
        get.return_value = _resp({"observations": [
            {"date": "2026-09-01", "value": "."},
            {"date": "2026-08-31", "value": "103.5"},
            {"date": "2026-08-30", "value": "103.0"},
        ]})

        result = _fetch_fred("DXY")

        self.assertEqual(result["close"], 103.5)
        self.assertEqual(result["source"], "fred")

    @patch.dict("os.environ", ALL_KEYS, clear=True)
    @patch("src.market_sources.requests.get")
    def test_stooq_csv_parsing(self, get):
        csv = "Date,Open,High,Low,Close,Volume\n2026-08-30,1,2,0,100.0,10\n2026-08-31,1,2,0,102.0,10"
        get.return_value = _resp(text=csv)

        result = _fetch_stooq("SPX")

        self.assertEqual(result["close"], 102.0)
        self.assertEqual(result["change_pct"], 2.0)
        self.assertEqual(result["source"], "stooq")

    @patch("src.market_sources.requests.get")
    def test_stooq_needs_no_api_key(self, get):
        get.return_value = _resp(text="Date,Close\n2026-08-31,100.0")
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNotNone(_fetch_stooq("SPX"))


class ChainBehaviourTest(unittest.TestCase):
    def test_first_success_wins_and_stops_chain(self):
        hit = {"close": 1.0, "change_pct": 0.0, "sma20": None, "dev_pct": None, "source": "fmp"}
        with patch.dict("src.market_sources.FETCHERS", {
            "fmp": MagicMock(return_value=hit),
            "fred": MagicMock(return_value=None),
            "stooq": MagicMock(return_value=None),
        }):
            result = fetch_fallback("VIX")
            self.assertEqual(result["source"], "fmp")

    def test_falls_through_to_next_source_on_exception(self):
        good = {"close": 2.0, "change_pct": 0.0, "sma20": None, "dev_pct": None, "source": "fred"}
        with patch.dict("src.market_sources.FETCHERS", {
            "fmp": MagicMock(side_effect=RuntimeError("http 500")),
            "fred": MagicMock(return_value=good),
            "stooq": MagicMock(return_value=None),
        }):
            result = fetch_fallback("VIX")
            self.assertEqual(result["source"], "fred")

    def test_falls_through_when_source_returns_none(self):
        good = {"close": 3.0, "change_pct": 0.0, "sma20": None, "dev_pct": None, "source": "stooq"}
        with patch.dict("src.market_sources.FETCHERS", {
            "fmp": MagicMock(return_value=None),
            "fred": MagicMock(return_value=None),
            "stooq": MagicMock(return_value=good),
        }):
            self.assertEqual(fetch_fallback("VIX")["source"], "stooq")

    def test_exhausted_chain_returns_none(self):
        with patch.dict("src.market_sources.FETCHERS", {
            "fmp": MagicMock(return_value=None),
            "fred": MagicMock(return_value=None),
            "stooq": MagicMock(return_value=None),
        }):
            self.assertIsNone(fetch_fallback("VIX"))

    def test_unknown_indicator_returns_none(self):
        self.assertIsNone(fetch_fallback("UNKNOWN_TICKER"))


class CollectorIntegrationTest(unittest.TestCase):
    @patch("src.collector.fetch_fallback")
    @patch("src.collector.yf.Ticker")
    def test_only_failed_indicators_use_fallback(self, ticker, fallback):
        # yfinance 전면 실패 상황
        ticker.side_effect = RuntimeError("yahoo down")
        fallback.return_value = {
            "close": 1.0, "change_pct": 0.1, "sma20": None, "dev_pct": None, "source": "fred",
        }

        from src.collector import TICKERS, fetch_market_data

        data = fetch_market_data()

        self.assertEqual(fallback.call_count, len(TICKERS))
        self.assertTrue(all(m["source"] == "fred" for m in data.values()))

    @patch("src.collector.fetch_fallback")
    @patch("src.collector.yf.Ticker")
    def test_successful_indicators_are_not_refetched(self, ticker, fallback):
        import pandas as pd

        hist = pd.DataFrame({"Close": [100.0] * 25 + [110.0]})
        ticker.return_value.history.return_value = hist

        from src.collector import fetch_market_data

        data = fetch_market_data()

        fallback.assert_not_called()
        self.assertTrue(all(m["source"] == "yfinance" for m in data.values()))


if __name__ == "__main__":
    unittest.main()
