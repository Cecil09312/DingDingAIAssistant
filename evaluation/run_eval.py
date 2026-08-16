"""评估运行入口 CLI。

执行流程：
1. 加载评估数据集
2. 对每个问题运行智能体获取回答
3. 运行 RAG 质量评估器 + 智能体评估器
4. 拉取 LangSmith trace 做路径合理性分析
5. 输出 JSON 报告 + 控制台汇总表格

用法：
    python -m evaluation.run_eval                  # 运行全部评估
    python -m evaluation.run_eval --no-agent       # 跳过智能体调用（使用数据集自带 answer）
    python -m evaluation.run_eval --output report.json
"""

import argparse
import json
import sys
import time
from pathlib import Path


def run_evaluation(run_agent: bool = True, output: str = "") -> dict:
    """运行完整评估流程，返回报告字典。"""
    from evaluation.datasets import get_dataset
    from evaluation.rag_eval import evaluate_batch
    from evaluation.agent_eval import (
        analyze_trace_paths,
        fetch_recent_traces,
        run_agent_eval,
    )

    dataset = get_dataset()
    samples = []
    print(f"[eval] 加载评估数据集，共 {len(dataset)} 个样本")

    # 步骤1: 运行智能体获取回答（可选）
    if run_agent:
        from agent.graph import get_compiled_graph

        graph = get_compiled_graph()
        print("[eval] 运行智能体获取回答...")
        for i, s in enumerate(dataset, 1):
            question = s["question"]
            print(f"  [{i}/{len(dataset)}] {question[:30]}...")
            try:
                config = {"configurable": {"thread_id": f"eval-{i}"}}
                result = graph.invoke(
                    {"user_input": question, "user_id": "eval-user", "session_id": f"eval-{i}"},
                    config=config,
                )
                answer = result.get("answer", "")
            except Exception as e:
                print(f"    智能体调用失败: {e}")
                answer = f"[智能体调用失败] {e}"
            samples.append({
                "question": question,
                "answer": answer,
                "expected_answer": s["expected_answer"],
                "reference_context": s["reference_context"],
                "reference_docs": s["reference_docs"],
            })
    else:
        # 用数据集自带期望答案作为 answer（用于纯评估器验证）
        for s in dataset:
            samples.append({
                "question": s["question"],
                "answer": s["expected_answer"],
                "expected_answer": s["expected_answer"],
                "reference_context": s["reference_context"],
                "reference_docs": s["reference_docs"],
            })

    # 步骤2: RAG 质量评估
    print("[eval] 运行 RAG 质量评估...")
    rag_results = evaluate_batch(samples)

    # 步骤3: 智能体评估
    print("[eval] 运行智能体评估...")
    agent_results = []
    for s in rag_results:
        agent_eval_res = run_agent_eval(s)
        agent_results.append({**s, "agent_evaluation": agent_eval_res})

    # 步骤4: LangSmith trace 路径分析
    print("[eval] 拉取 LangSmith trace 做路径合理性分析...")
    traces = fetch_recent_traces(limit=20)
    path_analysis = analyze_trace_paths(traces)

    # 汇总
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sample_count": len(dataset),
        "rag_evaluation": agent_results,
        "path_analysis": path_analysis,
        "summary": _summarize(agent_results),
    }

    # 输出
    _print_summary(report)
    if output:
        out_path = Path(output)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[eval] 报告已保存至 {out_path}")
    return report


def _summarize(results: list[dict]) -> dict:
    """汇总各评估维度的平均分。"""
    metrics = {}
    for r in results:
        rag = r.get("evaluation", {})
        agent = r.get("agent_evaluation", {})
        for cat, scores in (("rag", rag), ("agent", agent)):
            for name, res in scores.items():
                key = f"{cat}/{name}"
                score = res.get("score") if isinstance(res, dict) else None
                if score is not None:
                    metrics.setdefault(key, []).append(float(score))
    return {k: round(sum(v) / len(v), 3) for k, v in metrics.items() if v}


def _print_summary(report: dict):
    """控制台打印评估汇总表。"""
    print("\n" + "=" * 60)
    print("评估汇总报告")
    print("=" * 60)
    print(f"样本数: {report['sample_count']}")
    print(f"时间: {report['timestamp']}")
    pa = report.get("path_analysis", {})
    if pa:
        print(f"\n-- LangSmith 路径分析 --")
        print(f"  总 trace: {pa.get('total', 0)}")
        print(f"  成功: {pa.get('succeeded', 0)}  失败: {pa.get('failed', 0)}")
        print(f"  成功率: {pa.get('success_rate', 0)}")
    print(f"\n-- 各维度平均分 --")
    summary = report.get("summary", {})
    if summary:
        for k, v in summary.items():
            print(f"  {k:30s}  {v}")
    else:
        print("  (无可用评分，请检查 LLM 配置)")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="智能体评估运行工具")
    parser.add_argument("--no-agent", action="store_true", help="跳过智能体调用，使用数据集自带答案")
    parser.add_argument("--output", "-o", default="", help="评估报告输出路径(JSON)")
    args = parser.parse_args()
    run_evaluation(run_agent=not args.no_agent, output=args.output)


if __name__ == "__main__":
    sys.exit(0)
