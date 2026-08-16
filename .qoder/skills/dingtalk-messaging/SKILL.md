---
name: dingtalk-messaging
description: 连接钉钉并收发消息：通过工作通知发送文本/Markdown 消息、通过机器人 webhook 回复群消息、接收钉钉机器人消息（推荐 Stream 客户端模式，无需公网 IP；也支持 HTTP 回调服务器模式 + HMAC-SHA256 签名校验）。当需要给钉钉发送消息、回复钉钉群、处理钉钉机器人回调、校验钉钉签名或测试钉钉连通性时使用。
---

# 钉钉消息 Skill

自包含实现钉钉连接、消息发送与消息接收，全部功能位于本 skill 的 `scripts/` 目录，
不依赖项目源码中的任何模块（智能体调用除外）。所有发送函数内置输入校验与异常处理，
失败时返回错误码而非抛异常。

## 前置条件

凭证从环境变量读取（脚本会自动加载当前工作目录下 `.env` 中的变量）：

| 配置项 | 用途 |
|--------|------|
| DINGTALK_APP_KEY / DINGTALK_APP_SECRET | 获取企业 access_token（工作通知发送必需） |
| DINGTALK_ROBOT_CODE | 工作通知的 agent_id |
| DINGTALK_ROBOT_SECRET | 回调签名校验密钥 |
| DINGTALK_ROBOT_TOKEN | 机器人 webhook 回复的备用 token |

## 脚本清单

所有脚本从项目根目录执行：

| 脚本 | 功能 |
|------|------|
| `scripts/dingtalk_lib.py` | 公共库：token 获取、send_text/send_markdown/reply_robot、sign/verify_signature |
| `scripts/send_message.py` | 发送工作通知测试消息（文本/Markdown） |
| `scripts/stream_client.py` | 钉钉 Stream 客户端模式接收服务（推荐，无需公网 IP） |
| `scripts/webhook_server.py` | 钉钉 HTTP 回调接收服务（服务器模式，需公网地址；签名校验 → 智能体回答 → 回复） |
| `scripts/test_webhook.py` | 向 webhook_server 发送带正确签名的模拟回调 |

## 发送消息

```python
import sys
sys.path.insert(0, ".qoder/skills/dingtalk-messaging/scripts")
from dingtalk_lib import send_text, send_markdown, reply_robot

# 工作通知发文本（需 AppKey/AppSecret）
result = send_text(["user001"], "部署完成")
if result.get("errcode") != 0:
    print("发送失败:", result["errmsg"])

# 工作通知发 Markdown
send_markdown(["user001"], "日报", "## 今日进展\n- 完成 RAG 入库")

# 群机器人回复（webhook_token 来自回调体的 sessionWebhook）
reply_robot(webhook_token, "已收到您的问题", at_user_ids=["user001"])
```

或使用命令行：

```bash
python .qoder/skills/dingtalk-messaging/scripts/send_message.py --users user001 --text "测试消息"
python .qoder/skills/dingtalk-messaging/scripts/send_message.py --users user001 --title "标题" --markdown "## 正文"
```

## 接收钉钉消息

### 方式一：Stream 客户端模式（推荐）

主动向钉钉建立 WebSocket 长连接，**无需公网 IP/回调地址**，本地即可运行：

```bash
pip install dingtalk-stream
python .qoder/skills/dingtalk-messaging/scripts/stream_client.py
```

前置：钉钉开放平台开发者后台将机器人消息接收模式设为「Stream 模式」；
凭证复用 `DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET`（作为 Client ID/Secret）。
收到消息后自动调用项目智能体并通过长连接回复。

### 方式二：HTTP 回调服务器模式（需公网地址）

接收链路由独立服务 `webhook_server.py` 提供（不占用主服务端口，默认 8001）：

```bash
python .qoder/skills/dingtalk-messaging/scripts/webhook_server.py --port 8001
```

端点 `POST /dingtalk/webhook` 处理流程：

1. 从 query 参数读取 `timestamp` 与 `sign`
2. 配置了 `DINGTALK_ROBOT_SECRET` 时校验签名，失败返回 403
3. 从回调体提取 `text.content`（消息内容）、`senderId`、`conversationId`、`sessionWebhook`
4. 调用项目智能体（`agent.graph`）生成回答
5. 优先 `reply_robot(sessionWebhook, answer)` 回复群消息，无 token 时回退 `send_text([senderId], answer)`

本地模拟回调测试：

```bash
python .qoder/skills/dingtalk-messaging/scripts/test_webhook.py --text "你好" --port 8001
```

## 错误处理约定

- 发送函数失败不抛异常：返回 `errcode=-1` 表示本地错误（参数为空、无凭证、网络异常），具体原因看 `errmsg`
- `get_access_token` 未配置凭证时返回空串，后续发送自动失败
- 签名校验失败直接返回 403，不处理消息
