---
kind: external_dependency
name: HuggingFace 模型生态
slug: huggingface
category: external_dependency
category_hints:
    - client_constraint
scope:
    - '**'
---

使用 HuggingFace Hub 管理模型，通过 sentence-transformers 加载本地 Embedding 模型 bge-small-zh-v1.5。由于国内网络环境限制，默认配置 HF_ENDPOINT=https://hf-mirror.com 镜像端点加速模型下载。在 main.py 最顶部设置环境变量确保所有导入前生效，避免连接 huggingface.co 超时问题。支持离线模式（HF_HUB_OFFLINE=1）使用已缓存模型。