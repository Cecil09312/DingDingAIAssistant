# Agent 兜底方案

> 本文档汇总钉钉AI智能体助手在各链路中采用的兜底策略，遵循「辅助链路静默降级、主链路友好提示」的设计原则，保证服务在异常情况下仍可对外提供响应。

## 一、设计原则总览

| 层级 | 策略 | 目的 |
|------|------|------|
| 辅助链路（记忆/抽取/缓存） | 静默降级返回空值 | 不阻断主链路 |
| 主链路（生成） | 友好错误提示 | 保证用户有响应 |
| 路由判断 | 三层兜底（规则 → LLM → 关键词） | 提高路由可靠性 |
| 后台任务（预热/记忆更新） | 仅记日志 | 不影响服务可用性 |
| 历史上下文 | 摘要注入防硬丢失 | 保留长程上下文 |

---

## 二、路由层兜底

### 1. 三层路由判断

**位置**：`agent/nodes.py` `_decide_route` 函数（第 54-84 行）

路由判断采用「规则短路 → LLM 判断 → 关键词兜底」三层结构，RAG 优先：

- **规则短路**：
  - 空输入 → `chat`
  - 时效性问题（明确时间/天气，`_is_realtime_query`）且联网开启 → `web`（绕过 LLM 路由提速）
  - 短输入（<=4 字且不含疑问词/搜索词）→ `chat`
- **LLM 路由**：调用小模型判断 `rag` / `web` / `chat`
  - 若 `web_search_enabled=False`，`web` 自动降级为 `rag`
- **LLM 失败兜底**：异常时进入关键词兜底分支
  - 命中知识库关键词（`ROUTER_KEYWORDS`）→ `rag`
  - 命中联网搜索关键词（`WEB_SEARCH_KEYWORDS`）且开关开启 → `web`
  - 其余 → `chat`

---

## 三、检索/记忆层兜底（静默降级）

### 2. 问答记忆匹配

**位置**：`agent/nodes.py` `qa_match_node` 函数（第 106-149 行）

问答记忆是答案缓存（SQLite `qa_memory` 表），命中时直接复用历史答案，跳过 LLM：

- 功能关闭（`memory_qa_cache_enabled=False`）→ `memory_hit=False`
- 输入过短（<4 字）或 `user_id` 为空 → `memory_hit=False`
- 时效性问题（明确时间/天气，`_is_realtime_query`）→ 跳过匹配，避免命中过时历史答案直接联网
- 问题向量化失败 → 静默降级 `memory_hit=False`，不阻断
- 问答记忆检索失败 → 静默降级 `hit=None`，不阻断
- 命中时写入 `answer` 与 `memory_hit=True`，后续条件边短路到 END

### 3. RAG 检索

**位置**：`agent/nodes.py` `retrieve_node` 函数（第 153-163 行）

- 检索异常 → `rag_context=""`，生成节点继续执行（无上下文生成）

### 4. 联网搜索

**位置**：`agent/nodes.py` `web_search_node` 函数（第 167-183 行）

- 搜索异常 → `rag_context=""`
- 未找到结果 → `rag_context=""`，并打印日志

### 5. 长期记忆加载

**位置**：`agent/nodes.py` `load_memory_node` 函数（第 187-202 行）

两个独立 try 块，互不影响：

- `build_memory_context` 失败 → `ctx=""`
- `get_session_summary` 失败 → `sess=""`

---

## 四、生成层兜底

### 6. LLM 生成失败

**位置**：`agent/nodes.py` `generate_node` 函数（第 339-341 行）

- LLM 调用异常 → 返回友好提示：
  ```
  抱歉，我暂时无法处理您的请求。错误信息: {e}
  ```
- 仍然写入 `messages`，保证后续多轮上下文连贯

### 7. 历史消息防丢失

**位置**：`agent/nodes.py` `build_chat_history` 函数（第 269-292 行）

超出窗口/字符预算的旧消息不直接硬丢：

- 取最近 `window` 条消息（默认 8 条）
- 超出字符预算（默认 2000）时，从最旧消息开始丢弃
- 若存在会话压缩摘要，以【更早对话摘要】`SystemMessage` 注入到消息列表头部，保留主线上下文

---

## 五、后台记忆层兜底（失败仅记日志）

