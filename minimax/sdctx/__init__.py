"""sdctx — 面向软件持续演进的 SDD 上下文模块。

职责:
  * 用 seekdb / OceanBase 存储并检索工程资料（规格、代码、测试）;
  * 为一次开发任务组装"可追溯"的开发上下文;
  * 用 PowerContext 保存任务进展，并在后续运行中读取复用;
  * 把上下文交给 MiniMax，由模型产出真实的代码修改。

入口: ``python -m sdctx --help``
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
