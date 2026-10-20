"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call. Its rating is the run's
``final_rating``, and the decision is rendered to markdown as
``final_trade_decision`` for the memory log, CLI display and saved reports.
When a provider does not expose structured output, the agent falls back to
free-text generation and the rating is read from that text.
"""

from __future__ import annotations

from tradingagents.agents.context import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_portfolio_context_from_state,
)
from tradingagents.agents.rating import parse_rating
from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.structured import NO_EXTERNAL_TOOLS, bind_structured, invoke_structured
from tradingagents.dataflows.vn_stock import is_vn_stock
from tradingagents.markets.vietnam.registry import get_vn_instrument
from tradingagents.markets.vietnam.rules import round_to_tick


def _enforce_vn_price_ticks(ticker: str, decision: PortfolioDecision) -> PortfolioDecision:
    """Normalize LLM price fields to executable exchange ticks."""
    if not is_vn_stock(ticker):
        return decision
    instrument = get_vn_instrument(ticker)
    if instrument is None:
        return decision.model_copy(update={
            "executive_summary": decision.executive_summary
            + " Cảnh báo: chưa xác minh được sàn nên các mức giá chưa được xác nhận."
        })
    changes = {}
    adjusted = []
    for field in ("price_target", "entry_price", "stop_loss"):
        value = getattr(decision, field)
        if value is not None:
            normalized = round_to_tick(float(value), instrument.exchange)
            changes[field] = normalized
            if normalized != value:
                adjusted.append(field)
    if adjusted:
        changes["executive_summary"] = (
            decision.executive_summary
            + f" Các mức {', '.join(adjusted)} đã được làm tròn theo bước giá {instrument.exchange}."
        )
    return decision.model_copy(update=changes) if changes else decision


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        ticker = str(state["company_of_interest"])
        instrument_context = get_instrument_context_from_state(state)
        portfolio_context = get_portfolio_context_from_state(state)

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

{portfolio_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

Ground every conclusion in specific evidence from the analysts. The risk debate always contains conflicting stances; deciding which is stronger is the job, so conflict alone is not a reason to Hold. Commit to the stronger case, sized by how decisively it wins. Choose Hold only when the evidence is still balanced after that weighing, or too thin to support a call; do not force a direction to appear decisive. Weigh the analysts on their merits, independent of speaking order.

## Output

Write these sections, in this order, starting with the rating on its own line:

- **Rating**: exactly one of Buy / Overweight / Hold / Underweight / Sell
- **Executive Summary**: the call and how to act on it (stating specific entry/sell price levels, stop loss, and position sizing)
- **Investment Thesis**: the evidence that decided it, and what would change it
- **Price Target**, **Time Horizon**, **Entry Price**, **Stop Loss**, **Position Sizing**: concrete execution levels

{NO_EXTERNAL_TOOLS}{get_language_instruction()}"""

        # The typed rating is the decision; the rendered text only carries it.
        # Read back from text, a rating the thesis quotes could replace it.
        decision = invoke_structured(structured_llm, prompt, "Portfolio Manager")
        if decision is not None:
            decision = _enforce_vn_price_ticks(ticker, decision)
            final_trade_decision = render_pm_decision(decision)
            final_rating = decision.rating.value
        else:
            final_trade_decision = llm.invoke(prompt).content
            final_rating = parse_rating(final_trade_decision)

        new_risk_debate_state = {
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "final_rating": final_rating,
        }

    return portfolio_manager_node