**时效性问题跳过**（`memory_background_node`）：若输入为时效性问题（明确时间/天气，`_is_realtime_query`），整段后台记忆（关键信息抽取 + 记忆更新 + QA 缓存）直接返回空，不写入任何长期记忆，避免天气等过时信息污染记忆库。

### 8. 关键信息抽取

**位置**：`agent/nodes.py` `extract_facts_node` 函数（第 206-266 行）

- 规则抽取失败 → 忽略，不阻断
- `memory_extract_llm_enabled=False` → 直接返回，仅走规则路径
- LLM 抽取失败 → 静默跳过
- JSON 解析失败（`_parse_json_block`）→ 返回空 dict
- `priority` 字段解析失败 → 默认值 5
- `profile` / `facts` 字段为空或非字符串 → 跳过该条目

### 9. 记忆更新

**位置**：`agent/nodes.py` `memory_update_node` 函数（第 347-375 行）

三个独立 try 块，任一失败不影响其他：

- `increment_turn` / `summarize_and_store` 失败 → 忽略
- `_refresh_session_summary` 失败 → 忽略
- `_save_qa_cache` 失败 → 忽略

### 10. QA 缓存写入过滤

**位置**：`agent/nodes.py` `_save_qa_cache` 函数（第 378-410 行）

满足以下任一条件不写入缓存，避免污染缓存：

- 功能关闭或本轮已命中缓存
- 路由非 `rag` / `web`（闲聊不缓存）
- 输入长度 <4 字
- 回答长度 <20 字
- 回答以「抱歉」开头（说明是错误兜底回答）
- 向量化失败 → 不缓存

### 11. 会话摘要刷新节流

**位置**：`agent/nodes.py` `_refresh_session_summary` 函数（第 413-438 行）

- 无 `session_id` → 跳过
- 历史消息数 <= 窗口大小 → 跳过
- 新增滑出窗口的消息 <4 条 → 跳过（避免每轮重复调用 LLM）
- 满足条件时调用 `summarize_messages` 压缩旧消息，保存为会话摘要

---

## 六、服务启动层兜底

### 12. LLM 连接预热

**位置**：`main.py` `warmup_llm` 函数（第 44-76 行）

启动时后台线程预热路由小模型与主模型（TLS 握手 + DNS 解析 + 连接池建立）：

- 预热失败 → 仅 `warning` 日志，不影响服务启动
- 后台 `daemon` 线程执行，不阻塞应用启动

### 13. 向量库预热

**位置**：`main.py` `warmup_vectorstore` 函数（第 79-104 行）

启动时检查向量库，内存模式下自动入库：

- 初始化失败 → 仅 `warning` 日志
- 向量库为空 → 自动后台入库
- 已有数据 → 跳过入库

预热末尾在 `_do_warmup` 内启动知识库文件监听（`start_file_watcher`，对应 `rag/file_watcher.py`）：

- 开关关闭（`rag_auto_sync_enabled=false`）→ 跳过监听，仅记 info 日志
- 监听启动失败 → 仅 `warning` 日志（不影响服务启动，用户仍可手动 `python -m rag.ingest` 同步）
- 消费线程为 daemon，随主进程退出，不阻塞服务停止
- 自动增量入库失败 → 仅 `error` 日志，不抛异常、不中断监听循环，下次文件变更仍会重试

---

## 七、Web 接口层兜底

### 14. `/api/chat` 同步聊天接口

**位置**：`main.py` `api_chat` 函数（第 123-155 行）

- 空输入 → 返回 `{"errcode": 1, "errmsg": "empty input"}`
- 调用异常 → 返回 HTTP 500 + `{"errcode": 500, "errmsg": str(e)}`
- `result` 无 `answer` 字段 → 返回默认提示「抱歉，我暂时无法回答您的问题。」

### 15. `/api/chat/stream` 流式聊天接口

**位置**：`main.py` `api_chat_stream` 函数（第 174-215 行）

- 空输入 → 返回 `{"errcode": 1, "errmsg": "empty input"}`
- 整轮无 token 产出 → 补发默认提示 token，再发 `done` 事件
- 异常 → 发送 `{"type": "error", "errmsg": str(e)}` 事件

### 16. 根路径前端页面

**位置**：`main.py` `root` 函数（第 114-120 行）

- `static/index.html` 不存在 → 返回 HTTP 404 +「前端页面未找到」

---

## 八、兜底链路调用流程

