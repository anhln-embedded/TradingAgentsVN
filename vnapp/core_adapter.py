"""The web/core boundary. Contract tests catch upstream API changes here."""
import copy
import math
import os
import re

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

from vnapp.settings import data_dir, reports_dir


class DashboardGraph(TradingAgentsGraph):
    def _get_provider_kwargs(self):
        kwargs = super()._get_provider_kwargs()
        value = self.config.get("llm_timeout")
        if value is not None and value != "":
            if isinstance(value, bool):
                raise ValueError("llm_timeout must be a positive finite number")
            timeout = float(value)
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("llm_timeout must be a positive finite number")
            kwargs["timeout"] = timeout
        return kwargs


def build_config():
    config = copy.deepcopy(DEFAULT_CONFIG)
    keys = {
        "TRADINGAGENTS_LLM_PROVIDER": "llm_provider",
        "TRADINGAGENTS_DEEP_THINK_LLM": "deep_think_llm",
        "TRADINGAGENTS_QUICK_THINK_LLM": "quick_think_llm",
        "TRADINGAGENTS_LLM_BACKEND_URL": "backend_url",
    }
    for env, key in keys.items():
        if env in os.environ:
            config[key] = os.environ[env]
    config["llm_timeout"] = float(os.environ.get("TRADINGAGENTS_LLM_TIMEOUT", config.get("llm_timeout") or 60))
    config["llm_max_retries"] = int(os.environ.get("TRADINGAGENTS_LLM_MAX_RETRIES", config.get("llm_max_retries") if config.get("llm_max_retries") is not None else 4))
    config["results_dir"] = str(reports_dir())
    if "TA_DATA_DIR" in os.environ:
        config["data_cache_dir"] = str(data_dir() / "cache")
        config["memory_log_path"] = str(data_dir() / "memory" / "trading_memory.md")
    return config


def stream_states(graph, graph_input, args):
    """Normalize legacy value streaming and the newer public stream_run API."""
    accumulated = {}
    if callable(getattr(graph, "stream_run", None)):
        for messages, delta in graph.stream_run(graph_input, **args):
            if delta:
                accumulated.update(delta)
            yield {**accumulated, "messages": messages}
    else:
        for delta in graph.graph.stream(graph_input, **args):
            accumulated.update(delta)
            yield dict(accumulated)


def normalize_decision(state):
    """New core moves decisions out of debate state; retain the web contract."""
    result = dict(state)
    for debate_key, decision_key in (("investment_debate_state", "investment_plan"), ("risk_debate_state", "final_trade_decision")):
        debate = result.get(debate_key)
        if isinstance(debate, dict):
            result[debate_key] = {**debate, "judge_decision": result.get(decision_key) or debate.get("judge_decision", "")}
    return result


def decision_signal(state):
    rating = state.get("final_rating")
    if rating:
        return str(rating).upper()
    text = state.get("final_trade_decision", "")
    field = re.search(r"(?:^|\n)[*#\s]*(?:Action|Rating)[*\s]*:[*\s]*([^\n]+)", text, re.I)
    match = re.search(r"\b(BUY|SELL|HOLD|UNDERWEIGHT|OVERWEIGHT|TRIM)\b", field.group(1) if field else "", re.I)
    return match.group(1).upper() if match else "HOLD"
