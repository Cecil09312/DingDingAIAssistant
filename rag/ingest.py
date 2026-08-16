"""文档加载、切分与入库 CLI。

支持格式：.txt .md .pdf .docx .xlsx .png .jpg .jpeg .bmp .tiff
用法：
    python -m rag.ingest               # 入库 data/docs/ 全部支持格式
    python -m rag.ingest --rebuild      # 清空重建索引
"""

import argparse
import logging
import sys
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config.settings import get_settings
from rag.vectorstore import add_documents, add_image_documents, get_vectorstore

logger = logging.getLogger(__name__)

# 支持的文件扩展名
SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".xlsx",
                         ".png", ".jpg", ".jpeg", ".bmp", ".tiff"}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}


def load_text_file(filepath: Path) -> list[Document]:
    """加载 .txt/.md 文件。"""
    try:
        text = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = filepath.read_text(encoding="gbk", errors="ignore")
    return [Document(page_content=text, metadata={"source": str(filepath.name), "title": filepath.stem})]


def load_pdf(filepath: Path) -> list[Document]:
    """PDF 加载：文本优先，扫描页 OCR 兜底。

    1. 用 pypdf 提取文本
    2. 若某页文本过少（<配置阈值，疑似扫描件），渲染为图片 → OCR
    3. 对含图像的页面，额外生成图像 Document（metadata.image_path）
    """
    from rag.ocr import ocr_pdf, render_pdf_pages

    page_results = ocr_pdf(filepath)
    docs = []

    for pr in page_results:
        text = pr["text"]
        if text.strip():
            docs.append(Document(
                page_content=text,
                metadata={
                    "source": str(filepath.name),
                    "title": filepath.stem,
                    "page": pr["page"] + 1,
                    "ocr_used": pr["ocr_used"],
                },
            ))

    # 如果 PDF 有页面，额外为每页生成图像 Document（用于多模态检索）
    try:
        page_images = render_pdf_pages(filepath)
        import io

        image_dir = get_settings().docs_dir / ".page_images" / filepath.stem
        image_dir.mkdir(parents=True, exist_ok=True)
        for i, img in enumerate(page_images):
            img_path = image_dir / f"page_{i+1}.png"
            img.save(str(img_path), "PNG")
            docs.append(Document(
                page_content=f"[PDF页面图像 {filepath.name} 第{i+1}页]",
                metadata={
                    "source": str(filepath.name),
                    "title": filepath.stem,
                    "page": i + 1,
                    "image_path": str(img_path),
                    "type": "image",
                },
            ))
    except Exception as e:
        logger.warning("PDF 页面图像生成跳过: %s", e)

    return docs


def load_docx(filepath: Path) -> list[Document]:
    """Word .docx 加载：使用 langchain Docx2txtLoader。"""
    from langchain_community.document_loaders import Docx2txtLoader

    loader = Docx2txtLoader(str(filepath))
    docs = loader.load()
    # 补充 metadata
    for doc in docs:
        doc.metadata.setdefault("source", str(filepath.name))
        doc.metadata.setdefault("title", filepath.stem)
    return docs


def load_excel(filepath: Path) -> list[Document]:
    """Excel .xlsx 加载：openpyxl 遍历所有 sheet，提取单元格文本。"""
    from openpyxl import load_workbook

    wb = load_workbook(str(filepath), data_only=True)
    docs = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows_text = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None and str(c).strip()]
            if cells:
                rows_text.append(" | ".join(cells))
        if rows_text:
            text = "\n".join(rows_text)
            docs.append(Document(
                page_content=text,
                metadata={
                    "source": str(filepath.name),
                    "title": filepath.stem,
                    "sheet": sheet_name,
                },
            ))
    wb.close()
    return docs


