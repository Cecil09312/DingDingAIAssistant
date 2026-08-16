"""OCR 模块：使用 easyocr 对图片/扫描页进行文字识别。

功能：
- ocr_image: 对 PIL Image 或图片路径做 OCR，返回识别文本
- render_pdf_pages: 用 pypdfium2 将 PDF 每页渲染为 PIL Image
- ocr_pdf: 对 PDF 逐页处理（文本优先，文本过少则 OCR 兜底）

easyocr 模型首次运行自动下载（ch_sim + en 权重），
已配置 HF_ENDPOINT 镜像加速。GPU 可选，默认 CPU。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

from PIL import Image

logger = logging.getLogger(__name__)

# 模块级 Reader 单例（延迟初始化，避免启动时加载模型）
_reader = None


def get_reader():
    """返回 easyocr.Reader 单例（ch_sim+en，gpu 按配置）。

    首次调用会从镜像下载模型权重，后续直接复用。
    """
    global _reader
    if _reader is None:
        import easyocr

        from config.settings import get_settings

        s = get_settings()
        _reader = easyocr.Reader(
            s.ocr_language_list,
            gpu=s.ocr_use_gpu,
        )
        logger.info("easyocr Reader 初始化完成 (langs=%s, gpu=%s)", s.ocr_language_list, s.ocr_use_gpu)
    return _reader


def ocr_image(image: Union[Image.Image, str, Path]) -> str:
    """对 PIL Image 或图片路径做 OCR，返回识别文本。

    Args:
        image: PIL.Image 对象，或图片文件路径

    Returns:
        识别到的文本字符串（各行用 \\n 拼接）
    """
    reader = get_reader()

    # 如果是路径，转为 PIL Image
    if isinstance(image, (str, Path)):
        image = Image.open(image)

    # easyocr 接受 numpy 数组或 PIL Image
    # 转为 numpy 数组以确保兼容性
    import numpy as np

    img_array = np.array(image)
    results = reader.readtext(img_array)
    # results 是 [(bbox, text, confidence), ...]
    lines = [item[1] for item in results if item[2] > 0.3]
    return "\n".join(lines)


def render_pdf_pages(pdf_path: Union[str, Path]) -> list[Image.Image]:
    """用 pypdfium2 将 PDF 每页渲染为 PIL Image。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        PIL Image 列表，每个元素对应一页
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    images = []
    for i in range(len(pdf)):
        page = pdf[i]
        # render_scale=2 提高清晰度（默认 1）
        bitmap = page.render(scale=2)
        pil_image = bitmap.to_pil()
        images.append(pil_image)
    return images


def ocr_pdf(pdf_path: Union[str, Path]) -> list[dict]:
    """对 PDF 逐页处理：文本优先提取，文本过少则 OCR 兜底。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        列表，每个元素 {"page": 页码(0-based), "text": 文本, "ocr_used": 是否用了OCR}
    """
    from config.settings import get_settings

    s = get_settings()
    min_len = s.ocr_min_text_length

    # 先用 pypdf 提取文本
    texts_by_page: list[str] = []
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        for page in reader.pages:
            t = page.extract_text() or ""
            texts_by_page.append(t.strip())
    except Exception as e:
        logger.warning("pypdf 文本提取失败: %s，将全部使用 OCR", e)
        texts_by_page = [""] * len(render_pdf_pages(pdf_path))

    results = []
    need_ocr = any(len(t) < min_len for t in texts_by_page)

    # 如果有任何页面文本过少，渲染全部页面为图片（用于 OCR）
    page_images = render_pdf_pages(pdf_path) if need_ocr else []

    for i, text in enumerate(texts_by_page):
        ocr_used = False
        if len(text) < min_len and i < len(page_images):
            # 文本过少，疑似扫描件，用 OCR
            try:
                ocr_text = ocr_image(page_images[i])
                if len(ocr_text) > len(text):
                    text = ocr_text
                    ocr_used = True
            except Exception as e:
                logger.warning("第 %d 页 OCR 失败: %s", i, e)

        results.append({"page": i, "text": text, "ocr_used": ocr_used})

    return results
