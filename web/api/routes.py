"""API Routes for TradingAgentsVN Web Dashboard.

Provides endpoints for:
- Market data (OHLCV for TradingView Candlestick charts)
- Vietnamese Tickers catalog
- Past analysis reports and history
- Real-time SSE streaming for running analyses
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from tradingagents.dataflows.vn_stock import clean_vn_symbol, fetch_vn_ohlcv
from web.api.stream import stream_analysis_events
from vnapp.settings import reports_dir, git_updates_enabled
from vnapp.storage import report_folder
from vnapp.runtime import RUN_LOCK

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

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
    return {"status": "ok", "app": "TradingAgentsVN Web API", "time": datetime.now().isoformat(), "analysis_running": RUN_LOCK.locked()}


@router.get("/tickers")
def get_tickers():
    return {"tickers": POPULAR_VN_TICKERS}


@router.get("/market-data/{ticker}")
def get_market_data(
    ticker: str,
    count: int = 150,
    end_date: Optional[str] = None,
    timeframe: str = Query("1D", description="Khung thời gian nến: 1D (Ngày), 1W (Tuần), 1M (Tháng)"),
):
    """Return OHLCV candles formatted for TradingView Lightweight Charts (supports 1D, 1W, 1M)."""
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
            "timeframe": tf,
            "count": len(candles),
            "candles": candles,
            "latest_price": candles[-1]["close"] if candles else None,
        }
    except Exception as e:
        logger.warning(f"Error fetching candles for {clean_sym} ({tf}): {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/history")
def get_report_history():
    """Scan and list past analysis reports from results directory."""
    results_dir = reports_dir()
    if not results_dir.exists():
        return {"reports": []}

    reports = []

    # 1. Check custom analysis folders (e.g. bsr_analysis, plx_analysis)
    for folder in results_dir.glob("*_analysis"):
        state_file = folder / "final_state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    ticker = data.get("company_of_interest", folder.name.replace("_analysis", "").upper())
                    trade_date = data.get("trade_date", "")
                    signal = "UNDERWEIGHT" if "UNDERWEIGHT" in str(data) else "BUY"
                    reports.append({
                        "id": folder.name,
                        "ticker": ticker,
                        "trade_date": trade_date,
                        "signal": signal,
                        "source": "analysis_dir",
                        "saved_at": state_file.stat().st_mtime,
                        "folder": str(folder),
                    })
            except Exception as e:
                logger.warning(f"Failed to parse {state_file}: {e}")

    # 2. Check results/reports/
    reports_dir = results_dir / "reports"
    if reports_dir.exists():
        for sub in sorted(reports_dir.iterdir(), reverse=True):
            if sub.is_dir() and (sub / "complete_report.md").exists():
                name_parts = sub.name.split("_")
                ticker = name_parts[0] if name_parts else sub.name
                reports.append({
                    "id": f"report_{sub.name}",
                    "ticker": ticker,
                    "trade_date": "_".join(name_parts[1:]) if len(name_parts) > 1 else "",
                    "signal": "COMPLETED",
                    "source": "reports_dir",
                    "saved_at": (sub / "complete_report.md").stat().st_mtime,
                    "folder": str(sub),
                })

    reports.sort(key=lambda item: item["saved_at"], reverse=True)
    return {"reports": reports}


@router.get("/history/{report_id}")
def get_report_detail(report_id: str):
    """Retrieve full content of a historical report."""
    results_dir = reports_dir()

    try:
        target_folder = report_folder(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Đường dẫn báo cáo không hợp lệ")

    if not target_folder.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy báo cáo yêu cầu")

    # Try loading final_state.json
    state_file = target_folder / "final_state.json"
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                import ast
                for k in ["investment_debate_state", "risk_debate_state"]:
                    val = data.get(k)
                    if isinstance(val, str):
                        try:
                            data[k] = ast.literal_eval(val)
                        except Exception:
                            try:
                                data[k] = json.loads(val)
                            except Exception:
                                pass
                return {"id": report_id, "type": "json_state", "data": data}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi đọc file final_state.json: {e}")

    # Try loading complete_report.md
    report_file = target_folder / "complete_report.md"
    if report_file.exists():
        def read_section(relative_path):
            path = target_folder / relative_path
            return path.read_text(encoding="utf-8") if path.exists() else ""

        content = report_file.read_text(encoding="utf-8")
        import re
        ticker_match = re.search(r"^# Trading Analysis Report: (\S+)", content)
        decision = read_section("5_portfolio/decision.md")
        if not decision:
            match = re.search(r"^## V\. Portfolio Manager Decision\s*\n(?:### Portfolio Manager\s*\n)?([\s\S]*)", content, re.MULTILINE)
            decision = match.group(1).strip() if match else ""
        data = {
            "company_of_interest": ticker_match.group(1) if ticker_match else target_folder.name.split("_")[0],
            "trade_date": "",
            "final_trade_decision": decision,
            "trader_investment_plan": read_section("3_trading/trader.md"),
            "investment_debate_state": {
                "bull_history": read_section("2_research/bull.md"),
                "bear_history": read_section("2_research/bear.md"),
                "judge_decision": read_section("2_research/manager.md"),
            },
            "risk_debate_state": {
                "aggressive_history": read_section("4_risk/aggressive.md"),
                "conservative_history": read_section("4_risk/conservative.md"),
                "neutral_history": read_section("4_risk/neutral.md"),
                "judge_decision": decision,
            },
        }
        for section in ("market", "sentiment", "news", "fundamentals"):
            data[f"{section}_report"] = read_section(f"1_analysts/{section}.md")
        return {"id": report_id, "type": "json_state", "data": data}

    raise HTTPException(status_code=404, detail="Không tìm thấy nội dung báo cáo trong thư mục")


@router.get("/analyze/stream")
async def analyze_stream(
    ticker: str = Query(..., description="Mã chứng khoán (VD: VNM, BSR, FPT)"),
    trade_date: Optional[str] = Query(None, description="Ngày giao dịch (YYYY-MM-DD)"),
    analysts: str = Query("market,social,news,fundamentals", description="Danh sách chuyên gia phân tích"),
):
    """SSE endpoint streaming live agent thought process, debates, and final reports."""
    clean_sym = clean_vn_symbol(ticker)
    date_str = trade_date or datetime.now().strftime("%Y-%m-%d")

    # Map aliases like 'sentiment' -> 'social'
    analyst_key_map = {
        "sentiment": "social",
        "social": "social",
        "market": "market",
        "news": "news",
        "fundamentals": "fundamentals",
    }
    raw_list = [a.strip().lower() for a in analysts.split(",") if a.strip()]
    analyst_list = []
    for a in raw_list:
        mapped = analyst_key_map.get(a, a)
        if mapped not in analyst_list:
            analyst_list.append(mapped)

    if not analyst_list:
        analyst_list = ["market", "social", "news", "fundamentals"]

    try:
        events = stream_analysis_events(clean_sym, date_str, tuple(analyst_list))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def event_generator():
        async for event_item in events:
            yield {
                "event": event_item.get("event", "message"),
                "data": json.dumps(event_item.get("data", {}), ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())


# =========================================================================
# AI Provider & Configuration Endpoints
# =========================================================================

from pydantic import BaseModel
from web.api.config_helper import get_current_ai_config, update_ai_config
from web.api.git_helper import check_upstream_status, pull_upstream_code


class ConfigUpdateRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    deep_think_llm: Optional[str] = None
    quick_think_llm: Optional[str] = None
    backend_url: Optional[str] = None
    llm_timeout: Optional[float] = None
    llm_max_retries: Optional[int] = None


@router.get("/config")
def get_config():
    """Retrieve current LLM provider, models, and masked API key."""
    return get_current_ai_config()


@router.post("/config")
def update_config(payload: ConfigUpdateRequest):
    """Save new LLM provider, API key, and model settings to .env and runtime."""
    if RUN_LOCK.locked():
        raise HTTPException(status_code=409, detail="Chờ phiên phân tích hoàn tất trước khi đổi cấu hình")
    try:
        updated = update_ai_config(
            provider=payload.provider,
            api_key=payload.api_key,
            deep_think_llm=payload.deep_think_llm,
            quick_think_llm=payload.quick_think_llm,
            backend_url=payload.backend_url,
            llm_timeout=payload.llm_timeout,
            llm_max_retries=payload.llm_max_retries,
        )
        return {"success": True, "message": "Đã cập nhật cấu hình thành công!", "config": updated}
    except Exception as e:
        logger.exception("Error updating config: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# Git Upstream Endpoints
# =========================================================================

@router.get("/git/status")
def get_git_status():
    """Fetch and return upstream update status."""
    if not git_updates_enabled():
        return {"disabled": True, "message": "Cập nhật bằng image đã kiểm thử; Git bị tắt trên server."}
    return check_upstream_status()


@router.post("/git/pull")
def pull_upstream():
    """Prepare an upstream candidate outside the running checkout."""
    if not git_updates_enabled():
        raise HTTPException(status_code=403, detail="Không merge mã nguồn trên server production")
    if RUN_LOCK.locked():
        raise HTTPException(status_code=409, detail="Đang phân tích; chưa thể cập nhật")
    result = pull_upstream_code()
    return result
