"""自然语言时间解析工具。

将中文自然语言时间表达转换为 ISO 8601 格式。
支持"明天下午3点"、"下周一上午10点"、"2小时后"等常见表达。
"""

import re
from datetime import datetime, timedelta


def parse_natural_time(text: str, now: datetime = None) -> str:
    """将自然语言时间解析为 ISO 8601 格式字符串。

    Args:
        text: 包含时间表达的自然语言文本
        now: 基准时间（默认当前时间）

    Returns:
        ISO 8601 格式时间字符串（如 2026-08-17T15:00:00+08:00）
    """
    if now is None:
        now = datetime.now()

    target = _parse_date(text, now)
    target = _parse_time(text, target)
    return target.isoformat()


def parse_natural_date_range(text: str, now: datetime = None) -> tuple:
    """解析自然语言日期范围为 (start, end) 元组。

    如"这周" → (本周一, 本周日), "明天" → (明天0点, 明天23:59)
    """
    if now is None:
        now = datetime.now()

    text_lower = text.lower()

    if "今天" in text or "今日" in text:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1) - timedelta(seconds=1)
    elif "明天" in text or "明日" in text:
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1) - timedelta(seconds=1)
    elif "本周" in text or "这周" in text:
        weekday = now.weekday()
        start = (now - timedelta(days=weekday)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7) - timedelta(seconds=1)
    elif "下周" in text:
        weekday = now.weekday()
        start = (now + timedelta(days=7 - weekday)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7) - timedelta(seconds=1)
    elif "本月" in text:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            end = now.replace(year=now.year + 1, month=1, day=1) - timedelta(seconds=1)
        else:
            end = now.replace(month=now.month + 1, day=1) - timedelta(seconds=1)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1) - timedelta(seconds=1)

    return start.isoformat(), end.isoformat()


def _parse_date(text: str, now: datetime) -> datetime:
    """解析日期部分。"""
    if "后天" in text:
        return now + timedelta(days=2)
    elif "明天" in text or "明日" in text:
        return now + timedelta(days=1)
    elif "昨天" in text:
        return now - timedelta(days=1)
    elif "下下周" in text:
        return now + timedelta(weeks=2)
    elif "下周" in text:
        return now + timedelta(weeks=1)
    elif "本周" in text or "这周" in text:
        return now
    else:
        # 尝试匹配"X天后"
        match = re.search(r"(\d+)\s*天后", text)
        if match:
            return now + timedelta(days=int(match.group(1)))
        # 尝试匹配"X小时后"
        match = re.search(r"(\d+)\s*小时后", text)
        if match:
            return now + timedelta(hours=int(match.group(1)))
        return now


def _parse_time(text: str, target: datetime) -> datetime:
    """解析时间部分。"""
    # 匹配 "下午3点"、"上午10点"、"15点"、"3点半"、"10:30"
    match = re.search(r"(上午|下午|晚上|早上)?(\d{1,2})\s*[点时:：](\d{0,2})", text)
    if match:
        period = match.group(1)
        hour = int(match.group(2))
        minute_str = match.group(3)
        minute = int(minute_str) if minute_str else 0

        # 上下午处理
        if period in ("下午", "晚上") and hour < 12:
            hour += 12
        elif period in ("上午", "早上") and hour == 12:
            hour = 0

        target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        # 匹配 "X点半"
        match = re.search(r"(上午|下午|晚上|早上)?(\d{1,2})\s*点半", text)
        if match:
            period = match.group(1)
            hour = int(match.group(2))
            if period in ("下午", "晚上") and hour < 12:
                hour += 12
            target = target.replace(hour=hour, minute=30, second=0, microsecond=0)

    return target
