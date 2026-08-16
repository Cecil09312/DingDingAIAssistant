"""LangGraph StateGraph 构建。

工作流（并行预检查 + 四路分流 + 后台记忆）：
    START -> pre_check(qa_match + emotion_route 并行)
                ├── 缓存命中 → END（直接复用历史答案）
                └── 未命中 → pre_check_condition 四路分流：
                    ┌───────────┬───────────┬───────────┐
                    ↓           ↓           ↓           ↓
              retrieve     web_search   load_memory   generate
              (知识库检索)  (联网搜索)   (长期记忆)    (直接生成)
                    ↓           ↓           ↓           |
                    └──────────→ generate ←─┘           |
                                ↓
                        memory_background(后台异步: 抽取+记忆更新)
                                ↓
                              END

优化点：
1. qa_match + emotion_route 并行执行（省 0.3-0.5s 串行等待）
2. emotion + route 合并为一次小模型调用（省 1-2s）
3. extract_facts + memory_update 后台线程执行（不阻塞响应）
4. 缓存命中直接返回（省全部链路）

编译时挂载 checkpointer（MemorySaver），按 thread_id 维护短期上下文。
"""

from typing import AsyncIterator, Iterator

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.nodes import (
    pre_check_node,
    pre_check_condition,
    generate_node,
    load_memory_node,
    memory_background_node,
    retrieve_node,
    tool_node,
    web_search_node,
)
from agent.state import AgentState


def build_graph(checkpointer=None) -> CompiledStateGraph:
    """构建并编译智能体状态图。

    Args:
        checkpointer: 可选的 checkpointer 实例。
            传入 None 时自动获取（进程内 MemorySaver）。
            传入 False 时不挂载 checkpointer（用于评估等无状态场景）。

    Returns:
        编译后的 CompiledStateGraph
    """
    g = StateGraph(AgentState)

    # 注册节点
    g.add_node("pre_check", pre_check_node)
    g.add_node("load_memory", load_memory_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("web_search", web_search_node)
    g.add_node("tool", tool_node)
    g.add_node("generate", generate_node)
    g.add_node("memory_background", memory_background_node)

    # 线性边
    # 四路并列：各自独立分支汇入 generate
    g.add_edge("load_memory", "generate")
    g.add_edge("retrieve", "generate")
    g.add_edge("web_search", "generate")
    # tool 节点直接到 END（不需要 generate，直接返回结果）
    g.add_edge("tool", END)
    # generate 完成后后台执行记忆更新，不阻塞响应
    g.add_edge("generate", "memory_background")
    g.add_edge("memory_background", END)

    # 条件边：pre_check 并行完成后统一分流
    # 缓存命中 → END（复用历史答案）
    # 未命中 → 按 search_route 五路分流（tool/retrieve/web_search/load_memory）
    g.add_edge(START, "pre_check")
    g.add_conditional_edges(
        "pre_check",
        pre_check_condition,
        {
            "end": END,
            "tool": "tool",
            "retrieve": "retrieve",
            "web_search": "web_search",
            "load_memory": "load_memory",
            "generate": "generate",
        },
    )

    # checkpointer
    if checkpointer is None:
        from memory.short_term import get_checkpointer

        checkpointer = get_checkpointer()

    if checkpointer:
        return g.compile(checkpointer=checkpointer)
    return g.compile()


# 模块级单例（按需创建）
_compiled = None


def get_compiled_graph() -> CompiledStateGraph:
    """返回编译后的图单例。"""
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


def chat(user_input: str, user_id: str = "default", session_id: str = "default") -> str:
    """便捷调用入口：单轮问答。

    Args:
        user_input: 用户输入文本
        user_id: 用户标识（用于长期记忆）
        session_id: 会话标识（用于短期记忆/checkpointer）

    Returns:
        智能体回答文本
    """
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}
    result = graph.invoke(
        {
            "user_input": user_input,
            "user_id": user_id,
            "session_id": session_id,
        },
        config=config,
    )
    return result.get("answer", "")


