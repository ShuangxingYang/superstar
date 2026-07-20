"""
agent/tools —— 工具注册表 + 具体工具的登记处。

为什么 registry 放在包的 __init__ 里:Python 不允许同一个包下既有 tools.py 又有 tools/ 目录
(同名冲突)。设计文档把「注册表」和「工具函数」画成两层,落地时注册表就是这个包本身,
fs.py / search.py 是包内模块。外部统一 `from app.agent.tools import registry`。

ToolRegistry 的三件事:
  1. register:登记「函数 + Pydantic 入参模型 + 描述」
  2. to_openai_schema:把入参模型转成 OpenAI function calling 的 JSON schema
  3. run:执行一次工具调用,三处自愈(未知工具/参数错/执行异常),永远返回字符串,绝不抛
  4. run_async:async 版的 run,同步工具走 to_thread,async 工具直 await
"""
import asyncio
import inspect
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
        self.is_async = inspect.iscoroutinefunction(func)


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

    def to_openai_schema(self, names: set[str] | None = None) -> list[dict]:
        """每个工具 → {type:'function', function:{name, description, parameters}}。
        parameters 直接用 Pydantic 的 model_json_schema()(标准 JSON Schema,OpenAI 认)。
        names 为 None 时导出全部;给定时只导出集合内存在的工具(供子 Agent 取只读写子集)。"""
        tools = (
            self._tools.values() if names is None
            else [self._tools[n] for n in names if n in self._tools]
        )
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.args_model.model_json_schema(),
                },
            }
            for t in tools
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

    async def run_async(self, name: str, raw_args: dict) -> str:
        """async 版 run:async 工具直 await,同步工具 to_thread。
        兜错逻辑与 run 完全一致(未知/参数错/安全/执行异常都返字符串)。"""
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
            if tool.is_async:
                result = await tool.func(args)
            else:
                result = await asyncio.to_thread(tool.func, args)
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
    "读取一个文件的文本内容(绝对路径,或相对默认工作目录);须在允许目录内。超大文件会自动截断。",
)

from app.agent.tools.fs import WriteFileArgs, write_file  # noqa: E402

registry.register(
    "write_file", write_file, WriteFileArgs,
    "把文本内容写入一个文件(绝对路径,或相对默认工作目录);不存在则新建,存在则整体覆盖。允许目录内自动写入、无需审批;写允许目录之外会被拒绝。",
)

from app.agent.tools.search import GlobArgs, GrepArgs, glob, grep  # noqa: E402

registry.register(
    "grep", grep, GrepArgs,
    "按正则逐行搜索,返回 绝对路径:行号:内容。留空 path 搜所有允许目录。命中过多会截断。",
)
registry.register(
    "glob", glob, GlobArgs,
    "按通配模式(如 **/*.py)在所有允许目录下列出匹配的文件(绝对路径)。",
)

from app.agent.tools.shell import RunCommandArgs, run_command  # noqa: E402

registry.register(
    "run_command", run_command, RunCommandArgs,
    "执行一条 shell 命令并返回输出(退出码 + stdout/stderr);默认在默认工作目录,可传 cwd 指定允许目录内的其他目录。危险命令会被拒绝,其余需用户审批。",
)

from app.agent.tools.rag import SearchKbArgs, search_kb  # noqa: E402

registry.register(
    "search_kb", search_kb, SearchKbArgs,
    "在文档知识库里语义检索,返回最相关的片段和来源。需要引用资料/文档内容回答时用它。",
)

from app.agent.tools.workspace import (  # noqa: E402
    AddWorkspaceArgs, RemoveWorkspaceArgs, add_workspace, remove_workspace,
)

registry.register(
    "add_workspace", add_workspace, AddWorkspaceArgs,
    "把一个目录(绝对路径)加入可访问白名单,之后就能读写它。此操作需用户审批。",
)
registry.register(
    "remove_workspace", remove_workspace, RemoveWorkspaceArgs,
    "把一个目录从可访问白名单移除。",
)

from app.agent.tools.memory import (  # noqa: E402
    UpdateProfileArgs, UpdateSoulArgs, update_profile, update_soul,
)

registry.register(
    "update_profile", update_profile, UpdateProfileArgs,
    "沉淀关于用户本人的个人信息(姓名、身份、职业、个人偏好等跟人强相关的稳定事实)。"
    "只有特别确定是用户个人信息时才记;项目/技术的客观事实用 update_memory,不要往这里塞。"
    "整份覆盖:先基于 system 里已注入的现有画像合并,再写回完整内容。",
)
registry.register(
    "update_soul", update_soul, UpdateSoulArgs,
    "调整你自己的长期行为准则。整份覆盖:先基于 system 里已注入的现有准则合并,再写回完整内容。",
)

from app.agent.tools.memory import AppendLogArgs, append_log  # noqa: E402

registry.register(
    "append_log", append_log, AppendLogArgs,
    "把今天发生的具体事/操作/踩的坑追加到当天日志(流水账,带时间戳)。"
    "开会话时会自动看到今天+昨天的日志。记'今天的事'用它;"
    "用户个人信息用 update_profile,项目客观事实用 update_memory。",
)

from app.agent.tools.memory import UpdateMemoryArgs, update_memory  # noqa: E402

registry.register(
    "update_memory", update_memory, UpdateMemoryArgs,
    "沉淀需要长期记住的客观事实与既定结论(项目约定、技术栈、架构决策、重要背景等跟人无关的稳定知识)。"
    "区别于 profile(用户个人信息)、区别于日志(今天的流水)。"
    "整份覆盖:先基于 system 里已注入的现有长期记忆合并,再写回完整内容。",
)

from app.agent.tools.subagent import (  # noqa: E402
    DispatchSubagentArgs, DispatchSubagentsArgs, dispatch_subagent, dispatch_subagents,
)

registry.register(
    "dispatch_subagent", dispatch_subagent, DispatchSubagentArgs,
    "派发一个子 Agent 去独立完成一个子任务(搜代码、读文件、查知识库、写文件)。"
    "子 Agent 有独立上下文,只把最终结论返回给你——适合「要翻很多文件/大量检索/批量改动」的活,"
    "避免这些中间过程塞满当前对话。子 Agent 能读能写,但不能跑命令、不能改目录权限;"
    "需要跑命令时,它会把建议写进结论,你再自己执行。"
    "传入 task:自足的子任务描述(子 Agent 看不到当前对话)。",
)

registry.register(
    "dispatch_subagents", dispatch_subagents, DispatchSubagentsArgs,
    "并行派发多个子 Agent,各自独立完成一个子任务(搜代码/读文件/查知识库/写文件),"
    "全部完成后按顺序把结论一起返回。适合「几件互不依赖的调研/改动想同时进行」的场景,"
    "比串行一个个派更快。只派一个用 dispatch_subagent(单数)即可。"
    "传入 tasks:自足的子任务描述列表(子 Agent 看不到当前对话)。",
)
