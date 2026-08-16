"""钉钉功能自包含库（本 skill 内所有脚本的公共依赖）。

整合了 access_token 获取、工作通知发送（文本/Markdown）、机器人 webhook 回复、
HMAC-SHA256 回调签名生成与校验。不依赖项目源码，凭证从环境变量读取
（首次使用时自动加载当前工作目录下 .env 中的 DINGTALK_* 变量）。

环境变量：
    DINGTALK_APP_KEY / DINGTALK_APP_SECRET  获取企业 access_token（工作通知必需）
    DINGTALK_ROBOT_CODE                     工作通知的 agent_id
    DINGTALK_ROBOT_SECRET                   回调签名校验密钥
    DINGTALK_ROBOT_TOKEN                    机器人 webhook 回复备用 token
    DINGTALK_API_BASE                       API 基址（默认 https://oapi.dingtalk.com）
"""

import base64
import hashlib
import hmac
import logging
import os
import time
from pathlib import Path
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

_ENV_LOADED = False


def _load_dotenv() -> None:
    """简易 .env 加载：将当前工作目录 .env 中的变量写入 os.environ（不覆盖已有值）。"""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
    except Exception as e:
        logger.warning(f".env 加载失败: {e}")


def _env(name: str, default: str = "") -> str:
    _load_dotenv()
    return os.environ.get(name, default)


def _api_base() -> str:
    return _env("DINGTALK_API_BASE", "https://oapi.dingtalk.com")


# ===== 签名（原 dingtalk/crypto.py）=====

