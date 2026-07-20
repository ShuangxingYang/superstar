"""
tools/subagent.py —— dispatch_subagent / dispatch_subagents 工具外壳(薄封装,像 tools/memory.py)。

真正的循环在 agent/subagent.py 的 run_subagent;这里只做 Pydantic 入参 + 转调。
对引擎的 import 放在函数体内(延迟),避开「tools 包加载期 ↔ agent.subagent」的循环引用。
  - dispatch_subagent(单数):派一个子 Agent。
  - dispatch_subagents(复数):asyncio.gather 并发派多个,保序汇总(gather 返回顺序=传入顺序);
    并发度用信号量限到 _MAX_PARALLEL,防主 Agent 一次派太多打爆 API/机器。
"""
import asyncio

from pydantic import BaseModel, Field

_MAX_PARALLEL = 5   # 并发上限:防主 Agent 一次派太多打爆 API/机器(gather 本身不节流,靠信号量)


class DispatchSubagentArgs(BaseModel):
    task: str = Field(description="交给子 Agent 的子任务描述。子 Agent 看不到当前对话,"
                                  "所以要把背景、目标、要它产出什么都交代清楚、自足。")


async def dispatch_subagent(args: DispatchSubagentArgs) -> str:
    from app.agent.subagent import run_subagent   # 函数内延迟 import,避开包加载期循环
    return await run_subagent(args.task)


class DispatchSubagentsArgs(BaseModel):
    tasks: list[str] = Field(
        description="要并行派发的子任务描述列表,每个子 Agent 独立跑一个、互不可见,"
                    "全部完成后按顺序把各自结论汇总返回给你。每个描述都要自足(子 Agent 看不到当前对话)。")


async def dispatch_subagents(args: DispatchSubagentsArgs) -> str:
    from app.agent.subagent import run_subagent   # 函数内延迟 import,避开包加载期循环
    tasks = args.tasks
    if not tasks:
        return "没有要派发的子任务"
    # run_subagent 整体兜底(绝不抛),asyncio.gather 保序:结果顺序 = 任务顺序。
    # gather 本身不限并发,用信号量把在飞的子 Agent 数限到 _MAX_PARALLEL(等价原线程池 max_workers)。
    # 某个失败返回失败串,不影响其余(return_exceptions=False 也 OK,因 run_subagent 不抛)。
    sem = asyncio.Semaphore(_MAX_PARALLEL)

    async def _run(t: str) -> str:
        async with sem:
            return await run_subagent(t)

    results = await asyncio.gather(*[_run(t) for t in tasks])
    return "\n\n".join(
        f"【子任务 {i + 1}】{task}\n{result}"
        for i, (task, result) in enumerate(zip(tasks, results))
    )
