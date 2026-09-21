"""Sentiment analyst — multi-source sentiment analysis for a target ticker.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news — which led LLMs to fabricate
Reddit/X/StockTwits content under prompt pressure (verified live).

The redesigned agent pre-fetches three complementary data sources before
the LLM is invoked and injects them into the prompt as structured blocks:

  1. News headlines     — Yahoo Finance (institutional framing)
  2. StockTwits messages — retail-trader posts indexed by cashtag, with
                           user-labeled Bullish/Bearish sentiment tags
  3. Reddit posts        — r/wallstreetbets, r/stocks, r/investing

Each source is trimmed to the analysis window. These text feeds serve recent
items and are not archived as of a past date, so sentiment inputs for a
historical run are not guaranteed to be point-in-time.

The agent does not use tool-calling; the data is in the prompt from
turn 0. Output uses the structured-output pattern (json_schema for
OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic), falling
back to free-text generation for providers that lack native support, so
the sentiment header (band + score + confidence) is deterministic across
runs and providers instead of free-form per-model prose.

See: https://github.com/TauricResearch/TradingAgents/issues/557
See: https://github.com/TauricResearch/TradingAgents/issues/796
"""

from datetime import datetime, timedelta

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.schemas import SentimentReport, render_sentiment_report
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_news,
)
from tradingagents.agents.utils.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.reddit import fetch_reddit_posts
from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages
from tradingagents.dataflows.vn_stock import (
    clean_vn_symbol,
    get_vn_corporate_disclosures,
    is_vn_stock,
)


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches news + StockTwits + Reddit data, injects them into the
    prompt as structured blocks, and produces a deterministic sentiment
    report via structured output (with a free-text fallback for providers
    that do not support it).
    """
    structured_llm = bind_structured(llm, SentimentReport, "Sentiment Analyst")

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = get_instrument_context_from_state(state)

        # Pre-fetch all three sources. Each fetcher degrades gracefully and
        # returns a string (no exceptions surface from here), so the LLM
        # always sees something — either real data or a clear placeholder.
        news_block = get_news.func(ticker, start_date, end_date)

        if is_vn_stock(ticker):
            clean_sym = clean_vn_symbol(ticker)
            disclosures_block = get_vn_corporate_disclosures(clean_sym, limit=8)
            market_sentiment_block = (
                f"- **Tâm lý Nhà đầu tư Cá nhân ({clean_sym}):** Chiếm ~80-85% thanh khoản. Đang theo dõi sát sao tiến độ chia cổ tức tiền mặt, biến động giá hàng hóa/ngành, và thảo luận sôi nổi trên F319, FireAnt.\n"
                f"- **Giao dịch Khối Ngoại & Room Ngoại:** Khối ngoại là chỉ báo dòng tiền dẫn dắt. Cần theo dõi tỷ lệ sở hữu và mức độ mua/bán ròng của quỹ ETF ngoại (Fubon, Diamond).\n"
                f"- **Áp lực Đòn bẩy Margin:** Mức độ dư nợ margin tại các CTCK, rủi ro call margin chéo khi thị trường điều chỉnh."
            )
            system_message = _build_vn_sentiment_system_message(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                news_block=news_block,
                disclosures_block=disclosures_block,
                market_sentiment_block=market_sentiment_block,
            )
        else:
            # Pass the analysis window so a historical run trims social posts to it
            # instead of leaking today's chatter into a backtest (#1220).
            stocktwits_block = fetch_stocktwits_messages(
                ticker, limit=30, start_date=start_date, end_date=end_date
            )
            reddit_block = fetch_reddit_posts(ticker, start_date=start_date, end_date=end_date)
            system_message = _build_system_message(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                news_block=news_block,
                stocktwits_block=stocktwits_block,
                reddit_block=reddit_block,
            )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Report what your tools support; another agent decides the trade."
                    # No tool-calling here: the data is pre-fetched into the
                    # prompt, so tool-range wording would only invite a
                    # hallucinated tool call (#1130).
                    " Today's date is {current_date}; treat it as 'now' for all analysis. {instrument_context}"
                    " " + NO_EXTERNAL_TOOLS +
                    "\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # Format the template into a concrete message list so the structured
        # and free-text paths receive the same input. No bind_tools — the
        # data is already in the prompt.
        formatted_messages = prompt.format_messages(messages=state["messages"])

        report_text = invoke_structured_or_freetext(
            structured_llm,
            llm,
            formatted_messages,
            render_sentiment_report,
            "Sentiment Analyst",
        )

        return {
            "messages": [AIMessage(content=report_text)],
            "sentiment_report": report_text,
        }

    return sentiment_analyst_node


def _build_vn_sentiment_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    news_block: str,
    disclosures_block: str,
    market_sentiment_block: str,
) -> str:
    """Assemble the sentiment-analyst system message for Vietnamese equities."""
    clean_sym = clean_vn_symbol(ticker)
    return f"""Bạn là Chuyên gia Phân tích Tâm lý & Dòng tiền Thị trường Chứng khoán Việt Nam (Senior Sentiment & Market Flow Analyst).
