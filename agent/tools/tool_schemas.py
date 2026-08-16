"""工具 Schema 定义（供 LLM Function Calling 使用）。

定义 6 个钉钉操作工具的名称、描述和参数结构，
供 tool_node 中的 LLM 调用以提取用户意图和参数。
"""

# 工具调用参数提取 prompt
TOOL_EXTRACT_PROMPT = """从用户消息中识别需要执行的操作并提取参数。

可用工具：
1. create_todo - 创建待办（需 title, due_time; 可选 description, priority）
2. query_todos - 查询待办（可选 status: all/pending/done）
3. create_meeting - 创建会议（需 title, start_time; 可选 end_time, attendees, location, description）
4. cancel_meeting - 取消会议（需 meeting_title 或 meeting_id; 可选 notify_attendees）
5. update_meeting - 修改会议（需 meeting_title 或 meeting_id; 可选 updates: title/start_time/end_time/location）
6. query_meetings - 查询会议（可选 date_from, date_to）

时间格式：ISO 8601（如 2026-08-17T15:00:00+08:00）
attendees 为姓名或手机号列表。

只输出 JSON，格式：
{{"tool": "工具名", "parameters": {{...}}}}

若无匹配工具，输出：{{"tool": "none"}}

用户消息：{input}"""


# 工具 Schema 列表（供参考和文档生成）
TOOL_SCHEMAS = [
    {
        "name": "create_todo",
        "description": "创建钉钉待办事项",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "待办标题"},
                "due_time": {"type": "string", "description": "截止时间 ISO 8601"},
                "description": {"type": "string", "description": "待办详情"},
                "priority": {"type": "integer", "description": "优先级 1-5"},
            },
            "required": ["title", "due_time"],
        },
    },
    {
        "name": "query_todos",
        "description": "查询当前用户的待办列表",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["all", "pending", "done"]},
            },
        },
    },
    {
        "name": "create_meeting",
        "description": "创建钉钉日程/会议",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "会议主题"},
                "start_time": {"type": "string", "description": "开始时间 ISO 8601"},
                "end_time": {"type": "string", "description": "结束时间 ISO 8601"},
                "attendees": {"type": "array", "items": {"type": "string"}, "description": "参会人姓名或手机号"},
                "location": {"type": "string", "description": "会议地点"},
                "description": {"type": "string", "description": "会议描述"},
            },
            "required": ["title", "start_time"],
        },
    },
    {
        "name": "cancel_meeting",
        "description": "取消已创建的钉钉会议",
        "parameters": {
            "type": "object",
            "properties": {
                "meeting_id": {"type": "string", "description": "会议ID"},
                "meeting_title": {"type": "string", "description": "会议标题（无ID时用于匹配）"},
                "notify_attendees": {"type": "boolean", "description": "是否通知参会人", "default": True},
            },
        },
    },
    {
        "name": "update_meeting",
        "description": "修改已创建的钉钉会议",
        "parameters": {
            "type": "object",
            "properties": {
                "meeting_id": {"type": "string", "description": "会议ID"},
                "meeting_title": {"type": "string", "description": "会议标题（无ID时用于匹配）"},
                "updates": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start_time": {"type": "string"},
                        "end_time": {"type": "string"},
                        "location": {"type": "string"},
                    },
                },
            },
        },
    },
    {
        "name": "query_meetings",
        "description": "查询当前用户的会议日程",
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "查询起始日期 ISO 8601"},
                "date_to": {"type": "string", "description": "查询结束日期 ISO 8601"},
            },
        },
    },
]

# 写操作工具集合（需要用户确认）
WRITE_TOOLS = {"create_todo", "create_meeting", "cancel_meeting", "update_meeting"}

# 读操作工具集合（直接执行，无需确认）
READ_TOOLS = {"query_todos", "query_meetings"}
