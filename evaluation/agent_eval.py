"""智能体评估模块。

使用 OpenEvals 评估回答质量（correctness、hallucination）与推理路径合理性（plan_adherence），
并结合 LangSmith 客户端做 trace 拉取与数据集管理，实现二者互补评估。

评估维度：
1. 正确性 (correctness)：回答是否与参考答案一致
2. 幻觉检测 (hallucination)：回答是否包含编造内容
3. 推理路径合理性 (plan_adherence)：智能体是否按预期步骤推进
"""

from typing import Optional

from openevals.llm import create_llm_as_judge
from openevals.prompts import CORRECTNESS_PROMPT, HALLUCINATION_PROMPT, PLAN_ADHERENCE_PROMPT


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


def build_agent_evaluators():
    """构建智能体评估器集合。"""
    return {
        "correctness": create_llm_as_judge(
            prompt=CORRECTNESS_PROMPT,
            judge=_judge_llm(),
            feedback_key="correctness",
            continuous=True,
        ),
        "hallucination": create_llm_as_judge(
            prompt=HALLUCINATION_PROMPT,
            judge=_judge_llm(),
            feedback_key="hallucination",
            continuous=True,
        ),
        "plan_adherence": create_llm_as_judge(
            prompt=PLAN_ADHERENCE_PROMPT,
            judge=_judge_llm(),
            feedback_key="plan_adherence",
            continuous=True,
        ),
    }


def run_agent_eval(sample: dict) -> dict:
    """对单个样本运行全部智能体评估器。"""
    evaluators = build_agent_evaluators()
    # 各评估器所需的 prompt 变量映射（prompt 变量名 -> sample 字段名）
    call_kwargs = {
        "correctness": {
            "inputs": sample["question"],
            "outputs": sample["answer"],
            "reference_outputs": sample["expected_answer"],
        },
        "hallucination": {
            "inputs": sample["question"],
            "outputs": sample["answer"],
            "context": sample["reference_context"],
            "reference_outputs": sample["expected_answer"],
        },
        "plan_adherence": {
            "inputs": sample["question"],
            "outputs": sample["answer"],
        },
    }
    results = {}
    for name, evaluator in evaluators.items():
        try:
            res = evaluator(**call_kwargs[name])
            results[name] = res
        except Exception as e:
            results[name] = {"score": None, "comment": f"评估失败: {e}", "error": True}
    return results


def get_langsmith_client():
    """获取 LangSmith 客户端（用于 trace 拉取与数据集管理）。

    需要 LANGSMITH_API_KEY 配置，不可用时返回 None。
    """
    from config.settings import get_settings

    s = get_settings()
    if not s.langsmith_api_key:
        print("[langsmith] 未配置 LANGSMITH_API_KEY，跳过 LangSmith 集成")
        return None
    try:
        from langsmith import Client

        return Client(
            api_key=s.langsmith_api_key,
            api_url=s.langsmith_endpoint,
        )
    except Exception as e:
        print(f"[langsmith] 客户端初始化失败: {e}")
        return None


def fetch_recent_traces(limit: int = 10):
    """从 LangSmith 拉取最近的运行 trace，用于路径合理性分析。"""
    client = get_langsmith_client()
    if client is None:
        return []
    try:
        from config.settings import get_settings

        s = get_settings()
        traces = list(client.list_runs(
            project_name=s.langsmith_project,
            limit=limit,
            is_root=True,
        ))
        return [
            {
                "run_id": str(t.id),
                "name": t.name,
                "status": t.status,
                "inputs": t.inputs,
                "outputs": t.outputs,
                "error": t.error,
            }
            for t in traces
        ]
    except Exception as e:
        print(f"[langsmith] 拉取 trace 失败: {e}")
        return []


def analyze_trace_paths(traces: list[dict]) -> dict:
    """分析智能体推理路径合理性（基于 trace 的节点执行情况）。"""
    if not traces:
        return {"total": 0, "succeeded": 0, "failed": 0, "success_rate": 0.0}
    total = len(traces)
    succeeded = sum(1 for t in traces if t.get("status") == "success")
    failed = total - succeeded
    return {
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "success_rate": round(succeeded / total, 3) if total else 0.0,
    }
