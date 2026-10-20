from datetime import datetime, timezone

from tradingagents.dataflows.vn_stock import is_vn_stock
from tradingagents.markets.vietnam.calendar import market_session
from tradingagents.markets.vietnam.registry import get_vn_instrument
from tradingagents.markets.vietnam.rules import calculate_price_band, validate_order


def test_bare_global_tickers_are_not_misclassified():
    assert not is_vn_stock("IBM")
    assert not is_vn_stock("AMD")
    assert not is_vn_stock("SAP")
    assert is_vn_stock("FPT")
    assert is_vn_stock("XYZ.VN")


def test_registry_resolves_each_exchange():
    assert get_vn_instrument("FPT").exchange == "HOSE"
    assert get_vn_instrument("PVS").exchange == "HNX"
    assert get_vn_instrument("ACV").exchange == "UPCOM"


def test_price_bands_and_order_constraints_are_exchange_specific():
    assert calculate_price_band(100_000, "HOSE").ceiling == 107_000
    assert calculate_price_band(100_000, "HNX").ceiling == 110_000
    assert calculate_price_band(100_000, "UPCOM").floor == 85_000
    assert not validate_order("FPT", "BUY", 100_025, 100).valid
    assert not validate_order("PVS", "SELL", 30_000, 200, holdings=100).valid
    assert validate_order("PVS", "BUY", 30_000, 100, reference=29_000).valid


def test_market_clock_uses_vietnam_timezone_and_exchange_sessions():
    # 02:05 UTC is 09:05 in Vietnam: ATO only exists on HOSE.
    instant = datetime(2026, 10, 20, 2, 5, tzinfo=timezone.utc)
    assert market_session("HOSE", instant)["label"] == "ATO"
    assert market_session("HNX", instant)["label"] == "Phiên sáng"


def test_configured_market_holiday_is_closed(monkeypatch):
    monkeypatch.setenv("VN_MARKET_HOLIDAYS", "2026-10-20")
    instant = datetime(2026, 10, 20, 2, 5, tzinfo=timezone.utc)
    assert market_session("HOSE", instant)["status"] == "closed"
