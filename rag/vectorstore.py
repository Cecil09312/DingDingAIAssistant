"""Chroma 向量库封装（多模态支持）。

提供向量库的初始化、文档入库与检索能力，
持久化到本地磁盘 data/chroma 目录。

支持文本和图像文档共存于同一 collection：
- 文本 Document: 通过 embed_documents 编码
- 图像 Document (metadata.image_path): 通过 embed_images 编码
"""

from typing import List, Tuple

from langchain_core.documents import Document

from rag.embeddings import get_embeddings


def get_vectorstore():
    """返回 Chroma 向量库单例（持久化模式）。"""
    from langchain_community.vectorstores import Chroma

    from config.settings import get_settings

    settings = get_settings()
    return Chroma(
        collection_name=settings.chroma_collection,
        embedding_function=get_embeddings(),
        persist_directory=str(settings.chroma_persist_path),
    )


def add_documents(docs: List[Document]) -> List[str]:
    """将文本文档列表加入向量库并持久化。

    Args:
        docs: 已切分的 Document 列表（文本块）

    Returns:
        新增文档的 id 列表
    """
    vs = get_vectorstore()
    ids = vs.add_documents(docs)
    # Chroma 0.5.x 需显式 persist
    try:
        vs.persist()
    except Exception:
        pass
    return ids


def add_image_documents(docs: List[Document]) -> List[str]:
    """将图像文档列表加入向量库并持久化。

    图像 Document 的 metadata 必须含 image_path。
    使用多模态 Embedding 的 embed_images 方法编码，
    手动写入 ChromaDB（绕过 Chroma 的文本 Embedding 路径）。

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

    # 获取 Chroma 底层 collection 直接写入
    vs = get_vectorstore()
    collection = vs._collection

    import uuid

    ids = []
    for i, doc in enumerate(docs):
        img_path = doc.metadata.get("image_path", "")
        doc_id = str(uuid.uuid4())
        # 构造 metadata（Chroma 不支持 None 值）
        meta = {k: str(v) for k, v in doc.metadata.items() if v is not None}
        collection.add(
            ids=[doc_id],
            embeddings=[image_vectors[i]],
            documents=[doc.page_content],
            metadatas=[meta],
        )
        ids.append(doc_id)

    try:
        vs.persist()
    except Exception:
        pass
    return ids


def search(query: str, k: int = 4) -> List[Tuple[Document, float]]:
    """带分数的语义检索（文本查询 → 检索文本+图像混合结果）。

    Args:
        query: 查询文本
        k: 返回 top-k 结果数

    Returns:
        (Document, score) 元组列表，score 为距离（越小越相关）
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
