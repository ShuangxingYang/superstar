"""
tools/subagent.py —— dispatch_subagent / dispatch_subagents 工具外壳(薄封装,像 tools/memory.py)。

真正的循环在 agent/subagent.py 的 run_subagent;这里只做 Pydantic 入参 + 转调。
对引擎的 import 放在函数体内(延迟),避开「tools 包加载期 ↔ agent.subagent」的循环引用。
  - dispatch_subagent(单数):派一个子 Agent。
  - dispatch_subagents(复数):线程池并发派多个,保序汇总(run_subagent 本就同步+兜底,client 线程安全)。
"""
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

_MAX_PARALLEL = 5   # 并发上限:防主 Agent 一次派太多打爆 API/机器


class DispatchSubagentArgs(BaseModel):
    task: str = Field(description="交给子 Agent 的子任务描述。子 Agent 看不到当前对话,"
                                  "所以要把背景、目标、要它产出什么都交代清楚、自足。")


def dispatch_subagent(args: DispatchSubagentArgs) -> str:
    from app.agent.subagent import run_subagent   # 函数内延迟 import,避开包加载期循环
    return run_subagent(args.task)


class DispatchSubagentsArgs(BaseModel):
    tasks: list[str] = Field(
        description="要并行派发的子任务描述列表,每个子 Agent 独立跑一个、互不可见,"
                    "全部完成后按顺序把各自结论汇总返回给你。每个描述都要自足(子 Agent 看不到当前对话)。")


def dispatch_subagents(args: DispatchSubagentsArgs) -> str:
    from app.agent.subagent import run_subagent   # 函数内延迟 import,避开包加载期循环
    tasks = args.tasks
    if not tasks:
        return "没有要派发的子任务"
    # run_subagent 本就同步 + 整体兜底(绝不抛),OpenAI client 线程安全 → 直接丢线程池并发。
    # ex.map 保序:结果顺序 = 任务顺序,主 Agent 好对应。某个失败返回失败串,不影响其余。
    with ThreadPoolExecutor(max_workers=min(len(tasks), _MAX_PARALLEL)) as ex:
        results = list(ex.map(run_subagent, tasks))
    return "\n\n".join(
        f"【子任务 {i + 1}】{task}\n{result}"
        for i, (task, result) in enumerate(zip(tasks, results))
    )
