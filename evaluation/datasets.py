"""评估数据集：RAG 与智能体评测样本。

每个样本包含 question（用户问题）、expected_answer（期望答案/参考）、
reference_context（参考资料文本）、reference_docs（检索到的文档片段列表）。
用于驱动 evaluation/rag_eval 与 agent_eval 的评估器。
"""

from typing import List, TypedDict


class EvalSample(TypedDict):
    """单个评估样本。"""

    question: str
    expected_answer: str
    reference_context: str
    reference_docs: list[str]


# RAG 评估数据集（与 data/docs 中的知识文档对应）
RAG_EVAL_DATASET: List[EvalSample] = [
    {
        "question": "钉钉AI智能体助手支持哪些核心功能？",
        "expected_answer": "支持情感分析、智能问答、RAG检索增强生成、短期与长期记忆、智能体评估等功能。",
        "reference_context": "钉钉AI智能体助手基于LangChain和LangGraph构建，支持情感分析、智能问答、RAG检索增强生成、短期记忆与长期记忆（SQLite），并提供LangSmith与OpenEvals评估能力。",
        "reference_docs": ["钉钉AI智能体助手基于LangChain和LangGraph构建，支持情感分析、智能问答、RAG检索增强生成。"],
    },
    {
        "question": "智能体的长期记忆使用什么存储？",
        "expected_answer": "使用SQLite作为长期记忆的持久化存储后端。",
        "reference_context": "长期记忆使用SQLite作为持久化存储后端，保存跨会话的用户偏好、关键信息摘要及历史交互摘要。",
        "reference_docs": ["长期记忆使用SQLite作为持久化存储后端，保存跨会话的用户偏好、关键信息摘要及历史交互摘要。"],
    },
    {
        "question": "RAG质量评估包含哪些维度？",
        "expected_answer": "包含检索相关性评估和生成质量评估（答案忠实度、相关性打分）。",
        "reference_context": "RAG质量评估机制包括检索相关性评估（上下文精确率/召回率）和生成质量评估（答案忠实度、相关性打分），可使用RAGAS框架或OpenEvals进行自动化评测。",
        "reference_docs": ["RAG质量评估包括检索相关性评估和生成质量评估（答案忠实度、相关性打分）。"],
    },
    {
        "question": "项目使用什么框架实现智能体工作流编排？",
        "expected_answer": "使用LangGraph实现智能体的状态图工作流编排。",
        "reference_context": "使用LangChain框架构建应用逻辑，使用LangGraph实现智能体的状态图StateGraph工作流编排，支持多步骤推理与决策路由。",
        "reference_docs": ["使用LangGraph实现智能体的状态图StateGraph工作流编排，支持多步骤推理与决策路由。"],
    },
    {
        "question": "智能体评估使用什么工具？",
        "expected_answer": "使用LangSmith和/或OpenEvals构建智能体评估体系。",
        "reference_context": "使用LangSmith和/或OpenEvals构建智能体的评估体系，支持对回答质量、推理路径合理性、工具调用准确性等维度进行系统化评测。",
        "reference_docs": ["使用LangSmith和/或OpenEvals构建智能体评估体系。"],
    },
]


def get_dataset() -> List[EvalSample]:
    """返回评估数据集。"""
    return RAG_EVAL_DATASET
