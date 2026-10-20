import sys
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

# DEFAULT_CONFIG already applies TRADINGAGENTS_* env-var overrides
# (llm_provider, deep_think_llm, quick_think_llm, backend_url, etc.),
# so users can switch models or endpoints purely via .env without
# editing this script. Override individual keys here only when you
# want a hard-coded value that should ignore the environment.
config = DEFAULT_CONFIG.copy()

# Initialize with custom config
ta = TradingAgentsGraph(debug=True, config=config)

from datetime import datetime

# forward propagate for Vietnamese stock using current date (2026-09-21)
trade_date = datetime.now().strftime("%Y-%m-%d")
print(f"=== Đang phân tích cổ phiếu VNM theo dữ liệu thị trường ngày: {trade_date} ===")
_, decision = ta.propagate("VNM", trade_date)
print(decision)
