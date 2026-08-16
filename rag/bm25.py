"""BM25 关键词检索（内存索引，与向量检索互补）。

从 Chroma 向量库拉取全部文本文档构建 BM25 索引，
检索时返回 (Document, score) 列表，score 越大越相关。

中文分词采用字符级切分，无需额外分词依赖；
索引为进程内单例，内存模式下重启需重建（与向量库预热同步）。
"""

import logging
from typing import List, Tuple

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# 进程内单例：BM25 索引与对应的文档列表
_bm25_index = None
_bm25_docs: List[Document] = []


def get_bm25_index():
    """返回 BM25 索引单例（从向量库拉取文本文档构建）。

    Returns:
        (bm25_index, docs) 元组；向量库为空时返回 (None, [])
    """
    global _bm25_index, _bm25_docs
    if _bm25_index is not None:
        return _bm25_index, _bm25_docs

    try:
        from rag.vectorstore import get_vectorstore

        vs = get_vectorstore()
        collection = vs._collection
        result = collection.get()
    except Exception as e:
        logger.warning("[bm25] 从向量库拉取文档失败: %s", e)
        return None, []

    _bm25_docs = []
    texts: List[str] = []
    documents = result.get("documents", []) or []
    metadatas = result.get("metadatas", []) or []
    for doc_text, meta in zip(documents, metadatas):
        # 跳过图像文档（page_content 为占位描述，无检索价值）
        if meta and meta.get("type") == "image":
            continue
        if not doc_text or not doc_text.strip():
            continue
        _bm25_docs.append(Document(page_content=doc_text, metadata=meta or {}))
        texts.append(doc_text)

    if not texts:
        logger.info("[bm25] 向量库无文本文档，BM25 索引为空")
        return None, []

    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning("[bm25] rank_bm25 未安装，BM25 检索不可用（pip install rank-bm25）")
        return None, []

    # 中文按字符级切分（兼顾中英文混合，无需 jieba 依赖）
    tokenized = [list(t) for t in texts]
    _bm25_index = BM25Okapi(tokenized)
    logger.info("[bm25] 索引构建完成，共 %d 个文档", len(texts))
    return _bm25_index, _bm25_docs


def bm25_search(query: str, k: int = 10) -> List[Tuple[Document, float]]:
    """BM25 关键词检索，返回 (Document, score) 列表（score 越大越相关）。

    Args:
        query: 查询文本
        k: 返回 top-k 结果数

    Returns:
        (Document, score) 元组列表；索引未构建或无命中时返回空列表
    """
    index, docs = get_bm25_index()
    if index is None or not docs:
        return []

    # 查询同样按字符级切分
    tokenized_query = list(query)
    if not tokenized_query:
        return []

    try:
        scores = index.get_scores(tokenized_query)
    except Exception as e:
        logger.warning("[bm25] 检索失败: %s", e)
        return []

    # 按分数降序取 top-k，过滤零分文档（无任何关键词命中）
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    result = [(docs[i], float(s)) for i, s in ranked[:k] if s > 0]
    return result


def reset_bm25_index() -> None:
    """重置 BM25 索引单例（向量库重建后调用，使其下次检索时重新构建）。"""
    global _bm25_index, _bm25_docs
    _bm25_index = None
    _bm25_docs = []
    logger.info("[bm25] 索引已重置")
