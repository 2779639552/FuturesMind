"""yfinance-based news data fetching functions."""

import contextlib  # 【调用包】静默吞掉日期解析异常(suppress)
from datetime import datetime  # 【调用包】解析新闻发布时间

import yfinance as yf  # 【调用包】Yahoo Finance 新闻/搜索数据源
from dateutil.relativedelta import relativedelta  # 【调用包】回推/推进日期(窗口判断)

from .config import get_config  # 【调用包】读取新闻条数/回看天数等配置
from .stockstats_utils import yf_retry  # 【调用包】yfinance 限流重试
from .symbol_utils import normalize_symbol  # 【调用包】符号归一化


# 【功能】从 yfinance 新闻条目中提取规范化的文章数据(兼容嵌套 'content' 结构)。
# 【参数】article: yfinance 新闻条目 dict。
# 【返回】dict: {title, summary, publisher, link, pub_date}。
# 【关键】嵌套结构从 content 内取字段, URL 取 canonicalUrl 或 clickThroughUrl;
#         扁平结构则回退, 并把 providerPublishTime(epoch) 解析为 pub_date, 使
#         扁平文章也能参与日期窗口过滤, 避免泄漏未来新闻(#992/#1007)。
def _extract_article_data(article: dict) -> dict:
    """Extract article data from yfinance news format (handles nested 'content' structure)."""
    # Handle nested content structure
    if "content" in article:
        content = article["content"]  # 【变量】嵌套内容主体
        title = content.get("title", "No title")
        summary = content.get("summary", "")
        provider = content.get("provider", {})
        publisher = provider.get("displayName", "Unknown")  # 【变量】发布方显示名

        # Get URL from canonicalUrl or clickThroughUrl
        url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}  # 【变量】URL 对象
        link = url_obj.get("url", "")

        # Get publish date
        pub_date_str = content.get("pubDate", "")  # 【变量】ISO 发布时间字符串
        pub_date = None  # 【变量】解析后的发布 datetime
        if pub_date_str:
            with contextlib.suppress(ValueError, AttributeError):  # 【调用函数】解析失败静默跳过
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))  # 【调用函数】ISO 串解析(Z 转时区)

        return {
            "title": title,
            "summary": summary,
            "publisher": publisher,
            "link": link,
            "pub_date": pub_date,
        }
    else:
        # Fallback for flat structure. Parse the epoch publish time so flat
        # articles are date-filterable too (otherwise they bypass the
        # historical window and leak future news, #992/#1007).
        pub_date = None
        ts = article.get("providerPublishTime")  # 【变量】扁平结构的 epoch 发布时间
        if ts:
            with contextlib.suppress(ValueError, OSError, TypeError):
                pub_date = datetime.fromtimestamp(ts)  # 【调用函数】epoch 秒转 datetime
        return {
            "title": article.get("title", "No title"),
            "summary": article.get("summary", ""),
            "publisher": article.get("publisher", "Unknown"),
            "link": article.get("link", ""),
            "pub_date": pub_date,
        }


# 【功能】判断文章是否属于 [start_dt, end_dt] 窗口。
# 【参数】pub_date: 文章发布日期(可为 None); start_dt/end_dt: 窗口边界。
# 【返回】布尔值。
# 【关键】① 有日期的文章仅在窗口内保留(终点放宽 1 天); ② 无日期的文章仅在
#         窗口到达"现在"的实时运行时保留——历史/回测窗口一律排除, 因无法证明其
#         不是未来新闻(前视安全 #992/#1007)。
def _in_news_window(pub_date, start_dt, end_dt) -> bool:
    """Whether an article belongs in the [start_dt, end_dt] window.

    Dated articles are kept only if they fall in the window. An undated article
    is kept only when the window reaches the present (live run) — in a
    historical/backtest window it's excluded, since we can't prove it isn't
    future news (look-ahead safety, #992/#1007).
    """
    if pub_date is not None:
        naive = pub_date.replace(tzinfo=None) if hasattr(pub_date, "replace") else pub_date  # 【变量】去掉时区便于比较
        return start_dt <= naive <= end_dt + relativedelta(days=1)  # 【调用函数】落在窗口内(含终点后 1 天缓冲)
    return end_dt >= datetime.now() - relativedelta(days=1)  # 【调用函数】无日期文章: 仅实时窗口保留


