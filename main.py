"""FastAPI 主入口。

提供 Web 聊天接口、健康检查、以及本地 REPL 调试模式。
（钉钉回调接收已迁移至 .qoder/skills/dingtalk-messaging/scripts/webhook_server.py）

启动 Web 服务：
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

本地 REPL 调试：
    python main.py --repl
"""

# ===== HuggingFace 镜像配置（必须在任何 HF/langchain 导入之前设置）=====
import os

if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
# 本地已有缓存时启用离线模式可完全跳过网络请求；如需下载则不设此变量
# os.environ["HF_HUB_OFFLINE"] = "1"

# 禁用 ChromaDB 匿名遥测（posthog 版本不兼容会导致 ERROR 日志噪音，不影响功能）
if not os.environ.get("ANONYMIZED_TELEMETRY"):
    os.environ["ANONYMIZED_TELEMETRY"] = "false"

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("dingtalk-agent")

app = FastAPI(title="钉钉AI智能体助手", version="1.0.0")

# 静态文件目录
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def warmup_llm():
    """启动时预热 LLM 连接（TLS 握手 + DNS 解析 + 连接池建立）。

    首次 LLM 请求通常较慢（TLS 握手、DNS 解析、HTTP/2 连接建立），
    在启动时主动发起一次最小请求，可显著降低首条用户请求的延迟。
    预热对象：路由小模型（预检查路径最常用）+ 主模型（生成路径）。
    后台线程执行，不阻塞应用启动；失败仅记日志，不影响服务可用性。
    """
    import threading

    from langchain_core.messages import HumanMessage

    from agent.nodes import _get_llm, _get_router_llm

    def _do_warmup():
        # 预热 temperature 必须与实际调用一致，否则 lru_cache 会缓存成不同实例，
        # 预热的连接池无法被复用。
        # - router 实际用 0.0（emotion_route / extract / route 均传 0.0）
        # - main_llm 实际用默认 0.7（generate_node 调用 _get_llm()）
        warmup_targets = [
            ("router_llm", lambda: _get_router_llm(temperature=0.0)),
            ("main_llm", lambda: _get_llm(temperature=0.7)),
        ]
        for name, factory in warmup_targets:
            try:
                llm = factory()
                llm.invoke([HumanMessage(content="hi")])
                logger.info(f"[warmup] {name} 连接预热完成")
            except Exception as e:
                logger.warning(f"[warmup] {name} 预热失败（不影响启动）: {e}")

    threading.Thread(target=_do_warmup, daemon=True).start()


@app.on_event("startup")
def warmup_vectorstore():
    """启动时检查向量库，如果为空则自动入库（后台线程，不阻塞启动）。

    内存模式（CHROMA_PERSIST_DIR 为空）下每次重启都需要重新入库；
    持久化模式下已有数据则跳过。
    """
    import threading

    def _do_warmup():
        try:
            from rag.vectorstore import get_vectorstore
            from rag.ingest import ingest

            vs = get_vectorstore()
            count = vs._collection.count()
            if count == 0:
                logger.info("[warmup] 向量库为空，开始自动入库...")
                n = ingest(rebuild=False)
                logger.info(f"[warmup] 自动入库完成，共 {n} 个 chunk")
            else:
                logger.info(f"[warmup] 向量库已有 {count} 个文档，跳过入库")

            # BM25 索引预热（开启多路召回时，避免首请求阻塞）
            from config.settings import get_settings

            if get_settings().rag_bm25_enabled:
                try:
                    from rag.bm25 import get_bm25_index

                    idx, docs = get_bm25_index()
                    if idx is not None:
                        logger.info(f"[warmup] BM25 索引预热完成，共 {len(docs)} 个文档")
                    else:
                        logger.info("[warmup] BM25 索引为空（向量库无文本文档）")
                except Exception as e:
                    logger.warning(f"[warmup] BM25 索引预热失败（不影响启动）: {e}")
        except Exception as e:
            logger.warning(f"[warmup] 向量库初始化失败（不影响启动）: {e}")

    threading.Thread(target=_do_warmup, daemon=True).start()


class ChatRequest(BaseModel):
    """Web 端聊天请求体。"""
    user_input: str
    user_id: str = "web-user"
    session_id: str = "web-session"


@app.get("/", response_class=HTMLResponse)
async def root():
    """根路径返回聊天前端页面。"""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>前端页面未找到</h1>", status_code=404)


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    """Web 端聊天接口，直接调用智能体生成回答。

    纯 Web 聊天，无需鉴权，直接返回智能体回答。

    注意：使用普通 def（而非 async def），因为 graph.invoke() 是同步阻塞调用。
    FastAPI 会自动将其放入线程池执行，避免阻塞事件循环导致服务无响应。
    """
    if not req.user_input.strip():
        return JSONResponse(content={"errcode": 1, "errmsg": "empty input"})

    # 输入安全过滤（关键词黑名单 + prompt 注入检测）
    from agent.safety import check_input

    passed, reason = check_input(req.user_input)
    if not passed:
        return JSONResponse(status_code=403, content={"errcode": 403, "errmsg": reason})

    # 限流检查（按 user_id 滑动窗口）
    from agent.rate_limiter import check_rate_limit

    ok, reason = check_rate_limit(req.user_id)
    if not ok:
        return JSONResponse(status_code=429, content={"errcode": 429, "errmsg": reason})

    # 熔断检查（连续失败达阈值后拒绝请求）
    from agent.rate_limiter import check_circuit, record_failure, record_success

    ok, reason = check_circuit()
    if not ok:
        return JSONResponse(status_code=503, content={"errcode": 503, "errmsg": reason})

    try:
        from agent.graph import get_compiled_graph

        graph = get_compiled_graph()
        config = {"configurable": {"thread_id": req.session_id}}
        result = graph.invoke(
            {
                "user_input": req.user_input,
                "user_id": req.user_id,
                "session_id": req.session_id,
            },
            config=config,
        )
        answer = result.get("answer", "抱歉，我暂时无法回答您的问题。")
        record_success()
        return JSONResponse(content={"errcode": 0, "errmsg": "ok", "answer": answer})
    except Exception as e:
        logger.error(f"Web 聊天调用失败: {e}", exc_info=True)
        record_failure()
        return JSONResponse(
            status_code=500,
            content={"errcode": 500, "errmsg": str(e)},
        )