def load_image(filepath: Path) -> list[Document]:
    """图片加载：OCR 提取文本 + 图像 Document（用于多模态检索）。"""
    from rag.ocr import ocr_image

    docs = []
    # OCR 提取文本
    try:
        ocr_text = ocr_image(filepath)
        if ocr_text.strip():
            docs.append(Document(
                page_content=ocr_text,
                metadata={
                    "source": str(filepath.name),
                    "title": filepath.stem,
                    "ocr": True,
                },
            ))
    except Exception as e:
        logger.warning("图片 OCR 失败 %s: %s", filepath.name, e)

    # 图像 Document（page_content 用占位描述，metadata.image_path 存路径）
    docs.append(Document(
        page_content=f"[图片 {filepath.name}]",
        metadata={
            "source": str(filepath.name),
            "title": filepath.stem,
            "image_path": str(filepath),
            "type": "image",
        },
    ))
    return docs


def load_file(filepath: Path) -> list[Document]:
    """按扩展名分发到对应加载器。"""
    ext = filepath.suffix.lower()
    if ext in (".txt", ".md"):
        return load_text_file(filepath)
    elif ext == ".pdf":
        return load_pdf(filepath)
    elif ext == ".docx":
        return load_docx(filepath)
    elif ext == ".xlsx":
        return load_excel(filepath)
    elif ext in IMAGE_EXTENSIONS:
        return load_image(filepath)
    else:
        logger.warning("不支持的文件格式: %s", filepath)
        return []


def load_files(docs_dir: Path) -> list[Document]:
    """加载 docs_dir 下所有支持格式的文件为 Document。"""
    docs: list[Document] = []
    for fp in sorted(docs_dir.iterdir()):
        if fp.is_file() and fp.suffix.lower() in SUPPORTED_EXTENSIONS:
            try:
                docs.extend(load_file(fp))
                logger.info("已加载: %s", fp.name)
            except Exception as e:
                logger.error("加载失败 %s: %s", fp.name, e)
    return docs


def split_documents(docs: list[Document]) -> list[Document]:
    """使用递归字符切分器将文档切分为小块。

    切片大小与重叠从配置读取（rag_chunk_size / rag_chunk_overlap）。
    图像 Document（metadata.type=="image"）不参与切分，直接保留。
    """
    settings = get_settings()
    text_docs = [d for d in docs if d.metadata.get("type") != "image"]
    image_docs = [d for d in docs if d.metadata.get("type") == "image"]

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", ".", " ", ""],
    )
    chunks = splitter.split_documents(text_docs)
    # 图像文档直接追加
    chunks.extend(image_docs)
    return chunks


def ingest(rebuild: bool = False) -> int:
    """执行文档入库流程，返回入库 chunk 数。"""
    settings = get_settings()
    docs = load_files(settings.docs_dir)
    if not docs:
        print(f"[ingest] 未在 {settings.docs_dir} 找到支持的文档")
        print(f"[ingest] 支持格式: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        return 0

    if rebuild:
        try:
            vs = get_vectorstore()
            vs.delete_collection()
            print("[ingest] 已清空旧索引")
        except Exception as e:
            print(f"[ingest] 清空索引跳过: {e}")

    chunks = split_documents(docs)

    # 分离文本块和图像块
    text_chunks = [c for c in chunks if c.metadata.get("type") != "image"]
    image_chunks = [c for c in chunks if c.metadata.get("type") == "image"]

    # 入库文本块
    text_ids = []
    if text_chunks:
        text_ids = add_documents(text_chunks)

    # 入库图像块
    image_ids = []
    if image_chunks:
        image_ids = add_image_documents(image_chunks)

    total = len(text_ids) + len(image_ids)
    print(f"[ingest] 加载 {len(docs)} 个文档，切分为 {len(text_chunks)} 个文本块 + {len(image_chunks)} 个图像块，入库完成")
    return total


def main():
    parser = argparse.ArgumentParser(description="RAG 多模态文档入库工具")
    parser.add_argument("--rebuild", action="store_true", help="清空旧索引后重建")
    args = parser.parse_args()
    count = ingest(rebuild=args.rebuild)
    print(f"[ingest] 完成，共 {count} 个 chunk")
    return count


if __name__ == "__main__":
    sys.exit(0)
