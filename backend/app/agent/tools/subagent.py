"""
tools/subagent.py —— dispatch_subagent 工具外壳(薄封装,像 tools/memory.py)。

真正的循环在 agent/subagent.py 的 run_subagent;这里只做 Pydantic 入参 + 转调。
对引擎的 import 放在函数体内(延迟),避开「tools 包加载期 ↔ agent.subagent」的循环引用。
"""
from pydantic import BaseModel, Field


class DispatchSubagentArgs(BaseModel):
    task: str = Field(description="交给子 Agent 的子任务描述。子 Agent 看不到当前对话,"
                                  "所以要把背景、目标、要它产出什么都交代清楚、自足。")


def dispatch_subagent(args: DispatchSubagentArgs) -> str:
    from app.agent.subagent import run_subagent   # 函数内延迟 import,避开包加载期循环
    return run_subagent(args.task)
