---
kind: logging_system
name: 基于 Python 标准库 logging 的日志系统
category: logging_system
scope:
    - '**'
source_files:
    - main.py
    - dingtalk/client.py
---

本项目的日志系统完全基于 Python 标准库 `logging` 模块，采用最简配置方式，未引入第三方日志框架（如 loguru、structlog、logzero 等）。

**初始化与根 Logger**
- 在入口文件 `main.py` 中通过 `logging.basicConfig()` 统一配置根 logger，设置级别为 `INFO`，输出格式为 `%(asctime)s [%(name)s] %(levelname)s: %(message)s`。
- 项目级 logger 命名为 `dingtalk-agent`，所有业务模块通过 `logging.getLogger(__name__)` 获取子 logger，形成以模块名为命名空间的层级结构。

**使用模式**
- 各模块（如 `dingtalk/client.py`）自行创建 `logger = logging.getLogger(__name__)`，并在关键路径调用 `logger.info/warning/error` 记录日志。
- 错误日志统一使用 `logger.error(..., exc_info=True)` 附带异常堆栈，便于问题排查。
- 日志内容以结构化字符串为主，未使用 JSON 结构化字段或专用日志模型。

**输出目标**
- 仅通过 `basicConfig` 默认输出到控制台（stderr），未配置文件 handler、RotatingFileHandler 或远程 sink。
- 无日志轮转、分级输出（debug/info/warn/error 分离文件）、异步写入等高级特性。

**约束与约定**
- 所有 logger 必须通过 `logging.getLogger(__name__)` 获取，禁止直接调用 `logging.info()` 等 root 方法。
- 异常必须使用 `exc_info=True` 参数输出完整堆栈。
- 未定义自定义日志级别或格式化器，全部沿用标准 level 和默认格式。

该方案简单直接，适合开发调试；生产环境如需持久化、轮转或集中收集，需扩展 handler 配置。