```
用户输入
    │
    ▼
pre_check (qa_match 问答缓存匹配 + route 路由判断)
    │
    ├─ 缓存命中 ──────────────────────────► END (直接复用历史答案)
    │
    └─ 未命中 ──► route 三层判断 (规则→LLM→关键词)
                    │
        ┌───────────┼───────────┬───────────┐
        ▼           ▼           ▼           ▼
    retrieve    web_search   load_memory   generate
    (失败→空)   (失败→空)    (失败→空)     │
        │           │           │           │
        └───────────┴───────────┘           │
                    │                       │
                    ▼                       │
                 generate ◄─────────────────┘
                 (LLM失败→友好提示)
                    │
                    ▼
            memory_background (后台: 抽取+记忆更新, 全部静默)
                    │
                    ▼
                   END
```

---

## 九、新增兜底方案（P0/P1 落实）

在原有兜底基础上，新增以下 P0/P1 级别兜底方案，提升服务可用性与回答质量。

### 17. LLM 调用重试与超时（P0）

**位置**：`config/settings.py` 新增配置 + `agent/nodes.py` `_get_llm` / `_get_router_llm`

针对 LLM 服务的 429 限流、网络抖动等可重试错误，引入重试与超时机制：

- 新增配置项：
  - `llm_max_retries: int = 3` — 最大重试次数
  - `llm_request_timeout: int = 60` — 单次请求超时（秒）
- `_get_llm` 与 `_get_router_llm` 创建 `ChatOpenAI` 实例时传入 `max_retries` 与 `request_timeout`
- LangChain 内部按指数退避自动重试，重试耗尽后抛出异常交由上层兜底

### 18. 检索相关度阈值过滤（P0）

**位置**：`config/settings.py` 新增配置 + `agent/nodes.py` `retrieve_node`

避免低相关度上下文注入生成导致幻觉：

- 新增配置项：`rag_min_relevance: float = 0.3`（归一化到 0~1，越大越相关）
- `retrieve_node` 调用 `retrieve` 拿原始 `(doc, score)` 列表后，按 `1/(1+|score|)` 归一化
- 低于阈值的文档被过滤，全部被过滤时返回空上下文（由生成节点依赖 LLM 自身知识作答）
- **仅在纯向量检索路径下生效**（rerank/BM25 关闭时）：CrossEncoder 分数与 RRF 融合分数的语义与向量距离不同，统一归一化会导致过滤逻辑反转或失效
- 过滤生效时打印日志：`[retrieve] 相关度过滤: N -> M (阈值=0.3)`

### 19. 模型降级兜底（P1）

**位置**：`config/settings.py` 新增配置 + `agent/nodes.py` `_try_fallback_generate` / `_stream_answer`

主模型生成失败时按优先级依次尝试备用模型列表，保证用户有响应：

- 配置项：
  - `llm_fallback_model: str = "qwen-turbo"` — 单模型降级（向后兼容）
  - `llm_fallback_models: str = ""` — 多模型优先级列表（逗号分隔，优先使用）
- 新增辅助函数：
  - `_stream_answer(llm, chat_messages)` — 提取流式生成逻辑，供主模型与降级模型复用
  - `_try_fallback_generate(chat_messages, settings, primary_error)` — 降级重试入口
- 降级链路：主模型失败 → 解析 `llm_fallback_models` 列表 → 按优先级依次尝试 → 某个成功即返回 → 全部失败返回汇总错误
- `_get_llm` 新增可选 `model` 参数，降级时传入模型名复用同一函数
- 向后兼容：`llm_fallback_models` 为空时回退到 `llm_fallback_model` 单模型

### 20. 流式接口超时保护（P1）

**位置**：`config/settings.py` 新增配置 + `main.py` `api_chat_stream`

防止流式生成卡死导致连接耗尽：

- 新增配置项：`stream_timeout: int = 60`（整体超时秒数）
- `event_stream` 使用 `asyncio.wait_for` 包裹每次 `__anext__()` 调用，按累计 deadline 判断整体超时
- 超时后停止拉取，返回已生成的 token + `done` 事件
- 完全无 token 产出时补发「抱歉，响应超时，请稍后重试。」

### 21. 路由小模型配置（P1）

**位置**：`config/settings.py` 新增配置 + `agent/nodes.py` `_get_router_llm`

补全路由小模型配置，供路由判断/抽取等轻量任务使用，降低成本与延迟：

