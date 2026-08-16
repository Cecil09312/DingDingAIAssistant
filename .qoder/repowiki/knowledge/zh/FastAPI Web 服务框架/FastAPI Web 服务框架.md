---
kind: external_dependency
name: FastAPI Web 服务框架
slug: fastapi-uvicorn
category: external_dependency
category_hints:
    - framework_behavior
scope:
    - '**'
---

使用 FastAPI 0.115+ 构建 Web 服务，提供钉钉 Webhook 回调、健康检查、Web 聊天界面等 HTTP API。Uvicorn 作为 ASGI 服务器，启用 --reload 热重载功能，仅监视 *.py 文件变更避免误触发。静态文件目录 static/ 存放前端 HTML 页面。HTTPX 用于钉钉 API 客户端的网络请求。