Nhiệm vụ của bạn là lập một báo cáo phân tích tâm lý thị trường toàn diện, sâu sắc cho mã cổ phiếu {clean_sym} trong giai đoạn từ {start_date} đến {end_date}, dựa trên 3 nguồn dữ liệu thị trường thực tế đã được thu thập sẵn:

## CÁC NGUỒN DỮ LIỆU ĐÃ THU THẬP SẴN (PRE-FETCHED):

### 1. Báo chí Tài chính & Truyền thông Chuyên ngành Việt Nam (CafeF, Vietstock, VnEconomy, Báo Đầu tư...)
Góc nhìn truyền thông chính thống, phản ánh thông tin vĩ mô, ngành nghề, và các bài viết phân tích từ giới phân tích/chuyên gia.

<start_of_financial_news>
{news_block}
<end_of_financial_news>

### 2. Công bố Thông tin Doanh nghiệp & Nghị quyết HĐQT (UBCKNN, HOSE/HNX, Doanh nghiệp)
Thông tin chính thống về hoạt động doanh nghiệp: chi trả cổ tức, kết quả kinh doanh, biến động nhân sự cấp cao, dự án lớn và kế hoạch tăng vốn.

<start_of_corporate_disclosures>
{disclosures_block}
<end_of_corporate_disclosures>

### 3. Tâm lý Cộng đồng Nhà đầu tư Cá nhân, Dòng tiền Khối Ngoại & Đòn bẩy Margin
- Nhà đầu tư cá nhân chiếm ~80-85% thanh khoản toàn thị trường. Tâm lý thị trường thể hiện mạnh qua các diễn đàn chứng khoán lớn (F319, FireAnt) và các hội nhóm Zalo/Facebook.
- Khối ngoại: Xu hướng mua/bán ròng của nhà đầu tư nước ngoài và các quỹ ETF (Fubon, Diamond, VanEck), tỷ lệ sở hữu và Room ngoại còn lại.
- Trạng thái đòn bẩy Margin: Mức độ dư nợ margin tại các công ty chứng khoán và rủi ro bán giải chấp chéo khi thị trường biến động.

<start_of_market_sentiment>
{market_sentiment_block}
<end_of_market_sentiment>

## HƯỚNG DẪN PHÂN TÍCH TÂM LÝ & DÒNG TIỀN (BEST PRACTICES CHO TTCK VN):

1. **Phân tích theo từng nguồn dữ liệu thực tế:** Đánh giá cụ thể các sự kiện tin tức và công bố thông tin đã thu thập ở trên. Phân tích rõ các sự kiện đó mang tính tích cực (Bullish), tiêu cực (Bearish) hay trung lập đối với triển vọng giá cổ phiếu {clean_sym}. Tuyệt đối không viện dẫn các nguồn tin không liên quan như Yahoo Finance, StockTwits hay Reddit khi phân tích cổ phiếu Việt Nam.
2. **Phân tích sự đồng thuận hay phân kỳ giữa các nguồn:** Có sự mâu thuẫn giữa thông tin cơ bản/tin tức báo chí với tâm lý dòng tiền trên thị trường không? (Ví dụ: Doanh nghiệp chi trả cổ tức lớn hoặc trúng thầu dự án nhưng khối ngoại bán ròng hoặc thị trường chung đang chịu áp lực điều chỉnh).
3. **Chủ đề tường thuật chủ đạo (Dominant Narrative Themes):** Yếu tố trọng tâm nào đang chi phối tâm lý và kỳ vọng của nhà đầu tư đối với mã {clean_sym} hiện nay (cổ tức tiền mặt, tiến độ dự án khí/LNG, kết quả kinh doanh quý, chính sách giá khí, chu kỳ ngành dầu khí/năng lượng, hay xu hướng VN-Index)?
4. **Chất xúc tác ngắn hạn (Catalysts) & Rủi ro tiềm ẩn (Risks):** Liệt kê rõ các yếu tố có thể kích hoạt biến động giá (ngày chốt quyền cổ tức, công bố BCTC, giá hàng hóa thế giới, áp lực margin thị trường chung).

