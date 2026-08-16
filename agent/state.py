"""智能体状态定义。

定义 LangGraph StateGraph 中流转的 AgentState 结构。
"""

from typing import Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """LangGraph 智能体状态。

    messages 使用 add_messages reducer，自动累加历史消息。
    其余字段为每次执行的覆盖型状态。
    """

    # 消息历史（带 reducer，多轮累加）
    messages: list[BaseMessage]
    # 当前用户输入
    user_input: str
    # 用户标识与会话标识
    user_id: str
    session_id: str
    # 路由判断结果："rag" / "web" / "chat"
    search_route: str
    # RAG 检索结果（也用于联网搜索结果）
    rag_context: str
    # 长期记忆上下文（分节格式化：用户画像/已知事实/历史摘要）
    long_term_context: str
    # 会话压缩摘要（短期记忆超窗后的更早对话摘要）
    session_summary: str
    # 问答记忆命中标志（命中时直接复用历史答案，跳过大模型生成）
    memory_hit: bool
    # 当前问题向量（qa_match 节点产出，供问答对落库复用，避免重复编码）
    query_embedding: Optional[list[float]]
    # 最终生成的回答
    answer: str
    # ===== 工具调用相关 =====
    # LLM 识别到的工具名（create_todo/query_todos/create_meeting/...）
    tool_name: str
    # LLM 提取的工具参数
    tool_params: dict
    # 工具执行结果
    tool_result: dict
    # 是否等待用户确认（写操作需确认后执行）
    pending_confirmation: bool
    # 待执行的工具+参数上下文（用户确认后执行）
    confirmation_context: dict


__all__ = ["AgentState"]
