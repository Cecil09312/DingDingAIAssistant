"""钉钉 Stream（客户端模式）消息接收服务。

主动向钉钉建立 WebSocket 长连接接收机器人消息，无需公网 IP / 回调地址，
推荐优先于 webhook_server.py（服务器模式 HTTP 回调）使用。

前置条件：
    1. pip install dingtalk-stream
    2. 钉钉开放平台开发者后台将机器人消息接收模式设置为「Stream 模式」
    3. .env 配置 DINGTALK_APP_KEY / DINGTALK_APP_SECRET（Stream 模式复用应用凭证作为 Client ID/Secret）

须在项目根目录执行（智能体 graph 从项目源码导入）：
    python .qoder/skills/dingtalk-messaging/scripts/stream_client.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# 同目录 dingtalk_lib + 项目根目录（agent.graph）加入 sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parents[3]))

from dingtalk_lib import _env  # noqa: E402

try:
    import dingtalk_stream
    from dingtalk_stream import ChatbotHandler
except ImportError:
    print("[stream] 未安装 dingtalk-stream，请先执行: pip install dingtalk-stream")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("dingtalk-stream")


class AgentChatbotHandler(ChatbotHandler):
    """接收机器人消息 -> 调用项目智能体 -> 通过长连接回复。"""

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        text = (incoming.text.content or "").strip() if incoming.text else ""
        sender_id = incoming.sender_id or "dingtalk-user"
        session_id = incoming.conversation_id or "dingtalk-session"
        logger.info(
            "收到消息 from %s(%s): %s", incoming.sender_nick, sender_id, text
        )

        if not text:
            answer = "请输入您的问题内容"
        else:
            try:
                from agent.graph import chat

                # 同步智能体调用放入线程池，避免阻塞 WebSocket 事件循环
                answer = await asyncio.to_thread(
                    chat,
                    text,
                    f"dingtalk-{sender_id}",
                    session_id,
                )
            except Exception as e:
                logger.error("智能体回答失败: %s", e, exc_info=True)
                answer = f"抱歉，处理暂时失败: {e}"

        try:
            self.reply_text(answer, incoming)
        except Exception as e:
            logger.error("回复消息失败: %s", e)

        return dingtalk_stream.AckMessage.STATUS_OK, "OK"


def main():
    client_id = _env("DINGTALK_APP_KEY")
    client_secret = _env("DINGTALK_APP_SECRET")
    if not client_id or not client_secret:
        print("[stream] 错误: 未配置 DINGTALK_APP_KEY / DINGTALK_APP_SECRET")
        print("        Stream 模式使用应用凭证作为 Client ID/Secret")
        sys.exit(1)

    credential = dingtalk_stream.Credential(client_id, client_secret)
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.register_callback_handler(
        dingtalk_stream.ChatbotMessage.TOPIC, AgentChatbotHandler()
    )
    print("[stream] 正在建立钉钉 Stream 长连接，等待机器人消息（Ctrl+C 退出）")
    client.start_forever()


if __name__ == "__main__":
    main()
