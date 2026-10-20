import pandas as pd

from tradingagents.memory import settlement


def test_vn_settlement_uses_vn_dataflow_not_yahoo(monkeypatch):
    dates = pd.date_range("2026-10-13", periods=12, freq="B")
    stock = pd.DataFrame({"Date": dates, "Close": range(100, 112)})
    benchmark = pd.DataFrame({"Date": dates, "Close": range(1000, 1012)})
    calls = []

    def fetch(symbol, **kwargs):
        calls.append(symbol)
        return benchmark if symbol == "VNINDEX" else stock

    monkeypatch.setattr(settlement, "fetch_vn_ohlcv", fetch)
    monkeypatch.setattr(settlement, "get_closes", lambda *args: (_ for _ in ()).throw(AssertionError("Yahoo must not be used")))
    raw, alpha, days, resolved = settlement.fetch_returns("FPT", "2026-10-13", 5, "^VNINDEX")
    assert calls == ["FPT", "VNINDEX"]
    assert raw is not None and alpha is not None and days == 5 and resolved
