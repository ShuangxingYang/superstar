# Superstar async 重构 实现计划(第一步:纯重构,行为不变)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development 执行。Steps 用 checkbox。

**Goal:** 把后端流式链路从 sync 改成 async(路由 + 主循环 + LLM client + 子 Agent),**行为完全不变、测试全绿**,为「中断生成」和飞书并发打地基。中断功能不在本计划(第二步单独做)。

**Architecture:** 主对话链路 + 子 Agent 全 async(`AsyncOpenAI` + `async for`);同步工具/存储经 `registry.run_async` 或 `asyncio.to_thread` 复用(源码零改);蒸馏/测试连接保持同步(独立 `OpenAI` client)。入口仍 uvicorn,`run.py` 不改。

**Tech Stack:** Python 3.11 · FastAPI(async 路由)· AsyncOpenAI · asyncio · pytest-asyncio · uv

**设计依据:** `docs/specs/2026-07-17-async-refactor-design.md`

## Global Constraints

- 测试命令:`cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q`;单文件 `... uv run pytest tests/test_xxx.py -v`。
- **铁律:纯重构,行为不变。** 本计划不加功能、不改行为。任何行为变化都是 bug。验收=全部测试绿、数量不减(除 mock 结构从 sync 改 async 外,断言逻辑不动)。
- **减法边界**:session_store/config_store/atomic_json/rag_store/pending + 同步工具实现(fs/search/shell/rag/workspace/memory)**源码零改动**,由调用方 `to_thread`/`run_async` 包。
- **双 client**:`get_llm_client()`→`AsyncOpenAI`(主链路+子Agent);`get_sync_llm_client()`→`OpenAI`(蒸馏+测试连接)。
- **取消令牌本步只预留**(可选参数 + TODO 注释),不实现中断逻辑。
- 安全红线:不 `git add` data/config.json、不 push、日志不打印 api_key。
- **commit 规矩**:本轮=整个 async 重构(spec→plan→build)。**全部 task 完成、用户审阅后**才 commit;中间 task 不逐个停等审、不逐个 commit(见记忆 no-auto-commit-defer-to-next-change)。但每个 task 仍各自 TDD 跑绿。
- TDD:每个 task 先让相关测试(可能需先改 mock)可跑 → 改实现 → 跑绿。

---

### Task 1: 引入 pytest-asyncio + llm.py 双 client

**Files:**
- Modify: `backend/pyproject.toml`(加 pytest-asyncio dev 依赖 + asyncio_mode)
- Modify: `backend/app/services/llm.py`(加 `get_sync_llm_client`,`get_llm_client` 改返回 AsyncOpenAI)
- Test: `backend/tests/test_llm.py`(更新)

**Interfaces:**
- Produces: `get_llm_client()->(AsyncOpenAI,str)`、`get_sync_llm_client()->(OpenAI,str)`;各自缓存。Task 2/3/4 消费。

- [ ] **Step 1: 装依赖 + 配 asyncio_mode**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv add --dev pytest-asyncio`
然后在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 加:
```toml
asyncio_mode = "auto"
```

- [ ] **Step 2: 写/改 llm 测试**

`tests/test_llm.py` 改成:`get_llm_client` 返回的 client 是 `AsyncOpenAI` 实例;新增 `get_sync_llm_client` 返回 `OpenAI` 实例;两者未配置时都 raise。参照原 test_llm.py 断言风格,把类型断言从 `OpenAI` 调整。核心断言:
```python
from openai import AsyncOpenAI, OpenAI
# 配好 llm 后:
client, model = llm.get_llm_client()
assert isinstance(client, AsyncOpenAI)
sync_client, _ = llm.get_sync_llm_client()
assert isinstance(sync_client, OpenAI)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `... uv run pytest tests/test_llm.py -v` → FAIL(get_sync_llm_client 不存在 / 类型不符)

- [ ] **Step 4: 改实现**

`llm.py`:
- import 加 `AsyncOpenAI`。
- `get_llm_client()`:构造 `AsyncOpenAI(...)`,返回 `(AsyncOpenAI, model)`。缓存变量、逻辑不变。
- 新增 `get_sync_llm_client()`:构造 `OpenAI(...)`,独立缓存(独立 `_sync_client`/`_sync_client_key`)。
- 二者未配置(缺 api_key/model)都 raise RuntimeError。
- `_reset_client()` 清两套缓存(测试用)。

- [ ] **Step 5: 跑测试确认通过** → `tests/test_llm.py` 绿。

---

### Task 2: ToolRegistry.run_async + async 工具标记

**Files:**
- Modify: `backend/app/agent/tools/__init__.py`(Tool 加 is_async 标记;registry 加 `run_async`)
- Test: `backend/tests/test_tools.py`(追加 run_async 测试)

