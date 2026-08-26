"""Milvus 向量库封装（多模态支持，Milvus Lite 本地文件持久化）。

提供向量库的初始化、文档入库与检索能力，
持久化到本地磁盘 data/milvus/milvus_lite.db 文件（Milvus Lite，无需独立部署服务）。

支持文本和图像文档共存于同一 collection：
- 文本 Document: 通过 embed_documents 编码
- 图像 Document (metadata.image_path): 通过 embed_images 编码
"""

import logging
import uuid
from typing import List, Tuple

from langchain_core.documents import Document

from rag.embeddings import get_embeddings

logger = logging.getLogger(__name__)

# 模块级 LangChain Milvus 向量库单例（延迟初始化，避免启动时连接）
_vs = None


def get_vectorstore():
    """返回 LangChain Milvus 向量库单例（Milvus Lite 本地文件持久化）。

    使用 langchain_milvus.Milvus 封装，connection_args.uri 指向本地 .db 文件
    即启用 Milvus Lite，无需独立部署 Milvus 服务。
    启用 enable_dynamic_field 以支持文本/图像异构 metadata 共存于同一 collection。
    """
    global _vs
    if _vs is None:
        from langchain_milvus import Milvus

        from config.settings import get_settings

        settings = get_settings()
        _vs = Milvus(
            collection_name=settings.milvus_collection,
            embedding_function=get_embeddings(),
            connection_args={"uri": str(settings.milvus_db_path)},
            index_params={
                "index_type": settings.milvus_index_type,
                "metric_type": settings.milvus_metric_type,
            },
            # 主键为 varchar，手动传入 uuid 作为 pk
            auto_id=False,
            # 启用动态字段，metadata 每个 key 作为独立动态字段存储，
            # 支持文本/图像等异构 metadata 共存
            enable_dynamic_field=True,
            drop_old=False,
        )
        logger.info(
            "Milvus 向量库初始化完成 (collection=%s, uri=%s, index=%s, metric=%s)",
            settings.milvus_collection,
            settings.milvus_db_path,
            settings.milvus_index_type,
            settings.milvus_metric_type,
        )
    return _vs


def add_documents(docs: List[Document]) -> List[str]:
    """将文本文档列表加入向量库并持久化。

    Args:
        docs: 已切分的 Document 列表（文本块）

    Returns:
        新增文档的 id 列表
    """
    vs = get_vectorstore()
    # 生成 varchar 主键（uuid4 字符串），手动管理 pk
    ids = [str(uuid.uuid4()) for _ in docs]
    # 过滤 metadata 中的 None 值（Milvus 动态字段不支持 None）
    clean_docs = []
    for d in docs:
        meta = {k: v for k, v in d.metadata.items() if v is not None}
        clean_docs.append(Document(page_content=d.page_content, metadata=meta))
    vs.add_documents(clean_docs, ids=ids)
    # Milvus 需显式 flush 确保写入磁盘并可查询
    try:
        from config.settings import get_settings

        vs.client.flush(get_settings().milvus_collection)
    except Exception as e:
        logger.warning("Milvus flush 失败（不影响内存可用）: %s", e)
    return ids


