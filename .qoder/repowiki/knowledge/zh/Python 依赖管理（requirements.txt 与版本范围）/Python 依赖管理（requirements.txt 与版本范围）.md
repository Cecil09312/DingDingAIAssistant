---
kind: dependency_management
name: Python 依赖管理（requirements.txt 与版本范围）
category: dependency_management
scope:
    - '**'
source_files:
    - requirements.txt
---

本项目使用 Python 生态中最基础的依赖管理方式，通过根目录的 `requirements.txt` 集中声明所有第三方包及其版本约束，未使用 pipenv、poetry、conda 或 pyproject.toml 等更高级工具。

**使用的系统/方法**
- 单一清单文件：`requirements.txt`，按功能分组注释（核心框架、模型与向量库、Web 服务、配置与数据、评估），便于阅读与维护。
- 版本约束策略：主要采用 `>=` 指定最低版本，部分包额外限制上限（如 `huggingface-hub>=0.34.0,<1.0`），避免破坏性升级。
- 无锁文件：仓库中不存在 `requirements.lock`、`Pipfile.lock`、`poetry.lock` 等锁定文件，也未见 `vendor/` 目录进行源码级 vendoring。
- 无私有源/镜像配置：未发现 `.pip/pip.conf`、`~/.config/pip/pip.conf` 或 `setup.cfg` 中的 index-url 配置，默认使用 PyPI。

**关键文件**
- `requirements.txt`：全部依赖声明所在，共 31 行，覆盖 LangChain/LangGraph 生态、OpenAI SDK、ChromaDB、FastAPI、Pydantic、LangSmith/OpenEvals 等。

**架构与约定**
- 依赖按模块域分组注释，与项目目录结构（agent、rag、evaluation、dingtalk 等）一一对应，体现“功能域 → 依赖”的映射约定。
- 对可能频繁变动的生态（langchain-*、openai、chromadb）统一使用 `>=` 放宽下限，但对 huggingface-hub 显式限制 `<1.0`，说明该包在 1.0 存在不兼容变更风险。
- 未引入 CI 自动更新脚本或 Dependabot/ Renovate 配置，依赖更新需人工维护。

**约束与规则**
- 所有第三方依赖必须添加到 `requirements.txt`，不得在代码中动态安装（未见 `subprocess` 调用 pip 或 `importlib.metadata` 探测）。
- 版本约束仅使用 `>=` 和可选的上界 `<`，未使用精确版本号（`==`），保持一定灵活性但牺牲了可重现性。
- 无虚拟环境配置文件（如 `.venv/` 未被 gitignore 排除），也未见 Dockerfile 或容器化依赖声明。