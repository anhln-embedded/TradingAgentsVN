import json

import pandas as pd

from tradingagents.dataflows import ssi_mcp
from tradingagents.dataflows import vn_stock


def test_ssi_rows_are_normalized_to_ohlcv():
    records = [
        {
            "Symbol": "FPT",
            "TradingDate": "19/10/2026",
            "OpenPrice": "101000",
            "HighestPrice": "103000",
            "LowestPrice": "100000",
            "ClosePrice": "102500",
            "TotalMatchVol": "1234567",
        }
    ]
    frame = ssi_mcp._rows_to_ohlcv(records, "FPT")
    assert list(frame.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert frame.iloc[0]["Date"] == pd.Timestamp("2026-10-19")
    assert frame.iloc[0]["Close"] == 102500
    assert frame.iloc[0]["Volume"] == 1234567


def test_mcp_text_content_is_unwrapped():
    payload = {
        "content": [{"type": "text", "text": json.dumps({"data": [{"symbol": "FPT"}]})}]
    }
    assert ssi_mcp._find_records(payload) == [{"symbol": "FPT"}]


def test_ssi_is_primary_and_success_is_cached(monkeypatch):
    vn_stock._OHLCV_CACHE.clear()
    expected = pd.DataFrame({
        "Date": [pd.Timestamp("2026-10-19")],
        "Open": [100.0], "High": [103.0], "Low": [99.0],
        "Close": [102.0], "Volume": [1000],
    })
    calls = []
    monkeypatch.setattr(vn_stock, "fetch_ssi_ohlcv", lambda *args: calls.append("ssi") or expected)
    monkeypatch.setattr(vn_stock, "_fetch_vietcap_ohlcv", lambda *args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(vn_stock, "_fetch_dnse_ohlcv", lambda *args: (_ for _ in ()).throw(AssertionError()))
    assert vn_stock.fetch_vn_ohlcv("FPT", 30).equals(expected)
    assert vn_stock.fetch_vn_ohlcv("FPT", 30).equals(expected)
    assert calls == ["ssi"]


def test_vietcap_then_dnse_are_fallbacks(monkeypatch):
    vn_stock._OHLCV_CACHE.clear()
    expected = pd.DataFrame({
        "Date": [pd.Timestamp("2026-10-19")], "Open": [100.0], "High": [103.0],
        "Low": [99.0], "Close": [102.0], "Volume": [1000],
    })
    calls = []

    def fail(name):
        calls.append(name)
        raise RuntimeError(name)

    monkeypatch.setattr(vn_stock, "fetch_ssi_ohlcv", lambda *args: fail("ssi"))
    monkeypatch.setattr(vn_stock, "_fetch_vietcap_ohlcv", lambda *args: fail("vietcap"))
    monkeypatch.setattr(vn_stock, "_fetch_dnse_ohlcv", lambda *args: calls.append("dnse") or expected)
    assert vn_stock.fetch_vn_ohlcv("FPT", 30).equals(expected)
    assert calls == ["ssi", "vietcap", "dnse"]


def test_cached_fallback_does_not_mask_ssi_after_oauth(monkeypatch):
    from tradingagents.dataflows.ssi_oauth import ssi_oauth

    vn_stock._OHLCV_CACHE.clear()
    fallback = pd.DataFrame({
        "Date": [pd.Timestamp("2026-10-19")], "Open": [100.0], "High": [103.0],
        "Low": [99.0], "Close": [101.0], "Volume": [1000],
    })
    primary = fallback.copy()
    primary["Close"] = 102.0
    connected = {"value": False}
    monkeypatch.setattr(ssi_oauth, "status", lambda: {
        "connected": connected["value"], "debug_token": False, "expires_at": None,
    })
    monkeypatch.setattr(vn_stock, "fetch_ssi_ohlcv", lambda *args: primary if connected["value"] else (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(vn_stock, "_fetch_vietcap_ohlcv", lambda *args: fallback)
    assert vn_stock.fetch_vn_ohlcv("FPT", 30).iloc[-1]["Close"] == 101.0
    connected["value"] = True
    assert vn_stock.fetch_vn_ohlcv("FPT", 30).iloc[-1]["Close"] == 102.0
