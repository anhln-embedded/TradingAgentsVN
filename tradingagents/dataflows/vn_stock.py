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

from .errors import NoMarketDataError

logger = logging.getLogger(__name__)

# Common non-VN 3-letter symbols (major US ETFs, commodities, crypto)
_COMMON_US_3_LETTER = frozenset({
    "SPY", "QQQ", "DIA", "IWM", "GLD", "SLV", "XLE", "XLF", "XLK", "XLV",
    "XLI", "XLY", "XLP", "XLU", "XLB", "USO", "UNG", "TLT", "EEM", "VXX",
    "BTC", "ETH", "SOL", "XRP", "DOG", "LTC", "BNB", "ADA", "DOT", "AVX",
})

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
    """Return True if symbol corresponds to a Vietnamese stock."""
    if not isinstance(symbol, str) or not symbol.strip():
        return False
    s = symbol.strip().upper()
    if s.endswith(".VN"):
        return True
    if len(s) == 3 and s.isalpha() and s not in _COMMON_US_3_LETTER:
        return True
    return False


def clean_vn_symbol(symbol: str) -> str:
    """Normalize symbol to bare 3-letter ticker for Vietnam market (e.g. VNM.VN -> VNM)."""
    s = symbol.strip().upper()
    if s.endswith(".VN"):
        return s[:-3]
    return s


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
    """Direct high-speed REST fetcher for Vietnamese stock OHLCV history with multi-provider fallback and caching."""
    clean_sym = clean_vn_symbol(symbol)
    cache_key = f"{clean_sym}:{count_back}:{end_date}"
    if cache_key in _OHLCV_CACHE:
        return _OHLCV_CACHE[cache_key].copy()

    # 1. Try Vietcap primary endpoint
    try:
        df = _fetch_vietcap_ohlcv(clean_sym, count_back, end_date)
        if not df.empty:
            _OHLCV_CACHE[cache_key] = df
            return df.copy()
    except Exception as e_vc:
        logger.warning(f"Vietcap API lỗi ({e_vc}), tự động chuyển sang nguồn dự phòng DNSE Entrade cho {clean_sym}...")

    # 2. Try DNSE Entrade fallback endpoint
    try:
        df = _fetch_dnse_ohlcv(clean_sym, count_back, end_date)
        if not df.empty:
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
    except Exception as e:
        logger.warning(f"Lỗi lấy chi tiết công ty {clean_sym}: {e}")

    lines.append(f"**Mã Chứng khoán:** {clean_sym} (Mã tổ chức: {organ_code})")
    lines.append(f"**Sàn giao dịch & Biên độ giá:** HOSE (Biên độ ±7%)")
    lines.append(f"**Quy tắc thanh toán:** T+2.5 (cổ phiếu về tài khoản chiều ngày T+2, KHÔNG CÓ BÁN KHỐNG)")
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
    """Return Vietnamese macro context and regulatory framework."""
    return """
# BỐI CẢNH VĨ MÔ & THỊ TRƯỜNG TÀI CHÍNH VIỆT NAM (SBV & TTCK VN)

1. **Chính sách Tiền tệ Ngân hàng Nhà nước (SBV):**
   - Định hướng điều hành: Ổn định tỷ giá USD/VND, kiểm soát lạm phát và hỗ trợ tăng trưởng kinh tế.
   - Các công cụ can thiệp: Nghiệp vụ thị trường mở (OMO), phát hành tín phiếu SBV (T-Bill) hút tiền khi tỷ giá căng thẳng, bán ngoại tệ can thiệp.
   - Lãi suất điều hành: Lãi suất tái cấp vốn, tái chiết khấu và trần lãi suất tiền gửi ngắn hạn.
   - Hạn mức tăng trưởng tín dụng (Room tín dụng) phân bổ cho các ngân hàng thương mại.

2. **Dòng tiền & Cơ cấu Nhà đầu tư:**
   - Nhà đầu tư cá nhân trong nước chiếm ~80-85% giá trị giao dịch toàn thị trường.
   - Giao dịch Khối ngoại: Là chỉ báo dẫn dắt tâm lý thị trường, đặc biệt tại các cổ phiếu kín room hoặc các quỹ ETF ngoại (Fubon, VanEck, Diamond ETF).
   - Áp lực Margin: Đòn bẩy ký quỹ tại các công ty chứng khoán (CTCK). Khi thị trường giảm mạnh dễ kích hoạt hiện tượng "Call Margin chéo".

3. **Vi cấu trúc & Quy tắc Giao dịch:**
   - Biên độ dao động: HOSE (±7%), HNX (±10%), UPCoM (±15%).
   - Chu kỳ thanh toán: T+2.5 (mua T+0 -> cổ phiếu về tài khoản chiều T+2).
   - KHÔNG CÓ BÁN KHỐNG (No short selling) trên thị trường cổ phiếu cơ sở.
"""