- 新增配置项：`llm_router_model: str = "qwen-turbo"`
- 补全 `_get_router_llm` 函数定义（此前被 `main.py` 引用但未定义），含重试与超时配置

### 22. 预检查与后台记忆节点补全

**位置**：`agent/nodes.py` `pre_check_node` / `pre_check_condition` / `memory_background_node`

补全 `graph.py` 引用但未定义的三个节点函数，恢复 graph 构建：

- `pre_check_node`：合并执行 `qa_match_node` + `route_node`，缓存命中时提前返回短路
- `pre_check_condition`：缓存命中→`end`；否则按 `search_route` 四路分流（rag→retrieve / web→web_search / chat→load_memory）
- `memory_background_node`：合并执行 `extract_facts_node` + `memory_update_node`，两者内部独立 try/except 互不影响

---

## 十、P2/P3 兜底方案（进阶，默认关闭）

在 P0/P1 基础上，新增以下进阶兜底方案，提升检索召回率与服务稳定性。所有方案默认关闭，需在 `.env` 中显式开启。

### 23. 多路召回 BM25 + RRF 融合（P2）

**位置**：`rag/bm25.py` + `rag/retriever.py` `_hybrid_recall` / `_rrf_fusion`

向量检索对专有名词、编号、代码片段不敏感时，BM25 关键词检索互补：

- 新增模块 `rag/bm25.py`：从 Milvus 拉取文本文档构建内存 BM25 索引（字符级分词，无需 jieba）
- `retrieve` 函数在 `rag_bm25_enabled=true` 时并行执行向量检索 + BM25 检索
- RRF (Reciprocal Rank Fusion) 融合两路结果：`score(d) = sum(1/(k+rank))`，仅依赖排名不依赖原始分数
- 内容相同的 chunk 合并分数（两路都命中的文档得分更高）
- 任一路为空时回退到另一路结果
- BM25 索引为进程内单例，向量库重建后需调用 `reset_bm25_index()` 重置
- 启动时在 `warmup_vectorstore` 中自动预热（`rag_bm25_enabled=true` 时），避免首请求阻塞
- 依赖 `rank-bm25` 库（已加入 requirements.txt，未安装时静默降级为仅向量检索）

### 24. 查询改写（P2）

**位置**：`agent/query_rewrite.py` + `agent/nodes.py` `retrieve_node`

用 LLM 改写查询以提升复杂问题召回率：

- 新增模块 `agent/query_rewrite.py`：调用路由小模型改写查询
- 改写策略：补全指代、去口语化、转为检索友好形式
- `retrieve_node` 检索前先调用 `rewrite_query`，改写后的查询传入 `retrieve`
- 兜底策略：
  - 功能关闭 → 返回原始查询
  - 查询过短（<6 字）→ 不触发改写（缺乏上下文，收益低）
  - LLM 改写失败 → 静默回退原始查询
  - 改写结果与原文相同 → 使用原始查询

### 25. 输入安全过滤（P3）

**位置**：`agent/safety.py` + `main.py` `api_chat` / `api_chat_stream`

钉钉对外场景下的输入安全防护：

- 新增模块 `agent/safety.py`：关键词黑名单 + prompt 注入检测
- **关键词黑名单**：命中配置的敏感词则拒绝（敏感词不回显，避免被探测枚举）
- **prompt 注入检测**：检测「忽略以上指令」「进入开发者模式」「DAN模式」等明确注入指令（中英文 15 种模式，已移除"你是一个"等易误报的弱模式）
- 集成位置：`api_chat` 与 `api_chat_stream` 入口处，拒绝时返回 HTTP 403
- 拦截原因可展示给用户，但敏感词本身不回显

### 26. 限流（滑动窗口）（P3）

**位置**：`agent/rate_limiter.py` `check_rate_limit` + `main.py` 接口层

按 user_id 维度的每分钟请求数上限：

- 滑动窗口实现（60 秒窗口），线程安全（`threading.Lock`）
- 超限返回 HTTP 429 + 提示「请求过于频繁」
- `rate_limit_per_minute <= 0` 时不限流（默认关闭）

### 27. 熔断器（P3）

**位置**：`agent/rate_limiter.py` `CircuitBreaker` + `main.py` 接口层

LLM 调用连续失败达阈值后熔断，避免雪崩：

