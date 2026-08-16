"""情感分析模块。

对用户输入消息进行情感分类（正面/负面/中性），并给出建议的回复语气。
LLM 不可用时回退到基于关键词词典的规则分析。
"""

import json
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage


class EmotionResult(TypedDict):
    """情感分析结果。"""

    label: str       # positive / negative / neutral
    score: float     # 置信度 0~1
    tone: str        # 建议回复语气


# 语气映射表：情感 -> 回复语气指引
TONE_MAP = {
    "positive": "热情、积极、给予肯定和鼓励",
    "negative": "共情、温和、安抚，先理解对方情绪再回应",
    "neutral": "客观、清晰、专业",
}

# 规则兜底用的中文情感词典
_POSITIVE_WORDS = [
    "开心", "高兴", "快乐", "满意", "感谢", "谢谢", "喜欢", "棒", "赞", "好用",
    "优秀", "完美", "太好了", "开心", "兴奋", "期待", "爱了", "点赞",
]
_NEGATIVE_WORDS = [
    "难过", "伤心", "生气", "愤怒", "失望", "糟糕", "烦", "崩溃", "投诉", "不满",
    "有问题", "出错", "失败", "痛", "焦虑", "抑郁", "担心", "害怕", "讨厌", "垃圾",
]


def _get_llm():
    """获取 LLM 实例（使用路由小模型，降低情感分析延迟）。"""
    from langchain_openai import ChatOpenAI

    from config.settings import get_settings

    s = get_settings()
    return ChatOpenAI(
        model=s.llm_router_model,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        temperature=0.1,
        max_retries=s.llm_max_retries,
        request_timeout=s.llm_request_timeout,
    )


def _rule_based_analyze(text: str) -> EmotionResult:
    """基于关键词词典的规则兜底分析。"""
    pos_hit = sum(1 for w in _POSITIVE_WORDS if w in text)
    neg_hit = sum(1 for w in _NEGATIVE_WORDS if w in text)
    if pos_hit > neg_hit and pos_hit > 0:
        label = "positive"
        score = min(0.5 + 0.1 * pos_hit, 0.95)
    elif neg_hit > pos_hit and neg_hit > 0:
        label = "negative"
        score = min(0.5 + 0.1 * neg_hit, 0.95)
    else:
        label = "neutral"
        score = 0.6
    return EmotionResult(label=label, score=score, tone=TONE_MAP[label])


EMOTION_SYSTEM_PROMPT = """你是一个情感分析专家。请分析用户消息的情感倾向。

只输出一个 JSON 对象，不要输出任何其他内容，格式如下：
{"label": "positive|negative|neutral", "score": 0.0~1.0, "reason": "简要原因"}

规则：
- positive：表达满意、感谢、喜爱、期待等积极情绪
- negative：表达不满、抱怨、愤怒、失望、焦虑等消极情绪
- neutral：无明显情感倾向的陈述或提问
- score 表示置信度，越接近 1 越确定"""


def analyze(text: str) -> EmotionResult:
    """分析文本情感，返回 EmotionResult。

    优先使用 LLM 结构化输出，失败时回退到规则分析。
    """
    if not text or not text.strip():
        return EmotionResult(label="neutral", score=0.5, tone=TONE_MAP["neutral"])

    try:
        llm = _get_llm()
        messages = [
            SystemMessage(content=EMOTION_SYSTEM_PROMPT),
            HumanMessage(content=f"用户消息：{text}"),
        ]
        resp = llm.invoke(messages)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        # 解析 JSON（容错：提取首个 { ... } 块）
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            data = json.loads(content[start:end])
            label = data.get("label", "neutral").lower()
            if label not in TONE_MAP:
                label = "neutral"
            score = float(data.get("score", 0.7))
            score = max(0.0, min(1.0, score))
            return EmotionResult(label=label, score=score, tone=TONE_MAP[label])
        raise ValueError(f"无法解析 LLM 输出: {content}")
    except Exception as e:
        print(f"[emotion] LLM 分析失败，回退规则分析: {e}")
        return _rule_based_analyze(text)


def get_tone_guidance(emotion: EmotionResult) -> str:
    """根据情感分析结果返回语气指引文案，供系统 prompt 注入。"""
    return f"当前用户情感倾向: {emotion['label']}（置信度 {emotion['score']:.2f}），回复语气应: {emotion['tone']}。"