# 节点进入时的状态栏提示
_NODE_STATUS = {
    "pre_check": "预检查中（缓存匹配+意图识别）...",
    "load_memory": "加载长期记忆中...",
    "retrieve": "检索知识库中...",
    "web_search": "联网搜索中...",
    "generate": "生成回答中...",
    "memory_background": "更新记忆中...",
}


def _sse(payload: dict) -> str:
    """格式化一条 SSE 消息。"""
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


@app.post("/api/chat/stream")
async def api_chat_stream(req: ChatRequest):
    """Web 端流式聊天接口（SSE）。

    事件类型：
        status：节点进度（情感分析/路由/检索/生成等）
        token ：回答的 token 增量
        error ：异常信息
        done  ：结束标记
    """
    if not req.user_input.strip():
        return JSONResponse(content={"errcode": 1, "errmsg": "empty input"})

    # 输入安全过滤
    from agent.safety import check_input

    passed, reason = check_input(req.user_input)
    if not passed:
        return JSONResponse(status_code=403, content={"errcode": 403, "errmsg": reason})

    # 限流检查
    from agent.rate_limiter import check_rate_limit

    ok, reason = check_rate_limit(req.user_id)
    if not ok:
        return JSONResponse(status_code=429, content={"errcode": 429, "errmsg": reason})

    # 熔断检查
    from agent.rate_limiter import check_circuit, record_failure, record_success

    ok, reason = check_circuit()
    if not ok:
        return JSONResponse(status_code=503, content={"errcode": 503, "errmsg": reason})

    async def event_stream():
        from agent.graph import astream_chat
        from config.settings import get_settings

        # 流式整体超时保护：超时后停止拉取并返回已生成的 token，防止连接卡死
        timeout = get_settings().stream_timeout
        got_token = False
        deadline = asyncio.get_event_loop().time() + timeout
        try:
            ait = astream_chat(req.user_input, req.user_id, req.session_id).__aiter__()
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    logger.warning(f"[stream] 整体超时 {timeout}s，返回已生成内容")
                    break
                try:
                    ev = await asyncio.wait_for(ait.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                if ev["type"] == "node":
                    status = _NODE_STATUS.get(ev["node"])
                    if status:
                        yield _sse({"type": "status", "status": status})
                else:
                    got_token = True
                    yield _sse({"type": "token", "content": ev["content"]})
            if not got_token:
                yield _sse({"type": "token", "content": "抱歉，我暂时无法回答您的问题。"})
            yield _sse({"type": "done"})
            # 正常完成（含超时已部分生成）记为成功，重置熔断器
            record_success()
        except asyncio.TimeoutError:
            logger.warning(f"[stream] 流式超时 {timeout}s")
            if not got_token:
                yield _sse({"type": "token", "content": "抱歉，响应超时，请稍后重试。"})
            yield _sse({"type": "done"})
            # 超时已部分生成，不计为失败
            record_success()
        except Exception as e:
            logger.error(f"流式聊天调用失败: {e}", exc_info=True)
            yield _sse({"type": "error", "errmsg": str(e)})
            # 异常记为失败，累计触发熔断
            record_failure()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 代理缓冲
        },
    )


@app.get("/health")
async def health():
    """健康检查端点。"""
    from memory.short_term import get_checkpointer_type

    return {
        "status": "ok",
        "service": "dingtalk-ai-agent",
        "checkpointer": get_checkpointer_type(),
    }


def repl():
    """本地 REPL 调试模式，交互式测试智能体（流式输出）。"""
    from agent.graph import chat_stream

    print("=" * 50)
    print("钉钉AI智能体助手 - REPL 调试模式（流式输出）")
    print("输入 'quit' 或 'exit' 退出，输入 'reset' 重置会话")
    print("=" * 50)
    session_id = "repl-session"
    user_id = "repl-user"
    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("再见！")
            break
        if user_input.lower() == "reset":
            session_id = f"repl-{int(time.time())}"
            print("已重置会话")
            continue
        print("助手: ", end="", flush=True)
        try:
            for ev in chat_stream(user_input, user_id=user_id, session_id=session_id):
                if ev["type"] == "token":
                    print(ev["content"], end="", flush=True)
            print()
        except Exception as e:
            print(f"[错误] {e}")


def main():
    parser = argparse.ArgumentParser(description="钉钉AI智能体助手")
    parser.add_argument("--repl", action="store_true", help="启动本地 REPL 调试模式")
    args = parser.parse_args()
    if args.repl:
        repl()
    else:
        import uvicorn

        print("启动 Web 服务: http://0.0.0.0:8000")
        print("健康检查: http://localhost:8000/health")
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            reload_includes=["*.py"],
            reload_excludes=["*.md", "*.txt", "data/*", "__pycache__/*"],
        )


if __name__ == "__main__":
    main()
