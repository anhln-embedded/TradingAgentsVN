from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.dataflows.date_window import as_of, as_of_window
from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    start_date, end_date = as_of_window(start_date, end_date, trade_date)
    try:
        return route_to_vendor("get_news", ticker, start_date, end_date, symbol=ticker)
    except Exception as e:
        return f"Lỗi lấy tin tức cho {ticker}: {e}"

@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int | None, "Days to look back; omit to use the configured default"] = None,
    limit: Annotated[int | None, "Max articles to return; omit to use the configured default"] = None,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    company_of_interest: Annotated[str, InjectedState("company_of_interest")] = "",
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor. Defaults for look_back_days and
    limit come from DEFAULT_CONFIG (global_news_lookback_days,
    global_news_article_limit); pass explicit values to override.

    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back; omit to inherit config
        limit (int): Maximum number of articles to return; omit to inherit config

    Returns:
        str: A formatted string containing global news data
    """
    try:
        return route_to_vendor("get_global_news", as_of(curr_date, trade_date), look_back_days, limit, symbol=company_of_interest)
    except Exception as e:
        return f"Lỗi lấy tin tức vĩ mô thị trường: {e}"

@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A report of insider transaction data
    """
    try:
        return route_to_vendor("get_insider_transactions", ticker, trade_date or None, symbol=ticker)
    except Exception as e:
        return f"Lỗi lấy giao dịch nội bộ cho {ticker}: {e}"
