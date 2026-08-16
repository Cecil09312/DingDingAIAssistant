"""冒烟测试：验证各模块可正常导入与基本功能。

运行：python -m pytest tests/test_smoke.py -v
或直接：python tests/test_smoke.py
"""

import sys
from pathlib import Path

# 确保项目根目录在 path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_config_import():
    """测试配置模块导入。"""
    from config.settings import get_settings

    s = get_settings()
    assert s.llm_model, "llm_model 未配置"
    assert s.long_term_db_path, "long_term_db_path 未配置"
    assert s.embedding_model, "embedding_model 未配置"
    assert s.ocr_min_text_length > 0, "ocr_min_text_length 未配置"
    assert len(s.ocr_language_list) > 0, "ocr_language_list 为空"
    print("[OK] config.settings 导入与读取正常")


def _cleanup_memory_test(conn, uid: str) -> None:
    """清理记忆测试产生的临时数据。"""
    from memory import long_term as lt

    with lt._db_lock:
        for table in ("user_profile", "user_facts", "user_memory", "summary_history", "user_prefs", "qa_memory"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM session_summary WHERE user_id=?", (uid,))
        conn.commit()


def test_memory_rule_extraction_and_context():
    """T1/T2/T3：规则抽取姓名当轮落库 + 跨轮召回 + 摘要不冲掉关键信息。"""
    import uuid

    from memory import long_term as lt

    uid = f"_smoke_{uuid.uuid4().hex[:8]}"
    conn = lt._get_conn()
    try:
        # T1 姓名当轮落库
        saved = lt.apply_extraction(uid, "我叫张三，请记住")
        assert saved.get("name") == "张三"
        assert lt.get_profile(uid).get("name") == "张三"

        # T2 跨轮召回：分层上下文含姓名与分节格式
        ctx = lt.build_memory_context(uid, query="我叫什么名字")
        assert "张三" in ctx and "用户画像" in ctx

        # T3 多次摘要不冲掉关键信息（画像不依赖摘要层）
        for i in range(3):
            lt.save_summary(uid, f"无关摘要{i}: 讨论了天气与新闻")
        ctx = lt.build_memory_context(uid, budget=1600)
        assert "张三" in ctx

        # 疑问句不得误抽为姓名
        lt.apply_extraction(uid, "我叫什么名字")
        assert lt.get_profile(uid).get("name") == "张三"
    finally:
        _cleanup_memory_test(conn, uid)
    print("[OK] 记忆规则抽取/召回/防丢失正常")


def test_memory_budget_control():
    """T4：大量记忆下预算控制且 P0 关键信息硬保留。"""
    import uuid

    from memory import long_term as lt

    uid = f"_smoke_{uuid.uuid4().hex[:8]}"
    conn = lt._get_conn()
    try:
        lt.save_profile(uid, "name", "李四")
        lt.save_fact(uid, "用户的项目是记忆改造", priority=1, source="rule")
        for i in range(30):
            lt.save_fact(uid, f"普通事实编号{i}" + "内容" * 10, priority=5)
        for i in range(10):
            lt.save_summary(uid, f"历史摘要{i}: " + "讨论内容" * 20)
        ctx = lt.build_memory_context(uid, budget=1600)
        # P0 硬保留：姓名与关键事实必须在
        assert "李四" in ctx and "记忆改造" in ctx
        # 总长度受控（P0 硬保留允许小幅超出，容差 600）
        assert len(ctx) <= 1600 + 600, f"上下文过长: {len(ctx)}"
    finally:
        _cleanup_memory_test(conn, uid)
    print("[OK] 记忆预算控制与 P0 硬保留正常")


def test_short_window_compression():
    """T5：超窗历史构建（窗口+预算+更早对话摘要注入）。"""
    from langchain_core.messages import AIMessage, HumanMessage

    from agent.nodes import build_chat_history

    messages = []
    for i in range(10):
        messages.append(HumanMessage(content=f"问题{i}"))
        messages.append(AIMessage(content=f"回答{i}"))
    # 20 条消息、窗口 8 → 最近 8 条 + 首条更早对话摘要
    out = build_chat_history(messages, session_summary="早前讨论了若干话题", window=8, budget=2000)
    assert len(out) == 9
    assert "更早对话摘要" in str(out[0].content)

    # 字符预算压力下从最旧丢弃
    long_messages = [HumanMessage(content="长" * 500) for _ in range(6)]
    out2 = build_chat_history(long_messages, session_summary="", window=8, budget=1200)
    total = sum(len(str(m.content)) for m in out2)
    assert total <= 1200 and 0 < len(out2) < 6
    print("[OK] 短期窗口预算与压缩摘要注入正常")


def test_qa_memory_cache():
    """问答缓存：save_qa/search_qa_by_embedding 相似度匹配与阈值过滤（离线，不加载模型）。"""
    import uuid

    from memory import long_term as lt

    uid = f"_smoke_{uuid.uuid4().hex[:8]}"
    conn = lt._get_conn()
    try:
        v1 = [1.0, 0.0, 0.0, 0.0]
        v_similar = [0.98, 0.1, 0.0, 0.0]  # 与 v1 余弦相似度 ≈0.995
        v_other = [0.0, 1.0, 0.0, 0.0]     # 正交，相似度 0
        assert lt.save_qa(uid, "什么是RAG", "RAG是检索增强生成，通过知识库检索提升回答准确性。", v1)

        # 高相似命中
        hit = lt.search_qa_by_embedding(uid, v_similar, threshold=0.9)
        assert hit is not None and hit[1].startswith("RAG是")
        # 低相似不命中
        assert lt.search_qa_by_embedding(uid, v_other, threshold=0.9) is None

        # 相同问题更新而非重复插入
        lt.save_qa(uid, "什么是RAG", "更新后的答案内容，长度足够触发更新逻辑。", v1)
        rows = conn.execute(
            "SELECT COUNT(*) AS c FROM qa_memory WHERE user_id=?", (uid,)
        ).fetchone()["c"]
        assert rows == 1
    finally:
        _cleanup_memory_test(conn, uid)
    print("[OK] 问答记忆缓存存取/相似度匹配正常")


def test_agent_state():
    """测试 AgentState 类型定义。"""
    from agent.state import AgentState

    fields = AgentState.__annotations__
    assert "messages" in fields
    assert "user_input" in fields
    assert "search_route" in fields
    print("[OK] agent.state 定义正常")


def test_graph_build_no_checkpointer():
    """测试图可编译（无 checkpointer 模式）。"""
    from agent.graph import build_graph

    g = build_graph(checkpointer=False)
    assert g is not None
    print("[OK] LangGraph StateGraph 编译正常")


def test_evaluation_imports():
    """测试评估模块导入。"""
    from evaluation.rag_eval import build_rag_evaluators
    from evaluation.agent_eval import build_agent_evaluators
    from evaluation.datasets import get_dataset

    assert len(get_dataset()) > 0
    print("[OK] evaluation 模块导入正常")


def test_rag_retriever_format():
    """测试 RAG 上下文格式化函数（不依赖向量库）。"""
    from rag.retriever import format_context
    from langchain_core.documents import Document

    docs = [(Document(page_content="测试内容", metadata={"source": "test.txt"}), 0.3)]
    ctx = format_context(docs)
    assert "测试内容" in ctx
    assert "test.txt" in ctx
    print("[OK] rag 上下文格式化正常")


def test_long_term_memory():
    """测试长期记忆 SQLite 存取。"""
    try:
        from memory.long_term import save_pref, get_prefs, get_context, save_summary, get_history

        uid = "test-user-smoke"
        save_pref(uid, "name", "测试用户")
        prefs = get_prefs(uid)
        assert prefs.get("name") == "测试用户"
        ctx = get_context(uid)
        assert "测试用户" in ctx
        # 测试摘要存取
        save_summary(uid, "这是一个测试摘要")
        history = get_history(uid, limit=3)
        assert any("测试摘要" in h for h in history)
        print("[OK] memory 长期记忆 SQLite 存取正常")
    except Exception as e:
        print(f"[FAIL] long_term 测试失败: {e}")
        raise


def test_ocr_module_import():
    """测试 OCR 模块导入（不实际加载模型）。"""
    from rag.ocr import get_reader, ocr_image, render_pdf_pages, ocr_pdf

    assert callable(get_reader)
    assert callable(ocr_image)
    assert callable(render_pdf_pages)
    assert callable(ocr_pdf)
    print("[OK] rag.ocr 模块导入正常")


def test_multimodal_embeddings_class():
    """测试 JinaClipEmbeddings 类定义（不实际加载模型）。"""
    from rag.embeddings import JinaClipEmbeddings, get_embeddings

    # 验证类定义与方法存在
    assert hasattr(JinaClipEmbeddings, "embed_documents")
    assert hasattr(JinaClipEmbeddings, "embed_query")
    assert hasattr(JinaClipEmbeddings, "embed_images")
    assert hasattr(JinaClipEmbeddings, "embed_query_image")
    assert callable(get_embeddings)
    print("[OK] rag.embeddings 多模态 Embedding 类定义正常")


def test_supported_extensions():
    """测试支持的文件扩展名配置。"""
    from rag.ingest import SUPPORTED_EXTENSIONS, IMAGE_EXTENSIONS

    expected = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
    assert SUPPORTED_EXTENSIONS == expected
    assert IMAGE_EXTENSIONS == {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
    # 验证 settings 中的配置也一致
    from config.settings import get_settings

    s = get_settings()
    for ext in expected:
        assert ext in s.supported_extensions_list, f"{ext} 不在 supported_file_extensions 中"
    print("[OK] rag 文件扩展名配置正常")


def test_reranker_module_import():
    """测试重排序模块导入（不实际加载模型）。"""
    from rag.reranker import get_reranker, rerank

    assert callable(get_reranker)
    assert callable(rerank)
    print("[OK] rag.reranker 模块导入正常")


def test_rerank_config():
    """测试重排序配置项读取。"""
    from config.settings import get_settings

    s = get_settings()
    assert hasattr(s, "rerank_enabled"), "rerank_enabled 未配置"
    assert hasattr(s, "rerank_model"), "rerank_model 未配置"
    assert hasattr(s, "rerank_candidate_count"), "rerank_candidate_count 未配置"
    assert hasattr(s, "rerank_top_k"), "rerank_top_k 未配置"
    assert s.rerank_candidate_count > 0, "rerank_candidate_count 应大于 0"
    assert s.rerank_top_k > 0, "rerank_top_k 应大于 0"
    print("[OK] 重排序配置项读取正常")


def test_web_search_module_import():
    """测试联网搜索模块导入。"""
    from rag.web_search import search_duckduckgo, search_baidu, web_search, format_search_results, search_and_format

    assert callable(search_duckduckgo)
    assert callable(search_baidu)
    assert callable(web_search)
    assert callable(format_search_results)
    assert callable(search_and_format)
    print("[OK] rag.web_search 模块导入正常")


def test_web_search_config():
    """测试联网搜索配置项读取。"""
    from config.settings import get_settings

    s = get_settings()
    assert hasattr(s, "web_search_enabled"), "web_search_enabled 未配置"
    assert hasattr(s, "web_search_max_results"), "web_search_max_results 未配置"
    assert hasattr(s, "web_search_timeout"), "web_search_timeout 未配置"
    assert s.web_search_max_results > 0, "web_search_max_results 应大于 0"
    print("[OK] 联网搜索配置项读取正常")


def test_format_search_results():
    """测试搜索结果格式化函数（不依赖网络）。"""
    from rag.web_search import format_search_results

    results = [
        {"title": "测试标题", "url": "https://example.com", "snippet": "测试内容"},
        ]
    formatted = format_search_results(results)
    assert "测试标题" in formatted
    assert "https://example.com" in formatted
    assert "测试内容" in formatted
    assert "联网搜索" in formatted

    # 空结果
    assert format_search_results([]) == ""
    print("[OK] 联网搜索结果格式化正常")


def test_multimodal_vectorstore():
    """测试 vectorstore 多模态函数定义。"""
    from rag.vectorstore import add_documents, add_image_documents, search, get_retriever

    assert callable(add_documents)
    assert callable(add_image_documents)
    assert callable(search)
    assert callable(get_retriever)
    print("[OK] rag.vectorstore 多模态函数定义正常")


def main():
    tests = [
        test_config_import,
        test_memory_rule_extraction_and_context,
        test_memory_budget_control,
        test_short_window_compression,
        test_qa_memory_cache,
        test_agent_state,
        test_graph_build_no_checkpointer,
        test_evaluation_imports,
        test_rag_retriever_format,
        test_long_term_memory,
        test_ocr_module_import,
        test_multimodal_embeddings_class,
        test_supported_extensions,
        test_multimodal_vectorstore,
        test_reranker_module_import,
        test_rerank_config,
        test_web_search_module_import,
        test_web_search_config,
        test_format_search_results,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
            failed += 1
    print(f"\n结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
