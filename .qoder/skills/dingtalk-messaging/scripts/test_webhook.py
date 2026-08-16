"""钉钉 Webhook 回调模拟脚本。

向 webhook_server.py 提供的 /dingtalk/webhook 端点发送模拟回调请求，
自动携带正确的 timestamp/sign 签名，用于测试消息接收链路。

前置：先启动接收服务：
    python .qoder/skills/dingtalk-messaging/scripts/webhook_server.py --port 8001

用法（在项目根目录执行）：
    python .qoder/skills/dingtalk-messaging/scripts/test_webhook.py --text "你好"
    python .qoder/skills/dingtalk-messaging/scripts/test_webhook.py --text "你好" --port 8001
"""

import argparse
import sys
import time
from pathlib import Path

# 同目录的 dingtalk_lib 加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from dingtalk_lib import sign  # noqa: E402
from dingtalk_lib import _env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="钉钉 Webhook 回调模拟测试")
    parser.add_argument("--text", default="你好", help="模拟的用户消息内容")
    parser.add_argument("--port", type=int, default=8001, help="webhook_server 服务端口")
    args = parser.parse_args()

    # 构造签名参数（配置了 secret 时服务端会校验）
    params = {}
    secret = _env("DINGTALK_ROBOT_SECRET")
    if secret:
        ts = int(time.time())
        params = {"timestamp": str(ts), "sign": sign(secret, ts)}

    # 模拟钉钉 outgoing webhook 请求体
    body = {
        "msgtype": "text",
        "text": {"content": args.text},
        "senderId": "test-user",
        "senderNick": "测试用户",
        "conversationId": "test-session",
    }

    url = f"http://localhost:{args.port}/dingtalk/webhook"
    print(f"发送模拟回调到 {url} ...")
    try:
        resp = httpx.post(url, json=body, params=params, timeout=120)
        print(f"状态码: {resp.status_code}")
        print(f"响应: {resp.text}")
        return 0 if resp.status_code == 200 else 1
    except httpx.ConnectError:
        print(
            f"错误: 无法连接 {url}，请先启动接收服务: "
            f"python .qoder/skills/dingtalk-messaging/scripts/webhook_server.py --port {args.port}"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
