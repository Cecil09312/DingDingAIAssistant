---
kind: external_dependency
name: LangGraph 工作流编排
slug: langgraph
category: external_dependency
category_hints:
    - framework_behavior
scope:
    - '**'
---

使用 LangGraph 1.x 进行智能体状态图工作流编排，通过 StateGraph 实现多步骤推理与决策路由。短期记忆使用 langgraph-checkpoint 的 MemorySaver（进程内），替代了之前讨论中的 RedisSaver。LangGraph 负责情感分析→路由判断→RAG检索/生成→记忆更新的完整工作流编排。