**Interfaces:**
- Consumes: 现有 `registry.run`
- Produces: `async def registry.run_async(name, raw_args) -> str`:async 工具直 `await func(args)`;同步工具 `await asyncio.to_thread(同步执行)`。Task 3(主循环)、Task 4(子 Agent 注册为 async 工具)消费。

- [ ] **Step 1: 写失败测试**(test_tools.py 末尾)

```python
import pytest, asyncio
from app.agent.tools import registry

async def test_run_async_sync_tool():
    # 同步工具经 run_async 正常执行(内部走 to_thread)
    out = await registry.run_async("grep", {"pattern": "x", "path": "/nonexistent"})
    assert isinstance(out, str)   # 不抛,返回字符串(可能是"无匹配"/错误串)

async def test_run_async_unknown_tool():
    out = await registry.run_async("nope", {})
    assert "未知工具" in out
```
(async 测试函数,asyncio_mode=auto 自动识别。)

- [ ] **Step 2: 跑失败** → `run_async` 不存在。

- [ ] **Step 3: 实现**

`tools/__init__.py`:
- `Tool.__init__` 加 `is_async: bool = False`,记录 func 是否协程函数(`inspect.iscoroutinefunction(func)`)。
- `register(...)` 自动探测:`is_async=inspect.iscoroutinefunction(func)`。
- 新增:
```python
import asyncio, inspect
async def run_async(self, name: str, raw_args: dict) -> str:
    """async 工具直 await;同步工具 to_thread。兜错逻辑复用 run 的同款(未知/参数错/异常都返字符串)。"""
    tool = self._tools.get(name)
    if tool is None:
        return f"错误:未知工具 {name}"
    try:
        args = tool.args_model(**raw_args)
    except ValidationError as e:
        return f"参数错误:{e}"
    try:
        if tool.is_async:
            result = await tool.func(args)
        else:
            result = await asyncio.to_thread(tool.func, args)
        return result
    except SecurityError as e:
        return f"安全拦截:{e}"
    except Exception as e:
        return f"工具执行失败:{e}"
```
(把 run/run_async 的兜错抽公共 helper 以免重复,或接受少量重复——实现者判断,保持行为一致。)

- [ ] **Step 4: 跑绿** → test_tools.py 全绿(原有 run 测试 + 新 run_async)。

---

### Task 3: loop.py 主循环 async 化

**Files:**
- Modify: `backend/app/agent/loop.py`
- Test: `backend/tests/test_loop.py`(mock 从 sync 改 async)

**Interfaces:**
- Consumes: `get_llm_client()`(AsyncOpenAI, Task1)、`registry.run_async`(Task2)、同步 store/memory/config(to_thread 包)
- Produces: `async def run_agent_streaming(sid, cancel_event=None)`(async generator)、`async def resume_streaming(...)`、`async def reject_all_pending(...)`

- [ ] **Step 1: 改 mock 为 async**(test_loop.py)

把假 client 的 `create` 改成 `async def create(...)`,返回 **async 迭代器**(async generator 产 chunk)。所有测试函数改 `async def`,消费改 `async for event in loop.run_agent_streaming(...)`(或收集:`events = [e async for e in ...]`)。断言逻辑**不变**。
> 这是本 task 最容易错的地方:先改一个测试(如 test_grep_then_answer)跑通形成范式,再套用其余。

- [ ] **Step 2: 跑失败** → 现有 sync 实现与 async 测试不匹配。

- [ ] **Step 3: 改实现**

`loop.py`:
- `run_agent_streaming(sid, cancel_event: "asyncio.Event | None" = None)` → `async def`,内部 `yield`(async generator)。签名加 cancel_event(**本步不用它,仅预留 + TODO**)。
- `_accumulate(stream)` → `async def`,`async for chunk in stream`,内部 `yield`。外层 `async for ev in _accumulate(stream)` 消费(async gen 不能 `yield from`,改 `async for ... yield ev`,并单独拿返回值——注意:async generator **不能 return 值**,需改造:让 `_accumulate` 通过一个可变容器/或改成"边 yield 边填 list"回传 text_parts/reasoning/tool_calls。实现者按此调整,保持重组逻辑不变)。
- LLM 调用:`stream = await client.chat.completions.create(..., stream=True)`。
- 工具执行:`result = await registry.run_async(name, parsed)`(替代 `registry.run`)。
- 同步调用包 to_thread:`await asyncio.to_thread(session_store.read_messages, sid)`、`append_message`、`memory.build_memory_block`、`config_store.get`、`pending_store.*`。`gate_tool_call` 纯计算直接调。
- `resume_streaming`、`reject_all_pending` → async;内部同样 to_thread 包同步调用、`async for` 消费子生成器。
- 回合边界预留:`for _ in range(max_iters)` 开头加注释 `# TODO(第二步中断): if cancel_event and cancel_event.is_set(): return`。

