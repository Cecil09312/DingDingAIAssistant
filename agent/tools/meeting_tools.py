"""会议工具执行器。

调用 dingtalk_lib 的会议 API 方法，处理参数转换、参会人解析和结果格式化。
"""

import os
import sys

# 将 skill scripts 目录加入 sys.path 以导入 dingtalk_lib
_SKILL_SCRIPTS_PATH = os.path.join(
    os.getcwd(), ".qoder", "skills", "dingtalk-messaging", "scripts"
)
if _SKILL_SCRIPTS_PATH not in sys.path:
    sys.path.insert(0, _SKILL_SCRIPTS_PATH)


def execute_create_meeting(params: dict, user_id: str) -> dict:
    """执行创建会议。

    Args:
        params: LLM 提取的参数（title, start_time, end_time, attendees, location, description）
        user_id: 创建者钉钉用户ID

    Returns:
        钉钉 API 响应 dict
    """
    from dingtalk_lib import create_schedule

    # 解析参会人（姓名/手机号 → 钉钉用户ID）
    attendees_names = params.get("attendees", [])
    attendee_ids = _resolve_attendees(attendees_names)

    # 默认会议时长 1 小时
    start_time = params.get("start_time", "")
    end_time = params.get("end_time", "")
    if not end_time and start_time:
        end_time = _add_one_hour(start_time)

    return create_schedule(
        user_id=user_id,
        title=params.get("title", ""),
        start_time=start_time,
        end_time=end_time,
        attendees=attendee_ids,
        location=params.get("location", ""),
        description=params.get("description", ""),
    )


def execute_cancel_meeting(params: dict, user_id: str) -> dict:
    """执行取消会议。

    优先用 meeting_id，无 ID 时按标题查询后取消。
    """
    from dingtalk_lib import delete_schedule, query_schedules
    from datetime import datetime, timedelta

    meeting_id = params.get("meeting_id", "")
    meeting_title = params.get("meeting_title", "")
    notify = params.get("notify_attendees", True)

    # 无 meeting_id 时按标题查询
    if not meeting_id and meeting_title:
        now = datetime.now()
        date_from = now.isoformat()
        date_to = (now + timedelta(days=30)).isoformat()
        query_result = query_schedules(user_id, date_from, date_to)
        if query_result.get("errcode") == 0:
            schedules = query_result.get("schedules", query_result.get("result", []))
            for sched in schedules:
                if meeting_title in sched.get("summary", ""):
                    meeting_id = sched.get("schedule_id", "")
                    break
        if not meeting_id:
            return {"errcode": -1, "errmsg": f"未找到标题包含「{meeting_title}」的会议"}

    if not meeting_id:
        return {"errcode": -1, "errmsg": "缺少会议ID或会议标题"}

    return delete_schedule(user_id, meeting_id, notify)


def execute_update_meeting(params: dict, user_id: str) -> dict:
    """执行修改会议。

    优先用 meeting_id，无 ID 时按标题查询后修改。
    """
    from dingtalk_lib import update_schedule, query_schedules
    from datetime import datetime, timedelta

    meeting_id = params.get("meeting_id", "")
    meeting_title = params.get("meeting_title", "")
    updates = params.get("updates", {})

    # 无 meeting_id 时按标题查询
    if not meeting_id and meeting_title:
        now = datetime.now()
        date_from = now.isoformat()
        date_to = (now + timedelta(days=30)).isoformat()
        query_result = query_schedules(user_id, date_from, date_to)
        if query_result.get("errcode") == 0:
            schedules = query_result.get("schedules", query_result.get("result", []))
            for sched in schedules:
                if meeting_title in sched.get("summary", ""):
                    meeting_id = sched.get("schedule_id", "")
                    break
        if not meeting_id:
            return {"errcode": -1, "errmsg": f"未找到标题包含「{meeting_title}」的会议"}

    if not meeting_id:
        return {"errcode": -1, "errmsg": "缺少会议ID或会议标题"}

    return update_schedule(user_id, meeting_id, updates)


