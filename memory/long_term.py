"""长期记忆管理：基于 SQLite 持久化存储。

采用分层结构化记忆，保证关键个人信息（姓名/称呼/偏好）不丢失：
  - user_profile:     用户画像（单值可更新，最高优先级）
  - user_facts:       关键事实（多条，带优先级 1-10）
  - user_memory:      最新全局摘要（兼容旧表）
  - summary_history:  摘要历史（兼容旧表）
  - user_prefs:       旧版偏好表（启动时自动迁移，兼容保留）
  - session_summary:  会话压缩摘要（短期记忆超窗后的滚动摘要）
  - qa_memory:        问答记忆缓存（问题向量 + 历史答案，相似问题直接复用）
  - memory_meta:      元数据（迁移标记等）

加载策略见 build_memory_context：画像/关键事实硬保留，
最近摘要/关键词召回/旧摘要按预算逐层填充。
"""

import re
import sqlite3
import threading
import time
from typing import Dict, List, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

# 连接锁（SQLite 写操作需要串行化，同一进程内）
_db_lock = threading.Lock()
_conn = None


def _get_conn() -> sqlite3.Connection:
    """返回 SQLite 连接单例（开启 WAL 模式支持并发读）。"""
    global _conn
    if _conn is not None:
        return _conn
    from config.settings import get_settings

    s = get_settings()
    _conn = sqlite3.connect(
        str(s.long_term_db_file),
        check_same_thread=False,
    )
    _conn.row_factory = sqlite3.Row
    # 开启 WAL 模式：允许并发读，写操作仍串行但不会阻塞读
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA busy_timeout=5000")
    _init_tables(_conn)
    return _conn


def _init_tables(conn: sqlite3.Connection) -> None:
    """初始化表结构（幂等）。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_memory (
            user_id    TEXT PRIMARY KEY,
            summary    TEXT DEFAULT '',
            updated_at INTEGER DEFAULT 0,
            turn_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS user_prefs (
            user_id TEXT NOT NULL,
            key     TEXT NOT NULL,
            value   TEXT DEFAULT '',
            PRIMARY KEY (user_id, key)
        );

        CREATE TABLE IF NOT EXISTS summary_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            summary    TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_history_user
            ON summary_history(user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS user_profile (
            user_id    TEXT NOT NULL,
            field      TEXT NOT NULL,
            value      TEXT DEFAULT '',
            updated_at INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, field)
        );

        CREATE TABLE IF NOT EXISTS user_facts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            fact       TEXT NOT NULL,
            priority   INTEGER DEFAULT 5,
            source     TEXT DEFAULT '',
            created_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_facts_user
            ON user_facts(user_id, priority, updated_at DESC);

        CREATE TABLE IF NOT EXISTS session_summary (
            session_id    TEXT PRIMARY KEY,
            user_id       TEXT,
            summary       TEXT DEFAULT '',
            covered_until INTEGER DEFAULT 0,
            updated_at    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS memory_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS qa_memory (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            question   TEXT NOT NULL,
            answer     TEXT NOT NULL,
            embedding  BLOB,
            hit_count  INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0,
            updated_at INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_qa_user
            ON qa_memory(user_id, updated_at DESC);
        """
    )
    conn.commit()
    _migrate_legacy_prefs(conn)


# 用户画像字段与中文标签
PROFILE_FIELD_LABELS = {
    "name": "姓名",
    "nickname": "称呼",
    "role": "角色",
    "project": "项目",
    "company": "公司/团队",
}


def _migrate_legacy_prefs(conn: sqlite3.Connection) -> None:
    """一次性将旧版 user_prefs 数据迁移到 user_profile / user_facts（幂等）。"""
    row = conn.execute("SELECT value FROM memory_meta WHERE key='prefs_migrated'").fetchone()
    if row:
        return
    field_map = {
        "name": "name", "姓名": "name",
        "nickname": "nickname", "称呼": "nickname",
        "role": "role", "角色": "role",
        "project": "project", "项目": "project",
        "company": "company", "公司": "company",
    }
    now = int(time.time())
    prefs = conn.execute("SELECT user_id, key, value FROM user_prefs").fetchall()
    for r in prefs:
        if not r["value"]:
            continue
        field = field_map.get(r["key"])
        if field:
            conn.execute(
                "INSERT INTO user_profile (user_id, field, value, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, field) DO NOTHING",
                (r["user_id"], field, r["value"], now),
            )
        else:
            conn.execute(
                "INSERT INTO user_facts (user_id, fact, priority, source, created_at, updated_at) "
                "VALUES (?, ?, 3, 'legacy', ?, ?)",
                (r["user_id"], f"{r['key']}: {r['value']}", now, now),
            )
    conn.execute("INSERT OR REPLACE INTO memory_meta (key, value) VALUES ('prefs_migrated', '1')")
    conn.commit()