## CÁC TRƯỜNG DỮ LIỆU ĐẦU RA (STRUCTURED OUTPUT):

- **overall_band**: Chọn chính xác một trong các nhãn: Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Dùng Mixed khi các nguồn tin hoặc dòng tiền có sự phân hóa trái chiều; Dùng Neutral khi thông tin hoàn toàn trầm lắng.
- **overall_score**: Điểm số từ 0 (cực kỳ tiêu cực) đến 10 (cực kỳ tích cực); 5 là trung lập.
- **confidence**: low / medium / high, dựa trên độ đầy đủ và chất lượng dữ liệu thu thập được.
- **narrative**: Báo cáo chi tiết bằng tiếng Việt chuyên nghiệp, theo đúng cấu trúc:
  1. Phân Tích Chi Tiết Từng Nguồn Dữ Liệu:
     - Báo chí tài chính & Truyền thông (CafeF, Vietstock, VnEconomy)
     - Công bố thông tin & Nghị quyết HĐQT
     - Tâm lý cộng đồng nhà đầu tư cá nhân, Khối ngoại & Đòn bẩy Margin
  2. Phân Tích Sự Phân Kỳ Và Đồng Thuận Giữa Các Nguồn
  3. Chủ Đề Tường Thuật Chủ Đạo (Dominant Narrative Themes)
  4. Các Yếu Tố Xúc Tác (Catalysts) Và Rủi Ro Tiềm Ẩn (Risks)
  5. Bảng Markdown tổng hợp các tín hiệu tâm lý (Chiều hướng, Nguồn tin, Bằng chứng thực tế).

{get_language_instruction()}"""


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    news_block: str,
    stocktwits_block: str,
    reddit_block: str,
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on three complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### StockTwits messages — retail-trader social platform indexed by cashtag
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### Reddit posts — r/wallstreetbets, r/stocks, r/investing (past 7 days)
Community discussion, without vote or comment counts. Subreddit character matters (r/wallstreetbets is often contrarian/exuberant; r/stocks more measured; r/investing longer-term).

<start_of_reddit>
{reddit_block}
<end_of_reddit>

## How to analyze this data (best practices)

1. **Read the StockTwits Bullish/Bearish ratio as a leading retail-sentiment signal.** A 70/30 bullish/bearish split is moderately bullish; ≥90/10 may indicate over-extension and contrarian risk; 50/50 is uncertainty. Sample size matters — base rates on the actual message count, not percentages alone.

2. **Look for cross-source divergences.** If news framing is bearish but StockTwits is overwhelmingly bullish, that mismatch is itself a signal — it can mean retail is leaning into a thesis the news flow hasn't caught up to (or vice versa, that retail is chasing while institutions are cautious).

3. **Read Reddit posts for substance.** The feed carries no vote or comment counts, so judge a post by its body excerpt, not its title alone, and do not infer engagement.

4. **Distinguish opinion from event.** A news headline ("Nvidia announces $500M Corning deal") is an event; a StockTwits post ("buying NVDA, this is going to moon") is opinion. Both are inputs but should be weighted differently in your conclusions.

5. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment.

6. **Be honest about data limits.** If StockTwits returned only a handful of messages, or one or more sources returned an "<unavailable>" placeholder, the sentiment read is less robust — flag this explicitly in the `confidence` field and the narrative. If the sources are silent on a given subreddit, say so.

7. **Identify catalysts and risks** that emerge across sources — news of upcoming earnings, product launches, competitive threats, macro headlines, etc.

8. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

## Output fields

Fill the following fields:

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Use Mixed when sources point in clearly different directions; Neutral only when all sources are genuinely silent.
- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish); 5 is neutral. Keep it consistent with overall_band.
- **confidence**: low / medium / high, based on data quality and sample size.
- **narrative**: Full source-by-source breakdown, divergences, dominant narrative themes, catalysts and risks, and a markdown summary table of key sentiment signals (direction, source, supporting evidence).

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
