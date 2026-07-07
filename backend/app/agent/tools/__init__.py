"""
agent/tools —— 工具注册表 + 具体工具的登记处。

为什么 registry 放在包的 __init__ 里:Python 不允许同一个包下既有 tools.py 又有 tools/ 目录
(同名冲突)。设计文档把「注册表」和「工具函数」画成两层,落地时注册表就是这个包本身,
fs.py / search.py 是包内模块。外部统一 `from app.agent.tools import registry`。

ToolRegistry 的三件事:
  1. register:登记「函数 + Pydantic 入参模型 + 描述」
  2. to_openai_schema:把入参模型转成 OpenAI function calling 的 JSON schema
  3. run:执行一次工具调用,三处自愈(未知工具/参数错/执行异常),永远返回字符串,绝不抛
"""
import logging
from typing import Callable

from pydantic import BaseModel, ValidationError

from app.services.security import SecurityError

logger = logging.getLogger(__name__)


class Tool:
    """一个工具 = 名字 + 函数 + 入参模型 + 给模型看的描述。"""

    def __init__(
        self,
        name: str,
        func: Callable[[BaseModel], str],
        args_model: type[BaseModel],
        description: str,
    ):
        self.name = name
        self.func = func
        self.args_model = args_model
        self.description = description


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        func: Callable[[BaseModel], str],
        args_model: type[BaseModel],
        description: str,
    ) -> None:
        self._tools[name] = Tool(name, func, args_model, description)

    def to_openai_schema(self) -> list[dict]:
        """每个工具 → {type:'function', function:{name, description, parameters}}。
        parameters 直接用 Pydantic 的 model_json_schema()(标准 JSON Schema,OpenAI 认)。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.args_model.model_json_schema(),
                },
            }
            for t in self._tools.values()
        ]

    def run(self, name: str, raw_args: dict) -> str:
        """执行一次工具调用,统一兜错(自愈核心),永远返回字符串。

        在 function calling 协议里工具结果本就是一条 role:tool 文本消息;把错误也变成
        一种「正常返回值」喂回,模型看到即自我修正。run() 从不向上抛,循环层无需管工具会不会炸。
        """
        tool = self._tools.get(name)
        if tool is None:
            logger.warning("未知工具: name=%s", name)
            return f"错误:未知工具 {name}"                       # ① 模型幻觉的工具名
        try:
            args = tool.args_model(**raw_args)                     # Pydantic 校验
        except ValidationError as e:
            logger.info("工具参数校验失败: name=%s", name)
            return f"参数错误:{e}"                                 # ② 参数不对 → 喂回让模型改
        try:
            result = tool.func(args)
            logger.info("工具执行完成: name=%s, result_len=%d", name, len(result))
            return result
        except SecurityError as e:
            logger.warning("工具安全拦截: name=%s", name)
            return f"安全拦截:{e}"                                 # ③a 越界
        except Exception as e:  # noqa: BLE001
            logger.warning("工具执行失败: name=%s, err=%s", name, type(e).__name__)
            return f"工具执行失败:{e}"                             # ③b 任何异常 → 喂回,不崩流


# 全局单例:整个 Agent 共用一份注册表。真实工具在 fs.py / search.py 定义,
# 由本文件末尾在 Task 3/4 导入并登记(先定义 registry 再 import,天然避免循环引用)。
registry = ToolRegistry()

# ---- 登记具体工具(放最后:此时 registry 已就绪,import 工具模块不会循环)----
from app.agent.tools.fs import ReadFileArgs, read_file  # noqa: E402

registry.register(
    "read_file", read_file, ReadFileArgs,
    "读取工作区内一个文件的文本内容(相对路径)。超大文件会自动截断。",
)

from app.agent.tools.fs import WriteFileArgs, write_file  # noqa: E402

registry.register(
    "write_file", write_file, WriteFileArgs,
    "把文本内容写入工作区内一个文件(相对路径);不存在则新建,存在则整体覆盖。此操作需用户审批。",
)

from app.agent.tools.search import GlobArgs, GrepArgs, glob, grep  # noqa: E402

registry.register(
    "grep", grep, GrepArgs,
    "在工作区内按正则逐行搜索,返回 相对路径:行号:内容。命中过多会截断。",
)
registry.register(
    "glob", glob, GlobArgs,
    "按通配模式(如 **/*.py)列出工作区内匹配的文件路径。",
)

from app.agent.tools.shell import RunCommandArgs, run_command  # noqa: E402

registry.register(
    "run_command", run_command, RunCommandArgs,
    "在工作区目录下执行一条 shell 命令并返回输出(退出码 + stdout/stderr)。危险命令会被拒绝,其余需用户审批。",
)

from app.agent.tools.rag import SearchKbArgs, search_kb  # noqa: E402

registry.register(
    "search_kb", search_kb, SearchKbArgs,
    "在文档知识库里语义检索,返回最相关的片段和来源。需要引用资料/文档内容回答时用它。",
)