def save_summary(user_id: str, summary: str) -> None:
    """保存/更新用户对话摘要到长期记忆。"""
    if not user_id:
        return
    conn = _get_conn()
    now = int(time.time())
    with _db_lock:
        conn.execute(
            "INSERT INTO user_memory (user_id, summary, updated_at, turn_count) "
            "VALUES (?, ?, ?, 0) "
            "ON CONFLICT(user_id) DO UPDATE SET summary=excluded.summary, updated_at=excluded.updated_at",
            (user_id, summary, now),
        )
        # 追加摘要到历史表
        conn.execute(
            "INSERT INTO summary_history (user_id, summary, created_at) VALUES (?, ?, ?)",
            (user_id, summary, now),
        )
        # 仅保留最近 20 条摘要
        conn.execute(
            "DELETE FROM summary_history WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM summary_history WHERE user_id=? ORDER BY created_at DESC LIMIT 20)",
            (user_id, user_id),
        )
        conn.commit()


def save_pref(user_id: str, key: str, value: str) -> None:
    """保存用户偏好（如姓名、角色、关注点等）。"""
    if not user_id:
        return
    conn = _get_conn()
    with _db_lock:
        conn.execute(
            "INSERT INTO user_prefs (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
            (user_id, key, value),
        )
        conn.commit()


def get_prefs(user_id: str) -> dict:
    """获取用户所有偏好（旧版接口，兼容保留）。"""
    if not user_id:
        return {}
    conn = _get_conn()
    rows = conn.execute(
        "SELECT key, value FROM user_prefs WHERE user_id=?", (user_id,)
    ).fetchall()
    return {row["key"]: row["value"] for row in rows} if rows else {}


# ===== 用户画像（P0 最高优先级，单值可更新）=====

def save_profile(user_id: str, field: str, value: str) -> bool:
    """保存/更新用户画像字段。返回是否实际更新（空值或同值不更新）。"""
    if not user_id or not field:
        return False
    value = (value or "").strip()
    if not value:
        return False
    conn = _get_conn()
    now = int(time.time())
    with _db_lock:
        row = conn.execute(
            "SELECT value FROM user_profile WHERE user_id=? AND field=?", (user_id, field)
        ).fetchone()
        if row and row["value"] == value:
            return False
        conn.execute(
            "INSERT INTO user_profile (user_id, field, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, field) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (user_id, field, value, now),
        )
        conn.commit()
    return True


def get_profile(user_id: str) -> Dict[str, str]:
    """获取用户画像（field -> value）。"""
    if not user_id:
        return {}
    conn = _get_conn()
    rows = conn.execute(
        "SELECT field, value FROM user_profile WHERE user_id=? AND value != ''", (user_id,)
    ).fetchall()
    return {row["field"]: row["value"] for row in rows} if rows else {}


# ===== 关键事实（P0/P1，多条带优先级）=====

def save_fact(user_id: str, fact: str, priority: int = 5, source: str = "") -> bool:
    """保存一条关键事实（完全重复则仅提升优先级/更新时间）。返回是否新增。"""
    if not user_id:
        return False
    fact = (fact or "").strip()
    if not fact:
        return False
    priority = max(1, min(10, int(priority)))
    conn = _get_conn()
    now = int(time.time())
    with _db_lock:
        row = conn.execute(
            "SELECT id, priority FROM user_facts WHERE user_id=? AND fact=?", (user_id, fact)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE user_facts SET priority=MIN(priority, ?), updated_at=? WHERE id=?",
                (priority, now, row["id"]),
            )
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO user_facts (user_id, fact, priority, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, fact, priority, source, now, now),
        )
        conn.commit()
    return True


