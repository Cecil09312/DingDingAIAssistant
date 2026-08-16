"""待办工具执行器。

调用 dingtalk_lib 的待办 API 方法，处理参数转换和结果格式化。
通过 sys.path.insert 导入 skill 的 dingtalk_lib，保持 skill 自包含特性。
"""

import os
import sys

# 将 skill scripts 目录加入 sys.path 以导入 dingtalk_lib
_SKILL_SCRIPTS_PATH = os.path.join(
    os.getcwd(), ".qoder", "skills", "dingtalk-messaging", "scripts"
)
if _SKILL_SCRIPTS_PATH not in sys.path:
    sys.path.insert(0, _SKILL_SCRIPTS_PATH)


def execute_create_todo(params: dict, user_id: str) -> dict:
    """执行创建待办。

    Args:
        params: LLM 提取的参数（title, due_time, description, priority）
        user_id: 钉钉用户ID

    Returns:
        钉钉 API 响应 dict
    """
    from dingtalk_lib import create_todo

    return create_todo(
        user_id=user_id,
        title=params.get("title", ""),
        due_time=params.get("due_time", ""),
        description=params.get("description", ""),
        priority=params.get("priority", 3),
    )


def execute_query_todos(params: dict, user_id: str) -> dict:
    """执行查询待办。"""
    from dingtalk_lib import query_todos

    status = params.get("status", "pending")
    return query_todos(user_id, status)


def format_todo_result(tool_name: str, result: dict) -> str:
    """格式化待办工具执行结果为用户可读文本。"""
    if result.get("errcode") != 0:
        return f"操作失败: {result.get('errmsg', '未知错误')}"

    if tool_name == "create_todo":
        todo_id = result.get("todo_id", "")
        return f"待办已创建成功！" + (f"\n待办ID: {todo_id}" if todo_id else "")

    elif tool_name == "query_todos":
        todos = result.get("todos", result.get("result", []))
        if not todos:
            return "暂无待办事项。"
        lines = [f"共有 {len(todos)} 条待办："]
        for i, todo in enumerate(todos, 1):
            title = todo.get("title", "无标题")
            due = todo.get("due_time", "无截止时间")
            priority = todo.get("priority", 0)
            stars = "*" * min(priority, 5)
            lines.append(f"  {i}. [{stars}] {title} (截止: {due})")
        return "\n".join(lines)

    return "操作完成。"


def format_todo_confirmation(tool_name: str, params: dict) -> str:
    """格式化待办确认消息。"""
    if tool_name == "create_todo":
        return (
            f"请确认以下待办信息：\n"
            f"  标题: {params.get('title', '')}\n"
            f"  截止时间: {params.get('due_time', '未指定')}\n"
            f"  详情: {params.get('description', '无')}\n"
            f"  优先级: {params.get('priority', 3)}\n"
            f"\n回复「确认」执行，或修改后重新发送。"
        )
    return f"确认执行操作 {tool_name} 吗？回复「确认」执行。"