def add_image_documents(docs: List[Document]) -> List[str]:
    """将图像文档列表加入向量库并持久化。

    图像 Document 的 metadata 必须含 image_path。
    使用多模态 Embedding 的 embed_images 方法编码，
    手动调用 MilvusClient.insert 写入（绕过 LangChain 的文本 embed_documents 路径）。

    Args:
        docs: 图像 Document 列表

    Returns:
        新增文档的 id 列表
    """
    if not docs:
        return []

    embeddings_fn = get_embeddings()

    # 提取图像路径并编码
    image_paths = [d.metadata["image_path"] for d in docs if "image_path" in d.metadata]
    if not image_paths:
        return []

    image_vectors = embeddings_fn.embed_images(image_paths)

    # 获取 LangChain Milvus 实例，用底层 MilvusClient 直接写入图像向量
    vs = get_vectorstore()
    from config.settings import get_settings

    settings = get_settings()

    ids = []
    entities = []
    for i, doc in enumerate(docs):
        doc_id = str(uuid.uuid4())
        # 构造 metadata（Milvus 动态字段不支持 None，值统一转字符串）
        meta = {k: str(v) for k, v in doc.metadata.items() if v is not None}
        # entity 字段名与 LangChain Milvus 默认 schema 一致：pk / text / vector + 动态 metadata
        entity = {
            "pk": doc_id,
            "text": doc.page_content,
            "vector": image_vectors[i],
        }
        # metadata 作为动态字段直接展开到 entity
        entity.update(meta)
        entities.append(entity)
        ids.append(doc_id)

    # 批量插入图像实体（含预计算的图像向量，绕过文本 Embedding 路径）
    vs.client.insert(settings.milvus_collection, entities)
    vs.client.flush(settings.milvus_collection)
    return ids


def search(query: str, k: int = 4) -> List[Tuple[Document, float]]:
    """带分数的语义检索（文本查询 → 检索文本+图像混合结果）。

    Args:
        query: 查询文本
        k: 返回 top-k 结果数

    Returns:
        (Document, score) 元组列表，score 为 L2 距离（越小越相关）
    """
    vs = get_vectorstore()
    from config.settings import get_settings

    settings = get_settings()
    results = vs.similarity_search_with_score(query, k=k or settings.rag_top_k)
    # 过滤低相关度结果（距离大于阈值则丢弃）
    filtered = [
        (doc, score) for doc, score in results if score <= settings.rag_score_filter
    ]
    # 若过滤后为空，回退返回原始结果（保证至少有内容）
    return filtered if filtered else results


def get_retriever(k: int = 4):
    """返回 LangChain retriever 对象，便于在链中使用。"""
    vs = get_vectorstore()
    return vs.as_retriever(search_kwargs={"k": k})


def count_documents() -> int:
    """返回 collection 中的文档数（用于启动时检查是否需要自动入库）。

    通过 MilvusClient.get_collection_stats 获取 row_count。
    """
    from config.settings import get_settings

    settings = get_settings()
    vs = get_vectorstore()
    if not vs.client.has_collection(settings.milvus_collection):
        return 0
    stats = vs.client.get_collection_stats(settings.milvus_collection)
    return int(stats.get("row_count", 0))


def drop_collection() -> None:
    """删除当前 collection（用于 --rebuild 重建索引时清空旧数据）。

    同时重置模块级单例，确保下次 get_vectorstore 重新初始化。
    """
    from config.settings import get_settings

    settings = get_settings()
    vs = get_vectorstore()
    if vs.client.has_collection(settings.milvus_collection):
        vs.client.drop_collection(settings.milvus_collection)
        logger.info("已删除 Milvus collection: %s", settings.milvus_collection)
    # 重置单例，使下次访问重新创建 collection
    global _vs
    _vs = None


def get_all_text_documents() -> List[Tuple[str, dict]]:
    """拉取 collection 中全部文档的文本与 metadata（用于 BM25 索引构建）。

    Returns:
        (text, metadata) 元组列表；跳过图像文档（type=image）
    """
    from config.settings import get_settings

    settings = get_settings()
    vs = get_vectorstore()
    if not vs.client.has_collection(settings.milvus_collection):
        return []

    # MilvusClient.query：filter 为空表示返回全部，output_fields=None 返回除 vector 外所有字段
    # limit 通过 kwargs 传入，足够大以覆盖知识库规模
    result = vs.client.query(
        settings.milvus_collection,
        filter="",
        output_fields=None,
        limit=16384,
    )

    docs = []
    for item in result:
        text = item.get("text", "")
        if not text or not text.strip():
            continue
        # 排除 pk 与 text，剩余字段作为 metadata
        meta = {k: v for k, v in item.items() if k not in ("text", "pk")}
        # 跳过图像文档（page_content 为占位描述，无关键词检索价值）
        if meta.get("type") == "image":
            continue
        docs.append((text, meta))
    return docs