def get_facts(user_id: str, max_priority: int = 0, limit: int = 0) -> List[str]:
    """获取关键事实列表（按优先级升序、时间降序）。

    Args:
        max_priority: 仅返回 priority <= 该值的事实，0 表示不过滤
        limit: 条数上限，0 表示不限
    """
    if not user_id:
        return []
    conn = _get_conn()
    sql = "SELECT fact FROM user_facts WHERE user_id=?"
    params: list = [user_id]
    if max_priority > 0:
        sql += " AND priority<=?"
        params.append(max_priority)
    sql += " ORDER BY priority ASC, updated_at DESC"
    if limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [row["fact"] for row in rows] if rows else []


def search_facts(user_id: str, keywords: List[str], limit: int = 5) -> List[str]:
    """关键词召回：返回 fact 中包含任一关键词的事实（按优先级排序）。"""
    if not user_id or not keywords:
        return []
    conn = _get_conn()
    out: List[str] = []
    seen = set()
    for kw in keywords:
        if not kw:
            continue
        rows = conn.execute(
            "SELECT fact FROM user_facts WHERE user_id=? AND fact LIKE ? "
            "ORDER BY priority ASC, updated_at DESC LIMIT ?",
            (user_id, f"%{kw}%", limit),
        ).fetchall()
        for r in rows:
            if r["fact"] not in seen:
                seen.add(r["fact"])
                out.append(r["fact"])
        if len(out) >= limit:
            break
    return out[:limit]


# ===== 会话压缩摘要（短期记忆超窗后的滚动摘要）=====

def save_session_summary(session_id: str, user_id: str, summary: str, covered_until: int) -> None:
    """保存/更新会话压缩摘要（covered_until 为已覆盖的消息条数）。"""
    if not session_id or not summary:
        return
    conn = _get_conn()
    now = int(time.time())
    with _db_lock:
        conn.execute(
            "INSERT INTO session_summary (session_id, user_id, summary, covered_until, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "user_id=excluded.user_id, summary=excluded.summary, "
            "covered_until=excluded.covered_until, updated_at=excluded.updated_at",
            (session_id, user_id, summary, covered_until, now),
        )
        conn.commit()


def get_session_summary(session_id: str) -> str:
    """获取会话压缩摘要。"""
    if not session_id:
        return ""
    conn = _get_conn()
    row = conn.execute(
        "SELECT summary FROM session_summary WHERE session_id=?", (session_id,)
    ).fetchone()
    return row["summary"] if row and row["summary"] else ""


def get_session_coverage(session_id: str) -> int:
    """获取会话摘要已覆盖的消息条数（无记录返回 0）。"""
    if not session_id:
        return 0
    conn = _get_conn()
    row = conn.execute(
        "SELECT covered_until FROM session_summary WHERE session_id=?", (session_id,)
    ).fetchone()
    return int(row["covered_until"]) if row and row["covered_until"] else 0


def get_summary(user_id: str) -> str:
    """获取用户的最新摘要。"""
    if not user_id:
        return ""
    conn = _get_conn()
    row = conn.execute(
        "SELECT summary FROM user_memory WHERE user_id=?", (user_id,)
    ).fetchone()
    return row["summary"] if row and row["summary"] else ""


