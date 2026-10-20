"""API Routes for TradingAgentsVN Web Dashboard.

Provides endpoints for:
- Market data (OHLCV for TradingView Candlestick charts)
- Vietnamese Tickers catalog
- Past analysis reports and history
- Real-time SSE streaming for running analyses
"""

import logging
import time
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException, Query, Request

from tradingagents.dataflows.vn_stock import clean_vn_symbol, fetch_vn_ohlcv
from tradingagents.markets.vietnam.calendar import market_session
from tradingagents.markets.vietnam.registry import get_vn_instrument
from tradingagents.markets.vietnam.rules import calculate_price_band
from vnapp.settings import git_updates_enabled
from vnapp.accounts import require_admin
from vnapp.database import jobs, system_lock, transaction
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_QUOTE_CACHE = {}


def get_live_quote(clean_sym: str) -> dict:
    """Fetch live stock quote from SSI iBoard API with VPS and OHLCV fallbacks."""
    now = time.time()
    cached = _QUOTE_CACHE.get(clean_sym)
    if cached and (now - cached["_time"] < 2.5):
        return {k: v for k, v in cached.items() if k != "_time"}

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    instrument = get_vn_instrument(clean_sym)
    exchange = instrument.exchange if instrument else None

    def band_values(reference: float) -> tuple[float | None, float | None]:
        if not exchange or not reference:
            return None, None
        band = calculate_price_band(reference, exchange)
        return band.ceiling, band.floor

    # 1. Primary: SSI iBoard real-time query
    try:
        r = requests.get(f"https://iboard-query.ssi.com.vn/stock/{clean_sym}", timeout=3, headers=headers)
        if r.status_code == 200:
            d = r.json().get("data", {})
            price = d.get("matchedPrice") or d.get("priorClosePrice") or d.get("refPrice")
            ref = d.get("refPrice") or price
            if price:
                chg = d.get("priceChange") if d.get("priceChange") is not None else (price - ref)
                pct = d.get("priceChangePercent") if d.get("priceChangePercent") is not None else (round((price - ref) / ref * 100, 2) if ref else 0.0)
                ceiling, floor = band_values(float(ref))
                res = {
                    "symbol": clean_sym,
                    "price": float(price),
                    "ref_price": float(ref),
                    "change": float(chg),
                    "change_percent": float(pct),
                    "open": float(d.get("openPrice") or ref),
                    "high": float(d.get("highest") or price),
                    "low": float(d.get("lowest") or price),
                    "ceiling": float(d["ceiling"]) if d.get("ceiling") is not None else ceiling,
                    "floor": float(d["floor"]) if d.get("floor") is not None else floor,
                    "volume": int(d.get("nmTotalTradedQty") or d.get("totalVolume") or 0),
                    "source": "SSI",
                    "exchange": exchange,
                    "timestamp": now,
                }
                _QUOTE_CACHE[clean_sym] = {**res, "_time": now}
                return res
    except Exception as e:
        logger.debug(f"SSI quote error: {e}")

    # 2. Secondary: VPS Datafeed
    try:
        r = requests.get(f"https://bgapidatafeed.vps.com.vn/getliststockdata/{clean_sym}", timeout=3, headers=headers)
        if r.status_code == 200:
            items = r.json()
            if isinstance(items, list) and items:
                it = items[0]
                price = float(it.get("lastPrice") or it.get("r") or 0) * 1000
                ref = float(it.get("r") or (price / 1000)) * 1000
                chg = float(it.get("ot") or 0) * 1000
                pct = round((price - ref) / ref * 100, 2) if ref else 0.0
                ceiling, floor = band_values(ref)
                res = {
                    "symbol": clean_sym,
                    "price": price,
                    "ref_price": ref,
                    "change": chg,
                    "change_percent": pct,
                    "open": float(it.get("open") or it.get("r") or 0) * 1000,
                    "high": float(it.get("h") or (price / 1000)) * 1000,
                    "low": float(it.get("l") or (price / 1000)) * 1000,
                    "ceiling": float(it["c"]) * 1000 if it.get("c") is not None else ceiling,
                    "floor": float(it["f"]) * 1000 if it.get("f") is not None else floor,
                    "volume": int(it.get("lot") or 0),
                    "source": "VPS",
                    "exchange": exchange,
                    "timestamp": now,
                }
                _QUOTE_CACHE[clean_sym] = {**res, "_time": now}
                return res
    except Exception as e:
        logger.debug(f"VPS quote error: {e}")

    # 3. Fallback: Local/Cached OHLCV
    try:
        df = fetch_vn_ohlcv(clean_sym, count_back=5)
        if not df.empty:
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else last
            price = float(last["Close"])
            ref = float(prev["Close"])
            chg = price - ref
            pct = round(chg / ref * 100, 2) if ref else 0.0
            ceiling, floor = band_values(ref)
            res = {
                "symbol": clean_sym,
                "price": price,
                "ref_price": ref,
                "change": chg,
                "change_percent": pct,
                "open": float(last["Open"]),
                "high": float(last["High"]),
                "low": float(last["Low"]),
                "ceiling": ceiling,
                "floor": floor,
                "volume": int(last["Volume"]),
                "source": df.attrs.get("source", "OHLCV"),
                "exchange": exchange,
                "timestamp": now,
            }
            _QUOTE_CACHE[clean_sym] = {**res, "_time": now}
            return res
    except Exception as e:
        logger.error(f"Quote fallback error: {e}")

    raise HTTPException(status_code=404, detail=f"Không thể lấy giá trực tuyến cho mã {clean_sym}")


