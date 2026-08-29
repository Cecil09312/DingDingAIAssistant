"""LangGraph 智能体节点函数。

每个节点接收/返回 AgentState（或其增量），
由 graph.py 编排为状态图工作流。
"""

import json
import re
from typing import Any, List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from agent.prompts import (
    ROUTER_KEYWORDS,
    WEB_SEARCH_KEYWORDS,
    TOOL_KEYWORDS,
    ROUTER_PROMPT,
    build_system_prompt,
)
from agent.state import AgentState


def _get_llm(temperature: float = 0.7, model: str = None):
    """获取主 LLM 实例（含重试与超时配置）。

    Args:
        temperature: 采样温度
        model: 可选模型名，留空则使用配置中的主模型 llm_model；
               降级时传入 llm_fallback_model 复用此函数
    """
    from langchain_openai import ChatOpenAI

    from config.settings import get_settings

    s = get_settings()
    return ChatOpenAI(
        model=model or s.llm_model,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        temperature=temperature,
        max_retries=s.llm_max_retries,
        request_timeout=s.llm_request_timeout,
    )


def _get_router_llm(temperature: float = 0.0):
    """获取路由小模型实例（路由/抽取等轻量任务，含重试与超时）。"""
    from langchain_openai import ChatOpenAI

    from config.settings import get_settings

    s = get_settings()
    return ChatOpenAI(
        model=s.llm_router_model,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        temperature=temperature,
        max_retries=s.llm_max_retries,
        request_timeout=s.llm_request_timeout,
    )


# 时效性问题：含明确时间表达或天气查询，这类答案会随时间变化，
# 应优先走联网搜索且不写入长期记忆（避免过时信息污染记忆库）
_WEATHER_KEYWORDS = (
    "天气", "气温", "天气预报", "空气质量", "雾霾",
    "下雨吗", "下雪吗", "紫外线", "pm2.5",
)
_TIME_EXPR_RE = re.compile(
    r"今天|明天|后天|大后天|大前天|前天|"  # 相对日期
    r"昨晚|昨夜|今早|今晚|今夜|今儿|明早|明晚|明夜|前晚|后晚|"  # 早/晚/夜补充
    r"(?:[本下这]周|周[一二三四五六日天]|星期[一二三四五六日天])|"  # 周几
    r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]|"  # X月X日/号
    r"\d{4}\s*年|"  # 年份
    r"\d{1,2}\s*[点时](?:半|钟|\s*\d{1,2}\s*分)?|"  # X点/点半/X点X分
    r"\d{1,2}\s*[:：]\s*\d{2}"  # HH:MM
)


def _is_realtime_query(user_input: str) -> bool:
    """判断是否为时效性问题（含明确时间表达或天气查询）。

    这类问题答案随时间变化，应优先联网查询且不写入长期记忆。
    """
    if not user_input:
        return False
    if any(k in user_input for k in _WEATHER_KEYWORDS):
        return True
    return _TIME_EXPR_RE.search(user_input) is not None


# ---------- 节点 0: 预检查（缓存匹配 + 路由判断）----------
def pre_check_node(state: AgentState) -> dict:
    """预检查节点：合并执行问答缓存匹配 + 路由判断。

    顺序执行 qa_match_node / route_node 并合并输出；
    问答缓存命中时提前返回，后续条件边短路到 END，跳过整条生成链路。
    优先处理 pending_confirmation 状态（用户确认工具操作）。
    """
    # 优先处理确认状态：上一轮有 pending_confirmation 时检查用户是否在确认
    if state.get("pending_confirmation"):
        user_input = state.get("user_input", "").strip()
        if user_input in ("确认", "确定", "yes", "y", "YES", "OK", "ok"):
            # 用户确认，执行待确认的工具
            ctx = state.get("confirmation_context", {})
            tool_name = ctx.get("name", "")
            params = ctx.get("parameters", {})
            user_id = state.get("user_id", "")
            result = _execute_tool(tool_name, params, user_id)
            answer = _format_tool_result(tool_name, result)
            return {
                "answer": answer,
                "pending_confirmation": False,
                "confirmation_context": {},
                "memory_hit": True,  # 标记命中以短路到 END
            }
        else:
            # 用户修改内容或取消，清除确认状态，走正常流程
            pass

    result = {}
    # 问答缓存匹配（命中则短路，跳过路由）
    result.update(qa_match_node(state))
    if result.get("memory_hit"):
        return result
    # 路由判断（LLM + 关键词兜底）
    result.update(route_node(state))
    return result


