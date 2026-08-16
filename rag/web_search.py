"""联网搜索模块：DuckDuckGo（主）+ 百度（备）双引擎。

策略：
- 优先使用 DuckDuckGo（ddgs 库）搜索，国际通用、结果质量好
- DuckDuckGo 不可用或无结果时，自动回退百度搜索（baidusearch 库），国内网络稳定
- 搜索结果统一格式化为上下文字符串，供生成节点使用

返回格式：[{title, url, snippet}]，format_search_results 转为带来源标记的文本块。
"""

from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


def search_duckduckgo(query: str, max_results: int = 5) -> List[dict]:
    """使用 ddgs 搜索，返回 [{title, url, snippet}]。

    Args:
        query: 搜索关键词
        max_results: 最大结果数

    Returns:
        搜索结果列表，每个元素含 title/url/snippet
    """
    try:
        from ddgs import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href") or r.get("url", ""),
                    "snippet": r.get("body") or r.get("snippet", ""),
                })
        logger.info("DuckDuckGo 搜索 '%s' 返回 %d 条结果", query, len(results))
        return results
    except Exception as e:
        logger.warning("DuckDuckGo 搜索失败: %s", e)
        return []


def search_baidu(query: str, max_results: int = 5) -> List[dict]:
    """使用 baidusearch 搜索，返回 [{title, url, snippet}]。

    Args:
        query: 搜索关键词
        max_results: 最大结果数

    Returns:
        搜索结果列表，每个元素含 title/url/snippet
    """
    try:
        from baidusearch.baidusearch import search as baidu_search

        raw_results = baidu_search(query, num_results=max_results)
        results = []
        for r in raw_results:
            # baidusearch 返回对象有 title/link/abstract 属性
            results.append({
                "title": getattr(r, "title", "") or str(r.get("title", "")),
                "url": getattr(r, "link", "") or str(r.get("link", "")),
                "snippet": getattr(r, "abstract", "") or str(r.get("abstract", "")),
            })
        logger.info("百度搜索 '%s' 返回 %d 条结果", query, len(results))
        return results
    except Exception as e:
        logger.warning("百度搜索失败: %s", e)
        return []


def web_search(query: str, max_results: int = 5) -> List[dict]:
    """双引擎搜索：DuckDuckGo 优先，失败回退百度。

    Args:
        query: 搜索关键词
        max_results: 最大结果数

    Returns:
        搜索结果列表（至少来自一个引擎），可能为空列表
    """
    # 优先 DuckDuckGo
    results = search_duckduckgo(query, max_results=max_results)
    if results:
        return results

    # 回退百度
    logger.info("DuckDuckGo 无结果，回退百度搜索")
    results = search_baidu(query, max_results=max_results)
    return results


def format_search_results(results: List[dict]) -> str:
    """将搜索结果格式化为上下文字符串（含来源URL）。

    Args:
        results: web_search 返回的结果列表

    Returns:
        格式化的文本块，与 RAG 上下文格式类似
    """
    if not results:
        return ""

    blocks = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "未知标题")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        header = f"[网页{i}] 来源: {url}"
        if title:
            header += f" | 标题: {title}"
        header += " | 类型: 联网搜索"
        blocks.append(f"{header}\n{snippet}")
    return "\n\n".join(blocks)


def search_and_format(query: str, max_results: int = 5) -> str:
    """一站式：搜索并格式化，直接返回上下文字符串。

    Args:
        query: 搜索关键词
        max_results: 最大结果数

    Returns:
        格式化的搜索结果上下文字符串
    """
    results = web_search(query, max_results=max_results)
    return format_search_results(results)