def _emit_message_events(events_iter):
    """将 graph.stream/astream 双模式输出转为统一的 node/token 事件。

    过滤规则：
    - 仅透传 generate 节点产出的 AIMessageChunk（排除 emotion/route 的内部 LLM 调用）；
    - 同一次 LLM 流式调用的所有 chunk 共享同一 lc_run id，不能按 id 去重；
    - 流结束后 LangGraph 还会 yield 节点返回的完整 AIMessage（内容与已输出
      增量之和重复），需按内容匹配跳过。

    Args:
        events_iter: 产出 (mode, chunk) 元组的迭代器（同步或异步）

    Yields:
        dict: {"type": "node", "node": 节点名} 或 {"type": "token", "content": token 文本}
    """
    from langchain_core.messages import AIMessage, AIMessageChunk

    acc = ""  # 已输出的增量内容累计，用于跳过末尾的完整消息重复
    for mode, chunk in events_iter:
        if mode == "updates":
            for node in chunk:
                yield {"type": "node", "node": node}
                # 问答记忆命中：不经过 generate 节点，将缓存答案补发为 token 事件
                if node == "qa_match":
                    data = chunk[node] or {}
                    if data.get("memory_hit") and data.get("answer"):
                        yield {"type": "token", "content": data["answer"]}
                # 工具调用：不经过 generate 节点，将 answer 补发为 token 事件
                elif node == "tool":
                    data = chunk[node] or {}
                    if data.get("answer"):
                        yield {"type": "token", "content": data["answer"]}
        elif mode == "messages":
            msg, metadata = chunk
            if metadata.get("langgraph_node") != "generate":
                continue
            if isinstance(msg, AIMessageChunk):
                if isinstance(msg.content, str) and msg.content:
                    acc += msg.content
                    yield {"type": "token", "content": msg.content}
            elif isinstance(msg, AIMessage):
                # 节点返回值产生的完整消息，内容已随增量输出，跳过
                if isinstance(msg.content, str) and msg.content == acc:
                    continue
                if isinstance(msg.content, str) and msg.content:
                    acc += msg.content
                    yield {"type": "token", "content": msg.content}


def chat_stream(user_input: str, user_id: str = "default", session_id: str = "default") -> Iterator[dict]:
    """流式调用入口（同步）：逐 token 产出回答片段。

    采用双模式流：updates 提供节点级进度，messages 提供 LLM token 级增量。

    Yields:
        dict: {"type": "node", "node": 节点名} 或 {"type": "token", "content": token 文本}
    """
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}
    events = graph.stream(
        {"user_input": user_input, "user_id": user_id, "session_id": session_id},
        config=config,
        stream_mode=["updates", "messages"],
    )
    yield from _emit_message_events(events)


async def astream_chat(
    user_input: str, user_id: str = "default", session_id: str = "default"
) -> AsyncIterator[dict]:
    """异步流式调用入口：逐事件产出，供 Web SSE 接口使用。

    Yields:
        dict: {"type": "node", "node": 节点名} 或 {"type": "token", "content": token 文本}
    """
    from langchain_core.messages import AIMessage, AIMessageChunk

    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}
    acc = ""  # 已输出增量累计，用于跳过末尾的完整消息重复
    async for mode, chunk in graph.astream(
        {"user_input": user_input, "user_id": user_id, "session_id": session_id},
        config=config,
        stream_mode=["updates", "messages"],
    ):
        if mode == "updates":
            for node in chunk:
                yield {"type": "node", "node": node}
                # 问答记忆命中：不经过 generate 节点，将缓存答案补发为 token 事件
                if node == "qa_match":
                    data = chunk[node] or {}
                    if data.get("memory_hit") and data.get("answer"):
                        acc += data["answer"]
                        yield {"type": "token", "content": data["answer"]}
                # 工具调用：不经过 generate 节点，将 answer 补发为 token 事件
                elif node == "tool":
                    data = chunk[node] or {}
                    if data.get("answer"):
                        acc += data["answer"]
                        yield {"type": "token", "content": data["answer"]}
        elif mode == "messages":
            msg, metadata = chunk
            if metadata.get("langgraph_node") != "generate":
                continue
            if isinstance(msg, AIMessageChunk):
                if isinstance(msg.content, str) and msg.content:
                    acc += msg.content
                    yield {"type": "token", "content": msg.content}
            elif isinstance(msg, AIMessage):
                if isinstance(msg.content, str) and msg.content == acc:
                    continue
                if isinstance(msg.content, str) and msg.content:
                    acc += msg.content
                    yield {"type": "token", "content": msg.content}