@router.get("/quote/{ticker}")
def get_quote(ticker: str, request: Request):
    """Return real-time quote for a Vietnamese stock ticker."""
    clean_sym = clean_vn_symbol(ticker)
    return get_live_quote(clean_sym)


@router.get("/market-session/{ticker}")
def get_market_session(ticker: str):
    instrument = get_vn_instrument(clean_vn_symbol(ticker))
    if instrument is None:
        return market_session("UNKNOWN")
    return market_session(instrument.exchange)


# Curated list of prominent Vietnamese stocks for quick-selection
POPULAR_VN_TICKERS = [
    {"symbol": "BSR", "name": "Lọc hóa dầu Bình Sơn", "exchange": "HOSE", "sector": "Năng lượng / Dầu khí"},
    {"symbol": "PLX", "name": "Tập đoàn Xăng dầu Việt Nam (Petrolimex)", "exchange": "HOSE", "sector": "Năng lượng / Phân phối"},
    {"symbol": "FPT", "name": "Tập đoàn FPT", "exchange": "HOSE", "sector": "Công nghệ thông tin"},
    {"symbol": "VNM", "name": "Vinamilk", "exchange": "HOSE", "sector": "Thực phẩm & Đồ uống"},
    {"symbol": "HPG", "name": "Tập đoàn Hòa Phát", "exchange": "HOSE", "sector": "Thép & Vật liệu"},
    {"symbol": "SSI", "name": "Chứng khoán SSI", "exchange": "HOSE", "sector": "Dịch vụ tài chính"},
    {"symbol": "VCB", "name": "Vietcombank", "exchange": "HOSE", "sector": "Ngân hàng"},
    {"symbol": "MBB", "name": "Ngân hàng TMCP Quân Đội", "exchange": "HOSE", "sector": "Ngân hàng"},
    {"symbol": "TCB", "name": "Techcombank", "exchange": "HOSE", "sector": "Ngân hàng"},
    {"symbol": "MWG", "name": "Thế Giới Di Động", "exchange": "HOSE", "sector": "Bán lẻ"},
    {"symbol": "VIC", "name": "Tập đoàn Vingroup", "exchange": "HOSE", "sector": "Bất động sản / Công nghiệp"},
    {"symbol": "VHM", "name": "Vinhomes", "exchange": "HOSE", "sector": "Bất động sản"},
    {"symbol": "DGC", "name": "Hóa chất Đức Giang", "exchange": "HOSE", "sector": "Hóa chất"},
    {"symbol": "PVD", "name": "Khoan và Dịch vụ Khoan Dầu khí (PV Drilling)", "exchange": "HOSE", "sector": "Dịch vụ Dầu khí"},
    {"symbol": "PVS", "name": "Dịch vụ Kỹ thuật Dầu khí Việt Nam (PTSC)", "exchange": "HNX", "sector": "Dịch vụ Dầu khí"},
    {"symbol": "STB", "name": "Sacombank", "exchange": "HOSE", "sector": "Ngân hàng"},
    {"symbol": "GMD", "name": "Gemadept", "exchange": "HOSE", "sector": "Cảng biển & Logistics"},
]


@router.get("/health")
def health_check():
    return {"status": "ok", "app": "TradingAgentsVN Web API"}


@router.get("/tickers")
def get_tickers():
    return {"tickers": POPULAR_VN_TICKERS}


