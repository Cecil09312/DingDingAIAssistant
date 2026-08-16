"""钉钉 Webhook 接收服务（独立运行，不侵入项目源码）。

提供 POST /dingtalk/webhook 端点：校验钉钉回调签名 → 调用项目智能体生成回答
→ 优先机器人 webhook 回复群消息，无 token 时回退工作通知。

须在项目根目录执行（智能体 graph 从项目源码导入）：
    python .qoder/skills/dingtalk-messaging/scripts/webhook_server.py
    python .qoder/skills/dingtalk-messaging/scripts/webhook_server.py --port 8001
"""

import argparse
import logging
import sys
from pathlib import Path

# 同目录 dingtalk_lib + 项目根目录（agent.graph）加入 sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parents[3]))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from dingtalk_lib import _env, reply_robot, send_text, verify_signature_skill  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("dingtalk-webhook-server")

app = FastAPI(title="钉钉回调接收服务（Skill）", version="1.0.0")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "dingtalk-webhook-server"}


@app.post("/dingtalk/webhook")
async def dingtalk_webhook(request: Request):
    """钉钉机器人回调处理。

    钉钉自定义机器人通过 Outgoing Webhook 调用本端点，
    请求体含 timestamp、sign（校验）、senderId、senderNick、text 等字段。
    """
    from agent.graph import get_compiled_graph

    # 解析查询参数中的签名信息
    params = dict(request.query_params)
    timestamp = params.get("timestamp", "")
    sign_value = params.get("sign", "")

    body = await request.json()
    logger.info(f"收到钉钉回调: {body.get('senderNick', '?')}")

    # 签名校验（配置了 DINGTALK_ROBOT_SECRET 时启用）
    if _env("DINGTALK_ROBOT_SECRET"):
        if not verify_signature_skill(timestamp, sign_value):
            logger.warning("签名校验失败")
            return JSONResponse(
                status_code=403,
                content={"errcode": 40001, "errmsg": "签名校验失败"},
            )

    # 提取消息内容
    text = body.get("text", {}).get("content", "").strip()
    if not text:
        text = body.get("content", "").strip()
    sender_id = body.get("senderStaffId") or body.get("senderId") or "unknown"
    session_id = body.get("conversationId") or f"session-{sender_id}"
    webhook_token = body.get("sessionWebhook", "") or _env("DINGTALK_ROBOT_TOKEN")

    if not text:
        return JSONResponse(content={"errcode": 0, "errmsg": "empty text"})

    # 调用智能体
    try:
        graph = get_compiled_graph()
        config = {"configurable": {"thread_id": session_id}}
        result = graph.invoke(
            {
                "user_input": text,
                "user_id": sender_id,
                "session_id": session_id,
            },
            config=config,
        )
        answer = result.get("answer", "抱歉，我暂时无法回答您的问题。")
    except Exception as e:
        logger.error(f"智能体调用失败: {e}", exc_info=True)
        answer = f"智能体处理异常，请稍后重试。错误: {e}"

    # 回复消息：优先用机器人 webhook 回复群消息，回退到工作通知
    if webhook_token:
        reply_robot(webhook_token, answer)
    else:
        send_text([sender_id], answer)

    return JSONResponse(content={"errcode": 0, "errmsg": "ok", "answer": answer})


def main():
    parser = argparse.ArgumentParser(description="钉钉回调接收服务")
    parser.add_argument("--port", type=int, default=8001, help="监听端口（默认 8001，避免与主服务 8000 冲突）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    args = parser.parse_args()

    import uvicorn

    print(f"钉钉回调接收服务: http://{args.host}:{args.port}/dingtalk/webhook")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
