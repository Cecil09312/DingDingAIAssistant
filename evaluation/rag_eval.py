"""RAG 质量评估模块。

使用 OpenEvals 的 create_llm_as_judge + 预置 RAG 评估 prompt，
对检索相关性、答案忠实度（groundedness）、帮助度、答案相关性进行自动化评测。

评估维度：
1. 检索相关性 (retrieval_relevance)：检索到的文档是否与问题相关
2. 忠实度 (groundedness)：答案是否基于检索到的上下文（无幻觉）
3. 帮助度 (helpfulness)：回答是否对用户有帮助
4. 答案相关性 (answer_relevance)：回答是否切题
"""

from typing import Any

from openevals.llm import create_llm_as_judge
from openevals.prompts import (
    ANSWER_RELEVANCE_PROMPT,
    RAG_GROUNDEDNESS_PROMPT,
    RAG_HELPFULNESS_PROMPT,
    RAG_RETRIEVAL_RELEVANCE_PROMPT,
)


def _judge_model():
    """获取评判模型标识。"""
    from config.settings import get_settings

    s = get_settings()
    return s.llm_judge_model


def _judge_llm():
    """获取评判 LLM 实例。"""
    from langchain_openai import ChatOpenAI

    from config.settings import get_settings

    s = get_settings()
    return ChatOpenAI(
        model=s.llm_judge_model,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        temperature=0.0,
    )


def _make_evaluator(prompt, feedback_key):
    """构建一个 OpenEvals LLM 评判器。

    Args:
        prompt: openevals.prompts 中的预置 prompt 模板
        feedback_key: 评估结果存储键名
    """
    return create_llm_as_judge(
        prompt=prompt,
        judge=_judge_llm(),
        feedback_key=feedback_key,
        continuous=True,
    )


def build_rag_evaluators():
    """构建并返回一组 RAG 质量评估器。"""
    return {
        "retrieval_relevance": _make_evaluator(
            RAG_RETRIEVAL_RELEVANCE_PROMPT,
            "retrieval_relevance",
        ),
        "groundedness": _make_evaluator(
            RAG_GROUNDEDNESS_PROMPT,
            "groundedness",
        ),
        "helpfulness": _make_evaluator(
            RAG_HELPFULNESS_PROMPT,
            "helpfulness",
        ),
        "answer_relevance": _make_evaluator(
            ANSWER_RELEVANCE_PROMPT,
            "answer_relevance",
        ),
    }


def run_rag_eval(sample: dict) -> dict:
    """对单个样本运行全部 RAG 评估器。

    Args:
        sample: 含 question, answer, reference_context, reference_docs 的字典

    Returns:
        {评估器名: {score, comment}} 的结果字典
    """
    evaluators = build_rag_evaluators()
    # 各评估器所需的 prompt 变量映射（prompt 变量名 -> sample 字段名）
    call_kwargs = {
        "retrieval_relevance": {"inputs": sample["question"], "context": sample["reference_docs"]},
        "groundedness": {"outputs": sample["answer"], "context": sample["reference_context"]},
        "helpfulness": {"inputs": sample["question"], "outputs": sample["answer"]},
        "answer_relevance": {"inputs": sample["question"], "outputs": sample["answer"]},
    }
    results = {}
    for name, evaluator in evaluators.items():
        try:
            res = evaluator(**call_kwargs[name])
            results[name] = res
        except Exception as e:
            results[name] = {"score": None, "comment": f"评估失败: {e}", "error": True}
    return results


def evaluate_batch(samples: list[dict]) -> list[dict]:
    """批量评估，返回每个样本的评估结果。"""
    out = []
    for i, s in enumerate(samples, 1):
        print(f"[rag_eval] 评估样本 {i}/{len(samples)}: {s.get('question', '')[:30]}...")
        eval_result = run_rag_eval(s)
        out.append({**s, "evaluation": eval_result})
    return out