def sign(secret: str, timestamp: int) -> str:
    """生成钉钉机器人签名：HMAC-SHA256(secret, "timestamp\\n+secret") 的 base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def verify_signature(
    timestamp: str,
    sign_value: str,
    secret: str,
    max_age_seconds: int = 3600,
) -> bool:
    """校验钉钉回调签名（含时间戳防重放，compare_digest 防时序攻击）。"""
    if not secret or not sign_value or not timestamp:
        return False
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False
    if abs(int(time.time()) - ts) > max_age_seconds:
        return False
    return hmac.compare_digest(sign(secret, ts), sign_value)


def verify_signature_skill(timestamp: str, sign_value: str, max_age_seconds: int = 3600) -> bool:
    """回调签名校验，自动从环境变量 DINGTALK_ROBOT_SECRET 读取密钥。"""
    secret = _env("DINGTALK_ROBOT_SECRET")
    if not secret:
        logger.warning("verify_signature_skill: DINGTALK_ROBOT_SECRET 未配置")
        return False
    return verify_signature(timestamp, sign_value, secret, max_age_seconds)


# ===== Token 与发送（原 dingtalk/client.py + dingtalk/skills/）=====

_token_cache: dict = {}


def get_access_token() -> str:
    """获取企业内部应用 access_token（带本地缓存）。

    端点: GET /gettoken?appkey=...&appsecret=...（钉钉旧版接口要求 GET）
    """
    cached = _token_cache.get("token")
    expire = _token_cache.get("expire_at", 0)
    if cached and time.time() < expire - 60:
        return cached

    app_key = _env("DINGTALK_APP_KEY")
    app_secret = _env("DINGTALK_APP_SECRET")
    if not app_key or not app_secret:
        logger.warning("DINGTALK_APP_KEY/DINGTALK_APP_SECRET 未配置，无法获取 access_token")
        return ""

    url = f"{_api_base()}/gettoken"
    params = {"appkey": app_key, "appsecret": app_secret}
    try:
        resp = httpx.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("errcode") != 0:
            logger.error(f"获取 access_token 失败: {data}")
            return ""
        token = data["access_token"]
        expires_in = data.get("expires_in", 7200)
        _token_cache.update({"token": token, "expire_at": time.time() + expires_in})
        return token
    except Exception as e:
        logger.error(f"获取 access_token 异常: {e}")
        return ""


def _async_send(body: dict) -> dict:
    """工作通知发送公共逻辑（POST /topapi/message/corpconversation/asyncsend_v2）。"""
    token = get_access_token()
    if not token:
        return {"errcode": -1, "errmsg": "无可用 access_token"}
    url = f"{_api_base()}/topapi/message/corpconversation/asyncsend_v2"
    body["agent_id"] = _env("DINGTALK_ROBOT_CODE")
    try:
        resp = httpx.post(url, json=body, params={"access_token": token}, timeout=10)
        return resp.json()
    except Exception as e:
        logger.error(f"工作通知发送异常: {e}")
        return {"errcode": -1, "errmsg": str(e)}


def send_text(user_ids: List[str], content: str) -> dict:
    """通过工作通知发送文本消息。失败不抛异常，返回 errcode=-1 + errmsg。"""
    if not user_ids:
        return {"errcode": -1, "errmsg": "user_ids 不能为空"}
    if not content or not content.strip():
        return {"errcode": -1, "errmsg": "content 不能为空"}
    body = {
        "userid_list": ",".join(user_ids),
        "msg": {"msgtype": "text", "text": {"content": content}},
    }
    return _async_send(body)


def send_markdown(user_ids: List[str], title: str, text: str) -> dict:
    """通过工作通知发送 Markdown 消息。失败不抛异常，返回 errcode=-1 + errmsg。"""
    if not user_ids:
        return {"errcode": -1, "errmsg": "user_ids 不能为空"}
    if not title or not text:
        return {"errcode": -1, "errmsg": "title/text 不能为空"}
    body = {
        "userid_list": ",".join(user_ids),
        "msg": {"msgtype": "markdown", "markdown": {"title": title, "text": text}},
    }
    return _async_send(body)


def reply_robot(webhook_token: str, content: str, at_user_ids: Optional[List[str]] = None) -> bool:
    """通过机器人 webhook 回复群消息（webhook_token 为完整 sessionWebhook URL 或 access_token）。"""
    if not webhook_token:
        logger.warning("reply_robot: webhook_token 为空")
        return False
    if not content or not content.strip():
        logger.warning("reply_robot: content 为空")
        return False
    url = webhook_token if webhook_token.startswith("http") else f"{_api_base()}/robot/send?access_token={webhook_token}"
    body = {"msgtype": "text", "text": {"content": content}}
    if at_user_ids:
        body["at"] = {"atUserIds": at_user_ids}
    try:
        resp = httpx.post(url, json=body, timeout=10)
        return resp.json().get("errcode") == 0
    except Exception as e:
        logger.error(f"机器人回复异常: {e}")
        return False


# ===== 通用 TOP API 请求封装 =====

def _post_api(path: str, body: dict) -> dict:
    """通用钉钉 TOP API POST 请求（复用 access_token 缓存）。

    失败不抛异常，返回 errcode=-1 + errmsg。
    """
    token = get_access_token()
    if not token:
        return {"errcode": -1, "errmsg": "无可用 access_token"}
    url = f"{_api_base()}{path}"
    try:
        resp = httpx.post(url, json=body, params={"access_token": token}, timeout=10)
        return resp.json()
    except Exception as e:
        logger.error(f"API 请求异常 {path}: {e}")
        return {"errcode": -1, "errmsg": str(e)}


# ===== 待办 API =====

def create_todo(user_id: str, title: str, due_time: str,
                description: str = "", priority: int = 3) -> dict:
    """创建钉钉待办事项。

    Args:
        user_id: 钉钉用户ID
        title: 待办标题
        due_time: 截止时间（ISO 8601 格式，如 2026-08-17T15:00:00+08:00）
        description: 待办详情
        priority: 优先级 1-5，5为最高

    Returns:
        钉钉 API 响应 dict，成功 errcode=0
    """
    body = {
        "userid": user_id,
        "title": title,
        "description": description,
        "due_time": due_time,
        "priority": priority,
    }
    return _post_api("/topapi/todo/createTodo", body)


def query_todos(user_id: str, status: str = "pending") -> dict:
    """查询用户待办列表。

    Args:
        user_id: 钉钉用户ID
        status: 过滤状态（all/pending/done）

    Returns:
        钉钉 API 响应 dict，含待办列表
    """
    body = {"userid": user_id, "status": status}
    return _post_api("/topapi/todo/queryTodo", body)


def delete_todo(user_id: str, todo_id: str) -> dict:
    """删除待办事项。"""
    body = {"userid": user_id, "todo_id": todo_id}
    return _post_api("/topapi/todo/deleteTodo", body)


# ===== 会议/日程 API =====

def create_schedule(user_id: str, title: str, start_time: str, end_time: str,
                    attendees: Optional[List[str]] = None,
                    location: str = "", description: str = "") -> dict:
    """创建钉钉日程/会议。

    Args:
        user_id: 创建者钉钉用户ID
        title: 会议主题
        start_time: 开始时间（ISO 8601）
        end_time: 结束时间（ISO 8601）
        attendees: 参会人钉钉用户ID列表
        location: 会议地点
        description: 会议描述

    Returns:
        钉钉 API 响应 dict，含 schedule_id
    """
    body = {
        "userid": user_id,
        "summary": title,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "description": description,
        "attendees": attendees or [],
    }
    return _post_api("/topapi/calendar/createSchedule", body)


def query_schedules(user_id: str, date_from: str, date_to: str) -> dict:
    """查询用户日程列表。

    Args:
        user_id: 钉钉用户ID
        date_from: 查询起始日期（ISO 8601）
        date_to: 查询结束日期（ISO 8601）

    Returns:
        钉钉 API 响应 dict，含日程列表
    """
    body = {"userid": user_id, "start_time": date_from, "end_time": date_to}
    return _post_api("/topapi/calendar/querySchedule", body)


def update_schedule(user_id: str, schedule_id: str, updates: dict) -> dict:
    """修改日程。

    Args:
        user_id: 钉钉用户ID
        schedule_id: 日程ID
        updates: 需更新的字段 dict（title/start_time/end_time/location 等）
    """
    body = {"userid": user_id, "schedule_id": schedule_id, "updates": updates}
    return _post_api("/topapi/calendar/updateSchedule", body)


def delete_schedule(user_id: str, schedule_id: str, notify: bool = True) -> dict:
    """取消日程/会议。

    Args:
        user_id: 钉钉用户ID
        schedule_id: 日程ID
        notify: 是否通知参会人
    """
    body = {"userid": user_id, "schedule_id": schedule_id, "notify": notify}
    return _post_api("/topapi/calendar/deleteSchedule", body)


# ===== 通讯录查询（参会人解析）=====

def get_user_by_mobile(mobile: str) -> dict:
    """通过手机号查询钉钉用户ID。"""
    body = {"mobile": mobile}
    return _post_api("/topapi/v2/user/getbymobile", body)


def search_users_by_name(name: str, cursor: int = 0, size: int = 10) -> dict:
    """按姓名查询用户列表。

    Returns:
        钉钉 API 响应 dict，含用户列表
    """
    body = {"name": name, "cursor": cursor, "size": size}
    return _post_api("/topapi/v2/user/list", body)
