"""短期记忆管理：基于 LangGraph checkpointer。

按 thread_id（= session_id）维护会话上下文与交互历史，
支持多轮对话的上下文连贯性。使用进程内 MemorySaver，无需外部服务。
"""

from langgraph.checkpoint.memory import MemorySaver

_checkpointer = None
_checkpointer_type = None


def get_checkpointer():
    """返回 checkpointer 单例（进程内 MemorySaver）。"""
    global _checkpointer, _checkpointer_type
    if _checkpointer is None:
        _checkpointer = MemorySaver()
        _checkpointer_type = "memory"
        print("[memory] 短期记忆后端: MemorySaver")
    return _checkpointer


def get_checkpointer_type() -> str:
    """返回当前 checkpointer 类型标识。"""
    if _checkpointer_type is None:
        get_checkpointer()
    return _checkpointer_type or "memory"
