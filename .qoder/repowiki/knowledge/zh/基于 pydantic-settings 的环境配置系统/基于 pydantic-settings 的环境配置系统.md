---
kind: configuration_system
name: 基于 pydantic-settings 的环境配置系统
category: configuration_system
scope:
    - '**'
source_files:
    - config/settings.py
    - main.py
    - requirements.txt
---

本项目的配置系统以 `config/settings.py` 为核心，采用 **pydantic-settings** 框架统一管理所有运行时参数，支持从环境变量和 `.env` 文件加载配置，并通过单例缓存模式提供全局访问。

### 1. 使用的框架与工具
- **pydantic-settings (BaseSettings)**：作为配置模型定义与加载的核心，提供类型校验、默认值、环境变量映射能力。
- **.env 文件**：通过 `SettingsConfigDict(env_file=...)` 指定项目根目录下的 `.env` 文件作为配置源之一。
- **lru_cache 单例**：`get_settings()` 函数使用 `@lru_cache` 装饰器确保 Settings 实例全局唯一，避免重复初始化。

### 2. 核心文件与结构
- **`config/settings.py`**：唯一配置模块，定义 `Settings` 类并集中管理所有子系统参数。
- **`main.py`**：应用入口，在钉钉 Webhook 处理器中通过 `from config.settings import get_settings` 按需获取配置。
- **`requirements.txt`**：声明 `pydantic>=2.10.0` 和 `pydantic-settings>=2.14.0` 为必需依赖。

### 3. 架构设计与约定
- **配置优先级**：环境变量 > `.env` 文件 > 代码默认值（由 pydantic-settings 自动处理）。
- **大小写不敏感**：`case_sensitive=False` 使环境变量名可忽略大小写。
- **额外字段忽略**：`extra="ignore"` 允许存在未定义的 env 变量而不报错。
- **路径自动解析**：`chroma_persist_path`、`long_term_db_file`、`docs_dir` 等属性自动将相对路径转换为项目根下的绝对路径，并自动创建目录。
- **模块化分组**：配置按功能域分组注释（LLM、Embedding、向量库、记忆、钉钉、LangSmith），便于维护。

### 4. 配置项分类与约束
- **大语言模型**：`llm_model`、`llm_base_url`、`llm_api_key`、`llm_temperature`、`llm_judge_model`
- **Embedding**：`embedding_model`（默认 `BAAI/bge-small-zh-v1.5`）
- **向量库**：`chroma_persist_dir`、`chroma_collection`、`rag_top_k`、`rag_score_filter`
- **记忆系统**：`long_term_db_path`、`memory_summary_every`
- **钉钉集成**：`dingtalk_app_key`、`dingtalk_app_secret`、`dingtalk_robot_token`、`dingtalk_robot_secret`、`dingtalk_robot_code`、`dingtalk_api_base`
- **LangSmith 评估**：`langsmith_api_key`、`langsmith_project`、`langsmith_tracing`、`langsmith_endpoint`

### 5. 使用方式
- 通过 `get_settings()` 获取全局配置实例，如 `settings = get_settings()`。
- 在钉钉 Webhook 处理器中按需导入并使用，避免启动时立即加载。
- 支持通过环境变量覆盖任意配置项，无需修改代码。

### 6. 设计约束与规则
- 配置文件必须放在项目根目录的 `.env` 文件中。
- 路径类配置支持相对路径，会自动解析为项目根下的绝对路径。
- 所有配置项都有明确的默认值，确保服务可在无配置环境下启动（部分功能可能不可用）。
- 敏感信息（如 API Key）应通过环境变量注入，而非硬编码在代码中。