def pre_check_condition(state: AgentState) -> str:
    """预检查条件路由：缓存命中→end，否则按 search_route 五路分流。

    - tool → tool（工具调用：待办/会议操作）
    - rag → retrieve（知识库检索）
    - web → web_search（联网搜索）
    - chat → load_memory（加载长期记忆后生成）
    - generate 分支保留供未来 need_memory=False 的纯闲聊直通
    """
    if state.get("memory_hit"):
        return "end"
    route = state.get("search_route", "chat")
    if route == "tool":
        return "tool"
    elif route == "rag":
        return "retrieve"
    elif route == "web":
        return "web_search"
    else:
        return "load_memory"


# ---------- 节点 1: 路由判断 ----------
def route_node(state: AgentState) -> dict:
    """判断使用哪种处理方式（rag/web/chat），写入 search_route。"""
    user_input = state.get("user_input", "")
    route = _decide_route(user_input)
    return {"search_route": route}


def _decide_route(user_input: str) -> str:
    """LLM + 关键词兜底判断使用哪种处理方式：rag/web/chat/tool（RAG 优先）。"""
    if not user_input.strip():
        return "chat"
    # 工具调用关键词优先短路（待办/会议操作意图明确）
    if any(k in user_input for k in TOOL_KEYWORDS):
        from config.settings import get_settings
        if get_settings().tool_calling_enabled:
            return "tool"
    # 时效性问题（明确时间/天气等）优先走联网搜索，绕过 LLM 路由提速
    if _is_realtime_query(user_input):
        from config.settings import get_settings
        if get_settings().web_search_enabled:
            return "web"
    # 短输入（<=4 字且不含疑问词/搜索词/工具词）倾向于闲聊
    if len(user_input) <= 4 and not any(
        k in user_input for k in ROUTER_KEYWORDS + WEB_SEARCH_KEYWORDS + TOOL_KEYWORDS
    ):
        return "chat"
    try:
        llm = _get_router_llm(temperature=0.0)
        resp = llm.invoke([HumanMessage(content=ROUTER_PROMPT.format(input=user_input))])
        content = (resp.content if isinstance(resp.content, str) else str(resp.content)).strip().lower()
        # 工具调用
        if "tool" in content:
            from config.settings import get_settings
            if get_settings().tool_calling_enabled:
                return "tool"
        # 检查是否联网搜索关闭
        if "web" in content:
            from config.settings import get_settings
            if not get_settings().web_search_enabled:
                return "rag"
            return "web"
        if "rag" in content:
            return "rag"
        return "chat"
    except Exception as e:
        print(f"[route] LLM 路由失败，使用关键词兜底: {e}")
        # 关键词兜底（工具优先 → RAG 优先 → 联网搜索）
        if any(k in user_input for k in TOOL_KEYWORDS):
            from config.settings import get_settings
            if get_settings().tool_calling_enabled:
                return "tool"
        if any(k in user_input for k in ROUTER_KEYWORDS):
            return "rag"
        if any(k in user_input for k in WEB_SEARCH_KEYWORDS):
            from config.settings import get_settings
            if get_settings().web_search_enabled:
                return "web"
        return "chat"


def route_condition(state: AgentState) -> str:
    """条件路由：search_route -> 'retrieve' / 'web_search' / 'generate'。"""
    route = state.get("search_route", "chat")
    if route == "rag":
        return "retrieve"
    elif route == "web":
        return "web_search"
    else:
        return "generate"


def qa_or_route_condition(state: AgentState) -> str:
    """条件路由：问答记忆命中 -> 'end'（跳过大模型），否则按 search_route 分流。"""
    if state.get("memory_hit"):
        return "end"
    return route_condition(state)


