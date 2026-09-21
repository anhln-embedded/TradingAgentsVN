"""Vietnam Stock Market (TTCK VN) Dataflow Adapter.

Provides market data, technical indicators, and VAS-compliant financial
statements for Vietnamese equities (HOSE, HNX, UPCoM) via vnstock.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Annotated

import pandas as pd
from dateutil.relativedelta import relativedelta

from .errors import NoMarketDataError
from .stockstats_utils import StockstatsUtils

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


def _df_to_markdown(df: pd.DataFrame) -> str:
    """Format DataFrame to markdown string, with plain string fallback."""
    try:
        return df.to_markdown(index=False)
    except Exception:
        return df.to_string(index=False)



def is_vn_stock(symbol: str) -> bool:
    """Return True if symbol corresponds to a Vietnamese stock."""
    if not isinstance(symbol, str) or not symbol.strip():
        return False
    s = symbol.strip().upper()
    if s.endswith(".VN"):
        return True
    # 3-letter alphabetic symbol that is not a well-known US ETF/crypto
    if len(s) == 3 and s.isalpha() and s not in _COMMON_US_3_LETTER:
        return True
    return False


def clean_vn_symbol(symbol: str) -> str:
    """Normalize symbol to bare 3-letter ticker for vnstock (e.g. VNM.VN -> VNM)."""
    s = symbol.strip().upper()
    if s.endswith(".VN"):
        return s[:-3]
    return s


def get_vn_stock_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Fetch OHLCV stock price history for a Vietnamese ticker via vnstock."""
    from vnstock.api.quote import Quote

    clean_sym = clean_vn_symbol(symbol)
    try:
        q = Quote(symbol=clean_sym, source="VCI", show_log=False)
        # Add 1 day to end_date to make it inclusive
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        end_inclusive = (end_dt + relativedelta(days=1)).strftime("%Y-%m-%d")
        df = q.history(start=start_date, end=end_inclusive)
    except Exception as e:
        logger.error(f"vnstock Quote error for {clean_sym}: {e}")
        raise NoMarketDataError(f"Không lấy được dữ liệu giá từ vnstock cho mã {clean_sym}: {e}") from e

    if df is None or df.empty:
        raise NoMarketDataError(f"Mã {clean_sym} không có dữ liệu giao dịch trong khoảng {start_date} đến {end_date}")

    # Standardize columns: time, open, high, low, close, volume -> Date, Open, High, Low, Close, Volume
    col_map = {
        "time": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    df = df.rename(columns=col_map)
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    df = df.set_index("Date").sort_index()

    # Round price columns
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
) -> str:
    """Compute technical indicators for a Vietnamese stock using stockstats."""
    from vnstock.api.quote import Quote

    clean_sym = clean_vn_symbol(symbol)
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    # To compute 200 SMA accurately, we need at least 300 calendar days of lookback
    start_dt = curr_dt - relativedelta(days=max(look_back_days + 120, 365))
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = curr_date

    try:
        q = Quote(symbol=clean_sym, source="VCI", show_log=False)
        df = q.history(start=start_date, end=end_date)
    except Exception as e:
        raise NoMarketDataError(f"Lỗi tải dữ liệu tính chỉ báo cho {clean_sym}: {e}") from e

    if df is None or df.empty:
        raise NoMarketDataError(f"Không có dữ liệu giá để tính chỉ báo {indicator} cho mã {clean_sym}")

    col_map = {
        "time": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    df = df.rename(columns=col_map)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()

    # Pass to StockstatsUtils
    indicator_value = StockstatsUtils.get_indicator(
        stock_df=df,
        indicator=indicator,
        look_back_days=look_back_days,
    )
    return indicator_value


def get_vn_fundamentals(symbol: str) -> str:
    """Fetch company profile, foreign room, valuation ratios, and exchange limits."""
    from vnstock.api.company import Company
    from vnstock.api.financial import Finance
    from vnstock.api.trading import Trading

    clean_sym = clean_vn_symbol(symbol)
    lines = [f"# BÁO CÁO PHÂN TÍCH DOANH NGHIỆP: {clean_sym} (Thị trường Chứng khoán Việt Nam)\n"]

    exchange_desc = "HOSE (Biên độ ±7%)"
    curr_price = "N/A"
    market_cap = "N/A"
    foreign_pct = "N/A"
    foreign_max_pct = "N/A"
    free_float_pct = "N/A"
    sector = "N/A"
    com_name = clean_sym

    # 1. Company Overview
    try:
        c = Company(symbol=clean_sym, source="VCI", show_log=False)
        ov = c.overview()
        if ov is not None and not ov.empty:
            row = ov.iloc[0]
            com_name = row.get("organ_name", clean_sym)
            sector = row.get("sector", "N/A")
            com_group = str(row.get("com_group_code", ""))
            if com_group in _VN_EXCHANGE_PRICE_LIMITS:
                exchange_desc = _VN_EXCHANGE_PRICE_LIMITS[com_group]["desc"]
            
            raw_p = row.get("current_price")
            curr_price = f"{float(raw_p):,.0f} VND" if pd.notna(raw_p) else "N/A"
            raw_mc = row.get("market_cap")
            market_cap = f"{float(raw_mc) / 1e9:,.1f} tỷ VND" if pd.notna(raw_mc) else "N/A"
            
            raw_fp = row.get("foreigner_percentage")
            foreign_pct = f"{float(raw_fp) * 100:.2f}%" if pd.notna(raw_fp) else "N/A"
            raw_fmax = row.get("maximum_foreign_percentage")
            foreign_max_pct = f"{float(raw_fmax) * 100:.2f}%" if pd.notna(raw_fmax) else "N/A"
            
            raw_ff = row.get("free_float_percentage")
            free_float_pct = f"{float(raw_ff) * 100:.2f}%" if pd.notna(raw_ff) else "N/A"
    except Exception as e:
        logger.warning(f"Không lấy được overview cho {clean_sym}: {e}")

    lines.append(f"**Tên Doanh nghiệp:** {com_name}")
    lines.append(f"**Ngành nghề kinh doanh:** {sector}")
    lines.append(f"**Sàn giao dịch & Biên độ giá:** {exchange_desc}")
    lines.append(f"**Quy tắc thanh toán:** T+2.5 (cổ phiếu về tài khoản chiều ngày T+2, không có bán khống)")
    lines.append(f"**Giá hiện tại:** {curr_price} | **Vốn hóa:** {market_cap}")
    lines.append(f"**Tỷ lệ sở hữu nước ngoài (Room ngoại):** {foreign_pct} / Tối đa: {foreign_max_pct}")
    lines.append(f"**Tỷ lệ cổ phiếu trôi nổi tự do (Free Float):** {free_float_pct}\n")

    # 2. Key Financial Ratios
    try:
        f = Finance(symbol=clean_sym, source="VCI", show_log=False)
        ratios = f.ratio(period="year")
        if ratios is not None and not ratios.empty:
            lines.append("## Các Chỉ Số Tài Chính & Định Giá:")
            lines.append(_df_to_markdown(ratios.head(10)))
            lines.append("")
    except Exception as e:
        logger.warning(f"Không lấy được financial ratio cho {clean_sym}: {e}")

    return "\n".join(lines)


def get_vn_balance_sheet(symbol: str) -> str:
    """Fetch balance sheet under VAS."""
    from vnstock.api.financial import Finance

    clean_sym = clean_vn_symbol(symbol)
    try:
        f = Finance(symbol=clean_sym, source="VCI", show_log=False)
        df = f.balance_sheet(period="year")
        if df is not None and not df.empty:
            return (
                f"# BẢNG CÂN ĐỐI KẾ TOÁN (VAS) - MÃ: {clean_sym}\n\n"
                + _df_to_markdown(df)
            )
    except Exception as e:
        logger.warning(f"Lỗi lấy Bảng CĐKT cho {clean_sym}: {e}")
    return f"Không có dữ liệu Bảng Cân đối Kế toán cho mã {clean_sym}."


def get_vn_income_statement(symbol: str) -> str:
    """Fetch income statement under VAS."""
    from vnstock.api.financial import Finance

    clean_sym = clean_vn_symbol(symbol)
    try:
        f = Finance(symbol=clean_sym, source="VCI", show_log=False)
        df = f.income_statement(period="year")
        if df is not None and not df.empty:
            return (
                f"# BÁO CÁO KẾT QUẢ KINH DOANH (VAS) - MÃ: {clean_sym}\n\n"
                + _df_to_markdown(df)
            )
    except Exception as e:
        logger.warning(f"Lỗi lấy Báo cáo KQKD cho {clean_sym}: {e}")
    return f"Không có dữ liệu Báo cáo Kết quả Kinh doanh cho mã {clean_sym}."


def get_vn_cashflow(symbol: str) -> str:
    """Fetch cash flow statement under VAS."""
    from vnstock.api.financial import Finance

    clean_sym = clean_vn_symbol(symbol)
    try:
        f = Finance(symbol=clean_sym, source="VCI", show_log=False)
        df = f.cash_flow(period="year")
        if df is not None and not df.empty:
            return (
                f"# BÁO CÁO LƯU CHUYỂN TIỀN TỆ (VAS) - MÃ: {clean_sym}\n\n"
                + _df_to_markdown(df)
            )
    except Exception as e:
        logger.warning(f"Lỗi lấy Báo cáo LCTT cho {clean_sym}: {e}")
    return f"Không có dữ liệu Báo cáo Lưu chuyển Tiền tệ cho mã {clean_sym}."


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
