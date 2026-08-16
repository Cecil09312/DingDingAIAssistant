"""模型预下载脚本（Docker 构建期使用）。

依次触发运行时所需全部模型的下载与加载校验：
1. 多模态 Embedding（jinaai/jina-clip-v2，AutoModel + AutoProcessor）
2. 重排序 CrossEncoder（BAAI/bge-reranker-base）
3. easyocr 中英文识别模型

任一步骤失败则以非 0 退出码终止，使 Docker 构建失败，避免产出坏镜像。

用法：
    python scripts/prefetch_models.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 保证从项目根目录可导入项目模块（scripts/ 为根目录子目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# HuggingFace 镜像加速（尊重外部已设置的值）
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"


def prefetch_embedding() -> None:
    """下载并加载多模态 Embedding 模型，执行一次文本编码校验。"""
    from config.settings import get_settings
    from rag.embeddings import JinaClipEmbeddings

    s = get_settings()
    emb = JinaClipEmbeddings(model_name=s.embedding_model, device=s.embedding_device)
    vec = emb.embed_query("预下载校验")
    assert len(vec) > 0, "Embedding 输出为空"
    print(f"[prefetch] OK embedding ({s.embedding_model}, dim={len(vec)})")


def prefetch_reranker() -> None:
    """下载并加载重排序 CrossEncoder，执行一次打分校验。"""
    from config.settings import get_settings

    s = get_settings()
    if not s.rerank_enabled:
        print("[prefetch] SKIP reranker (rerank_enabled=false)")
        return
    from sentence_transformers import CrossEncoder

    model = CrossEncoder(s.rerank_model, device=s.rerank_device, max_length=512)
    score = model.predict([["预下载校验", "这是一段候选文本"]], convert_to_numpy=True)
    assert score is not None, "CrossEncoder 打分失败"
    print(f"[prefetch] OK reranker ({s.rerank_model})")


def prefetch_ocr() -> None:
    """下载并初始化 easyocr 模型，执行一次识别校验。

    easyocr 模型从 GitHub releases 下载，国内网络不稳定，
    可能出现 Connection reset by peer，重试几次提高成功率。
    """
    import time

    from rag.ocr import get_reader

    last_err = None
    for attempt in range(1, 4):
        try:
            reader = get_reader()
            assert reader is not None, "easyocr Reader 初始化失败"
            print("[prefetch] OK easyocr")
            return
        except Exception as e:
            last_err = e
            if attempt < 3:
                print(f"[prefetch] easyocr 第 {attempt} 次失败: {e}，5秒后重试...")
                time.sleep(5)
    raise last_err


def main() -> int:
    steps = [
        ("embedding", prefetch_embedding),
        ("reranker", prefetch_reranker),
        ("easyocr", prefetch_ocr),
    ]
    for name, fn in steps:
        try:
            fn()
        except Exception as e:
            print(f"[prefetch] FAIL {name}: {e}")
            return 1
    print("[prefetch] 全部模型预下载完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
