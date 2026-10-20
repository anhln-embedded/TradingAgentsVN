"""Vietnam Stock Market (TTCK VN) Dataflow Adapter.

Provides high-speed, direct market data, technical indicators via stockstats,
company fundamentals, and VAS-compliant financial statements for Vietnamese
equities (HOSE, HNX, UPCoM).

Bypasses noisy third-party wrappers with direct, sub-second REST endpoints
to ensure reliability and zero console spam.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Dict, List, Optional

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta
from requests.adapters import HTTPAdapter
from stockstats import wrap
from urllib3.util.retry import Retry

from tradingagents.markets.vietnam.registry import (
    get_vn_instrument,
    is_known_vn_symbol,
    normalize_vn_symbol,
    register_instruments,
)
from tradingagents.markets.vietnam.rules import LIMIT_PCT

from .errors import NoMarketDataError
from .ssi_mcp import fetch_ssi_ohlcv

logger = logging.getLogger(__name__)

_VN_EXCHANGE_PRICE_LIMITS = {
    "VNINDEX": {"exchange": "HOSE", "limit_pct": 7.0, "desc": "HOSE (Biên độ ±7%)"},
    "HOSE": {"exchange": "HOSE", "limit_pct": 7.0, "desc": "HOSE (Biên độ ±7%)"},
    "HNXIndex": {"exchange": "HNX", "limit_pct": 10.0, "desc": "HNX (Biên độ ±10%)"},
    "HNX": {"exchange": "HNX", "limit_pct": 10.0, "desc": "HNX (Biên độ ±10%)"},
    "UpcomIndex": {"exchange": "UPCoM", "limit_pct": 15.0, "desc": "UPCoM (Biên độ ±15%)"},
    "UPCOM": {"exchange": "UPCoM", "limit_pct": 15.0, "desc": "UPCoM (Biên độ ±15%)"},
}

_BEST_IND_PARAMS = {
    # Moving Averages
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. "
        "Usage: Identify trend direction and serve as dynamic support/resistance. "
        "Tips: It lags price; combine with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. "
        "Usage: Confirm overall market trend and identify golden/death cross setups. "
        "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. "
        "Usage: Capture quick shifts in momentum and potential entry points. "
        "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
    ),
    # MACD Related
    "macd": (
        "MACD: Computes momentum via differences of EMAs. "
        "Usage: Look for crossovers and divergence as signals of trend changes. "
        "Tips: Confirm with other indicators in low-volatility or sideways markets."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. "
        "Usage: Use crossovers with the MACD line to trigger trades. "
        "Tips: Should be part of a broader strategy to avoid false positives."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal. "
        "Usage: Visualize momentum strength and spot divergence early. "
        "Tips: Can be volatile; complement with additional filters in fast-moving markets."
    ),
    # Momentum Indicators
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. "
        "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
        "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
    ),
    # Volatility Indicators
    "boll": (
        "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
        "Usage: Acts as a dynamic benchmark for price movement. "
        "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
    ),
    "boll_ub": (
        "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
        "Usage: Signals potential overbought conditions and breakout zones. "
        "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
    ),
    "boll_lb": (
        "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
        "Usage: Indicates potential oversold conditions. "
        "Tips: Use additional analysis to avoid false reversal signals."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. "
        "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
        "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
    ),
    # Volume-Based Indicators
    "vwma": (
        "VWMA: A moving average weighted by volume. "
        "Usage: Confirm trends by integrating price action with volume data. "
        "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
    ),
    "mfi": (
        "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
        "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
        "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
    ),
}

# Reusable HTTP session with browser headers and retries for Vietnamese market endpoints
_HTTP_SESSION = requests.Session()
_HTTP_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
})
_retry_strategy = Retry(
    total=2,
    backoff_factor=0.2,
    status_forcelist=[429, 500, 502, 503, 504],
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retry_strategy)
_HTTP_SESSION.mount("https://", _adapter)
_HTTP_SESSION.mount("http://", _adapter)

_OHLCV_CACHE: Dict[str, pd.DataFrame] = {}


def _ensure_session_cookies():
    """Warm up session cookies against priceboard if not present."""
    if not _HTTP_SESSION.cookies:
        try:
            _HTTP_SESSION.get("https://trading.vietcap.com.vn/priceboard", timeout=5)
        except Exception:
            pass


def is_vn_stock(symbol: str) -> bool:
    """Return True only for an explicit ``.VN`` symbol or a registry member."""
    if not isinstance(symbol, str) or not symbol.strip():
        return False
    s = symbol.strip().upper()
    return s.endswith(".VN") or is_known_vn_symbol(s)


def clean_vn_symbol(symbol: str) -> str:
    """Normalize symbol to bare 3-letter ticker for Vietnam market (e.g. VNM.VN -> VNM)."""
    return normalize_vn_symbol(symbol)


def _normalize_ohlcv(df: pd.DataFrame, symbol: str, source: str) -> pd.DataFrame:
    """Validate the common OHLCV contract and attach non-column metadata."""
    required = {"Date", "Open", "High", "Low", "Close", "Volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"{source} thiếu cột OHLCV: {', '.join(sorted(missing))}")
    frame = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.normalize()
    for column in ("Open", "High", "Low", "Close", "Volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Date", "Open", "High", "Low", "Close"])
    valid = (
        (frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
        & (frame["Low"] <= frame[["Open", "Close"]].min(axis=1))
        & (frame["High"] >= frame[["Open", "Close"]].max(axis=1))
        & (frame["Low"] <= frame["High"])
    )
    frame = frame.loc[valid].drop_duplicates("Date", keep="last").sort_values("Date")
    if frame.empty:
        raise ValueError(f"{source} không trả về nến OHLCV hợp lệ cho {symbol}")
    frame["Volume"] = frame["Volume"].fillna(0).clip(lower=0).astype("int64")
    frame.attrs.update({"source": source.lower(), "symbol": symbol, "currency": "VND"})
    return frame.reset_index(drop=True)


def _fetch_vietcap_ohlcv(clean_sym: str, count_back: int, end_date: Optional[str]) -> pd.DataFrame:
    """Fetch OHLCV from Vietcap trading API."""
    url = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        to_ts = int(end_dt.timestamp()) + 86400
    else:
        to_ts = int(datetime.now().timestamp()) + 86400

    payload = {
        "timeFrame": "ONE_DAY",
        "symbols": [clean_sym],
        "to": to_ts,
        "countBack": max(count_back, 60),
    }
    r = _HTTP_SESSION.post(url, json=payload, timeout=6)
    if r.status_code != 200:
        raise ValueError(f"Vietcap API status {r.status_code}")
    data = r.json()
    if not data or not isinstance(data, list):
        raise ValueError(f"Không có dữ liệu trả về từ Vietcap cho mã {clean_sym}")
    item = data[0]
    raw_t = item.get("t", [])
    if not raw_t:
        raise ValueError(f"Không có dữ liệu nến từ Vietcap cho mã {clean_sym}")

    timestamps = pd.Series(raw_t).astype(int)
    dates = pd.to_datetime(timestamps, unit="s").dt.normalize()

    df = pd.DataFrame({
        "Date": dates,
        "Open": item.get("o", []),
        "High": item.get("h", []),
        "Low": item.get("l", []),
        "Close": item.get("c", []),
        "Volume": item.get("v", []),
    })
    return df.sort_values("Date").reset_index(drop=True)


def _fetch_dnse_ohlcv(clean_sym: str, count_back: int, end_date: Optional[str]) -> pd.DataFrame:
    """Fallback fetcher for OHLCV from DNSE Entrade chart API."""
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        to_ts = int(end_dt.timestamp()) + 86400
    else:
        to_ts = int(datetime.now().timestamp()) + 86400

    # Ensure enough span for count_back trading days (approx 2 calendar days per trading day)
    from_ts = to_ts - max(count_back, 60) * 86400 * 2

    url = f"https://services.entrade.com.vn/chart-api/v2/ohlcs/stock?from={from_ts}&to={to_ts}&symbol={clean_sym}&resolution=1D"
    r = _HTTP_SESSION.get(url, timeout=6)
    if r.status_code != 200:
        raise ValueError(f"DNSE API status {r.status_code}")
    data = r.json()
    raw_t = data.get("t", [])
    if not raw_t:
        raise ValueError(f"Không có dữ liệu nến từ DNSE cho mã {clean_sym}")

    timestamps = pd.Series(raw_t).astype(int)
    dates = pd.to_datetime(timestamps, unit="s").dt.normalize()

    # DNSE prices are scaled in thousands VND (e.g. 88.6 = 88,600 VND)
    df = pd.DataFrame({
        "Date": dates,
        "Open": [float(x) * 1000 for x in data.get("o", [])],
        "High": [float(x) * 1000 for x in data.get("h", [])],
        "Low": [float(x) * 1000 for x in data.get("l", [])],
        "Close": [float(x) * 1000 for x in data.get("c", [])],
        "Volume": [int(x) for x in data.get("v", [])],
    })
    df = df.sort_values("Date").tail(max(count_back, 60)).reset_index(drop=True)
    return df


def fetch_vn_ohlcv(symbol: str, count_back: int = 350, end_date: Optional[str] = None) -> pd.DataFrame:
    """Fetch VN OHLCV from SSI MCP, with Vietcap and DNSE fallbacks."""
    clean_sym = clean_vn_symbol(symbol)
    # Do not let a cached public fallback mask SSI immediately after OAuth connects.
    try:
        from .ssi_oauth import ssi_oauth
        oauth_partition = int(ssi_oauth.status()["connected"] or ssi_oauth.status()["debug_token"])
    except Exception:
        oauth_partition = 0
    cache_key = f"{clean_sym}:{count_back}:{end_date}:ssi={oauth_partition}"
    if cache_key in _OHLCV_CACHE:
        return _OHLCV_CACHE[cache_key].copy()

    # 1. SSI is the official primary source. Missing/expired OAuth credentials,
    # historical requests outside its 730-day window, and transient failures
    # all degrade to the existing public fallbacks.
    try:
        df = fetch_ssi_ohlcv(clean_sym, count_back, end_date)
        if not df.empty:
            df = _normalize_ohlcv(df, clean_sym, "SSI")
            _OHLCV_CACHE[cache_key] = df
            return df.copy()
    except Exception as e_ssi:
        logger.warning(
            "SSI MCP không khả dụng (%s), chuyển sang Vietcap cho %s...",
            e_ssi,
            clean_sym,
        )

    # 2. Try Vietcap fallback endpoint
    try:
        df = _fetch_vietcap_ohlcv(clean_sym, count_back, end_date)
        if not df.empty:
            df = _normalize_ohlcv(df, clean_sym, "Vietcap")
            _OHLCV_CACHE[cache_key] = df
            return df.copy()
    except Exception as e_vc:
        logger.warning(f"Vietcap API lỗi ({e_vc}), tự động chuyển sang nguồn dự phòng DNSE Entrade cho {clean_sym}...")

    # 3. Try DNSE Entrade fallback endpoint
    try:
        df = _fetch_dnse_ohlcv(clean_sym, count_back, end_date)
        if not df.empty:
            df = _normalize_ohlcv(df, clean_sym, "DNSE")
            _OHLCV_CACHE[cache_key] = df
            return df.copy()
    except Exception as e_dnse:
        logger.error(f"Cả Vietcap và DNSE đều không tải được OHLCV cho {clean_sym}: {e_dnse}")
        raise NoMarketDataError(f"Không thể tải dữ liệu OHLCV cho {clean_sym} từ các nguồn dữ liệu TTCK VN") from e_dnse

    raise NoMarketDataError(f"Không có dữ liệu trả về cho mã {clean_sym}")


def get_vn_stock_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
    *args: Any,
    **kwargs: Any,
) -> str:
    """Fetch OHLCV stock price history for a Vietnamese ticker in CSV format."""
    clean_sym = clean_vn_symbol(symbol)
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    days_span = (end_dt - start_dt).days + 60

    df = fetch_vn_ohlcv(clean_sym, count_back=days_span, end_date=end_date)
    df = df[(df["Date"] >= start_dt) & (df["Date"] <= (end_dt + pd.Timedelta(days=1)))]

    if df.empty:
        raise NoMarketDataError(f"Mã {clean_sym} không có dữ liệu giao dịch trong khoảng {start_date} đến {end_date}")

    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    df = df.set_index("Date").sort_index()

    for col in ["Open", "High", "Low", "Close"]:
        if col in df.columns:
            df[col] = df[col].astype(float).round(2)
    if "Volume" in df.columns:
        df["Volume"] = df["Volume"].astype(int)

    csv_string = df.to_csv()
    header = (
        f"# Dữ liệu giá cổ phiếu {clean_sym} (Thị trường Chứng khoán Việt Nam) từ {start_date} đến {end_date}\n"
        f"# Tổng số phiên giao dịch: {len(df)}\n"
        f"# Thời gian truy xuất: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + csv_string


def get_vn_stock_stats_indicators_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
    *args: Any,
    **kwargs: Any,
) -> str:
    """Compute technical indicators for a Vietnamese stock using stockstats."""
    clean_sym = clean_vn_symbol(symbol)
    if indicator not in _BEST_IND_PARAMS:
        raise ValueError(
            f"Chỉ báo {indicator} chưa được hỗ trợ. Vui lòng chọn trong: {list(_BEST_IND_PARAMS.keys())}"
        )

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_dt - relativedelta(days=look_back_days)

    # To calculate 200 SMA accurately, we need at least 320 calendar days
    total_lookback = max(look_back_days + 150, 350)
    df = fetch_vn_ohlcv(clean_sym, count_back=total_lookback, end_date=curr_date)

    if df.empty:
        raise NoMarketDataError(f"Không có dữ liệu giá để tính chỉ báo {indicator} cho mã {clean_sym}")

    w = wrap(df.copy())
    w["Date"] = pd.to_datetime(w["Date"]).dt.strftime("%Y-%m-%d")

    # Trigger calculation in stockstats
    w[indicator]

    # Map date -> value
    indicator_data: Dict[str, str] = {}
    for _, row in w.iterrows():
        d_str = row["Date"]
        val = row[indicator]
        indicator_data[d_str] = "N/A" if pd.isna(val) else str(val)

    # Generate descending list of dates from curr_date back to before
    date_values = []
    current_dt = curr_dt
    while current_dt >= before:
        d_str = current_dt.strftime("%Y-%m-%d")
        if d_str in indicator_data:
            val = indicator_data[d_str]
        else:
            val = "N/A: Not a trading day (weekend or holiday)"
        date_values.append((d_str, val))
        current_dt = current_dt - relativedelta(days=1)

    ind_string = ""
    for d_str, val in date_values:
        ind_string += f"{d_str}: {val}\n"

    result_str = (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
        + ind_string
        + "\n\n"
        + _BEST_IND_PARAMS.get(indicator, "Không có mô tả.")
    )
    return result_str


def get_vn_fundamentals(symbol: str, *args: Any, **kwargs: Any) -> str:
    """Fetch company profile, exchange limits, foreign room, and valuation ratios."""
    _ensure_session_cookies()
    clean_sym = clean_vn_symbol(symbol)
    lines = [f"# BÁO CÁO PHÂN TÍCH DOANH NGHIỆP: {clean_sym} (Thị trường Chứng khoán Việt Nam)\n"]

    curr_date = kwargs.get("curr_date") or (args[0] if args and isinstance(args[0], str) and "-" in args[0] else None)

    # 1. Real-time / latest settled price from daily OHLCV
    curr_price = "N/A"
    market_cap = "N/A"
    shares = "N/A"
    organ_code = clean_sym
    latest_p_val = None

    try:
        df_p = fetch_vn_ohlcv(clean_sym, count_back=5, end_date=curr_date)
        if not df_p.empty:
            latest_p_val = float(df_p["Close"].iloc[-1])
            curr_price = f"{latest_p_val:,.0f} VND ({latest_p_val / 1000:.2f})"
    except Exception as e:
        logger.debug(f"Không lấy được nến giá cho {clean_sym}: {e}")

    # 2. Company profile details
    details_url = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/details?ticker={clean_sym}"
    try:
        r = _HTTP_SESSION.get(details_url, timeout=6)
        if r.status_code == 200:
            data = r.json().get("data", {})
            if data:
                if curr_price == "N/A" and data.get("currentPrice"):
                    latest_p_val = float(data["currentPrice"])
                    curr_price = f"{latest_p_val:,.0f} VND ({latest_p_val / 1000:.2f})"
                raw_mc = data.get("marketCap")
                raw_sh = data.get("issueShare") or data.get("numberOfSharesMktCap")
                if raw_sh:
                    n_shares = int(raw_sh)
                    shares = f"{n_shares:,} CP"
                    if latest_p_val:
                        market_cap = f"{latest_p_val * n_shares / 1e9:,.1f} tỷ VND"
                if market_cap == "N/A" and raw_mc:
                    market_cap = f"{float(raw_mc) / 1e9:,.1f} tỷ VND"
                organ_code = data.get("organCode", clean_sym)
                exchange = str(data.get("exchange") or data.get("comGroupCode") or "").upper()
                if exchange in {"HOSE", "HNX", "UPCOM"}:
                    register_instruments([{"symbol": clean_sym, "exchange": exchange,
                                           "name": data.get("enOrganName") or organ_code}])
    except Exception as e:
        logger.warning(f"Lỗi lấy chi tiết công ty {clean_sym}: {e}")

    instrument = get_vn_instrument(clean_sym)
    exchange = instrument.exchange if instrument else "Không xác định"
    limit = LIMIT_PCT.get(exchange)
    exchange_text = f"{exchange} (Biên độ ±{limit:g}%)" if limit else exchange
    lines.append(f"**Mã Chứng khoán:** {clean_sym} (Mã tổ chức: {organ_code})")
    lines.append(f"**Sàn giao dịch & Biên độ giá:** {exchange_text}")
    lines.append("**Quy tắc thanh toán:** Cổ phiếu cơ sở không hỗ trợ bán khống; "
                 "thời điểm chứng khoán/tiền khả dụng phụ thuộc quy định hiện hành của VSDC và CTCK.")
    lines.append(f"**Giá hiện tại:** {curr_price} | **Vốn hóa:** {market_cap} | **CP Lưu hành:** {shares}\n")

    # 2. Key Valuation & Financial Ratios
    stat_url = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{clean_sym}/statistics-financial"
    try:
        r2 = _HTTP_SESSION.get(stat_url, timeout=6)
        if r2.status_code == 200:
            stats = r2.json().get("data", [])
            if stats and isinstance(stats, list):
                # Take the latest 3 reporting periods
                recent_stats = stats[-3:]
                ratio_rows = []
                for s in recent_stats:
                    period_str = f"Năm {s.get('yearReport', s.get('year', 'N/A'))} Q{s.get('quarter', '')}".strip()
                    row_data = {
                        "Kỳ BCTC": period_str,
                        "P/E": f"{float(s['pe']):.2f}" if s.get("pe") is not None else "N/A",
                        "P/B": f"{float(s['pb']):.2f}" if s.get("pb") is not None else "N/A",
                        "P/S": f"{float(s['ps']):.2f}" if s.get("ps") is not None else "N/A",
                        "ROE (%)": f"{float(s['roe']) * 100:.2f}%" if s.get("roe") is not None else "N/A",
                        "ROA (%)": f"{float(s['roa']) * 100:.2f}%" if s.get("roa") is not None else "N/A",
                        "Biên LN Gộp (%)": f"{float(s['grossMargin']) * 100:.2f}%" if s.get("grossMargin") is not None else "N/A",
                        "Biên LN Ròng (%)": f"{float(s['afterTaxProfitMargin']) * 100:.2f}%" if s.get("afterTaxProfitMargin") is not None else "N/A",
                        "Nợ / VCSH": f"{float(s['debtToEquity']):.2f}" if s.get("debtToEquity") is not None else "N/A",
                        "Current Ratio": f"{float(s['currentRatio']):.2f}" if s.get("currentRatio") is not None else "N/A",
                    }
                    ratio_rows.append(row_data)

                if ratio_rows:
                    df_ratios = pd.DataFrame(ratio_rows)
                    lines.append("## Các Chỉ Số Định Giá & Hiệu Quả Hoạt Động (Key Ratios):")
                    try:
                        lines.append(df_ratios.to_markdown(index=False))
                    except Exception:
                        lines.append(df_ratios.to_string(index=False))
                    lines.append("")
    except Exception as e:
        logger.warning(f"Lỗi lấy financial ratio cho {clean_sym}: {e}")

    return "\n".join(lines)


def _fetch_vn_statement_table(symbol: str, section: str, title: str, freq: str = "quarterly") -> str:
    """Fetch and format financial statement into Markdown table."""
    _ensure_session_cookies()
    clean_sym = clean_vn_symbol(symbol)
    try:
        url = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{clean_sym}/financial-statement"
        r = _HTTP_SESSION.get(url, params={"section": section}, timeout=8)
        if r.status_code != 200:
            return f"Không thể lấy dữ liệu {title} cho mã {clean_sym} (HTTP {r.status_code})"

        data_json = r.json().get("data", {})
        # Choose quarterly or annual periods
        if freq == "annual":
            items = data_json.get("years", [])
        else:
            items = data_json.get("quarters") or data_json.get("years", [])

        if not items:
            return f"Không có dữ liệu {title} cho mã {clean_sym}"

        # Fetch field labels
        meta_url = f"https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{clean_sym}/financial-statement/metrics"
        r_meta = _HTTP_SESSION.get(meta_url, timeout=6)
        meta_map: Dict[str, str] = {}
        if r_meta.status_code == 200:
            meta_list = r_meta.json().get("data", {}).get(section, [])
            meta_map = {item["field"]: item["titleVi"] for item in meta_list if "field" in item and "titleVi" in item}

        rows = []
        for item in items[-4:]:
            year = item.get("yearReport", "N/A")
            quarter = item.get("lengthReport") if "quarter" not in item else item.get("quarter")
            period_label = f"{year} Q{quarter}" if quarter and str(quarter) != "5" else f"Năm {year}"
            d: Dict[str, Any] = {"Kỳ": period_label}
            for field, label in meta_map.items():
                val = item.get(field)
                if val is not None and val != 0.0:
                    d[label] = f"{val / 1e9:,.1f} tỷ" if abs(val) > 1e6 else f"{val:,.1f}"
            rows.append(d)

        df_report = pd.DataFrame(rows).set_index("Kỳ").T
        if df_report.empty:
            return f"Dữ liệu {title} trống."

        # Keep top 35 most important rows to keep prompt concise
        df_display = df_report.head(35)
        try:
            table_md = df_display.to_markdown()
        except Exception:
            table_md = df_display.to_string()

        return f"# {title} (VAS - {freq.upper()}) - MÃ: {clean_sym}\n\n{table_md}"
    except Exception as e:
        logger.warning(f"Lỗi lấy {title} cho {clean_sym}: {e}")
        return f"Không có dữ liệu {title} cho mã {clean_sym}: {e}"


def get_vn_balance_sheet(symbol: str, *args: Any, **kwargs: Any) -> str:
    """Fetch balance sheet under VAS."""
    freq = args[0] if args else kwargs.get("freq", "quarterly")
    return _fetch_vn_statement_table(symbol, "BALANCE_SHEET", "BẢNG CÂN ĐỐI KẾ TOÁN", freq=str(freq))


def get_vn_income_statement(symbol: str, *args: Any, **kwargs: Any) -> str:
    """Fetch income statement under VAS."""
    freq = args[0] if args else kwargs.get("freq", "quarterly")
    return _fetch_vn_statement_table(symbol, "INCOME_STATEMENT", "BÁO CÁO KẾT QUẢ KINH DOANH", freq=str(freq))


def get_vn_cashflow(symbol: str, *args: Any, **kwargs: Any) -> str:
    """Fetch cash flow statement under VAS."""
    freq = args[0] if args else kwargs.get("freq", "quarterly")
    return _fetch_vn_statement_table(symbol, "CASH_FLOW", "BÁO CÁO LƯU CHUYỂN TIỀN TỆ", freq=str(freq))


def get_vn_macro_data() -> str:
    """Return structural context without presenting stale figures as live data."""
    return """
