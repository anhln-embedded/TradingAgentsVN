import sys
import os
import json
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

def main():
    ticker = "BSR"
    trade_date = datetime.now().strftime("%Y-%m-%d")
    print(f"=== KHỞI CHẠY TRADINGAGENTS-VN PHÂN TÍCH {ticker} ===")
    print(f"Ngày giao dịch: {trade_date}")

    config = DEFAULT_CONFIG.copy()
    ta = TradingAgentsGraph(debug=True, config=config)

    final_state, decision = ta.propagate(ticker, trade_date)

    print("\n" + "="*50)
    print(f"=== KẾT QUẢ QUYẾT ĐỊNH ĐẦU TƯ CHO {ticker} ===")
    print("="*50)
    print(f"Decision: {decision}")

    report_path = ta.save_reports(final_state, ticker)
    print(f"Báo cáo chi tiết đã được lưu tại: {report_path}")

    out_dir = Path("results/bsr_analysis")
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "final_state.json", "w", encoding="utf-8") as f:
        json.dump({k: str(v) for k, v in final_state.items()}, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