def get_history(user_id: str, limit: int = 5) -> List[str]:
    """获取最近的若干条摘要历史（按时间倒序）。"""
    if not user_id:
        return []
    conn = _get_conn()
    rows = conn.execute(
        "SELECT summary FROM summary_history WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [row["summary"] for row in rows] if rows else []


def get_context(user_id: str) -> str:
    """拼装长期记忆上下文字符串（旧版接口，兼容保留；新代码请用 build_memory_context）。"""
    return build_memory_context(user_id)


def _extract_keywords(text: str, max_kw: int = 5) -> List[str]:
    """从用户输入中切出候选关键词（长度≥2 的连续片段）。"""
    tokens = re.split(r"[\s，。,．.！!？?；;：:、\"'()（）【】\[\]]+", text or "")
    return [t.strip() for t in tokens if len(t.strip()) >= 2][:max_kw]


# 分层预算（字符）：P0 层硬保留不受总预算约束
_BUDGET_PROFILE = 300
_BUDGET_KEY_FACTS = 500
_BUDGET_RECALLED = 300
_MAX_KEY_FACTS = 10
_MAX_RECALLED = 5


def build_memory_context(user_id: str, query: str = "", budget: int = 0) -> str:
    """按优先级分层构建长期记忆上下文（供生成节点注入 prompt）。

    层次（防丢失策略）：
      P0 用户画像（姓名/称呼等）+ 关键事实（priority<=3）——硬保留，永不截断
      P1 最近 2 条摘要 + 关键词召回事实——按预算逐条填充，超额丢旧条
      P2 更早摘要——剩余预算填充，超额丢弃

    Args:
        user_id: 用户标识
        query: 当前用户输入（用于关键词召回）
        budget: 总字符预算，0 表示读配置 memory_context_budget

    Returns:
        分节格式化的记忆上下文（【用户画像】/【已知事实】/【历史摘要】）
    """
    if not user_id:
        return ""
    if budget <= 0:
        from config.settings import get_settings

        budget = get_settings().memory_context_budget

    sections: List[str] = []

    # ---- P0：用户画像（硬保留）----
    profile = get_profile(user_id)
    if profile:
        lines = [
            f"- {PROFILE_FIELD_LABELS.get(k, k)}: {v}"
            for k, v in profile.items()
            if v
        ][: _MAX_KEY_FACTS]
        profile_section = "【用户画像】\n" + "\n".join(lines)
        sections.append(profile_section[: _BUDGET_PROFILE + 100])  # 画像极少超长，仅做保护

    # ---- P0：关键事实 priority<=3（硬保留，条数封顶）----
    key_facts = get_facts(user_id, max_priority=3, limit=_MAX_KEY_FACTS)
    fact_lines = [f"{i}. {f}" for i, f in enumerate(key_facts, 1)]

    # ---- P1：关键词召回事实（补充到已知事实节，预算内）----
    used = sum(len(s) for s in sections) + sum(len(l) + 1 for l in fact_lines)
    if query:
        recalled = [f for f in search_facts(user_id, _extract_keywords(query), limit=_MAX_RECALLED)
                    if f not in key_facts]
        idx = len(fact_lines)
        for f in recalled:
            line = f"{idx + 1}. {f}"
            if used + len(line) + 1 > budget:
                break  # 超出总预算，召回层先丢弃（P0 层不受影响）
            idx += 1
            fact_lines.append(line)
            used += len(line) + 1

    if fact_lines:
        sections.append("【已知事实】\n" + "\n".join(fact_lines))

    used = sum(len(s) + 2 for s in sections)

    # ---- P1：最近 2 条摘要；P2：更早摘要（逐条填充，超额丢旧条）----
    summaries = get_history(user_id, limit=5)
    kept: List[str] = []
    for sm in summaries:
        sm = sm.strip()
        if not sm:
            continue
        if used + len(sm) + 3 > budget:
            break  # 预算耗尽，更早的摘要丢弃
        kept.append(sm)
        used += len(sm) + 3
    if kept:
        sections.append("【历史摘要】\n" + "\n".join(f"- {s}" for s in kept))

    return "\n\n".join(sections)


def increment_turn(user_id: str) -> int:
    """增加对话轮次计数，返回当前轮次。"""
    if not user_id:
        return 0
    conn = _get_conn()
    with _db_lock:
        row = conn.execute(
            "SELECT turn_count FROM user_memory WHERE user_id=?", (user_id,)
        ).fetchone()
        new_count = (row["turn_count"] + 1) if row else 1
        now = int(time.time())
        conn.execute(
            "INSERT INTO user_memory (user_id, summary, updated_at, turn_count) "
            "VALUES (?, '', ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET turn_count=excluded.turn_count, updated_at=excluded.updated_at",
            (user_id, now, new_count),
        )
        conn.commit()
    return new_count


def get_turn_count(user_id: str) -> int:
    """获取当前对话轮次。"""
    if not user_id:
        return 0
    conn = _get_conn()
    row = conn.execute(
        "SELECT turn_count FROM user_memory WHERE user_id=?", (user_id,)
    ).fetchone()
    return int(row["turn_count"]) if row and row["turn_count"] else 0


SUMMARY_PROMPT = """请将以下对话历史总结为简洁的要点，保留关键事实、用户偏好和重要上下文，便于后续对话引用。直接输出摘要，不要添加多余说明。

对话历史：
{conversation}"""

# 增量合并摘要 prompt：携带旧摘要，强制保留关键信息（防止覆盖式丢失）
MERGE_SUMMARY_PROMPT = """你是记忆管理器。请将“旧摘要”与“新对话”合并为一份更新后的摘要，并抽取关键事实。
要求：
1. 必须保留旧摘要中的用户姓名、称呼、偏好等关键信息，不得删除；
2. 合并新对话中的重要事实，去除已被更新的过时信息；
3. 摘要简洁，不超过 300 字；
4. 只输出 JSON：{{"summary": "...", "key_facts": ["..."]}}，不要输出其他内容。

旧摘要：
{old_summary}

新对话：
{conversation}"""


def _messages_to_text(messages: List[BaseMessage], limit: int = 10) -> str:
    """将消息列表拼为对话文本（仅取最近 limit 条）。"""
    lines = []
    for m in messages[-limit:]:
        role = "用户" if isinstance(m, HumanMessage) else ("助手" if isinstance(m, AIMessage) else "系统")
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


def summarize_messages(messages: List[BaseMessage], old_summary: str = "") -> Tuple[str, List[str]]:
    """用 LLM 生成/合并对话摘要。

    Args:
        messages: 对话消息列表
        old_summary: 旧摘要（非空时执行增量合并，保证关键信息不被冲掉）

    Returns:
        (摘要文本, 关键事实列表)
    """
    if not messages:
        return "", []
    conversation = _messages_to_text(messages)
    try:
        from langchain_openai import ChatOpenAI

        from config.settings import get_settings

        s = get_settings()
        llm = ChatOpenAI(
            model=s.llm_model,
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            temperature=0.2,
        )
        if old_summary.strip():
            resp = llm.invoke(MERGE_SUMMARY_PROMPT.format(old_summary=old_summary, conversation=conversation))
        else:
            resp = llm.invoke(SUMMARY_PROMPT.format(conversation=conversation))
        content = resp.content if isinstance(resp.content, str) else str(resp.content)

        # 合并模式尝试解析 JSON，提取 key_facts
        if old_summary.strip():
            import json

            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(content[start:end + 1])
                    summary = str(data.get("summary", "")).strip() or content.strip()
                    facts = [str(f).strip() for f in (data.get("key_facts") or []) if str(f).strip()]
                    return summary, facts[:10]
                except (json.JSONDecodeError, AttributeError):
                    pass
        return content.strip(), []
    except Exception as e:
        # LLM 不可用时做简单截断
        print(f"[long_term] LLM 摘要失败，使用简单截断: {e}")
        fallback = conversation[:500]
        return (f"{old_summary}\n{fallback}".strip() if old_summary else fallback), []


def summarize_and_store(user_id: str, messages: List[BaseMessage]) -> str:
    """生成/合并对话摘要并写入长期记忆（增量合并，关键事实单独落库防丢失）。

    Args:
        user_id: 用户标识
        messages: 本轮对话的消息列表

    Returns:
        生成的摘要文本
    """
    if not user_id or not messages:
        return ""

    old_summary = get_summary(user_id)
    summary, key_facts = summarize_messages(messages, old_summary=old_summary)
    if summary:
        save_summary(user_id, summary)
    # 关键事实单独落库（priority=3 属 P0 硬保留层），避免随摘要被冲掉
    for fact in key_facts:
        save_fact(user_id, fact, priority=3, source="summary")
    return summary


# ===== 规则快速抽取（零 LLM 成本，保证姓名等高优先级信息当轮落库）=====

_NAME_PATTERNS = [
    re.compile(r"记住我叫\s*([^\s，。,．.！!？?]{1,10})"),
    re.compile(r"请记住我是\s*([^\s，。,．.！!？?]{1,10})"),
    re.compile(r"我(?:的)?名字(?:是|叫)\s*([^\s，。,．.！!？?]{1,10})"),
    re.compile(r"我叫\s*([^\s，。,．.！!？?]{1,10})"),
]
_PROJECT_PATTERN = re.compile(r"我的项目(?:是|叫|名为)?\s*([^\s，。,．.！!？?]{1,20})")
_COMPANY_PATTERN = re.compile(r"我(?:在|所在的)(?:公司|团队|单位)(?:是|叫)?\s*([^\s，。,．.！!？?]{1,20})")
# 疑问词黑名单：避免“我叫什么”这类提问被误抽为姓名
_NAME_BLACKLIST = {"什么", "谁", "啥", "什么人", "什么名字"}


def extract_key_info(text: str) -> Tuple[Dict[str, str], List[str]]:
    """规则快速抽取高优先级信息（不依赖 LLM）。

    Returns:
        (profile_updates, facts)：画像字段更新与附加事实列表
    """
    profile: Dict[str, str] = {}
    facts: List[str] = []
    if not text:
        return profile, facts

    for pat in _NAME_PATTERNS:
        m = pat.search(text)
        if m:
            name = m.group(1).strip("，。,．.！!？? \"'")
            if name and name not in _NAME_BLACKLIST:
                profile["name"] = name
            break

    m = _PROJECT_PATTERN.search(text)
    if m and m.group(1) not in _NAME_BLACKLIST:
        profile["project"] = m.group(1)
        facts.append(f"用户的项目是{m.group(1)}")

    m = _COMPANY_PATTERN.search(text)
    if m and m.group(1) not in _NAME_BLACKLIST:
        profile["company"] = m.group(1)
        facts.append(f"用户的公司/团队是{m.group(1)}")

    return profile, facts


def apply_extraction(user_id: str, user_input: str) -> Dict[str, object]:
    """规则快速路径：抽取并保存高优先级信息，返回实际保存内容概要。"""
    if not user_id or not user_input:
        return {}
    profile, facts = extract_key_info(user_input)
    saved: Dict[str, object] = {}
    for field, value in profile.items():
        if save_profile(user_id, field, value):
            saved[field] = value
    saved_facts = []
    for f in facts:
        if save_fact(user_id, f, priority=1, source="rule"):
            saved_facts.append(f)
    if saved_facts:
        saved["facts"] = saved_facts
    return saved


# ---------- 问答记忆缓存（相似问题直接复用历史答案，跳过大模型） ----------

def _vec_to_blob(vec: List[float]) -> bytes:
    """向量序列化为 BLOB（float32 小端）。"""
    import struct

    return struct.pack(f"<{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> List[float]:
    """BLOB 反序列化为向量。"""
    import struct

    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度（长度不一致或零向量返回 0）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def save_qa(
    user_id: str,
    question: str,
    answer: str,
    embedding: List[float] | None = None,
    max_records: int = 200,
) -> bool:
    """保存问答对到问答记忆（相同问题更新答案，超上限淘汰最旧）。

    Args:
        user_id: 用户标识
        question: 用户问题原文
        answer: 智能体回答
        embedding: 问题向量（用于相似度匹配，可为 None）
        max_records: 每用户最多保留条数，超出按更新时间淘汰

    Returns:
        是否实际写入/更新
    """
    if not user_id or not question.strip() or not answer.strip():
        return False
    now = int(time.time())
    blob = _vec_to_blob(embedding) if embedding else None
    with _db_lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT id FROM qa_memory WHERE user_id=? AND question=?",
            (user_id, question),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE qa_memory SET answer=?, embedding=COALESCE(?, embedding), updated_at=? WHERE id=?",
                (answer, blob, now, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO qa_memory (user_id, question, answer, embedding, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, question, answer, blob, now, now),
            )
        # 超上限淘汰最旧记录
        if max_records > 0:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM qa_memory WHERE user_id=?", (user_id,)
            ).fetchone()["c"]
            if count > max_records:
                conn.execute(
                    "DELETE FROM qa_memory WHERE user_id=? AND id IN ("
                    "SELECT id FROM qa_memory WHERE user_id=? ORDER BY updated_at ASC LIMIT ?)",
                    (user_id, user_id, count - max_records),
                )
        conn.commit()
    return True


def search_qa_by_embedding(
    user_id: str,
    query_vec: List[float],
    threshold: float = 0.9,
) -> Tuple[str, str, float] | None:
    """在该用户的问答记忆中检索与 query_vec 余弦相似度最高且达阈值的记录。

    命中时自动累加 hit_count 并刷新更新时间。

    Returns:
        (question, answer, similarity) 或 None
    """
    if not user_id or not query_vec:
        return None
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, question, answer, embedding FROM qa_memory WHERE user_id=? AND embedding IS NOT NULL",
        (user_id,),
    ).fetchall()
    best = None
    best_score = 0.0
    for row in rows:
        score = _cosine(query_vec, _blob_to_vec(row["embedding"]))
        if score > best_score:
            best, best_score = row, score
    if best is None or best_score < threshold:
        return None
    now = int(time.time())
    with _db_lock:
        conn.execute(
            "UPDATE qa_memory SET hit_count=hit_count+1, updated_at=? WHERE id=?",
            (now, best["id"]),
        )
        conn.commit()
    return best["question"], best["answer"], best_score

