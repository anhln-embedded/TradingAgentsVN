"""Vietnam exchange session status in the market's own timezone."""

from __future__ import annotations

from datetime import date, datetime, time
import os
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def configured_holidays() -> set[date]:
    """Read exchange closure dates supplied by operations as ISO dates."""
    values = os.environ.get("VN_MARKET_HOLIDAYS", "")
    holidays: set[date] = set()
    for value in values.split(","):
        value = value.strip()
        if value:
            try:
                holidays.add(date.fromisoformat(value))
            except ValueError:
                continue
    return holidays


def _local(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(VN_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=VN_TZ)
    return value.astimezone(VN_TZ)


def market_session(exchange: str, at: datetime | None = None, holidays: set[date] | None = None) -> dict:
    now = _local(at)
    exchange = exchange.upper()
    if exchange not in {"HOSE", "HNX", "UPCOM"}:
        return {"status": "unknown", "label": "Không xác định", "exchange": exchange, "time": now.isoformat()}
    closures = configured_holidays() if holidays is None else holidays
    if now.weekday() >= 5 or now.date() in closures:
        return {"status": "closed", "label": "Đóng cửa", "exchange": exchange, "time": now.isoformat()}
    current = now.time().replace(tzinfo=None)
    if current < time(9, 0):
        status, label = "closed", "Chưa mở cửa"
    elif exchange == "HOSE" and current < time(9, 15):
        status, label = "open", "ATO"
    elif current < time(11, 30):
        status, label = "open", "Phiên sáng"
    elif current < time(13, 0):
        status, label = "break", "Nghỉ trưa"
    elif current < time(14, 30):
        status, label = "open", "Phiên chiều"
    elif exchange in {"HOSE", "HNX"} and current < time(14, 45):
        status, label = "open", "ATC"
    elif current < time(15, 0):
        status, label = "agreement", "Thỏa thuận"
    else:
        status, label = "closed", "Đóng cửa"
    return {"status": status, "label": label, "exchange": exchange, "time": now.isoformat()}