# BỐI CẢNH VĨ MÔ & THỊ TRƯỜNG TÀI CHÍNH VIỆT NAM (SBV & TTCK VN)

> Đây là bối cảnh cấu trúc, không phải số liệu vĩ mô thời gian thực. Chỉ sử dụng
> số liệu lãi suất, tỷ giá, thanh khoản, giao dịch khối ngoại hoặc margin khi báo
> cáo dữ liệu kèm nguồn và ngày cập nhật.

1. **Chính sách Tiền tệ Ngân hàng Nhà nước (SBV):**
   - Định hướng điều hành: Ổn định tỷ giá USD/VND, kiểm soát lạm phát và hỗ trợ tăng trưởng kinh tế.
   - Các công cụ can thiệp: Nghiệp vụ thị trường mở (OMO), phát hành tín phiếu SBV (T-Bill) hút tiền khi tỷ giá căng thẳng, bán ngoại tệ can thiệp.
   - Lãi suất điều hành: Lãi suất tái cấp vốn, tái chiết khấu và trần lãi suất tiền gửi ngắn hạn.
   - Hạn mức tăng trưởng tín dụng (Room tín dụng) phân bổ cho các ngân hàng thương mại.

2. **Dòng tiền & Cơ cấu Nhà đầu tư:**
   - Đánh giá nhà đầu tư cá nhân, khối ngoại/ETF và margin chỉ khi có dữ liệu cập nhật.
   - Không suy diễn tỷ trọng dòng tiền hoặc room ngoại từ dữ liệu lịch sử không ghi ngày.