- [ ] **Step 4: 跑绿** → test_loop.py 全绿,事件序列/落盘断言与改造前一致。

---

### Task 4: 路由 async 化 + 子 Agent/dispatch 全 async

**Files:**
- Modify: `backend/app/api/routes/chat.py`(async 路由)
- Modify: `backend/app/agent/subagent.py`(run_subagent async)
- Modify: `backend/app/agent/tools/subagent.py`(dispatch 外壳 async;dispatch_subagents 用 gather)
- Modify: `backend/app/services/distill.py`(改用 get_sync_llm_client)
- Modify: `backend/app/api/routes/settings.py`(测试连接改用 get_sync_llm_client)
- Test: `test_chat_routes.py`(async mock)、`test_subagent.py`、`test_tools_subagent.py`、`test_distill.py` 相应调整

**Interfaces:**
- Consumes: `run_agent_streaming`(async, Task3)、`get_llm_client`(async)、`get_sync_llm_client`(sync)、`registry.run_async`
- Produces: `async def run_subagent(task, cancel_event=None)`;dispatch 工具为 async 工具(registry 自动识别 is_async)

- [ ] **Step 1: 改 chat 路由 async**

`chat.py`:`chat_stream`/`chat_resume` → `async def`;`event_stream` → async generator;`async for event in loop.run_agent_streaming(sid)`;内部同步调用(session_store/pending)to_thread 包。StreamingResponse 接受 async gen。
测试 `test_chat_routes.py`:TestClient 对 async 路由透明(仍同步调用),但内部 mock 的 client.create 要 async(同 Task3)。相应调整。

- [ ] **Step 2: 子 Agent async**

`subagent.py`:`run_subagent(task, cancel_event=None)` → `async def`;`await client.chat.completions.create(...)`(async client,`get_llm_client()`);工具调用 `await registry.run_async(name, args)`(替代原 registry.run + SUBAGENT_TOOLS 白名单校验保留:白名单判断逻辑不变,只是执行改 run_async)。
`tools/subagent.py`:`dispatch_subagent`/`dispatch_subagents` → `async def`(成为 async 工具,registry 自动识别);`dispatch_subagents` 内部 `results = await asyncio.gather(*[run_subagent(t) for t in tasks])`(替代 ThreadPoolExecutor;保序由 gather 保证;空列表短路不变;某个失败——run_subagent 内部兜底返串不抛,gather 正常收集)。

- [ ] **Step 3: 蒸馏/测试连接改同步 client**

`distill.py`:`get_llm_client()` → `get_sync_llm_client()`(其余不变,仍同步)。
`settings.py` 测试连接处:改用 `get_sync_llm_client()`。

- [ ] **Step 4: 调整相关测试**

- `test_subagent.py`:mock client.create 改 async;`run_subagent` 调用改 `await`(测试函数 async)。断言逻辑不变(正常闭环/能写/越权/递归/超限/异常/沙箱)。
- `test_tools_subagent.py`:`dispatch_subagents` 保序/失败不影响其余等测试——注意现在 monkeypatch 的 `run_subagent` 要是 async(`async def fake_run`);registry.run_async 调用。相应改。
- `test_distill.py`:改用 get_sync_llm_client 的 mock(同步 client,不变)。

- [ ] **Step 5: 全量回归** → `uv run pytest -q` 全绿,数量不减。

---

## 收尾(全部 task 完成后)

- [ ] 手动冒烟:重启后端(`uv run python run.py`,入口不变),前端发消息、派子 Agent(单+并行)、跑一次蒸馏——确认行为与重构前一致。
- [ ] 更新 `HANDOFF.md`:原则升级(业界标准做减法);技术栈标 async 流式链路;记「中断只在回合边界+LLM流、不强杀工具」约束(第二步用)。
- [ ] **本轮(async 重构)全部完成 → 汇总改动清单给用户审 → 审后 commit**(可拆语义化多 commit:llm双client / registry.run_async / loop async / 路由+子Agent async)。

## Self-Review

- **覆盖**:llm 双 client(T1)、registry.run_async(T2)、loop async(T3)、路由+子Agent+蒸馏(T4)。spec §3.1/3.2/3.6/§4/§6 全覆盖。✅
- **行为不变铁律**:每个 task 验收=对应测试绿、断言逻辑不变(只改 mock sync→async)。✅
- **减法边界**:存储/同步工具源码零改,仅调用方包装。✅
- **依赖顺序**:T1(client)→T2(run_async)→T3(loop 用 T1+T2)→T4(路由/子Agent 用 T1+T2+T3)。无逆序。✅
- **取消令牌**:仅预留参数+TODO,不实现中断(第二步)。✅
