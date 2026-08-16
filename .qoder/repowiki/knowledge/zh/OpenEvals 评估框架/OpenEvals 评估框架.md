---
kind: external_dependency
name: OpenEvals 评估框架
slug: openevals
category: external_dependency
category_hints:
    - sdk_real_api
scope:
    - '**'
---

用于 RAG 质量和智能体回答质量的自动化评估，支持 retrieval_relevance（检索相关性）、groundedness（忠实度）、helpfulness（帮助度）、answer_relevance（答案相关性）等维度。通过 create_llm_as_judge 和预置 Prompt 实现自动化评测，输出 score 和 reasoning。与 LangSmith 结合使用，提供完整的评估体系。