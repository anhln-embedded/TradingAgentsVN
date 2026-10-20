"""Deterministic order rules for Vietnamese cash equities."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .registry import get_vn_instrument

LIMIT_PCT = {"HOSE": 7.0, "HNX": 10.0, "UPCOM": 15.0}
BOARD_LOT = 100


@dataclass(frozen=True)
class PriceBand:
    exchange: str
    reference: float
    floor: float
    ceiling: float
    limit_pct: float


@dataclass(frozen=True)
class OrderValidation:
    valid: bool
    errors: tuple[str, ...]
    normalized_price: float | None
    exchange: str | None


def tick_size(price: float, exchange: str) -> float:
    """Return the regular-board VND tick size for a cash equity."""
    exchange = exchange.upper()
    if exchange == "HOSE":
        if price < 10_000:
            return 10.0
        if price < 50_000:
            return 50.0
        return 100.0
    return 100.0


def round_to_tick(price: float, exchange: str, direction: str = "nearest") -> float:
    step = tick_size(price, exchange)
    scaled = price / step
    if direction == "up":
        return math.ceil(scaled - 1e-12) * step
    if direction == "down":
        return math.floor(scaled + 1e-12) * step
    return round(scaled) * step


def calculate_price_band(reference: float, exchange: str) -> PriceBand:
    if reference <= 0:
        raise ValueError("reference price must be positive")
    exchange = exchange.upper()
    if exchange not in LIMIT_PCT:
        raise ValueError(f"unsupported Vietnamese exchange: {exchange}")
    pct = LIMIT_PCT[exchange]
    # Ceiling rounds upward and floor downward to a valid tick.
    ceiling = round_to_tick(reference * (1 + pct / 100), exchange, "up")
    floor = round_to_tick(reference * (1 - pct / 100), exchange, "down")
    return PriceBand(exchange, float(reference), floor, ceiling, pct)


def validate_order(
    symbol: str,
    side: str,
    price: float | None,
    quantity: int | None = None,
    reference: float | None = None,
    holdings: int | None = None,
) -> OrderValidation:
    instrument = get_vn_instrument(symbol)
    errors: list[str] = []
    if instrument is None:
        return OrderValidation(False, ("Không xác định được sàn giao dịch của mã.",), None, None)
    exchange = instrument.exchange
    normalized = None
    if price is not None:
        normalized = round_to_tick(float(price), exchange)
        if abs(normalized - float(price)) > 1e-9:
            errors.append(f"Giá {price:g} không đúng bước giá {tick_size(float(price), exchange):g} VND.")
        if reference is not None:
            band = calculate_price_band(float(reference), exchange)
            if float(price) < band.floor or float(price) > band.ceiling:
                errors.append(f"Giá nằm ngoài biên {band.floor:g}–{band.ceiling:g} VND của {exchange}.")
    if quantity is not None and (quantity <= 0 or quantity % BOARD_LOT != 0):
        errors.append(f"Khối lượng lô chẵn phải là bội số của {BOARD_LOT}.")
    if side.upper() == "SELL" and holdings is not None and quantity is not None and quantity > holdings:
        errors.append("Khối lượng bán vượt quá vị thế hiện có; không hỗ trợ bán khống cổ phiếu cơ sở.")
    return OrderValidation(not errors, tuple(errors), normalized, exchange)
