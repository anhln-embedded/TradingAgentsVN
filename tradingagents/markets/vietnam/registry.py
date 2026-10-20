"""Authoritative-enough Vietnamese instrument identity with safe fallbacks.

The bundled map covers the dashboard universe and common benchmark aliases.
Callers may register records returned by SSI/Vietcap at runtime.  Unknown bare
three-letter strings are deliberately *not* assumed to be Vietnamese stocks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from typing import Iterable, Mapping

import requests


@dataclass(frozen=True)
class VNInstrument:
    symbol: str
    exchange: str
    name: str = ""
    asset_type: str = "stock"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_SEED = (
    VNInstrument("BSR", "HOSE", "Lọc hóa dầu Bình Sơn"),
    VNInstrument("PLX", "HOSE", "Petrolimex"),
    VNInstrument("FPT", "HOSE", "Tập đoàn FPT"),
    VNInstrument("VNM", "HOSE", "Vinamilk"),
    VNInstrument("HPG", "HOSE", "Tập đoàn Hòa Phát"),
    VNInstrument("SSI", "HOSE", "Chứng khoán SSI"),
    VNInstrument("VCB", "HOSE", "Vietcombank"),
    VNInstrument("MBB", "HOSE", "MB Bank"),
    VNInstrument("TCB", "HOSE", "Techcombank"),
    VNInstrument("MWG", "HOSE", "Thế Giới Di Động"),
    VNInstrument("VIC", "HOSE", "Vingroup"),
    VNInstrument("VHM", "HOSE", "Vinhomes"),
    VNInstrument("DGC", "HOSE", "Hóa chất Đức Giang"),
    VNInstrument("PVD", "HOSE", "PV Drilling"),
    VNInstrument("PVS", "HNX", "PTSC"),
    VNInstrument("STB", "HOSE", "Sacombank"),
    VNInstrument("GMD", "HOSE", "Gemadept"),
    VNInstrument("ACV", "UPCOM", "Cảng hàng không Việt Nam"),
    VNInstrument("VGI", "UPCOM", "Viettel Global"),
    VNInstrument("VNINDEX", "HOSE", "VN-Index", "index"),
    VNInstrument("HNXINDEX", "HNX", "HNX-Index", "index"),
    VNInstrument("UPCOMINDEX", "UPCOM", "UPCoM-Index", "index"),
)
_instruments = {item.symbol: item for item in _SEED}
_lock = RLock()


def normalize_vn_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    return value[:-3] if value.endswith(".VN") else value


def register_instruments(records: Iterable[Mapping[str, object]]) -> None:
    """Merge vendor-provided identity rows into the process registry."""
    with _lock:
        for record in records:
            symbol = normalize_vn_symbol(str(record.get("symbol") or record.get("ticker") or ""))
            exchange = str(record.get("exchange") or record.get("market") or "").strip().upper()
            if exchange == "UPCOM":
                exchange = "UPCOM"
            if symbol and exchange in {"HOSE", "HNX", "UPCOM"}:
                _instruments[symbol] = VNInstrument(
                    symbol=symbol,
                    exchange=exchange,
                    name=str(record.get("name") or record.get("company_name") or "").strip(),
                    asset_type=str(record.get("asset_type") or "stock").strip().lower(),
                )


def get_vn_instrument(symbol: str) -> VNInstrument | None:
    return _instruments.get(normalize_vn_symbol(symbol))


def resolve_vn_instrument(symbol: str, timeout: float = 5) -> VNInstrument | None:
    """Resolve an unknown bare symbol against Vietcap and cache its exchange.

    This explicit resolver is used at input boundaries.  ``is_vn_stock`` stays
    network-free, preventing global tickers from causing surprise HTTP calls.
    """
    normalized = normalize_vn_symbol(symbol)
    known = get_vn_instrument(normalized)
    if known:
        return known
    if len(normalized) != 3 or not normalized.isalpha():
        return None
    response = requests.get(
        f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/details?ticker={normalized}",
        headers={"User-Agent": "TradingAgentsVN/0.7", "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json().get("data") or {}
    exchange = str(data.get("exchange") or data.get("comGroupCode") or "").upper()
    returned = normalize_vn_symbol(str(data.get("ticker") or data.get("organCode") or normalized))
    if exchange not in {"HOSE", "HNX", "UPCOM"} or returned != normalized:
        return None
    register_instruments([{
        "symbol": normalized,
        "exchange": exchange,
        "name": data.get("enOrganName") or data.get("organName") or data.get("organCode"),
    }])
    return get_vn_instrument(normalized)


def is_known_vn_symbol(symbol: str) -> bool:
    return get_vn_instrument(symbol) is not None


def all_instruments() -> list[VNInstrument]:
    return sorted(_instruments.values(), key=lambda item: item.symbol)
