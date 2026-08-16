---
kind: external_dependency
name: ChromaDB 向量数据库
slug: chromadb
category: external_dependency
category_hints:
    - vendor_identity
scope:
    - '**'
---

作为项目的向量数据库后端，存储文档的语义向量表示。配置在 config/settings.py 中设置持久化路径 data/chroma 和集合名称 dingtalk_kb。支持 similarity_search_with_score 语义检索，配合 bge-small-zh-v1.5 Embedding 模型实现中文语义搜索。数据持久化到本地磁盘，无需外部服务。