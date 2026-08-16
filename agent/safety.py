"""输入安全过滤：关键词黑名单 + prompt 注入检测。

检测策略：
- 关键词黑名单：命中配置的敏感词则拒绝（敏感词不回显，避免被探测）
- prompt 注入检测：检测"忽略以上指令"等常见注入模式

被拒绝的输入不进入 Agent 链路，直接返回拦截提示。
功能默认关闭，需在 .env 中设置 INPUT_FILTER_ENABLED=true 开启。
"""

# 常见 prompt 注入模式（中英文，小写匹配）
# 仅保留明确的注入指令，避免误拦正常用户问句（如"你是一个什么样的系统"）
_INJECTION_PATTERNS = [
    # 英文注入模式（明确指令类）
    "ignore previous",
    "ignore the above",
    "disregard previous",
    "disregard the above",
    "forget your instructions",
    "developer mode",
    "dan mode",
    # 中文注入模式（明确指令类）
    "忽略以上",
    "忽略上面",
    "忽略之前",
    "忽略上述",
    "不要遵守",
    "无视以上",
    "进入开发者模式",
    "dan模式",
    "越狱",
]


def check_input(user_input: str) -> tuple:
    """检查输入安全性。

    Args:
        user_input: 用户输入文本

    Returns:
        (passed, reason): passed=True 表示通过，reason 为空字符串；
                          passed=False 表示拒绝，reason 为拦截原因（可展示给用户）
    """
    from config.settings import get_settings

    settings = get_settings()
    if not settings.input_filter_enabled:
        return True, ""

    if not user_input or not user_input.strip():
        return True, ""

    text = user_input.lower()

    # 关键词黑名单检测（敏感词不回显，避免被探测枚举）
    for kw in settings.input_blocked_keywords_list:
        if kw and kw.lower() in text:
            return False, "输入包含敏感内容，请调整后重试"

    # prompt 注入检测
    if settings.input_injection_check_enabled:
        for pattern in _INJECTION_PATTERNS:
            if pattern.lower() in text:
                return False, "输入疑似包含指令注入，已被拦截"

    return True, ""
