"""多模态 Embedding：Jina CLIP v2，文本+图像统一向量空间。

使用 jinaai/jina-clip-v2 多模态模型，实现文本与图像在同一向量空间编码，
支持跨模态检索（用文本查询检索图像，或用图像查询检索文本）。

模型通过 HuggingFace 加载（已配置 hf-mirror.com 镜像加速），
实现 langchain Embeddings 接口（embed_documents/embed_query）用于文本编码，
额外提供 embed_images / embed_query_image 方法用于图像编码。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Union

from langchain_core.embeddings import Embeddings
from PIL import Image

logger = logging.getLogger(__name__)

# 设置 HuggingFace 镜像端点（国内网络加速下载），优先尊重用户已设值
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"


class JinaClipEmbeddings(Embeddings):
    """Jina CLIP v2 多模态 Embedding 封装。

    实现 langchain Embeddings 接口（embed_documents/embed_query），
    额外提供 embed_images / embed_query_image 方法。
    文本和图像映射到同一向量空间，支持跨模态检索。

    向量维度：1024（Jina CLIP v2 统一输出维度）
    """

    def __init__(
        self,
        model_name: str = "jinaai/jina-clip-v2",
        device: str = "cpu",
    ):
        self.model_name = model_name
        self.device = device
        self._model = None
        self._processor = None

    def _ensure_model(self):
        """延迟加载模型（避免初始化时即下载，首次调用时加载）。"""
        if self._model is not None:
            return
        import torch
        from transformers import AutoModel, AutoProcessor

        logger.info("正在加载多模态 Embedding 模型: %s (device=%s)", self.model_name, self.device)
        self._processor = AutoProcessor.from_pretrained(
            self.model_name, trust_remote_code=True
        )
        self._model = AutoModel.from_pretrained(
            self.model_name, trust_remote_code=True
        )
        self._model = self._model.to(self.device)
        self._model.eval()
        logger.info("多模态 Embedding 模型加载完成")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """文本批量编码（langchain Embeddings 接口）。

        Args:
            texts: 文本列表

        Returns:
            向量列表，每个向量是 List[float]（已 L2 归一化）
        """
        self._ensure_model()
        import torch

        with torch.no_grad():
            inputs = self._processor(
                text=texts,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=8192,
            ).to(self.device)
            outputs = self._model.get_text_features(**inputs)
            # L2 归一化
            embeddings = outputs / outputs.norm(dim=-1, keepdim=True)
            return embeddings.cpu().float().numpy().tolist()

    def embed_query(self, text: str) -> List[float]:
        """查询文本编码（langchain Embeddings 接口）。"""
        return self.embed_documents([text])[0]

    def embed_images(self, images: List[Union[Image.Image, str, Path]]) -> List[List[float]]:
        """图像批量编码。

        Args:
            images: PIL.Image 对象列表，或图片文件路径列表

        Returns:
            向量列表，每个向量是 List[float]（已 L2 归一化）
        """
        self._ensure_model()
        import torch

        # 转换路径为 PIL Image
        pil_images = []
        for img in images:
            if isinstance(img, (str, Path)):
                pil_images.append(Image.open(img).convert("RGB"))
            else:
                pil_images.append(img.convert("RGB") if img.mode != "RGB" else img)

        with torch.no_grad():
            inputs = self._processor(
                images=pil_images,
                return_tensors="pt",
            ).to(self.device)
            outputs = self._model.get_image_features(**inputs)
            # L2 归一化
            embeddings = outputs / outputs.norm(dim=-1, keepdim=True)
            return embeddings.cpu().float().numpy().tolist()

    def embed_query_image(self, image: Union[Image.Image, str, Path]) -> List[float]:
        """查询图像编码（单张图片）。"""
        return self.embed_images([image])[0]


class BgeEmbeddings(Embeddings):
    """BGE 纯文本 Embedding 封装（bge-small-zh-v1.5）。

    使用 sentence-transformers 加载，向量维度 512。
    比 Jina CLIP v2 快约 30 倍（CPU），适合纯文本知识库场景。
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._model = None

    def _ensure_model(self):
        """延迟加载模型（避免初始化时即下载，首次调用时加载）。"""
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        logger.info("正在加载纯文本 Embedding 模型: %s (device=%s)", self.model_name, self.device)
        self._model = SentenceTransformer(self.model_name, device=self.device)
        logger.info("纯文本 Embedding 模型加载完成")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """文本批量编码（langchain Embeddings 接口，已 L2 归一化）。"""
        self._ensure_model()
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> List[float]:
        """查询文本编码（langchain Embeddings 接口）。"""
        self._ensure_model()
        return self._model.encode(text, normalize_embeddings=True).tolist()


@lru_cache
def get_embeddings() -> Embeddings:
    """返回 Embedding 单例，根据配置自动选择 BGE 或 Jina。

    - 模型名含 "bge" 时使用 BgeEmbeddings（sentence-transformers，纯文本，快速）
    - 否则使用 JinaClipEmbeddings（多模态，文本+图像）
    """
    from config.settings import get_settings

    settings = get_settings()
    device = getattr(settings, "embedding_device", "cpu")
    model_name = settings.embedding_model

    if "bge" in model_name.lower():
        return BgeEmbeddings(model_name=model_name, device=device)
    return JinaClipEmbeddings(model_name=model_name, device=device)
