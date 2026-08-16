---
kind: external_dependency
name: LangChain 框架生态
slug: langchain
category: external_dependency
category_hints:
    - framework_behavior
scope:
    - '**'
---

项目基于 LangChain 1.x 系列框架构建，包括 langchain、langchain-core、langchain-community、langchain-openai（用于千问 OpenAI 兼容接口）、langchain-text-splitters（文本切分）和 langchain-huggingface（HuggingFace Embedding）。这些组件构成了智能体的核心框架，负责 LLM 调用、RAG 管道、Embedding 模型管理等。通过 requirements.txt 明确依赖版本，确保框架稳定性。