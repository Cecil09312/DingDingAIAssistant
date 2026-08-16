"""RAG 重排序模块：使用 BAAI/bge-reranker-base CrossEncoder 精排检索结果。

向量检索（双塔架构）速度快但精度有限，CrossEncoder（交叉编码器）将
query 与候选文档逐对拼接输入模型，精度更高但计算量更大。
两阶段流程：向量检索召回 top-N 候选 → CrossEncoder 精排取 top-k。

模型通过 HuggingFace 加载（已配置 hf-mirror.com 镜像加速），
使用 sentence_transformers.CrossEncoder 封装，延迟初始化单例。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import List, Tuple

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# 设置 HuggingFace 镜像端点（国内网络加速下载），优先尊重用户已设值
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# 模块级 CrossEncoder 单例（延迟初始化）
_model = None


def get_reranker():
    """返回 CrossEncoder 单例（首次调用从镜像下载模型）。

    模型：BAAI/bge-reranker-base（默认），中文优化，~1.1GB
    """
    global _model
    if _model is not None:
        return _model

    from sentence_transformers import CrossEncoder

    from config.settings import get_settings

    s = get_settings()
    logger.info("正在加载重排序模型: %s (device=%s)", s.rerank_model, s.rerank_device)
    _model = CrossEncoder(
        s.rerank_model,
        device=s.rerank_device,
        max_length=512,
    )
    logger.info("重排序模型加载完成")
    return _model


def rerank(
    query: str,
    docs: List[Tuple[Document, float]],
    top_k: int = 4,
) -> List[Tuple[Document, float]]:
    """对检索结果用 CrossEncoder 重新打分排序，返回 top-k。

    Args:
        query: 查询文本
        docs: 向量检索返回的 (Document, score) 候选列表
        top_k: 重排序后返回的数量

    Returns:
        (Document, rerank_score) 列表，rerank_score 越大越相关
        失败时回退返回原始 docs[:top_k]
    """
    if not docs:
        return []

    if len(docs) <= top_k:
        # 候选数不足以重排，直接返回
        return docs[:top_k]

    try:
        model = get_reranker()

        # 构造 query-doc 对
        pairs = [[query, doc.page_content] for doc, _ in docs]

        # CrossEncoder 打分（返回 float 列表，分数越高越相关）
        scores = model.predict(pairs, convert_to_numpy=True)

        # 组装并按分数降序排序
        scored = list(zip(docs, scores))
        scored.sort(key=lambda x: x[1], reverse=True)

        # 取 top-k，返回 (Document, rerank_score)
        result = [(doc_tuple[0], float(score)) for doc_tuple, score in scored[:top_k]]
        logger.info("重排序完成: %d 候选 → top-%d", len(docs), top_k)
        return result

    except Exception as e:
        logger.warning("重排序失败，回退到原始向量检索结果: %s", e)
        return docs[:top_k]
