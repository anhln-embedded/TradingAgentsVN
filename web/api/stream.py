"""SSE Streaming generator for TradingAgentsVN.

Consumes TradingAgentsGraph stream without modifying any core agent files,
emitting real-time JSON events for the web frontend.
"""

import asyncio
import json
import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict

from vnapp.core_adapter import DashboardGraph, build_config, stream_states, normalize_decision, decision_signal
from vnapp.storage import save_snapshot
from vnapp.runtime import EventBuffer, RUN_LOCK

logger = logging.getLogger(__name__)


class OpenAIRetryLogHandler(logging.Handler):
    """Capture retry and timeout logs from OpenAI client and forward to SSE stream."""

    def __init__(self, queue_ref: queue.Queue):
        super().__init__()
        self.queue_ref = queue_ref

    def emit(self, record):
        try:
            msg = self.format(record)
            if "retrying request" in msg.lower() or "retrying in" in msg.lower():
                self.queue_ref.put({
                    "event": "agent_message",
                    "data": {
                        "msg_type": "9ROUTER XOAY NHÀ CUNG CẤP",
                        "content": f"⚠️ Nhà cung cấp hiện tại phản hồi quá thời gian chờ (Timeout) hoặc gặp lỗi mạng. 9Router đang tự động gọi lại để xoay sang nhà cung cấp mới ({msg})...",
                        "timestamp": datetime.now().isoformat(),
                    },
                })
        except Exception:
            pass


def classify_message_info(message: Any) -> tuple[str, str, list]:
    """Extract message type, content and tool calls from LangChain message object."""
    msg_type = type(message).__name__
    content = getattr(message, "content", "")
    tool_calls = []

    if hasattr(message, "tool_calls") and message.tool_calls:
        for tc in message.tool_calls:
            if isinstance(tc, dict):
                tool_calls.append({"name": tc.get("name", ""), "args": tc.get("args", {})})
            else:
                tool_calls.append({"name": getattr(tc, "name", ""), "args": getattr(tc, "args", {})})

    return msg_type, content, tool_calls