def execute_query_meetings(params: dict, user_id: str) -> dict:
    """执行查询会议。"""
    from dingtalk_lib import query_schedules
    from agent.tools.time_parser import parse_natural_date_range

    date_from = params.get("date_from", "")
    date_to = params.get("date_to", "")

    # 未指定时间范围时默认查询本周
    if not date_from:
        date_from, date_to = parse_natural_date_range("本周")

    return query_schedules(user_id, date_from, date_to)


def format_meeting_result(tool_name: str, result: dict) -> str:
    """格式化会议工具执行结果为用户可读文本。"""
    if result.get("errcode") != 0:
        return f"操作失败: {result.get('errmsg', '未知错误')}"

    if tool_name == "create_meeting":
        sched_id = result.get("schedule_id", "")
        return f"会议已创建成功！" + (f"\n会议ID: {sched_id}" if sched_id else "")

    elif tool_name == "cancel_meeting":
        return "会议已取消，已通知参会人。"

    elif tool_name == "update_meeting":
        return "会议已更新，已通知参会人。"

    elif tool_name == "query_meetings":
        schedules = result.get("schedules", result.get("result", []))
        if not schedules:
            return "该时间段内暂无会议。"
        lines = [f"共有 {len(schedules)} 场会议："]
        for i, sched in enumerate(schedules, 1):
            title = sched.get("summary", "无主题")
            start = sched.get("start_time", "未知时间")
            location = sched.get("location", "")
            loc_str = f" @ {location}" if location else ""
            lines.append(f"  {i}. {title}\n     时间: {start}{loc_str}")
        return "\n".join(lines)

    return "操作完成。"


def format_meeting_confirmation(tool_name: str, params: dict) -> str:
    """格式化会议确认消息。"""
    if tool_name == "create_meeting":
        attendees = params.get("attendees", [])
        return (
            f"请确认以下会议信息：\n"
            f"  主题: {params.get('title', '')}\n"
            f"  时间: {params.get('start_time', '未指定')} ~ {params.get('end_time', '未指定')}\n"
            f"  参会人: {', '.join(attendees) if attendees else '无'}\n"
            f"  地点: {params.get('location', '未指定')}\n"
            f"\n回复「确认」执行，或修改后重新发送。"
        )
    elif tool_name == "cancel_meeting":
        title = params.get("meeting_title", params.get("meeting_id", ""))
        return f"确认取消会议「{title}」吗？\n回复「确认」执行。"
    elif tool_name == "update_meeting":
        title = params.get("meeting_title", params.get("meeting_id", ""))
        updates = params.get("updates", {})
        update_desc = ", ".join(f"{k}={v}" for k, v in updates.items())
        return f"确认修改会议「{title}」：{update_desc}？\n回复「确认」执行。"
    return f"确认执行操作 {tool_name} 吗？回复「确认」执行。"


def _resolve_attendees(names: list) -> list:
    """将姓名/手机号列表解析为钉钉用户ID列表。

    解析失败的名字保留原值（API 调用时会跳过无效ID）。
    """
    from dingtalk_lib import get_user_by_mobile, search_users_by_name
    import re

    user_ids = []
    for name in names:
        # 手机号直接查询
        if re.match(r"^1\d{10}$", name):
            result = get_user_by_mobile(name)
            if result.get("errcode") == 0 and result.get("userid"):
                user_ids.append(result["userid"])
            else:
                user_ids.append(name)  # 保留原值
        else:
            # 按姓名查询
            result = search_users_by_name(name)
            if result.get("errcode") == 0:
                users = result.get("result", {}).get("list", [])
                if users:
                    user_ids.append(users[0].get("userid", name))
                else:
                    user_ids.append(name)
            else:
                user_ids.append(name)
    return user_ids


def _add_one_hour(iso_time: str) -> str:
    """给 ISO 8601 时间加 1 小时。"""
    from datetime import datetime, timedelta

    try:
        dt = datetime.fromisoformat(iso_time)
        return (dt + timedelta(hours=1)).isoformat()
    except Exception:
        return iso_time