3. **Vi cấu trúc & Quy tắc Giao dịch:**
   - Biên độ dao động: HOSE (±7%), HNX (±10%), UPCoM (±15%).
   - Thời điểm tiền/chứng khoán khả dụng phải theo quy định VSDC và CTCK hiện hành.
   - Không giả định có thể bán khống cổ phiếu cơ sở.
"""


def get_vn_corporate_disclosures(symbol: str, limit: int = 8) -> str:
    """Fetch official corporate disclosures, board resolutions, and announcements from KBS API."""
    clean_sym = clean_vn_symbol(symbol)
    items_found: List[str] = []

    try:
        url_kbs = f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/stockinfo/news/{clean_sym}"
        r_kbs = _HTTP_SESSION.get(url_kbs, params={"l": 1, "p": 1, "s": max(limit, 10)}, timeout=6)
        if r_kbs.status_code == 200:
            kbs_data = r_kbs.json()
            if isinstance(kbs_data, list):
                for item in kbs_data[:limit]:
                    pub = (item.get("PublishTime") or "")[:10]
                    title = (item.get("Title") or "").strip()
                    head = (item.get("Head") or "").strip()
                    desc = f": {head}" if head and head != title else ""
                    if title:
                        items_found.append(f"- **[{pub}]** {title}{desc}")
    except Exception as e:
        logger.debug(f"Lỗi lấy KBS disclosures cho {clean_sym}: {e}")

    if not items_found:
        return f"Hiện chưa có công bố thông tin hay nghị quyết HĐQT mới được ghi nhận cho mã {clean_sym}."

    header = f"### Công Bố Thông Tin & Nghị Quyết HĐQT ({clean_sym}):\n\n"
    return header + "\n".join(items_found)


def get_vn_news(*args: Any, **kwargs: Any) -> str:
    """Fetch company news from Vietnamese financial media (CafeF, Vietstock, VnEconomy, etc.) and official disclosures."""
    import xml.etree.ElementTree as ET
    symbol = kwargs.get("symbol") or kwargs.get("ticker") or (args[0] if args else "")
    clean_sym = clean_vn_symbol(str(symbol))
    items_found: List[str] = []

    # 1. Official Corporate Disclosures & Board Resolutions
    try:
        url_kbs = f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/stockinfo/news/{clean_sym}"
        r_kbs = _HTTP_SESSION.get(url_kbs, params={"l": 1, "p": 1, "s": 6}, timeout=5)
        if r_kbs.status_code == 200:
            kbs_data = r_kbs.json()
            if isinstance(kbs_data, list):
                for item in kbs_data[:4]:
                    pub = (item.get("PublishTime") or "")[:10]
                    title = (item.get("Title") or "").strip()
                    head = (item.get("Head") or "").strip()
                    desc = f": {head}" if head and head != title else ""
                    if title:
                        items_found.append(f"- **[{pub}] [Công bố thông tin / HĐQT]** {title}{desc}")
    except Exception as e:
        logger.debug(f"Lỗi lấy KBS disclosures cho {clean_sym}: {e}")

    # 2. Financial press articles via Google News Vietnam
    try:
        query = f"cổ phiếu {clean_sym}"
        url_gnews = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=vi&gl=VN&ceid=VN:vi"
        r_gnews = _HTTP_SESSION.get(url_gnews, timeout=6)
        if r_gnews.status_code == 200:
            root = ET.fromstring(r_gnews.content)
            for item in root.findall(".//item")[:10]:
                pub_elem = item.find("pubDate")
                title_elem = item.find("title")
                pub_text = pub_elem.text[:16] if pub_elem is not None and pub_elem.text else ""
                title_text = title_elem.text if title_elem is not None and title_elem.text else ""
                if title_text:
                    items_found.append(f"- **[{pub_text}] [Báo chí tài chính]** {title_text}")
    except Exception as e:
        logger.debug(f"Lỗi lấy Google News cho {clean_sym}: {e}")

    if not items_found:
        return f"Hiện chưa có tin tức sự kiện mới được ghi nhận cho mã cổ phiếu {clean_sym}."

    header = f"### Tin Tức & Công Bố Thông Tin Cổ Phiếu {clean_sym} (Thị Trường Việt Nam):\n\n"
    return header + "\n".join(items_found)


def get_vn_global_news(*args: Any, **kwargs: Any) -> str:
    """Fetch broad macroeconomic and Vietnam stock market news (CafeF RSS + Macro news)."""
    import xml.etree.ElementTree as ET
    items_found: List[str] = []

    # 1. CafeF Market News RSS
    try:
        r_cf = _HTTP_SESSION.get("https://cafef.vn/thi-truong-chung-khoan.rss", timeout=5)
        if r_cf.status_code == 200:
            root = ET.fromstring(r_cf.content)
            for item in root.findall(".//item")[:6]:
                title = item.find("title").text if item.find("title") is not None else ""
                pub = item.find("pubDate").text[:16] if item.find("pubDate") is not None else ""
                if title:
                    items_found.append(f"- **[{pub}] [CafeF Thị Trường]** {title}")
    except Exception as e:
        logger.debug(f"Lỗi lấy CafeF RSS: {e}")

    # 2. VN-Index & Macro developments
    try:
        url_macro = "https://news.google.com/rss/search?q=th%E1%BB%8B+tr%C6%B0%E1%BB%9Dng+ch%E1%BB%A9ng+kho%C3%A1n+Vi%E1%BB%87t+Nam+VN-Index&hl=vi&gl=VN&ceid=VN:vi"
        r_macro = _HTTP_SESSION.get(url_macro, timeout=6)
        if r_macro.status_code == 200:
            root = ET.fromstring(r_macro.content)
            for item in root.findall(".//item")[:6]:
                title = item.find("title").text if item.find("title") is not None else ""
                pub = item.find("pubDate").text[:16] if item.find("pubDate") is not None else ""
                if title:
                    items_found.append(f"- **[{pub}] [Vĩ mô & TTCK Việt Nam]** {title}")
    except Exception as e:
        logger.debug(f"Lỗi lấy Google News vĩ mô VN: {e}")

    if not items_found:
        return "Không có tin tức vĩ mô mới được ghi nhận."

    header = "### Điểm Tin Vĩ Mô & Thị Trường Chứng Khoán Việt Nam (VN-Index):\n\n"
    return header + "\n".join(items_found)


def get_vn_insider_transactions(*args: Any, **kwargs: Any) -> str:
    """Fetch insider trading and major shareholder transactions for Vietnamese equities."""
    symbol = kwargs.get("symbol") or kwargs.get("ticker") or (args[0] if args else "")
    clean_sym = clean_vn_symbol(str(symbol))
    items_found: List[str] = []

    try:
        url_kbs = f"https://kbbuddywts.kbsec.com.vn/iis-server/investment/stockinfo/news/{clean_sym}"
        r_kbs = _HTTP_SESSION.get(url_kbs, params={"l": 1, "p": 1, "s": 25}, timeout=5)
        if r_kbs.status_code == 200:
            kbs_data = r_kbs.json()
            if isinstance(kbs_data, list):
                keywords = ["cổ đông", "nội bộ", "hđqt", "bổ nhiệm", "miễn nhiệm", "giao dịch", "cổ tức", "mua", "bán", "thoái vốn"]
                for item in kbs_data:
                    title = (item.get("Title") or "").strip()
                    pub = (item.get("PublishTime") or "")[:10]
                    if any(k in title.lower() for k in keywords):
                        items_found.append(f"- **[{pub}]** {title}")
    except Exception as e:
        logger.debug(f"Lỗi lấy insider transactions cho {clean_sym}: {e}")

    if not items_found:
        return f"Không có báo cáo giao dịch nội bộ hay biến động cổ đông lớn gần đây cho mã {clean_sym}."

    header = f"### Giao Dịch Cổ Đông Lớn & Thay Đổi Nhân Sự HĐQT ({clean_sym}):\n\n"
    return header + "\n".join(items_found[:8])