def run_graph_sync(
    ticker: str,
    trade_date: str,
    event_queue: queue.Queue,
    selected_analysts=("market", "social", "news", "fundamentals"),
):
    """Run TradingAgentsGraph synchronously in a worker thread and push events to queue."""
    config = {}
    ta = None
    retry_log_handler = None
    openai_logger = None
    try:
        # Register retry log capture for 9Router failover observability
        openai_logger = logging.getLogger("openai._base_client")
        retry_log_handler = OpenAIRetryLogHandler(event_queue)
        openai_logger.addHandler(retry_log_handler)

        config = build_config()

        ta = DashboardGraph(
            selected_analysts=selected_analysts,
            debug=True,
            config=config,
        )

        event_queue.put({
            "event": "init",
            "data": {
                "ticker": ticker,
                "trade_date": trade_date,
                "timestamp": datetime.now().isoformat(),
            },
        })

        # Setup initial run state
        init_agent_state = ta.create_run_state(ticker, trade_date, asset_type="stock")
        args = ta.propagator.get_graph_args()

        checkpoint_tid = ta.begin_checkpoint(ticker, trade_date, asset_type="stock")
        if checkpoint_tid is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_tid

        graph_input = ta.checkpoint_input(init_agent_state)

        # Track what has been sent to avoid duplicate events
        processed_message_ids = set()
        active_agents = set()
        completed_agents = set()

        # Initial notify for analyst group starting
        event_queue.put({
            "event": "phase_start",
            "data": {
                "phase": "analysts",
                "name": "Nhóm Chuyên Gia Phân Tích",
                "agents": list(selected_analysts),
            },
        })
        for a in selected_analysts:
            event_queue.put({
                "event": "agent_status",
                "data": {"agent": a, "code": a, "status": "in_progress"},
            })

        final_state = {}
        for chunk in stream_states(ta, graph_input, args):
            chunk = normalize_decision(chunk)
            final_state.update(chunk)
            # 1. Process messages (thoughts, dialogues, tool calls)
            for msg in chunk.get("messages", []):
                msg_id = getattr(msg, "id", None)
                if msg_id and msg_id in processed_message_ids:
                    continue
                if msg_id:
                    processed_message_ids.add(msg_id)

                msg_type, content, tool_calls = classify_message_info(msg)

                # Emit tool calls
                for tc in tool_calls:
                    event_queue.put({
                        "event": "tool_call",
                        "data": {
                            "tool": tc["name"],
                            "args": tc["args"],
                            "timestamp": datetime.now().isoformat(),
                        },
                    })

                # Emit agent thoughts/messages
                if content and str(content).strip():
                    event_queue.put({
                        "event": "agent_message",
                        "data": {
                            "msg_type": msg_type,
                            "content": str(content),
                            "timestamp": datetime.now().isoformat(),
                        },
                    })

            # 2. Check Analyst Reports
            analyst_mappings = [
                ("market_report", "Market Analyst", "market"),
                ("sentiment_report", "Sentiment Analyst", "sentiment"),
                ("news_report", "News Analyst", "news"),
                ("fundamentals_report", "Fundamentals Analyst", "fundamentals"),
            ]
            for key, agent_name, short_code in analyst_mappings:
                if chunk.get(key) and agent_name not in completed_agents:
                    completed_agents.add(agent_name)
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": agent_name, "code": short_code, "status": "completed"},
                    })
                    event_queue.put({
                        "event": "report_section",
                        "data": {"section": short_code, "name": agent_name, "content": chunk[key]},
                    })

            # 3. Check Research Team Debate
            if chunk.get("investment_debate_state"):
                debate = chunk["investment_debate_state"]
                if isinstance(debate, str):
                    import ast
                    try:
                        debate = ast.literal_eval(debate)
                    except Exception:
                        try:
                            debate = json.loads(debate)
                        except Exception:
                            debate = {}

                bull = debate.get("bull_history", "").strip() if isinstance(debate, dict) else ""
                bear = debate.get("bear_history", "").strip() if isinstance(debate, dict) else ""
                judge = debate.get("judge_decision", "").strip() if isinstance(debate, dict) else ""

                if (bull or bear) and "Research Team" not in active_agents:
                    active_agents.add("Research Team")
                    event_queue.put({
                        "event": "phase_start",
                        "data": {"phase": "research_debate", "name": "Sàn Đấu Tranh Luận (Bull vs Bear)"},
                    })
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Bull Researcher", "code": "bull", "status": "in_progress"},
                    })
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Bear Researcher", "code": "bear", "status": "in_progress"},
                    })
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Research Manager", "code": "research_judge", "status": "in_progress"},
                    })

                if bull or bear or judge:
                    event_queue.put({
                        "event": "debate_update",
                        "data": {
                            "team": "research",
                            "bull_history": bull,
                            "bear_history": bear,
                            "judge_decision": judge,
                        },
                    })

                if judge and "Research Team" not in completed_agents:
                    completed_agents.add("Research Team")
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Bull Researcher", "code": "bull", "status": "completed"},
                    })
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Bear Researcher", "code": "bear", "status": "completed"},
                    })
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Research Manager", "code": "research_judge", "status": "completed"},
                    })
                    # Trader starts next
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Trader", "code": "trader", "status": "in_progress"},
                    })

            # 4. Check Trader Investment Plan
            if chunk.get("trader_investment_plan"):
                if "Trader" not in completed_agents:
                    completed_agents.add("Trader")
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Trader", "code": "trader", "status": "completed"},
                    })
                    event_queue.put({
                        "event": "report_section",
                        "data": {
                            "section": "trader",
                            "name": "Kế Hoạch Giao Dịch",
                            "content": chunk["trader_investment_plan"],
                        },
                    })
                    # Risk council starts next
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Risk Council", "code": "risk_council", "status": "in_progress"},
                    })
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Portfolio Manager", "code": "portfolio_manager", "status": "in_progress"},
                    })

            # 5. Check Risk Management Debate
            if chunk.get("risk_debate_state"):
                risk = chunk["risk_debate_state"]
                if isinstance(risk, str):
                    import ast
                    try:
                        risk = ast.literal_eval(risk)
                    except Exception:
                        try:
                            risk = json.loads(risk)
                        except Exception:
                            risk = {}

                agg = risk.get("aggressive_history", "").strip() if isinstance(risk, dict) else ""
                con = risk.get("conservative_history", "").strip() if isinstance(risk, dict) else ""
                neu = risk.get("neutral_history", "").strip() if isinstance(risk, dict) else ""
                judge = risk.get("judge_decision", "").strip() if isinstance(risk, dict) else ""

                if (agg or con or neu) and "Risk Team" not in active_agents:
                    active_agents.add("Risk Team")
                    event_queue.put({
                        "event": "phase_start",
                        "data": {"phase": "risk_debate", "name": "Hội Đồng Quản Trị Rủi Ro"},
                    })

                if agg or con or neu or judge:
                    event_queue.put({
                        "event": "debate_update",
                        "data": {
                            "team": "risk",
                            "aggressive_history": agg,
                            "conservative_history": con,
                            "neutral_history": neu,
                            "judge_decision": judge,
                        },
                    })

                if judge and "Portfolio Manager" not in completed_agents:
                    completed_agents.add("Portfolio Manager")
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Risk Council", "code": "risk_council", "status": "completed"},
                    })
                    event_queue.put({
                        "event": "agent_status",
                        "data": {"agent": "Portfolio Manager", "code": "portfolio_manager", "status": "completed"},
                    })

        if not final_state:
            raise RuntimeError("Core returned no analysis state")
        final_state = normalize_decision(final_state)
        trade_decision = final_state.get("final_trade_decision") or final_state.get("risk_debate_state", {}).get("judge_decision", "")
        final_state["final_trade_decision"] = trade_decision
        decision = decision_signal(final_state)
        # Saving failure must emit run_error, never a misleading complete event.
        saved_file = ta.save_reports(final_state, ticker)
        report_path = str(saved_file)
        final_state["messages"] = []
        save_snapshot(ticker, final_state)
        ta.record_decision(ticker, trade_date, final_state)
        ta.clear_checkpoint_on_success(ticker, trade_date, asset_type="stock")

        event_queue.put({
            "event": "complete",
            "data": {
                "ticker": ticker,
                "trade_date": trade_date,
                "signal": decision,
                "final_trade_decision": trade_decision,
                "report_path": report_path,
                "finished_at": datetime.now().isoformat(),
            },
        })

    except Exception as e:
        logger.exception("Error during graph execution: %s", e)
        err_msg = str(e)
        err_type = type(e).__name__

        # Friendly explanation when 9Router proxy timeouts or connection fails
        if "timeout" in err_msg.lower() or "timed out" in err_msg.lower() or "Timeout" in err_type:
            user_friendly_err = (
                f"Yêu cầu AI đã vượt quá thời gian chờ (Timeout {config.get('llm_timeout', 60)}s) "
                f"sau {config.get('llm_max_retries', 4)} lần tự động gọi lại. "
                f"Máy chủ 9Router đã thử xoay vòng các nhà cung cấp nhưng hiện chưa có nhà cung cấp nào phản hồi kịp thời. "
                f"Bạn có thể tăng Timeout hoặc thử lại sau."
            )
        elif "connection" in err_msg.lower() or "502" in err_msg or "503" in err_msg:
            user_friendly_err = (
                f"Lỗi kết nối tới máy chủ 9Router: Không thể kết nối hoặc máy chủ proxy trả về 502/503. "
                f"Chi tiết: {err_msg}"
            )
        else:
            user_friendly_err = err_msg

        event_queue.put({
            "event": "run_error",
            "data": {
                "error": user_friendly_err,
                "error_type": err_type,
            },
        })
    finally:
        if ta is not None:
            try:
                ta.end_checkpoint()
            except Exception:
                logger.exception("Failed to close core checkpoint")
        if openai_logger and retry_log_handler:
            try:
                openai_logger.removeHandler(retry_log_handler)
            except Exception:
                pass
        event_queue.put(None)  # Sentinel for completion


def stream_analysis_events(
    ticker: str,
    trade_date: str,
    selected_analysts=("market", "social", "news", "fundamentals"),
) -> AsyncGenerator[Dict[str, Any], None]:
    """Start a run independently of SSE lifetime; save even after disconnect."""
    if not RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("Một phiên phân tích đang chạy. Vui lòng chờ hoàn tất.")
    event_queue = EventBuffer()

    def run():
        try:
            run_graph_sync(ticker, trade_date, event_queue, selected_analysts)
        finally:
            RUN_LOCK.release()

    worker = threading.Thread(target=run, daemon=True)
    try:
        worker.start()
    except BaseException:
        RUN_LOCK.release()
        raise

    async def events():
        try:
            while True:
                try:
                    item = await asyncio.to_thread(event_queue.get, timeout=1)
                    if item is None:
                        break
                    yield item
                except queue.Empty:
                    if not worker.is_alive():
                        break
                    yield {"event": "ping", "data": {"time": datetime.now().isoformat()}}
        finally:
            event_queue.detached.set()

    return events()