@router.get("/market-data/{ticker}")
def get_market_data(
    ticker: str,
    request: Request,
    count: int = Query(150, ge=1, le=700),
    end_date: Optional[str] = None,
    timeframe: str = Query("1D", description="Khung thời gian nến: 1D (Ngày), 1W (Tuần), 1M (Tháng)"),
):
    """Return OHLCV candles formatted for TradingView Lightweight Charts (supports 1D, 1W, 1M)."""
    request.app.state.accounts.throttle("market:" + request.state.user["id"], limit=60, seconds=60)
    import pandas as pd
    clean_sym = clean_vn_symbol(ticker)
    tf = timeframe.upper().strip()

    fetch_count = count
    if tf in ("1W", "W", "WEEK"):
        fetch_count = max(count * 5, 450)
    elif tf in ("1M", "M", "MONTH"):
        fetch_count = max(count * 22, 700)

    try:
        df = fetch_vn_ohlcv(clean_sym, count_back=fetch_count, end_date=end_date)
        data_source = df.attrs.get("source", "unknown")
        if df.empty:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy dữ liệu giá cho {clean_sym}")

        # Explicitly filter up to end_date if specified
        if end_date:
            end_dt = pd.to_datetime(end_date).normalize()
            df["Date"] = pd.to_datetime(df["Date"]).dt.normalize()
            df = df[df["Date"] <= end_dt]
            if df.empty:
                raise HTTPException(status_code=404, detail=f"Không có dữ liệu giá cho {clean_sym} trước hoặc vào ngày {end_date}")

        # Resample if timeframe is Weekly or Monthly
        if tf in ("1W", "W", "WEEK"):
            df_res = df.copy()
            df_res["Date"] = pd.to_datetime(df_res["Date"])
            df_res.set_index("Date", inplace=True)
            df = df_res.resample("W-FRI").agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }).dropna().reset_index()
            if count:
                df = df.tail(count).reset_index(drop=True)
        elif tf in ("1M", "M", "MONTH"):
            df_res = df.copy()
            df_res["Date"] = pd.to_datetime(df_res["Date"])
            df_res.set_index("Date", inplace=True)
            df = df_res.resample("ME").agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }).dropna().reset_index()
            if count:
                df = df.tail(count).reset_index(drop=True)

        candles = []
        for _, row in df.iterrows():
            date_str = row["Date"].strftime("%Y-%m-%d")
            candles.append({
                "time": date_str,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            })
        return {
            "symbol": clean_sym,
            "exchange": (get_vn_instrument(clean_sym).exchange
                         if get_vn_instrument(clean_sym) else None),
            "source": data_source,
            "currency": "VND",
            "timeframe": tf,
            "count": len(candles),
            "candles": candles,
            "latest_price": candles[-1]["close"] if candles else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Error fetching candles for {clean_sym} ({tf}): {e}")
        raise HTTPException(status_code=500, detail=str(e))



# =========================================================================
# AI Provider & Configuration Endpoints
# =========================================================================

from pydantic import BaseModel, Field, ConfigDict
from web.api.config_helper import get_current_ai_config, update_ai_config
from web.api.git_helper import check_upstream_status, pull_upstream_code


class ConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=50)
    api_key: Optional[str] = Field(None, max_length=4096)
    deep_think_llm: Optional[str] = Field(None, max_length=200)
    quick_think_llm: Optional[str] = Field(None, max_length=200)
    backend_url: Optional[str] = Field(None, max_length=2048)
    llm_timeout: Optional[float] = Field(None, gt=0, le=600, allow_inf_nan=False)
    llm_max_retries: Optional[int] = Field(None, ge=0, le=10, strict=True)


@router.get("/config")
def get_config(request: Request):
    require_admin(request.state.user)
    """Retrieve current LLM provider, models, and masked API key."""
    return get_current_ai_config()


@router.post("/config")
def update_config(payload: ConfigUpdateRequest, request: Request):
    require_admin(request.state.user)
    from web.api.config_helper import PROVIDER_METADATA
    if payload.provider not in PROVIDER_METADATA:
        raise HTTPException(422, "Nhà cung cấp không hợp lệ.")
    if payload.backend_url:
        from urllib.parse import urlparse
        parsed = urlparse(payload.backend_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HTTPException(422, "Backend URL phải là HTTP/HTTPS.")
    with transaction(request.app.state.jobs.engine) as connection:
        connection.execute(select(system_lock).where(system_lock.c.id == "server").with_for_update()).first()
        if connection.execute(select(jobs.c.id).where(jobs.c.status.in_(("queued", "running"))).limit(1)).first():
            raise HTTPException(409, "Chờ các phiên phân tích kết thúc trước khi đổi cấu hình.")
        try:
            updated = update_ai_config(**payload.model_dump())
            request.app.state.accounts._audit(connection, request.state.user, "server.config.update", "server",
                {"provider": payload.provider, "key_changed": bool(payload.api_key),
                 "deep_think_llm": payload.deep_think_llm, "quick_think_llm": payload.quick_think_llm,
                 "llm_timeout": payload.llm_timeout, "llm_max_retries": payload.llm_max_retries})
            return {"success": True, "message": "Đã cập nhật cấu hình.", "config": updated}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Server configuration update failed")
            raise HTTPException(500, "Không thể lưu cấu hình server.") from None


# =========================================================================
# Git Upstream Endpoints
# =========================================================================

@router.get("/git/status")
def get_git_status(request: Request):
    require_admin(request.state.user)
    """Fetch and return upstream update status."""
    if not git_updates_enabled():
        return {"disabled": True, "message": "Cập nhật bằng image đã kiểm thử; Git bị tắt trên server."}
    return check_upstream_status()


@router.post("/git/pull")
def pull_upstream(request: Request):
    require_admin(request.state.user)
    """Prepare an upstream candidate outside the running checkout."""
    if not git_updates_enabled():
        raise HTTPException(status_code=403, detail="Không merge mã nguồn trên server production")
    result = pull_upstream_code()
    request.app.state.accounts.record_admin_action(request.state.user, "server.upstream.prepare", {"success": result.get("success", False)})
    return result