# ---------- 节点 2b: 问答记忆匹配 ----------
def qa_match_node(state: AgentState) -> dict:
    """问答记忆匹配：新问题与历史问题高度相似时直接复用历史答案。

    命中时写入 answer 与 memory_hit=True，后续条件边短路到大模型之外；
    Embedding 或检索失败静默降级为未命中，不阻断主链路。
    """
    from config.settings import get_settings

    settings = get_settings()
    if not settings.memory_qa_cache_enabled:
        return {"memory_hit": False}

    user_input = state.get("user_input", "").strip()
    user_id = state.get("user_id", "")
    if not user_id or len(user_input) < 4:
        return {"memory_hit": False}

    # 时效性问题（明确时间/天气）不匹配问答缓存：答案随时间变化，
    # 命中历史缓存会返回过时答案，应直接联网查询
    if _is_realtime_query(user_input):
        return {"memory_hit": False}

    try:
        from rag.embeddings import get_embeddings

        vec = get_embeddings().embed_query(user_input)
    except Exception as e:
        print(f"[qa_match] 问题向量化失败: {e}")
        return {"memory_hit": False}

    try:
        from memory.long_term import search_qa_by_embedding

        hit = search_qa_by_embedding(user_id, vec, settings.memory_qa_threshold)
    except Exception as e:
        print(f"[qa_match] 问答记忆检索失败: {e}")
        hit = None

    if hit:
        question, answer, score = hit
        print(f"[qa_match] 命中问答记忆 (相似度={score:.3f}): {question[:30]}")
        return {
            "answer": answer,
            "memory_hit": True,
            "query_embedding": vec,
            # 命中轮次同样写入短期历史，保证后续多轮上下文连贯
            "messages": [HumanMessage(content=user_input), AIMessage(content=answer)],
        }
    return {"memory_hit": False, "query_embedding": vec}


# ---------- 节点 3: RAG 检索 ----------
def retrieve_node(state: AgentState) -> dict:
    """执行 RAG 检索，按相关度阈值过滤后写入 rag_context。

    流程：查询改写（可选）→ 多路召回+重排序 → 相关度阈值过滤 → 格式化。
    相关度过滤仅在纯向量检索路径下生效（rerank/BM25 关闭时），
    因为 rerank 的 CrossEncoder 分数与 RRF 融合分数的语义与向量距离不同，
    统一归一化会导致过滤逻辑反转或失效。
    """
    from config.settings import get_settings
    from rag.retriever import format_context, retrieve

    user_input = state.get("user_input", "")
    settings = get_settings()
    try:
        # 查询改写（开启时用 LLM 改写，失败回退原始查询）
        from agent.query_rewrite import rewrite_query

        search_query = rewrite_query(user_input)

        docs = retrieve(search_query)
        # 相关度阈值过滤：仅在纯向量检索路径下生效
        # （rerank/BM25 路径已精排或融合，分数语义不同，不适用统一归一化）
        if not settings.rerank_enabled and not settings.rag_bm25_enabled:
            min_rel = settings.rag_min_relevance
            filtered = [
                (doc, score) for doc, score in docs
                if 1 / (1 + abs(score)) >= min_rel
            ]
            if len(filtered) < len(docs):
                print(f"[retrieve] 相关度过滤: {len(docs)} -> {len(filtered)} (阈值={min_rel})")
        else:
            filtered = docs
        rag_context = format_context(filtered)
    except Exception as e:
        print(f"[retrieve] 检索失败: {e}")
        rag_context = ""
    return {"rag_context": rag_context}


# ---------- 节点 3b: 联网搜索 ----------
def web_search_node(state: AgentState) -> dict:
    """执行联网搜索，结果写入 rag_context（复用同一字段）。"""
    from rag.web_search import search_and_format

    user_input = state.get("user_input", "")
    try:
        from config.settings import get_settings

        s = get_settings()
        rag_context = search_and_format(user_input, max_results=s.web_search_max_results)
        if not rag_context:
            rag_context = ""
            print("[web_search] 未找到搜索结果")
    except Exception as e:
        print(f"[web_search] 联网搜索失败: {e}")
        rag_context = ""
    return {"rag_context": rag_context}


