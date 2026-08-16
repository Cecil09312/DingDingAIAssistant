"""查询改写：用 LLM 将用户问题改写为更利于检索的形式，提升召回率。

改写策略：
- 补全省略的主语/指代（如"它"指代什么）
- 去除口语化表达，转为检索友好的关键词形式
- 保留原意，不增加未提及的信息

失败时静默回退原始查询，不阻断检索链路。
"""

from langchain_core.messages import HumanMessage

_REWRITE_PROMPT = """请将以下用户问题改写为更利于知识库检索的形式。

要求：
1. 保留原意，不增加未提及的信息
2. 补全省略的主语或上下文（如"它"指代什么）
3. 去除口语化表达，转为检索友好的关键词形式
4. 只输出改写后的问题，不要任何解释

用户问题：{query}

改写后的问题："""


def rewrite_query(query: str) -> str:
    """用 LLM 改写查询以提升检索召回率，失败返回原始查询。

    Args:
        query: 原始用户查询

    Returns:
        改写后的查询；功能关闭或改写失败时返回原始查询
    """
    from config.settings import get_settings

    settings = get_settings()
    if not settings.rag_query_rewrite_enabled:
        return query

    # 过短的查询不触发改写（缺乏上下文，改写收益低）
    if len(query.strip()) < 6:
        return query

    try:
        from agent.nodes import _get_router_llm

        llm = _get_router_llm(temperature=0.0)
        resp = llm.invoke([HumanMessage(content=_REWRITE_PROMPT.format(query=query))])
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        rewritten = content.strip()
        if rewritten and rewritten != query:
            print(f"[query_rewrite] {query} -> {rewritten}")
            return rewritten
        return query
    except Exception as e:
        print(f"[query_rewrite] 改写失败，使用原始查询: {e}")
        return query
