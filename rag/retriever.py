"""检索器：向量检索 + 重排序 + 上下文格式化。

两阶段检索流程：
1. 向量检索（Milvus + Embedding 模型）召回 top-N 候选
2. CrossEncoder（BAAI/bge-reranker-base）对候选精排，取 top-k
3. 格式化为上下文字符串供生成节点使用

可通过配置 RERANK_ENABLED=false 关闭重排序，回退为直接向量检索 top-k。
"""

from typing import List, Tuple

from langchain_core.documents import Document

from rag.vectorstore import search


def retrieve(query: str, k: int = 4) -> List[Tuple[Document, float]]:
    """对 query 进行语义检索（含多路召回与重排序），返回 (文档, 分数) 列表。

    流程：
    - BM25 开启：向量检索 + BM25 并行召回 → RRF 融合 →（可选）重排序 → top-k
    - BM25 关闭 + 重排序开启：向量检索 top-N 候选 → CrossEncoder 精排 → top-k
    - BM25 关闭 + 重排序关闭：直接向量检索 top-k
    """
    from config.settings import get_settings

    settings = get_settings()

    # 多路召回：BM25 开启时向量检索 + BM25 并行，RRF 融合
    if settings.rag_bm25_enabled:
        candidates = _hybrid_recall(query, settings)
    elif settings.rerank_enabled:
        # 原有逻辑：仅向量检索召回候选
        candidates = search(query, k=settings.rerank_candidate_count)
    else:
        # 重排序关闭，直接向量检索 top-k
        return search(query, k=k)

    if not candidates:
        return []

    # 重排序（对融合或向量召回的候选精排）
    if settings.rerank_enabled:
        from rag.reranker import rerank

        final_k = k or settings.rerank_top_k
        return rerank(query, candidates, top_k=final_k)
    else:
        # 无重排序时取 top-k（RRF 融合已排序，或向量检索原始排序）
        final_k = k or settings.rag_top_k
        return candidates[:final_k]


def _hybrid_recall(query: str, settings) -> List[Tuple[Document, float]]:
    """多路召回：向量检索 + BM25，RRF 融合后返回候选列表。

    两路独立召回，任一路为空时回退到另一路的结果。
    """
    from rag.bm25 import bm25_search

    # 向量检索召回（复用 vectorstore.search，含分数过滤）
    vec_candidates = search(query, k=settings.rerank_candidate_count)
    # BM25 关键词召回
    bm25_candidates = bm25_search(query, k=settings.rag_bm25_candidate_count)

    if not vec_candidates and not bm25_candidates:
        return []
    if not bm25_candidates:
        return vec_candidates
    if not vec_candidates:
        return bm25_candidates

    # RRF 融合两路结果
    return _rrf_fusion(vec_candidates, bm25_candidates, settings.rag_rrf_k)


def _rrf_fusion(
    vec_results: List[Tuple[Document, float]],
    bm25_results: List[Tuple[Document, float]],
    rrf_k: int,
) -> List[Tuple[Document, float]]:
    """RRF (Reciprocal Rank Fusion) 融合两路检索结果。

    公式：score(d) = sum(1 / (rrf_k + rank))，rank 从 1 开始。
    仅依赖排名而非原始分数，可统一向量距离（越小越好）与 BM25 分数（越大越好）。
    内容相同的 chunk 合并分数（两路都命中的文档得分更高）。
    """
    scores: dict = {}
    docs_map: dict = {}

    # 向量检索结果按 rank 融合
    for rank, (doc, _) in enumerate(vec_results, 1):
        key = doc.page_content
        if key not in scores:
            scores[key] = 0.0
            docs_map[key] = doc
        scores[key] += 1.0 / (rrf_k + rank)

    # BM25 检索结果按 rank 融合
    for rank, (doc, _) in enumerate(bm25_results, 1):
        key = doc.page_content
        if key not in scores:
            scores[key] = 0.0
            docs_map[key] = doc
        scores[key] += 1.0 / (rrf_k + rank)

    # 按融合分数降序排序
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(docs_map[key], score) for key, score in ranked]


def format_context(docs: List[Tuple[Document, float]]) -> str:
    """将检索结果格式化为上下文字符串（含来源标记与相关度）。

    注意：重排序后的分数是 CrossEncoder 分数（越大越相关），
    向量检索的分数是距离（越小越相关）。
    为统一展示，使用 1/(1+|score|) 将两者归一化到 0~1 区间（越大越相关）。
    """
    if not docs:
        return ""
    blocks = []
    for i, (doc, score) in enumerate(docs, 1):
        source = doc.metadata.get("source", "未知来源")
        title = doc.metadata.get("title", "")
        header = f"[片段{i}] 来源: {source}"
        if title:
            header += f" | 标题: {title}"
        # 统一归一化：分数绝对值越小越相关 → 1/(1+|score|)
        relevance = 1 / (1 + abs(score))
        header += f" | 相关度: {relevance:.3f}"
        blocks.append(f"{header}\n{doc.page_content}")
    return "\n\n".join(blocks)


def retrieve_context(query: str, k: int = 4) -> str:
    """一站式：检索（含重排序）并格式化，直接返回上下文字符串。"""
    docs = retrieve(query, k=k)
    return format_context(docs)