# ---------- 节点 4: 长期记忆加载 ----------
def load_memory_node(state: AgentState) -> dict:
    """从 SQLite 分层加载长期记忆上下文与会话压缩摘要。"""
    from memory.long_term import build_memory_context, get_session_summary

    user_id = state.get("user_id", "")
    try:
        ctx = build_memory_context(user_id, query=state.get("user_input", ""))
    except Exception as e:
        print(f"[memory] 加载长期记忆失败: {e}")
        ctx = ""
    try:
        sess = get_session_summary(state.get("session_id", ""))
    except Exception as e:
        print(f"[memory] 加载会话摘要失败: {e}")
        sess = ""
    return {"long_term_context": ctx, "session_summary": sess}


# ---------- 节点 4b: 关键信息抽取 ----------
def extract_facts_node(state: AgentState) -> dict:
    """抽取并保存本轮高优先级信息（规则快速路径 + 可选 LLM 结构化抽取）。

    规则路径零 LLM 成本，保证“我叫某某”类信息当轮落库；
    LLM 抽取失败静默跳过，不影响主链路。
    """
    from memory.long_term import apply_extraction

    user_id = state.get("user_id", "")
    user_input = state.get("user_input", "")

    try:
        apply_extraction(user_id, user_input)
    except Exception as e:
        print(f"[memory] 规则抽取失败: {e}")

    from config.settings import get_settings

    if not get_settings().memory_extract_llm_enabled:
        return {}

    try:
        from agent.prompts import EXTRACT_PROMPT
        from memory.long_term import save_fact, save_profile

        llm = _get_router_llm(temperature=0.0)
        resp = llm.invoke([
            HumanMessage(content=EXTRACT_PROMPT.format(
                user_input=user_input, answer=state.get("answer", "")
            ))
        ])
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        data = _parse_json_block(content)
        for field, value in (data.get("profile") or {}).items():
            if isinstance(value, str) and value.strip():
                save_profile(user_id, field, value.strip())
        for item in (data.get("facts") or [])[:5]:
            fact = str(item.get("fact", "")).strip()
            if not fact:
                continue
            try:
                prio = max(1, min(10, int(item.get("priority", 5))))
            except (TypeError, ValueError):
                prio = 5
            save_fact(user_id, fact, priority=prio, source="llm")
    except Exception as e:
        print(f"[memory] LLM 抽取失败（已忽略）: {e}")

    return {}


