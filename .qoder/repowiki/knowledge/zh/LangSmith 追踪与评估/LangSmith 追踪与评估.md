---
kind: external_dependency
name: LangSmith 追踪与评估
slug: langsmith
category: external_dependency
category_hints:
    - vendor_identity
scope:
    - '**'
---

LangChain 官方的追踪和评估平台，用于收集智能体运行 trace 数据，分析推理路径合理性、成功率统计等。通过 LANGSMITH_API_KEY 和 LANGSMITH_PROJECT 配置接入。在评估模块中与 OpenEvals 互补使用，提供更全面的智能体质量分析能力。未配置时自动跳过相关功能。