- 三态状态机：`closed`（正常）→ `open`（熔断）→ `half_open`（半开试探）→ `closed`（恢复）
- **closed**：正常放行，记录失败次数
- **open**：连续失败达 `circuit_breaker_threshold` 后拒绝所有请求（HTTP 503）
- **half_open**：冷却时间（`circuit_breaker_recovery`）过后放行一次试探
  - 试探成功 → 恢复 closed
  - 试探失败 → 重新 open
- 集成位置：
  - `api_chat`：调用前 `check_circuit()`，成功 `record_success()`，异常 `record_failure()`
  - `api_chat_stream`：同上；超时已部分生成不计为失败
- `circuit_breaker_threshold <= 0` 时不熔断（默认关闭）
- 熔断器状态可通过 `get_circuit_state()` 查询（供健康检查扩展）

---

## 十一、新增配置项汇总（含 P2/P3）

在 `config/settings.py` 新增的兜底相关配置项：

| 配置项 | 默认值 | 说明 | 对应方案 |
|--------|--------|------|----------|
| `llm_router_model` | `qwen-turbo` | 路由小模型 | #21 路由小模型 |
| `llm_max_retries` | `3` | LLM 最大重试次数 | #17 重试机制 |
| `llm_request_timeout` | `60` | LLM 请求超时（秒） | #17 超时机制 |
| `llm_fallback_model` | `qwen-turbo` | 单模型降级（空则不降级） | #19 模型降级 |
| `llm_fallback_models` | `""` | 多模型优先级列表（逗号分隔，优先使用） | #19 模型降级 |
| `stream_timeout` | `60` | 流式接口整体超时（秒） | #20 流式超时 |
| `rag_min_relevance` | `0.3` | 检索最低相关度（0~1） | #18 阈值过滤 |
| `rag_bm25_enabled` | `false` | BM25 多路召回开关 | #23 多路召回 |
| `rag_bm25_candidate_count` | `10` | BM25 召回候选数 | #23 多路召回 |
| `rag_rrf_k` | `60` | RRF 融合常数 | #23 多路召回 |
| `rag_query_rewrite_enabled` | `false` | 查询改写开关 | #24 查询改写 |
| `input_filter_enabled` | `false` | 输入安全过滤开关 | #25 输入过滤 |
| `input_blocked_keywords` | `""` | 敏感词黑名单（逗号分隔） | #25 输入过滤 |
| `input_injection_check_enabled` | `true` | prompt 注入检测开关 | #25 输入过滤 |
| `rate_limit_per_minute` | `0` | 每用户每分钟请求上限（<=0 不限流） | #26 限流 |
| `circuit_breaker_threshold` | `0` | 熔断阈值（<=0 不熔断） | #27 熔断器 |
| `circuit_breaker_recovery` | `60` | 熔断恢复冷却时间（秒） | #27 熔断器 |
| `tool_calling_enabled` | `false` | 钉钉工具调用总开关（待办/会议） | #28 工具调用 |
| `tool_confirmation_required` | `true` | 写操作需用户确认 | #28 工具调用 |

---

## 十二、相关文件索引

| 文件 | 说明 |
|------|------|
| `agent/nodes.py` | 全部节点函数与兜底逻辑 |
| `agent/graph.py` | LangGraph 状态图编排 |
| `agent/state.py` | AgentState 状态定义 |
| `agent/prompts.py` | 路由关键词与提示词模板 |
| `agent/query_rewrite.py` | 查询改写模块（P2） |
| `agent/safety.py` | 输入安全过滤模块（P3） |
| `agent/rate_limiter.py` | 限流与熔断模块（P3） |
| `main.py` | FastAPI 入口、预热与接口兜底 |
| `config/settings.py` | 功能开关与阈值配置 |
| `rag/vectorstore.py` | 向量库（内存/持久化） |
| `rag/bm25.py` | BM25 关键词检索模块（P2） |
| `rag/retriever.py` | 多路召回 + 重排序 + 格式化 |
| `memory/long_term.py` | 长期记忆与问答缓存 |
| `agent/tools/tool_schemas.py` | 工具 Schema 定义（Function Calling） |
| `agent/tools/todo_tools.py` | 待办工具执行器 |
| `agent/tools/meeting_tools.py` | 会议工具执行器 |
| `agent/tools/time_parser.py` | 自然语言时间解析 |
| `.qoder/skills/dingtalk-messaging/scripts/dingtalk_lib.py` | 钉钉 API 客户端（含待办/会议 API） |