def _parse_json_block(text: str) -> dict:
    """从 LLM 输出中提取第一个 JSON 对象，解析失败返回空 dict。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def build_chat_history(
    messages: List[BaseMessage],
    session_summary: str = "",
    window: int = 8,
    budget: int = 2000,
) -> List[BaseMessage]:
    """构建注入生成的历史消息（窗口 + 字符预算 + 更早对话压缩摘要）。

    防丢失策略：超出窗口/预算的旧消息不直接硬丢，
    若存在会话压缩摘要则以【更早对话摘要】系统消息注入，保留主线上下文。

    Returns:
        消息列表（可能以摘要 SystemMessage 开头，不含主 system prompt 与当前输入）
    """
    history = [m for m in messages if not isinstance(m, SystemMessage)]
    recent = list(history[-window:]) if window > 0 else list(history)
    # 窗口内仍超字符预算时，从最旧消息开始丢弃
    while recent and sum(len(str(m.content)) for m in recent) > budget:
        recent.pop(0)
    out: List[BaseMessage] = []
    if len(history) > len(recent) and session_summary:
        out.append(SystemMessage(content=f"【更早对话摘要】\n{session_summary}"))
    out.extend(recent)
    return out


# ---------- 节点 5: 生成回答 ----------
def _stream_answer(llm, chat_messages):
    """流式生成回答，逐 token 累加并返回完整文本。

    供 generate_node 主模型与降级模型复用，保证流式透传逻辑一致。
    """
    # 使用 stream 逐 token 生成，供 stream_mode="messages" 透传增量；
    # 同时手动累加完整回答供状态与记忆使用
    full = None
    for chunk in llm.stream(chat_messages):
        full = chunk if full is None else full + chunk
    return full.content if full is not None and isinstance(full.content, str) else str(full.content if full is not None else "")


def _try_fallback_generate(chat_messages, settings, primary_error):
    """主模型失败后按优先级依次尝试备用模型。

    - 优先使用 llm_fallback_models 列表（逗号分隔，按优先级排序）
    - 向后兼容 llm_fallback_model 单模型配置
    - 某个备用模型成功即返回，全部失败时返回汇总错误信息
    """
    # 构建备用模型列表（按优先级）
    fallback_list = []
    if settings.llm_fallback_models:
        fallback_list = [m.strip() for m in settings.llm_fallback_models.split(",") if m.strip()]
    elif settings.llm_fallback_model:
        fallback_list = [settings.llm_fallback_model]

    if not fallback_list:
        return f"抱歉，我暂时无法处理您的请求。错误信息: {primary_error}"

    errors = [f"主模型: {primary_error}"]
    for i, model_name in enumerate(fallback_list, 1):
        try:
            print(f"[generate] 尝试备用模型 {i}/{len(fallback_list)}: {model_name}")
            fallback_llm = _get_llm(model=model_name)
            answer = _stream_answer(fallback_llm, chat_messages)
            print(f"[generate] 备用模型 {model_name} 生成成功")
            return answer
        except Exception as e:
            print(f"[generate] 备用模型 {model_name} 失败: {e}")
            errors.append(f"{model_name}: {e}")

    return f"抱歉，我暂时无法处理您的请求。错误信息: {' | '.join(errors)}"


def generate_node(state: AgentState) -> dict:
    """组装 prompt 并生成回答（主模型失败自动降级到小模型）。"""
    from config.settings import get_settings

    user_input = state.get("user_input", "")
    rag_context = state.get("rag_context", "")
    long_term_context = state.get("long_term_context", "")
    messages = state.get("messages", [])
    settings = get_settings()

    system_prompt = build_system_prompt(
        long_term_context=long_term_context,
        rag_context=rag_context,
    )

    # 构造消息列表：system + 预算化历史（含更早对话摘要）+ 当前输入
    chat_messages = [SystemMessage(content=system_prompt)]
    chat_messages.extend(build_chat_history(
        messages,
        session_summary=state.get("session_summary", ""),
        window=settings.memory_short_window,
        budget=settings.memory_history_budget,
    ))
    chat_messages.append(HumanMessage(content=user_input))

    # 主模型流式生成，失败时降级到小模型重试
    try:
        llm = _get_llm()
        answer = _stream_answer(llm, chat_messages)
    except Exception as e:
        print(f"[generate] 主模型生成失败: {e}")
        answer = _try_fallback_generate(chat_messages, settings, e)

    return {"answer": answer, "messages": [HumanMessage(content=user_input), AIMessage(content=answer)]}


# ---------- 节点 6: 更新长期记忆 ----------
def memory_update_node(state: AgentState) -> dict:
    """周期性更新长期记忆摘要，并在短期窗口超出时刷新会话压缩摘要。"""
    from config.settings import get_settings
    from memory.long_term import increment_turn, summarize_and_store

    user_id = state.get("user_id", "")
    messages = state.get("messages", [])
    settings = get_settings()

    try:
        turn = increment_turn(user_id)
        # 每 N 轮触发一次摘要（增量合并，关键信息不会被冲掉）
        if turn > 0 and turn % settings.memory_summary_every == 0:
            summarize_and_store(user_id, messages)
    except Exception as e:
        print(f"[memory] 更新长期记忆失败: {e}")

    try:
        _refresh_session_summary(state, settings)
    except Exception as e:
        print(f"[memory] 刷新会话摘要失败: {e}")

    try:
        _save_qa_cache(state, settings)
    except Exception as e:
        print(f"[memory] 保存问答记忆失败: {e}")

    # 返回空 dict，不修改状态
    return {}


def _save_qa_cache(state: AgentState, settings) -> None:
    """将本轮问答对存入问答记忆（供后续相似问题直接复用）。

    仅缓存 rag/web 路由的知识类回答（闲聊不缓存），
    过滤过短输入/回答与错误兜底，命中缓存的轮次不重复写入。
    """
    if not settings.memory_qa_cache_enabled or state.get("memory_hit"):
        return
    if state.get("search_route") not in ("rag", "web"):
        return
    user_input = state.get("user_input", "").strip()
    answer = state.get("answer", "")
    if len(user_input) < 4 or len(answer) < 20:
        return
    if answer.startswith("抱歉"):
        return
    vec = state.get("query_embedding")
    if vec is None:
        try:
            from rag.embeddings import get_embeddings

            vec = get_embeddings().embed_query(user_input)
        except Exception:
            return
    from memory.long_term import save_qa

    save_qa(
        state.get("user_id", ""),
        user_input,
        answer,
        vec,
        max_records=settings.memory_qa_max_records,
    )


def _refresh_session_summary(state: AgentState, settings) -> None:
    """短期窗口超出时，将窗口外旧消息压缩为会话摘要（防上下文硬丢失）。

    仅当新增滑出窗口的消息 ≥4 条时才重新摘要，避免每轮重复调用 LLM。
    """
    from memory.long_term import (
        get_session_coverage,
        save_session_summary,
        summarize_messages,
    )

    session_id = state.get("session_id", "")
    user_id = state.get("user_id", "")
    if not session_id:
        return
    history = [m for m in state.get("messages", []) if not isinstance(m, SystemMessage)]
    window = settings.memory_short_window
    if window <= 0 or len(history) <= window:
        return
    older = history[:-window]
    covered = get_session_coverage(session_id)
    if covered and len(older) - covered < 4:
        return
    summary, _ = summarize_messages(older)
    if summary:
        save_session_summary(session_id, user_id, summary[:500], len(older))


# ---------- 节点 7: 后台记忆（抽取 + 更新）----------
def memory_background_node(state: AgentState) -> dict:
    """后台记忆节点：合并执行关键信息抽取 + 长期记忆更新。

    顺序执行 extract_facts_node 与 memory_update_node，两者内部均有
    独立 try/except 兜底，任一失败不影响另一者；本节点不阻塞主响应链路。
    """
    # 时效性问题（明确时间/天气等）不保存长期记忆：答案随时间变化，无长期价值
    if _is_realtime_query(state.get("user_input", "")):
        return {}
    result = {}
    result.update(extract_facts_node(state))
    result.update(memory_update_node(state))
    return result


# ---------- 节点 8: 工具调用（待办/会议操作）----------
def tool_node(state: AgentState) -> dict:
    """工具调用节点：LLM 提取参数 → 确认（写操作）→ 执行 → 返回结果。

    流程：
    1. LLM 从用户输入提取工具名 + 参数
    2. 查询类操作（query_todos/query_meetings）直接执行
    3. 写操作需用户确认（tool_confirmation_required=True 时）
    4. 无需确认或确认后执行工具调用
    5. 格式化结果返回给用户
    """
    from config.settings import get_settings

    settings = get_settings()
    user_input = state.get("user_input", "")
    user_id = state.get("user_id", "")

    # Step 1: LLM 提取工具名 + 参数
    tool_call = _extract_tool_call(user_input)
    if not tool_call or tool_call.get("tool") == "none":
        # 未识别到工具，降级为普通回答
        return {"answer": "未能识别您的操作意图，请尝试更明确的表达，如「创建待办」「预定会议」等。"}

    tool_name = tool_call.get("tool", "")
    params = tool_call.get("parameters", {})

    # Step 2: 查询类操作直接执行
    if tool_name in ("query_todos", "query_meetings"):
        result = _execute_tool(tool_name, params, user_id)
        answer = _format_tool_result(tool_name, result)
        return {"answer": answer, "tool_name": tool_name, "tool_params": params, "tool_result": result}

    # Step 3: 写操作需用户确认
    if settings.tool_confirmation_required:
        confirmation_msg = _format_confirmation(tool_name, params)
        return {
            "answer": confirmation_msg,
            "pending_confirmation": True,
            "confirmation_context": {"name": tool_name, "parameters": params},
        }

    # Step 4: 无需确认直接执行
    result = _execute_tool(tool_name, params, user_id)
    answer = _format_tool_result(tool_name, result)
    return {"answer": answer, "tool_name": tool_name, "tool_params": params, "tool_result": result}


def _extract_tool_call(user_input: str) -> dict:
    """用路由小模型从用户输入提取工具名和参数。

    Returns:
        {"tool": "create_todo", "parameters": {...}} 或 {"tool": "none"}
    """
    from agent.tools.tool_schemas import TOOL_EXTRACT_PROMPT

    try:
        llm = _get_router_llm(temperature=0.0)
        resp = llm.invoke([HumanMessage(content=TOOL_EXTRACT_PROMPT.format(input=user_input))])
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        data = _parse_json_block(content)
        return data
    except Exception as e:
        print(f"[tool] LLM 提取工具参数失败: {e}")
        return {"tool": "none"}


def _execute_tool(tool_name: str, params: dict, user_id: str) -> dict:
    """执行工具调用，分发到对应的工具执行器。

    Args:
        tool_name: 工具名（create_todo/query_todos/create_meeting/...）
        params: LLM 提取的参数
        user_id: 钉钉用户ID

    Returns:
        钉钉 API 响应 dict
    """
    try:
        if tool_name == "create_todo":
            from agent.tools.todo_tools import execute_create_todo
            return execute_create_todo(params, user_id)
        elif tool_name == "query_todos":
            from agent.tools.todo_tools import execute_query_todos
            return execute_query_todos(params, user_id)
        elif tool_name == "create_meeting":
            from agent.tools.meeting_tools import execute_create_meeting
            return execute_create_meeting(params, user_id)
        elif tool_name == "cancel_meeting":
            from agent.tools.meeting_tools import execute_cancel_meeting
            return execute_cancel_meeting(params, user_id)
        elif tool_name == "update_meeting":
            from agent.tools.meeting_tools import execute_update_meeting
            return execute_update_meeting(params, user_id)
        elif tool_name == "query_meetings":
            from agent.tools.meeting_tools import execute_query_meetings
            return execute_query_meetings(params, user_id)
        else:
            return {"errcode": -1, "errmsg": f"未知工具: {tool_name}"}
    except Exception as e:
        print(f"[tool] 执行工具 {tool_name} 失败: {e}")
        return {"errcode": -1, "errmsg": str(e)}


def _format_tool_result(tool_name: str, result: dict) -> str:
    """格式化工具执行结果为用户可读文本。"""
    if result.get("errcode") != 0:
        return f"操作失败: {result.get('errmsg', '未知错误')}"

    # 待办类工具
    if tool_name in ("create_todo", "query_todos"):
        from agent.tools.todo_tools import format_todo_result
        return format_todo_result(tool_name, result)

    # 会议类工具
    if tool_name in ("create_meeting", "cancel_meeting", "update_meeting", "query_meetings"):
        from agent.tools.meeting_tools import format_meeting_result
        return format_meeting_result(tool_name, result)

    return "操作完成。"


def _format_confirmation(tool_name: str, params: dict) -> str:
    """格式化工具确认消息（写操作需用户确认）。"""
    # 待办类工具
    if tool_name in ("create_todo",):
        from agent.tools.todo_tools import format_todo_confirmation
        return format_todo_confirmation(tool_name, params)

    # 会议类工具
    if tool_name in ("create_meeting", "cancel_meeting", "update_meeting"):
        from agent.tools.meeting_tools import format_meeting_confirmation
        return format_meeting_confirmation(tool_name, params)

    return f"确认执行操作 {tool_name} 吗？回复「确认」执行。"