# 【功能】用 yfinance 拉取某标的的新闻, 并按 [start_date, end_date] 窗口过滤。
# 【参数】ticker: 股票代码; start_date/end_date: 起止日期(yyyy-mm-dd)。
# 【返回】格式化新闻文本; 无新闻或出错时返回相应说明字符串(不抛异常)。
# 【关键】用规范符号查询(原始券商/外盘/加密别名会静默返回空新闻), 但报告头部保留
#         用户原始代码并注明解析结果。
def get_news_yfinance(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    Retrieve news for a specific stock ticker using yfinance.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        Formatted string containing news articles
    """
    article_limit = get_config()["news_article_limit"]  # 【调用函数】读配置的新闻条数上限
    # Query Yahoo with the canonical symbol, like every other yfinance path —
    # a raw broker/forex/crypto alias (XAUUSD, BTCUSD) otherwise silently
    # returns no news. Keep the user's ticker in the report header.
    canonical = normalize_symbol(ticker)  # 【调用函数】符号归一化
    resolved = "" if canonical == ticker else f" (resolved to {canonical})"  # 【变量】报告头中的解析说明
    try:
        stock = yf.Ticker(canonical)  # 【调用函数】构造 Yahoo Ticker 对象
        news = yf_retry(lambda: stock.get_news(count=article_limit))  # 【调用函数】拉取新闻(带限流重试)

        if not news:
            return f"No news found for {ticker}{resolved}"

        # Parse date range for filtering
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")  # 【变量】窗口起点
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")  # 【变量】窗口终点

        news_str = ""  # 【变量】累计的新闻文本
        filtered_count = 0  # 【变量】实际保留的文章数

        for article in news:
            data = _extract_article_data(article)  # 【调用函数】提取规范化文章数据

            # Keep only articles within the requested window (look-ahead safe).
            if not _in_news_window(data["pub_date"], start_dt, end_dt):  # 【调用函数】窗口过滤(前视安全)
                continue

            news_str += f"### {data['title']} (source: {data['publisher']})\n"
            if data["summary"]:
                news_str += f"{data['summary']}\n"
            if data["link"]:
                news_str += f"Link: {data['link']}\n"
            news_str += "\n"
            filtered_count += 1

        if filtered_count == 0:
            return f"No news found for {ticker}{resolved} between {start_date} and {end_date}"  # 【调用函数】窗口内一篇都没有时明确说明

        return f"## {ticker}{resolved} News, from {start_date} to {end_date}:\n\n{news_str}"

    except Exception as e:
        return f"Error fetching news for {ticker}: {str(e)}"


# 【功能】用 yfinance Search 拉取全球/宏观财经新闻, 按窗口过滤并去重。
# 【参数】curr_date: 当前日期(yyyy-mm-dd); look_back_days: 回看天数(None 用配置
#         global_news_lookback_days); limit: 条数上限(None 用配置
#         global_news_article_limit)。
# 【返回】格式化新闻文本; 无新闻或出错时返回说明字符串。
# 【关键】按配置的查询词逐条搜索、按标题去重, 并对所有候选做与前视安全的窗口过滤。
def get_global_news_yfinance(
    curr_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> str:
    """
    Retrieve global/macro economic news using yfinance Search.

    Args:
        curr_date: Current date in yyyy-mm-dd format
        look_back_days: Number of days to look back. ``None`` falls back to
            ``global_news_lookback_days`` from the active config.
        limit: Maximum number of articles to return. ``None`` falls back to
            ``global_news_article_limit`` from the active config.

    Returns:
        Formatted string containing global news articles
    """
    config = get_config()  # 【调用函数】读配置
    if look_back_days is None:
        look_back_days = config["global_news_lookback_days"]  # 【变量】回看天数(取配置缺省)
    if limit is None:
        limit = config["global_news_article_limit"]  # 【变量】条数上限(取配置缺省)
    search_queries = config["global_news_queries"]  # 【变量】全局新闻搜索词列表

    all_news = []  # 【变量】收集的原始新闻条目
    seen_titles = set()  # 【变量】已见标题集合(用于去重)

    try:
        for query in search_queries:
            search = yf_retry(  # 【调用函数】带限流重试的搜索调用
                lambda q=query: yf.Search(  # 【调用函数】yfinance Search 对象
                    query=q,
                    news_count=limit,
                    enable_fuzzy_query=True,
                )
            )

            if search.news:
                for article in search.news:
                    # Handle both flat and nested structures
                    if "content" in article:
                        data = _extract_article_data(article)  # 【调用函数】提取标题
                        title = data["title"]
                    else:
                        title = article.get("title", "")

                    # Deduplicate by title
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        all_news.append(article)  # 【变量】按标题去重后收集

            if len(all_news) >= limit:
                break

        if not all_news:
            return f"No global news found for {curr_date}"

        # Calculate date range
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")  # 【变量】当前日期
        start_dt = curr_dt - relativedelta(days=look_back_days)  # 【变量】窗口起点
        start_date = start_dt.strftime("%Y-%m-%d")

        news_str = ""  # 【变量】累计的新闻文本
        kept = 0  # 【变量】窗口过滤后保留的文章数
        for article in all_news[:limit]:
            # Extract uniformly (flat + nested) and apply the same look-ahead-safe
            # window filter, so flat articles can't leak future news (#1007).
            data = _extract_article_data(article)  # 【调用函数】统一提取(兼容扁平/嵌套)
            if not _in_news_window(data["pub_date"], start_dt, curr_dt):  # 【调用函数】前视安全的窗口过滤
                continue
            news_str += f"### {data['title']} (source: {data['publisher']})\n"
            if data["summary"]:
                news_str += f"{data['summary']}\n"
            if data["link"]:
                news_str += f"Link: {data['link']}\n"
            news_str += "\n"
            kept += 1

        # All candidates fell outside the window -> say so rather than return an
        # empty-bodied report (#993).
        if kept == 0:
            return f"No global news found between {start_date} and {curr_date}"  # 【调用函数】全部候选都在窗口外时明确说明

        return f"## Global Market News, from {start_date} to {curr_date}:\n\n{news_str}"

    except Exception as e:
        return f"Error fetching global news: {str(e)}"
