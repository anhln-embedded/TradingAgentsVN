from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.dataflows.date_window import as_of
from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_macro_indicators(
    indicator: Annotated[
        str,
        "Macro indicator: a friendly alias such as 'cpi', 'core_pce', "
        "'unemployment', 'fed_funds_rate', '10y_treasury', 'yield_curve', "
        "'real_gdp', 'vix', or a raw FRED series ID such as 'CPIAUCSL'.",
    ],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format; the end of the window"],
    look_back_days: Annotated[
        int | None, "Trailing window length in days; omit for a 1-year window"
    ] = None,
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
    ticker: Annotated[str, InjectedState("ticker")] = "",
) -> str:
    """Retrieve macroeconomic indicators (policy rates, inflation, labor, growth).
    For Vietnamese stocks, returns SBV monetary policy and macro context.
    """
    from tradingagents.dataflows.vn_stock import is_vn_stock, get_vn_macro_data

    if ticker and is_vn_stock(ticker):
        return get_vn_macro_data()

    return route_to_vendor("get_macro_indicators", indicator, as_of(curr_date, trade_date), look_back_days)
