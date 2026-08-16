"""智能体 Prompt 模板。

包含系统 prompt、RAG 路由判断 prompt、情感语气适配、
记忆分节注入（用户画像/已知事实/历史摘要）与关键信息抽取 prompt。
"""

# 主系统 prompt 模板，由各占位符动态填充
SYSTEM_PROMPT_TEMPLATE = """你是一个钉钉 AI 智能体助手，友好、专业地为用户解答问题、提供建议。

{tone_guidance}

{memory_section}

{rag_section}

请根据用户当前输入和历史对话，给出有帮助、连贯的回答。如果检索到的参考资料与问题相关，请优先依据资料作答，并在末尾注明信息来源；若资料不足，可结合自身知识回答但需说明。{memory_instruction}"""

# 存在长期记忆时的显式使用指令（解决“模型不使用已有信息”问题）
MEMORY_INSTRUCTION = (
    "若用户询问关于其自身的信息（如姓名、称呼、偏好、项目等），"
    "必须优先使用上述“用户画像/已知事实”作答，不得回答不知道。"
)

# RAG/联网搜索上下文的默认字符预算（可被 settings.rag_context_budget 覆盖）
_DEFAULT_RAG_BUDGET = 2500


def _truncate_to_budget(text: str, budget: int) -> str:
    """按行边界截断文本到预算内（保留头部完整条目，避免硬截断乱码）。"""
    if len(text) <= budget:
        return text
    out, total = [], 0
    for line in text.splitlines():
        if total + len(line) + 1 > budget:
            break
        out.append(line)
        total += len(line) + 1
    if not out:
        return text[:budget]
    return "\n".join(out) + "\n…（参考资料过长已截断）"


def build_system_prompt(
    tone_guidance: str = "",
    long_term_context: str = "",
    rag_context: str = "",
) -> str:
    """组装最终系统 prompt。

    Args:
        tone_guidance: 情感语气指引文案
        long_term_context: 长期记忆上下文（已由 build_memory_context 分节格式化）
        rag_context: RAG 检索到的上下文（按预算截断）

    Returns:
        完整系统 prompt 字符串
    """
    try:
        from config.settings import get_settings

        rag_budget = get_settings().rag_context_budget
    except Exception:
        rag_budget = _DEFAULT_RAG_BUDGET
    rag_context = _truncate_to_budget(rag_context, rag_budget) if rag_context else ""

    memory_section = long_term_context  # 已含【用户画像】/【已知事实】/【历史摘要】分节
    memory_instruction = MEMORY_INSTRUCTION if long_term_context else ""
    rag_section = f"【参考资料】\n{rag_context}" if rag_context else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
        tone_guidance=tone_guidance,
        memory_section=memory_section,
        rag_section=rag_section,
        memory_instruction=memory_instruction,
    )


# 关键信息结构化抽取 prompt（每轮一次，失败静默跳过）
EXTRACT_PROMPT = """从本轮对话中抽取值得长期记住的用户信息，只抽取用户明确陈述的内容，不要推测。
输出 JSON（不要输出其他内容）：
{{"profile": {{"name": null, "role": null, "project": null, "company": null}},
 "facts": [{{"fact": "用一句话描述的事实", "priority": 1}}]}}
priority 取值 1-10，1 为最重要（如姓名、明确要求记住的信息）。
若无值得记忆的信息，输出：{{"profile": {{}}, "facts": []}}

对话：
用户: {user_input}
助手: {answer}"""


# 路由判断 prompt：决定使用知识库检索、联网搜索、直接闲聊还是工具调用（RAG 优先）
ROUTER_PROMPT = """判断用户输入需要哪种处理方式：

rag: 询问产品功能、使用方法、公司政策、文档内容、具体知识等可从知识库回答的问题
web: 询问最新新闻、实时信息、天气、当前事件、网络搜索等需要联网的问题
chat: 闲聊、问候、情感表达、简单确认等
tool: 需要执行操作（创建/查询/取消/修改 待办或会议）

tool 判断规则：
- 包含"提醒我/创建待办/帮我记一下"等待办操作意图时选 tool
- 包含"预定会议/创建会议/约个会/取消会议/修改会议/查询会议/查询待办"等操作意图时选 tool
- 纯知识问答（不是操作）不选 tool

路由优先级（重要）：
1. 若明确是操作意图（待办/会议），优先 tool
2. 其次优先 rag：凡是可以尝试从知识库回答的知识/方法/政策/流程类问题，一律选 rag
3. 仅当问题明确需要时效性信息（新闻/天气/实时动态），且知识库不可能包含时，才选 web
4. 不确定时选 rag，不要选 chat

只输出一个单词：rag、web、chat 或 tool，不要输出其他内容。

用户输入：{input}"""


# 简化的关键词路由兜底（当 LLM 不可用时，RAG 关键词优先于联网搜索关键词判断）
ROUTER_KEYWORDS = [
    "怎么", "如何", "什么", "为什么", "哪里", "是否", "能不能", "可以吗",
    "介绍", "说明", "功能", "用法", "政策", "规则", "流程", "文档",
    "价格", "费用", "时间", "区别", "对比", "帮助",
]

# 联网搜索关键词（用于关键词兜底判断是否需要联网）
WEB_SEARCH_KEYWORDS = [
    "最新", "今天", "昨天", "现在", "目前", "实时", "新闻", "天气",
    "热点", "近期", "最近", "当前", "上线", "发布", "热搜", "排行",
]

# 工具调用关键词（用于关键词兜底判断是否需要执行工具操作）
TOOL_KEYWORDS = [
    "创建待办", "提醒我", "待办", "创建会议", "预定会议", "约个会",
    "取消会议", "修改会议", "查询待办", "查询会议", "我的待办",
    "我的会议", "日程", "会议安排",
]


# P1 优化：合并情感分析 + 路由判断为一次 LLM 调用的 prompt
EMOTION_ROUTE_PROMPT = """请同时分析用户消息的情感倾向和处理路由，只输出一个 JSON 对象。

情感分类：
- positive: 满意、感谢、喜爱、期待
- negative: 不满、抱怨、愤怒、失望
- neutral: 陈述、提问

路由判断：
- rag: 询问功能、方法、政策、文档、知识等可从知识库回答的问题
- web: 询问最新新闻、天气、实时信息等需要联网的问题
- chat: 闲聊、问候、简单问答
- tool: 需要执行操作（创建/查询/取消/修改 待办或会议）

tool 判断规则：
- "提醒我/创建待办/帮我记一下"等待办操作 → tool
- "预定会议/创建会议/约个会/取消会议/修改会议/查询会议/查询待办"等操作 → tool
- 纯知识问答不选 tool

路由优先级：tool（明确操作意图）> rag > web > chat，不确定时选 rag 而非 chat。

只输出 JSON，格式：{{"emotion": "positive|negative|neutral", "route": "rag|web|chat|tool"}}

用户消息：{input}"""
