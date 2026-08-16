"""应用配置管理模块。

使用 pydantic-settings 从环境变量 / .env 文件加载全局配置，
涵盖 LLM、Embedding、向量库、记忆、钉钉、LangSmith 等所有子系统参数。
"""

import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 项目根目录（config/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """全局配置，按优先级从 环境变量 -> .env 文件 读取。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ===== 大语言模型（千问 OpenAI 兼容接口）=====
    llm_model: str = "qwen-plus"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = "sk-your-qwen-api-key-here"
    llm_temperature: float = 0.7
    # 评判模型（评估用）
    llm_judge_model: str = "qwen-plus"
    # 路由小模型（路由判断/抽取等轻量任务，降低成本与延迟）
    llm_router_model: str = "qwen-turbo"
    # LLM 调用最大重试次数（针对 429 限流、网络抖动等可重试错误）
    llm_max_retries: int = 3
    # LLM 单次请求超时（秒，含重试在内的总等待上限）
    llm_request_timeout: int = 60
    # 主模型失败时的降级模型（留空则不降级，直接返回错误提示）
    llm_fallback_model: str = "qwen-turbo"
    # 备用模型列表（主模型失败时按优先级依次尝试，逗号分隔）
    # 留空则回退到 llm_fallback_model 单模型降级
    llm_fallback_models: str = ""
    # 流式接口整体超时（秒，超时后返回已生成的 token）
    stream_timeout: int = 60

    # ===== Embedding（多模态 HuggingFace）=====
    embedding_model: str = "jinaai/jina-clip-v2"
    embedding_device: str = "cpu"

    # ===== 向量库 =====
    chroma_persist_dir: str = "data/chroma"
    chroma_collection: str = "dingtalk_kb"
    rag_top_k: int = 4
    # 检索分数过滤阈值（低于此值的文档丢弃，bge 距离越小越相关）
    rag_score_filter: float = 1.2
    # 检索结果最低相关度（归一化到 0~1，越大越相关）
    # 低于此值的文档不注入生成上下文，避免低质上下文导致幻觉
    rag_min_relevance: float = 0.3
    # 文档切片大小（字符数，入库时生效，调整后需 --rebuild 重建索引）
    rag_chunk_size: int = 500
    # 相邻切片重叠大小（字符数，避免语义在边界处被截断）
    rag_chunk_overlap: int = 80
    # 知识文档存放目录（相对路径基于项目根目录，支持绝对路径）
    rag_docs_dir: str = "data/docs"

    # ===== 多路召回（BM25 关键词检索）=====
    # 开启后向量检索 + BM25 并行召回，RRF 融合排序，提升精确匹配召回率
    rag_bm25_enabled: bool = False
    # BM25 检索召回数（融合前从关键词检索取的候选数）
    rag_bm25_candidate_count: int = 10
    # RRF 融合常数（k，rank 的平滑因子，典型值 60）
    rag_rrf_k: int = 60

    # ===== 查询改写（提升复杂问题召回率）=====
    # 开启后用 LLM 改写查询再检索，失败自动回退原始查询
    rag_query_rewrite_enabled: bool = False

    # ===== 重排序（Reranker）=====
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-base"
    rerank_device: str = "cpu"
    rerank_candidate_count: int = 20  # 向量检索召回的候选数
    rerank_top_k: int = 4  # 重排序后返回的 top-k

    # ===== OCR 配置 =====
    ocr_languages: str = "ch_sim,en"  # easyocr 语言（逗号分隔）
    ocr_use_gpu: bool = False
    ocr_min_text_length: int = 50  # PDF 页面文本少于此字数触发 OCR
    # 支持的文件扩展名（逗号分隔）
    supported_file_extensions: str = ".txt,.md,.pdf,.docx,.xlsx,.png,.jpg,.jpeg,.bmp,.tiff"

    # ===== 联网搜索 =====
    web_search_enabled: bool = True
    web_search_max_results: int = 5
    web_search_timeout: int = 10  # 搜索超时（秒）

    # ===== 记忆（长期记忆 SQLite 持久化；短期记忆为进程内 MemorySaver）=====
    long_term_db_path: str = "data/memory.db"
    # 每多少轮对话触发一次长期记忆摘要
    memory_summary_every: int = 6
    # 长期记忆上下文字符预算（画像/关键事实硬保留，摘要层按预算填充）
    memory_context_budget: int = 1600
    # 短期历史窗口（注入生成的最近消息条数）
    memory_short_window: int = 8
    # 短期历史字符预算（超预算从最旧消息开始丢弃）
    memory_history_budget: int = 2000
    # RAG/联网搜索上下文字符预算
    rag_context_budget: int = 2500
    # 每轮 LLM 结构化抽取开关（规则快速抽取始终启用，不受此开关影响）
    memory_extract_llm_enabled: bool = True
    # 问答记忆缓存：新问题与历史问题高度相似时直接复用历史答案，跳过大模型
    memory_qa_cache_enabled: bool = True
    # 问答记忆命中的余弦相似度阈值（越高越严格）
    memory_qa_threshold: float = 0.90
    # 每用户最多保留的问答记忆条数（超出按更新时间淘汰）
    memory_qa_max_records: int = 200

    # ===== 输入安全过滤 =====
    # 开启后对用户输入做关键词黑名单 + prompt 注入检测
    input_filter_enabled: bool = False
    # 敏感词黑名单（逗号分隔）
    input_blocked_keywords: str = ""
    # prompt 注入检测开关（检测"忽略以上指令"等模式）
    input_injection_check_enabled: bool = True

    # ===== 限流与熔断 =====
    # 每用户每分钟最大请求数（<=0 表示不限流）
    rate_limit_per_minute: int = 0
    # 熔断阈值：连续失败达此次数后熔断（<=0 表示不熔断）
    circuit_breaker_threshold: int = 0
    # 熔断恢复冷却时间（秒，过后进入半开状态试探）
    circuit_breaker_recovery: int = 60

    # ===== 钉钉工具调用（待办/会议管理）=====
    # 工具调用总开关（关闭后 tool 路由自动降级为 chat）
    tool_calling_enabled: bool = False
    # 写操作（创建/取消/修改）需用户确认
    tool_confirmation_required: bool = True

    # ===== LangSmith 评估与追踪 =====
    langsmith_api_key: str = ""
    langsmith_project: str = "dingtalk-ai-agent"
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    @property
    def ocr_language_list(self) -> list[str]:
        """返回 OCR 语言列表（从逗号分隔字符串解析）。"""
        return [lang.strip() for lang in self.ocr_languages.split(",") if lang.strip()]

    @property
    def supported_extensions_list(self) -> list[str]:
        """返回支持的文件扩展名列表。"""
        return [ext.strip() for ext in self.supported_file_extensions.split(",") if ext.strip()]

    @property
    def input_blocked_keywords_list(self) -> list[str]:
        """返回输入敏感词黑名单列表（从逗号分隔字符串解析）。"""
        return [kw.strip() for kw in self.input_blocked_keywords.split(",") if kw.strip()]

    @property
    def chroma_persist_path(self) -> Path:
        """返回向量库持久化的绝对路径。"""
        p = Path(self.chroma_persist_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def long_term_db_file(self) -> Path:
        """返回长期记忆 SQLite 数据库的绝对路径。"""
        p = Path(self.long_term_db_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def docs_dir(self) -> Path:
        """返回知识文档目录的绝对路径（从 rag_docs_dir 配置解析）。

        目录不存在时尝试自动创建；创建失败（如盘符不存在）仅告警不阻断。
        """
        p = Path(self.rag_docs_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("知识文档目录无法创建 %s: %s", p, e)
        return p


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例。"""
    return Settings()
