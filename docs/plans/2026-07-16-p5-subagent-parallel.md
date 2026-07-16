# P5 子 Agent 并行派发 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development 执行。Steps 用 checkbox(`- [ ]`)。

**Goal:** 新增 `dispatch_subagents`(复数)工具——一次传任务数组,线程池并发跑 N 个子 Agent,按任务顺序拼结论返回。主 Agent 想并行调研/改动时用它,避免串行等待。

**Architecture:** 复用现成的同步 `run_subagent()`,用 `ThreadPoolExecutor` 并发跑(OpenAI client 线程安全);`ex.map` 保序;拼成带编号的一段结论返回。主循环 `loop.py` 完全不动——并行封装在工具内部(方案 A)。单数版 `dispatch_subagent` 保留。

**Tech Stack:** Python 3.11 · concurrent.futures.ThreadPoolExecutor · OpenAI SDK · pytest · uv

**设计依据:** 本次会话对话确认(2026-07-16);延续 `docs/specs/2026-07-13-p5-subagent-design.md` 的子 Agent 设计。

## Global Constraints

- 测试命令:全量 `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q`;单文件 `... uv run pytest tests/test_tools_subagent.py -v`。
- 复用现有 `run_subagent`(同步、整体 try/except 兜底、绝不抛)——**不改它**。某子 Agent 失败返回「(子 Agent 执行失败:…)」串,不影响其余。
- **并发上限 5**:`max_workers=min(len(tasks), 5)`,防一次派太多打爆 API/机器。
- **保序**:`ex.map` 结果顺序 = 任务顺序。
- **空 tasks** → 返回「没有要派发的子任务」,不起线程池。
- `dispatch_subagents` **不进** `SUBAGENT_TOOLS` 白名单(防子 Agent 并行派孙 Agent)。
- 延迟 import `run_subagent`(函数体内),与单数版一致防循环引用。
- 安全红线:不 `git add` data/config.json、不 push、日志不打印 api_key。
- TDD:先写失败测试 → 红 → 实现 → 绿 → commit。

---

### Task 1: dispatch_subagents 复数工具 + 注册

**Files:**
- Modify: `backend/app/agent/tools/subagent.py`(加 `DispatchSubagentsArgs` + `dispatch_subagents`)
- Modify: `backend/app/agent/tools/__init__.py`(注册 `dispatch_subagents`)
- Test: `backend/tests/test_tools_subagent.py`(追加测试)

**Interfaces:**
- Consumes: `subagent.run_subagent`(现有)、`registry`
- Produces: `DispatchSubagentsArgs(tasks: list[str])`、`dispatch_subagents(args) -> str`;registry 多一个 `dispatch_subagents`(工具数 13→14)

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_tools_subagent.py` 末尾追加(参照文件现有对 `subagent.run_subagent` 的 monkeypatch 风格):

```python
def test_dispatch_subagents_registered():
    names = {s["function"]["name"] for s in registry.to_openai_schema()}
    assert "dispatch_subagents" in names


def test_dispatch_subagents_not_in_subagent_tools():
    # 复数版也不在子 Agent 白名单(防子 Agent 并行派孙 Agent)
    assert "dispatch_subagents" not in subagent.SUBAGENT_TOOLS


def test_dispatch_subagents_runs_all_and_preserves_order(monkeypatch):
    # monkeypatch run_subagent:按 task 回不同结果,验证并发跑全 + 保序
    def fake_run(task):
        return f"结论-{task}"
    monkeypatch.setattr(subagent, "run_subagent", fake_run)
    out = registry.run("dispatch_subagents", {"tasks": ["A", "B", "C"]})
    # 保序:A 在 B 前、B 在 C 前
    assert out.index("结论-A") < out.index("结论-B") < out.index("结论-C")
    # 每个任务描述也带上了(便于主 Agent 对应)
    assert "A" in out and "B" in out and "C" in out


def test_dispatch_subagents_empty_tasks():
    out = registry.run("dispatch_subagents", {"tasks": []})
    assert "没有要派发的子任务" in out


