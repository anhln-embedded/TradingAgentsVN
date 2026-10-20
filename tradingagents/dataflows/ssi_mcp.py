"""SSI MCP market-data client used as the primary Vietnam OHLCV source.

The remote server uses MCP Streamable HTTP and OAuth.  This module deliberately
accepts only a volatile access token from ``SSI_MCP_ACCESS_TOKEN``; it never
persists credentials.  Callers are expected to fall back to another vendor when
the token is absent, expired, or the MCP service is unavailable.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import pandas as pd
import requests

SSI_MCP_URL = "https://mcp.ssi.com.vn/mcp"
SSI_MCP_TOOL = "marketdata_list_stock_daily_data"
SSI_MCP_PROTOCOL_VERSION = "2025-03-26"


class SSIMCPError(RuntimeError):
    """SSI MCP could not provide usable market data."""


def _json_from_response(response: requests.Response) -> dict[str, Any]:
    """Read either a normal JSON response or MCP's SSE response format."""
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        payload = response.json()
        if not isinstance(payload, dict):
            raise SSIMCPError("SSI MCP returned a non-object response")
        return payload

    for line in reversed(response.text.splitlines()):
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            if isinstance(payload, dict):
                return payload
    raise SSIMCPError("SSI MCP returned an empty event stream")


def _post(
    session: requests.Session,
    token: str,
    payload: dict[str, Any],
    session_id: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": SSI_MCP_PROTOCOL_VERSION,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    response = session.post(SSI_MCP_URL, headers=headers, json=payload, timeout=15)
    return _json_from_response(response), response.headers.get("Mcp-Session-Id") or session_id


def _call_tool(token: str, arguments: dict[str, Any]) -> Any:
    """Initialize one short-lived MCP session and invoke the SSI daily-price tool."""
    with requests.Session() as session:
        initialized, session_id = _post(
            session,
            token,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": SSI_MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "TradingAgentsVN", "version": "0.7.0"},
                },
            },
        )
        if initialized.get("error"):
            raise SSIMCPError(f"SSI MCP initialize failed: {initialized['error']}")

        # Streamable HTTP accepts the initialized notification without a response.
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": SSI_MCP_PROTOCOL_VERSION,
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        notice = session.post(
            SSI_MCP_URL,
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=15,
        )
        notice.raise_for_status()

        called, _ = _post(
            session,
            token,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": SSI_MCP_TOOL, "arguments": arguments},
            },
            session_id,
        )
        if called.get("error"):
            raise SSIMCPError(f"SSI MCP tool failed: {called['error']}")
        result = called.get("result", {})
        if result.get("isError"):
            raise SSIMCPError(f"SSI MCP tool returned an error: {result.get('content')}")
        return result


def _decode_content(value: Any) -> Any:
    """Decode JSON embedded in MCP text content, preserving ordinary objects."""
    if isinstance(value, dict) and isinstance(value.get("content"), list):
        decoded = []
        for item in value["content"]:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                try:
                    decoded.append(json.loads(text))
                except (TypeError, json.JSONDecodeError):
                    decoded.append(text)
            else:
                decoded.append(item)
        return decoded
    return value


def _find_records(value: Any) -> list[dict[str, Any]]:
    """Find the first list of row objects inside SSI's response envelope."""
    value = _decode_content(value)
    if isinstance(value, list):
        if value and all(isinstance(row, dict) for row in value):
            # MCP content blocks are envelopes; search them before treating them as rows.
            envelope_keys = {"data", "items", "records", "rows", "result", "content"}
            if all("type" in row for row in value) or any(envelope_keys & set(row) for row in value):
                for row in value:
                    found = _find_records(row)
                    if found:
                        return found
            else:
                return value
        for item in value:
            found = _find_records(item)
            if found:
                return found
    elif isinstance(value, dict):
        for key in ("data", "items", "records", "rows", "result", "content"):
            if key in value:
                found = _find_records(value[key])
                if found:
                    return found
    elif isinstance(value, str):
        try:
            return _find_records(json.loads(value))
        except json.JSONDecodeError:
            return []
    return []


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _value(row: dict[str, Any], *aliases: str) -> Any:
    normalized = {_key(str(key)): value for key, value in row.items()}
    for alias in aliases:
        if _key(alias) in normalized:
            return normalized[_key(alias)]
    return None


def _range_for(count_back: int) -> str:
    # count_back is a trading-row target, so allow calendar-day headroom.
    calendar_days = max(int(count_back * 1.7), count_back)
    if calendar_days <= 1:
        return "1d"
    if calendar_days <= 90:
        return "3m"
    if calendar_days <= 180:
        return "6m"
    if calendar_days <= 365:
        return "1y"
    return "max_range"


def _rows_to_ohlcv(records: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        record_symbol = _value(record, "symbol", "stockSymbol", "ticker")
        if record_symbol and str(record_symbol).upper() != symbol.upper():
            continue
        rows.append({
            "Date": _value(record, "tradingDate", "date", "tradeDate", "trading_date"),
            "Open": _value(record, "openPrice", "open", "open_price"),
            "High": _value(record, "highestPrice", "highPrice", "high", "highest_price"),
            "Low": _value(record, "lowestPrice", "lowPrice", "low", "lowest_price"),
            "Close": _value(record, "closePrice", "close", "close_price", "matchPrice"),
            "Volume": _value(
                record, "totalMatchVolume", "totalMatchVol", "matchVolume", "volume",
                "totalVolume", "total_match_vol",
            ),
        })
    frame = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    if frame.empty:
        raise SSIMCPError(f"SSI MCP returned no daily OHLCV rows for {symbol}")
    frame["Date"] = pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce").dt.normalize()
    for column in ("Open", "High", "Low", "Close", "Volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Date", "Open", "High", "Low", "Close"])
    if frame.empty:
        raise SSIMCPError(f"SSI MCP rows for {symbol} did not contain recognizable OHLCV fields")
    frame["Volume"] = frame["Volume"].fillna(0).astype("int64")
    return frame.drop_duplicates(subset=["Date"], keep="last").sort_values("Date").reset_index(drop=True)


def fetch_ssi_ohlcv(symbol: str, count_back: int = 350, end_date: str | None = None) -> pd.DataFrame:
    """Fetch daily Vietnamese-stock OHLCV from SSI MCP.

    SSI currently limits time-based tools to the most recent 730 days.  The
    adapter requests the smallest supported range that can cover ``count_back``
    and trims the normalized result to the requested row count.
    """
    # Delayed import avoids a module cycle: the OAuth client imports the MCP
    # endpoint and error type from this module.
    from .ssi_oauth import ssi_oauth

    token = ssi_oauth.access_token()
    if count_back < 1:
        raise ValueError("count_back must be positive")

    to_date = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()
    if (datetime.now().date() - to_date.date()).days > 730:
        raise SSIMCPError("SSI MCP supports only the most recent 730 days")
    arguments = {
        "symbol": symbol.upper(),
        "range": _range_for(count_back),
        "toDate": to_date.strftime("%d/%m/%Y"),
        "pageSize": 1000,
    }
    records: list[dict[str, Any]] = []
    for page in range(1, 11):
        page_records = _find_records(_call_tool(token, {**arguments, "pageIndex": page}))
        if not page_records:
            break
        records.extend(page_records)
        if len(page_records) < arguments["pageSize"] or len(records) >= count_back:
            break
    return _rows_to_ohlcv(records, symbol).tail(count_back).reset_index(drop=True)