def test_dispatch_subagents_one_failure_does_not_break_others(monkeypatch):
    # 某个子 Agent 返回失败串(run_subagent 本就兜底不抛),其余照常
    def fake_run(task):
        if task == "bad":
            return "(子 Agent 执行失败:boom)"
        return f"结论-{task}"
    monkeypatch.setattr(subagent, "run_subagent", fake_run)
    out = registry.run("dispatch_subagents", {"tasks": ["ok1", "bad", "ok2"]})
    assert "结论-ok1" in out and "结论-ok2" in out
    assert "子 Agent 执行失败" in out          # 失败的也如实带回,不吞


def test_dispatch_subagents_missing_tasks_self_heals():
    out = registry.run("dispatch_subagents", {})
    assert "参数错误" in out                    # Pydantic 缺 tasks → registry 自愈
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_tools_subagent.py -v`
Expected: FAIL(`dispatch_subagents` 未注册 / `DispatchSubagentsArgs` 不存在)

- [ ] **Step 3a: 加复数工具**

在 `backend/app/agent/tools/subagent.py` 里(单数版 `dispatch_subagent` 之后)加:

```python
from concurrent.futures import ThreadPoolExecutor

_MAX_PARALLEL = 5   # 并发上限:防一次派太多打爆 API/机器


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
    # ex.map 保序:结果顺序 = 任务顺序,主 Agent 好对应。
    with ThreadPoolExecutor(max_workers=min(len(tasks), _MAX_PARALLEL)) as ex:
        results = list(ex.map(run_subagent, tasks))
    return "\n\n".join(
        f"【子任务 {i + 1}】{task}\n{result}"
        for i, (task, result) in enumerate(zip(tasks, results))
    )
```

(`BaseModel`/`Field` 该文件顶部已 import,无需新增。)

- [ ] **Step 3b: 注册**

在 `backend/app/agent/tools/__init__.py` 末尾(单数 `dispatch_subagent` 注册之后)追加。注意 import 行要把复数一起带上:

```python
from app.agent.tools.subagent import (  # noqa: E402
    DispatchSubagentArgs, DispatchSubagentsArgs, dispatch_subagent, dispatch_subagents,
)
```
(若单数版原本是 `from app.agent.tools.subagent import DispatchSubagentArgs, dispatch_subagent`,替换成上面这行合并导入,避免重复 import 语句。)

然后加注册:

```python
registry.register(
    "dispatch_subagents", dispatch_subagents, DispatchSubagentsArgs,
    "并行派发多个子 Agent,各自独立完成一个子任务(搜代码/读文件/查知识库/写文件),"
    "全部完成后按顺序把结论一起返回。适合「几件互不依赖的调研/改动想同时进行」的场景,"
    "比串行一个个派更快。只派一个用 dispatch_subagent(单数)即可。"
    "传入 tasks:自足的子任务描述列表(子 Agent 看不到当前对话)。",
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_tools_subagent.py -v`
Expected: PASS(原有 + 新增 5 个全绿)

- [ ] **Step 5: 全量回归**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q`
Expected: PASS(全绿;原有 202 + 新增 5)

- [ ] **Step 6: Commit**

```bash
git -C /Users/shuangxingyang/Desktop/myspace/superstar add backend/app/agent/tools/subagent.py backend/app/agent/tools/__init__.py backend/tests/test_tools_subagent.py
git -C /Users/shuangxingyang/Desktop/myspace/superstar commit -m "feat(p5): dispatch_subagents 并行派发多个子 Agent(线程池并发/保序/上限5/一个失败不影响其余)"
```

---

## 收尾

- [ ] 更新 `HANDOFF.md`:工具数 13→14;把「子 Agent 并行派发」从"待办"移到"已完成";记 `dispatch_subagents`。
- [ ] 手动验收:前端派一句「同时并行调研 3 件事」,看主 Agent 是否调 dispatch_subagents、结论按序回传、耗时接近单个而非 3 倍。

## Self-Review

- **设计覆盖**:并行(ThreadPoolExecutor)、保序(ex.map)、上限5、空列表、一个失败不影响其余、防递归(不进白名单)、延迟 import——全落到 Task 1 的实现与 5 个测试。✅
- **占位符**:无,代码完整。✅
- **类型一致**:`DispatchSubagentsArgs(tasks: list[str])`、`dispatch_subagents(args)->str` 定义与注册/测试调用一致。✅
