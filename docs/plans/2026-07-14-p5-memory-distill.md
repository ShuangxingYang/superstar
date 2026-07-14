# P5 记忆自动蒸馏 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 定时(默认关)或手动把最近 N 天日志单次 LLM 提炼进 `MEMORY.md`,补上「无人显式触发就永不沉淀」的洞。

**Architecture:** 核心是纯函数 `distill_memory()`(读最近 N 天日志 + 现有 MEMORY → 单次非流式 LLM 提炼 → 只有非空新全文才覆盖写)。两个调用者:后台 APScheduler 定时 job(默认关,挂 FastAPI lifespan)+ `POST /api/memory/distill` 手动接口。不复用会话循环。

**Tech Stack:** Python 3.11 · FastAPI(lifespan)· APScheduler · OpenAI SDK(非流式)· pytest · uv

**设计依据:** `docs/specs/2026-07-14-p5-memory-distill-design.md`

## Global Constraints

- 每个任务的要求都隐含包含本节。
- **测试命令**:全量 `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q`;单文件 `... uv run pytest tests/test_xxx.py -v`。
- **测试 fixture 惯例**(沿用现有):`monkeypatch.setattr(settings, "data_dir", str(tmp_path))` + `config_store._reset_cache()` +(需要时)`config_store.update({...})`。memory 相关测试只需 monkeypatch data_dir(见 test_tools_memory / test_memory)。
- **蒸馏的 LLM 调用是非流式**:`client.chat.completions.create(model=model, messages=messages)`,**不传 stream、不传 tools**。mock 的 `create` 签名用 `**kwargs` 容错(参照 `tests/test_subagent.py` 的非流式 mock:`_Fn`/`_TC`/`_Msg`/`_Resp`/`_ScriptClient`/`_RaisingClient`)。
- **写盘保护**:只有拿到**非空**新全文才 `memory.write_memory`;模型返空/异常都保留原记忆。
- **绝不崩、绝不毁记忆**:`distill_memory` 整体 try/except,任何失败收敛成人读的摘要串,绝不上抛。
- **默认值**:`distill.enabled=False`、`interval_hours=72`、`scan_days=3`。
- **安全红线**:不 `git add` data/config.json、不碰 qdrant_storage、日志不打印 api_key、**不 push**(仅本地 commit)。
- 每个任务遵循 TDD:先写失败测试 → 跑到红 → 最小实现 → 跑到绿 → commit。

---

### Task 1: config 加 distill 分区 + apscheduler 依赖

**Files:**
- Modify: `backend/app/services/config_store.py`(DEFAULTS 加 distill)
- Modify: `backend/pyproject.toml`(加 apscheduler 依赖)
- Test: `backend/tests/test_config_store.py`(追加 1 个测试)

**Interfaces:**
- Produces: `config_store.get()["distill"]` → `{"enabled": False, "interval_hours": 72, "scan_days": 3}`。Task 3/5 消费。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_config_store.py` 末尾追加:

```python
def test_defaults_has_distill_section(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    distill = config_store.get()["distill"]
    assert distill["enabled"] is False
    assert distill["interval_hours"] == 72
    assert distill["scan_days"] == 3
```

(若该测试文件顶部尚未 `from app.services import config_store`,按文件现有 import 风格补上。)

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_config_store.py::test_defaults_has_distill_section -v`
Expected: FAIL(`KeyError: 'distill'`)

- [ ] **Step 3: 加 DEFAULTS 分区**

在 `backend/app/services/config_store.py` 的 `DEFAULTS` 里,`"agent": {...}` 之后加:

```python
    # 记忆自动蒸馏:默认关,定时把最近 scan_days 天日志提炼进 MEMORY.md
    "distill": {
        "enabled": False,       # 默认关:后端起不自动跑
        "interval_hours": 72,   # 定时频率:每 3 天一次
        "scan_days": 3,         # 每次扫最近 3 天日志
    },
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_config_store.py -v`
Expected: PASS

- [ ] **Step 5: 加 apscheduler 依赖**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv add apscheduler`
Expected: `pyproject.toml` 的 dependencies 多出 `apscheduler>=...`,`uv.lock` 更新,依赖装好。
(若 `uv add` 不可用,手动在 pyproject.toml dependencies 里加一行 `"apscheduler>=3.10"` 再 `uv sync`。)

- [ ] **Step 6: Commit**

```bash
git -C /Users/shuangxingyang/Desktop/myspace/superstar add backend/app/services/config_store.py backend/tests/test_config_store.py backend/pyproject.toml backend/uv.lock
git -C /Users/shuangxingyang/Desktop/myspace/superstar commit -m "feat(p5): config 加 distill 分区(默认关/72h/3天)+ apscheduler 依赖"
```

---

### Task 2: memory.recent_log_days(n)

**Files:**
- Modify: `backend/app/services/memory.py`(加 `recent_log_days`)
- Test: `backend/tests/test_memory.py`(追加测试)

**Interfaces:**
- Consumes: `memory._today`、`memory.read_log`、`memory._log_path`(现有)
- Produces: `memory.recent_log_days(n: int) -> list[tuple[date, str]]`——最近 n 天非空日志,今天在前,n<=0 返回空。Task 3 消费。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_memory.py` 末尾追加(参照该文件现有对 `_today` 的 monkeypatch 风格;若无则用下面的):

```python
def test_recent_log_days(tmp_path, monkeypatch):
    from datetime import date
    from app.config import settings
    from app.services import memory
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    # 固定"今天"为可控日期,造 今天 / 前2天 / 前5天 三份日志
    fixed_today = date(2026, 7, 14)
    monkeypatch.setattr(memory, "_today", lambda: fixed_today)
    log_dir = tmp_path / "memory"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "2026-07-14.md").write_text("- 10:00 今天的事\n", encoding="utf-8")
    (log_dir / "2026-07-12.md").write_text("- 09:00 前两天的事\n", encoding="utf-8")
    (log_dir / "2026-07-09.md").write_text("- 08:00 前五天的事\n", encoding="utf-8")

    # n=3:只覆盖 07-14/13/12 → 命中 14 和 12,今天在前;09 在窗口外
    got = memory.recent_log_days(3)
    assert [d.isoformat() for d, _ in got] == ["2026-07-14", "2026-07-12"]
    assert "今天的事" in got[0][1]

    # n=0 → 空
    assert memory.recent_log_days(0) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_memory.py::test_recent_log_days -v`
Expected: FAIL(`AttributeError: module ... has no attribute 'recent_log_days'`)

- [ ] **Step 3: 实现**

在 `backend/app/services/memory.py` 的 `recent_logs()` 之后加:

```python
def recent_log_days(n: int) -> list[tuple[date, str]]:
    """返回最近 n 天里非空的 (日期, 内容),今天在前。n<=0 → 空。供蒸馏用。
    (与 recent_logs 区别:recent_logs 固定今天+昨天用于注入;本函数按 n 天用于蒸馏。)"""
    today = _today()
    out: list[tuple[date, str]] = []
    for i in range(max(n, 0)):
        d = today - timedelta(days=i)
        content = read_log(d).strip()
        if content:
            out.append((d, content))
    return out
```

(`date`/`timedelta` 已在文件顶部 `from datetime import date, datetime, timedelta` 导入,无需新增 import。)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_memory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C /Users/shuangxingyang/Desktop/myspace/superstar add backend/app/services/memory.py backend/tests/test_memory.py
git -C /Users/shuangxingyang/Desktop/myspace/superstar commit -m "feat(p5): memory 加 recent_log_days(n)——读最近 N 天非空日志(供蒸馏)"
```

---

### Task 3: distill.py 核心蒸馏

**Files:**
- Create: `backend/app/services/distill.py`
- Test: `backend/tests/test_distill.py`

**Interfaces:**
- Consumes: `config_store.get()["distill"]`(Task 1)、`memory.recent_log_days`(Task 2)、`memory.read_memory`/`memory.write_memory`(现有)、`llm.get_llm_client`(现有)
- Produces: `distill.distill_memory() -> str`(人读摘要);`distill.DISTILL_SYSTEM_PROMPT`。Task 4/5 消费。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_distill.py`:

```python
"""distill_memory:非流式假 LLM,断言 正常蒸馏/空日志短路不调LLM/返空不覆盖/异常兜底。"""
import pytest

from app.config import settings
from app.services import config_store, distill, llm, memory


# --- 非流式假对象(参照 test_subagent)---
class _Msg:
    def __init__(self, content):
        self.content = content


class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": _Msg(content)})()]


class _Completions:
    """记录被调用次数与收到的 messages;返回预设 content。"""
    def __init__(self, content):
        self.content = content
        self.calls = 0
        self.seen = None

    def create(self, model, messages, **kwargs):
        self.calls += 1
        self.seen = messages
        return _Resp(self.content)


class _Client:
    def __init__(self, content):
        self.comp = _Completions(content)
        self.chat = type("Chat", (), {"completions": self.comp})()


class _RaisingClient:
    class _C:
        def create(self, *a, **k):
            raise RuntimeError("boom")
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _RaisingClient._C()})()


@pytest.fixture
def mem_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    return tmp_path


def _make_log(tmp_path, memory_mod):
    # 造一天今天的日志
    d = memory_mod._today()
    log_dir = tmp_path / "memory"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{d.isoformat()}.md").write_text("- 10:00 项目测试用 uv run pytest\n", encoding="utf-8")


def test_distill_normal(mem_ready, monkeypatch):
    _make_log(mem_ready, memory)
    client = _Client("# 长期记忆\n- 测试用 uv run pytest")
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    out = distill.distill_memory()
    assert "蒸馏完成" in out
    assert memory.read_memory() == "# 长期记忆\n- 测试用 uv run pytest"   # 写回模型输出
    assert client.comp.calls == 1


def test_distill_no_logs_short_circuits(mem_ready, monkeypatch):
    # 没有任何日志 → 短路,不调 LLM
    client = _Client("不该被用到")
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    out = distill.distill_memory()
    assert "无日志" in out
    assert client.comp.calls == 0                       # 关键:没调模型


def test_distill_empty_response_keeps_old(mem_ready, monkeypatch):
    _make_log(mem_ready, memory)
    memory.write_memory("旧的长期记忆")                  # 先有旧记忆
    client = _Client("   ")                              # 模型返回空白
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    out = distill.distill_memory()
    assert "失败" in out
    assert memory.read_memory() == "旧的长期记忆"         # 未被覆盖


def test_distill_llm_exception_caught(mem_ready, monkeypatch):
    _make_log(mem_ready, memory)
    memory.write_memory("旧的长期记忆")
    monkeypatch.setattr(llm, "get_llm_client", lambda: (_RaisingClient(), "fake"))
    out = distill.distill_memory()
    assert out.startswith("蒸馏失败")                     # 收敛成串,没抛
    assert memory.read_memory() == "旧的长期记忆"          # 原记忆不动


def test_distill_prompt_includes_existing_and_logs(mem_ready, monkeypatch):
    _make_log(mem_ready, memory)
    memory.write_memory("现有记忆内容XYZ")
    client = _Client("新记忆")
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    distill.distill_memory()
    user_msg = client.comp.seen[-1]["content"]
    assert "现有记忆内容XYZ" in user_msg                  # 现有 MEMORY 进了 prompt
    assert "uv run pytest" in user_msg                    # 日志进了 prompt
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_distill.py -v`
Expected: FAIL(`app.services.distill` 不存在 → ImportError)

- [ ] **Step 3: 实现**

创建 `backend/app/services/distill.py`(逐字用设计文档 §6.3 的完整代码):

```python
"""
distill.py —— 记忆自动蒸馏:把最近 N 天日志单次 LLM 提炼进 MEMORY.md。

定时器(scheduler)和手动接口(routes/memory)共用 distill_memory()。
非流式 LLM 调用(与子 Agent 一致);整体兜错,绝不崩调度器、绝不覆盖坏记忆。
"""
import logging

from app.services import config_store, llm, memory

logger = logging.getLogger(__name__)

DISTILL_SYSTEM_PROMPT = (
    "你是一个记忆整理器。下面给你「现有长期记忆」和「最近若干天的日志流水」。"
    "任务:从日志里提炼出值得长期记住的客观事实与既定结论(项目约定、技术栈、架构决策、"
    "重要背景等跟人无关的稳定知识),与现有长期记忆合并去重,输出一份更新后的完整长期记忆。"
    "要求:① 只保留客观稳定的事实,丢弃一次性流水(如'今天改了X');"
    "② 现有记忆里已有的不要重复;③ 直接输出 Markdown 正文全文(会整份覆盖旧记忆),"
    "不要加任何解释、不要用代码块包裹。如果没有任何值得沉淀的,原样输出现有记忆。"
)


def distill_memory() -> str:
    """蒸馏一次:读最近 N 天日志 + 现有 MEMORY → 单次 LLM 提炼 → 覆盖写。
    返回一句人读的结果摘要。绝不抛(失败也返回摘要串)。"""
    try:
        cfg = config_store.get()["distill"]
        logs = memory.recent_log_days(cfg["scan_days"])
        if not logs:
            logger.info("蒸馏跳过:最近 %d 天无日志", cfg["scan_days"])
            return "最近无日志可蒸馏,跳过"
        existing = memory.read_memory()
        logs_text = "\n\n".join(f"## {d.isoformat()}\n{c}" for d, c in logs)
        client, model = llm.get_llm_client()
        logger.info("蒸馏开始:扫 %d 天日志, 现有记忆 len=%d", len(logs), len(existing))
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": DISTILL_SYSTEM_PROMPT},
                {"role": "user",
                 "content": f"现有长期记忆:\n{existing or '(空)'}\n\n最近日志:\n{logs_text}"},
            ],
        )
        new_memory = (resp.choices[0].message.content or "").strip()
        if not new_memory:
            logger.warning("蒸馏:模型返回空,不覆盖")
            return "蒸馏失败:模型返回空,已保留原记忆"
        memory.write_memory(new_memory)
        logger.info("蒸馏完成:新记忆 len=%d", len(new_memory))
        return f"蒸馏完成,长期记忆已更新(长度 {len(new_memory)})"
    except Exception as e:  # noqa: BLE001 - 后台任务,任何失败都收敛成摘要,绝不崩调度器
        logger.warning("蒸馏失败: err=%s", type(e).__name__)
        return f"蒸馏失败:{e}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_distill.py -v`
Expected: PASS(5 个测试全绿)

- [ ] **Step 5: Commit**

```bash
git -C /Users/shuangxingyang/Desktop/myspace/superstar add backend/app/services/distill.py backend/tests/test_distill.py
git -C /Users/shuangxingyang/Desktop/myspace/superstar commit -m "feat(p5): distill_memory 核心——单次非流式提炼日志进 MEMORY(空日志短路/返空不覆盖/异常兜底)"
```

---

### Task 4: 手动触发接口 POST /api/memory/distill

**Files:**
- Create: `backend/app/api/routes/memory.py`
- Modify: `backend/app/api/main.py`(注册 router)
- Test: `backend/tests/test_memory_routes.py`

**Interfaces:**
- Consumes: `distill.distill_memory`(Task 3)
- Produces: `POST /api/memory/distill` → `{"result": <摘要串>}`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_memory_routes.py`(参照现有 `tests/test_session_routes.py` / `test_kb_routes.py` 的 TestClient 用法):

```python
"""POST /api/memory/distill:手动触发蒸馏,返回 {"result": ...}。"""
from fastapi.testclient import TestClient

from app.services import distill


def test_distill_endpoint(monkeypatch):
    # monkeypatch 掉真实蒸馏,只验证路由把结果包成 {"result": ...}
    monkeypatch.setattr(distill, "distill_memory", lambda: "蒸馏完成,长期记忆已更新(长度 42)")
    from app.api.main import app
    client = TestClient(app)
    r = client.post("/api/memory/distill")
    assert r.status_code == 200
    assert r.json() == {"result": "蒸馏完成,长期记忆已更新(长度 42)"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_memory_routes.py -v`
Expected: FAIL(404,路由未注册)

- [ ] **Step 3a: 建路由**

创建 `backend/app/api/routes/memory.py`:

```python
"""routes/memory.py —— 记忆相关 HTTP 接口。目前只有手动触发蒸馏。"""
import logging

from fastapi import APIRouter

from app.services import distill

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.post("/distill")
def trigger_distill() -> dict:
    """手动立即蒸馏一次,返回结果摘要。与定时器共用 distill_memory()。"""
    logger.info("手动触发蒸馏")
    result = distill.distill_memory()
    return {"result": result}
```

- [ ] **Step 3b: 注册 router**

在 `backend/app/api/main.py`:
1. 顶部 import 区加:`from app.api.routes import memory as memory_routes`
2. 路由注册区(现有 `app.include_router(kb_routes.router)` 之后)加:`app.include_router(memory_routes.router)`

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_memory_routes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C /Users/shuangxingyang/Desktop/myspace/superstar add backend/app/api/routes/memory.py backend/app/api/main.py backend/tests/test_memory_routes.py
git -C /Users/shuangxingyang/Desktop/myspace/superstar commit -m "feat(p5): POST /api/memory/distill 手动触发蒸馏接口"
```

---

### Task 5: scheduler.py + lifespan 挂载

**Files:**
- Create: `backend/app/agent/scheduler.py`
- Modify: `backend/app/api/main.py`(加 lifespan)
- Test: `backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `config_store.get()["distill"]`(Task 1)、`distill.distill_memory`(Task 3)、APScheduler(Task 1 装)
- Produces: `scheduler.start_scheduler()`、`scheduler.stop_scheduler()`;模块级 `_scheduler`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_scheduler.py`:

```python
"""scheduler:enabled=false 不启动;enabled=true 注册 id='distill' 的 job;stop 后清空。"""
import pytest

from app.config import settings
from app.services import config_store


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    yield
    # 每个用例后确保调度器停掉,别把线程漏到别的测试
    from app.agent import scheduler
    scheduler.stop_scheduler()


def test_scheduler_disabled_does_not_start():
    from app.agent import scheduler
    config_store.update({"distill": {"enabled": False}})
    scheduler.start_scheduler()
    assert scheduler._scheduler is None


def test_scheduler_enabled_registers_job():
    from app.agent import scheduler
    config_store.update({"distill": {"enabled": True, "interval_hours": 72, "scan_days": 3}})
    scheduler.start_scheduler()
    assert scheduler._scheduler is not None
    assert scheduler._scheduler.get_job("distill") is not None
    scheduler.stop_scheduler()
    assert scheduler._scheduler is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_scheduler.py -v`
Expected: FAIL(`app.agent.scheduler` 不存在)

- [ ] **Step 3a: 建 scheduler**

创建 `backend/app/agent/scheduler.py`(逐字用设计文档 §6.4 的完整代码):

```python
"""
scheduler.py —— 后台定时调度(目前只挂记忆蒸馏)。

用 APScheduler BackgroundScheduler(守护线程,不阻塞主进程)。
按 config.distill.enabled 决定要不要注册 job:默认关 → 不启动,零开销。
由 api/main.py 的 lifespan 在启动/关闭时调用 start/stop。
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.services import config_store, distill

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    """按 config 决定是否注册蒸馏 job。enabled=false → 不启动。"""
    global _scheduler
    cfg = config_store.get()["distill"]
    if not cfg["enabled"]:
        logger.info("蒸馏调度未启用(distill.enabled=false),跳过")
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(distill.distill_memory, "interval",
                       hours=cfg["interval_hours"], id="distill")
    _scheduler.start()
    logger.info("蒸馏调度已启动:每 %d 小时一次", cfg["interval_hours"])


def stop_scheduler() -> None:
    """关闭时优雅停(如果起过)。"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("蒸馏调度已停止")
```

- [ ] **Step 3b: main.py 加 lifespan**

在 `backend/app/api/main.py`:
1. 顶部加 import:`from contextlib import asynccontextmanager` 和 `from app.agent import scheduler`
2. 在 `app = FastAPI(...)` **之前**定义:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start_scheduler()   # 启动:按 config 决定是否注册 job
    yield
    scheduler.stop_scheduler()    # 关闭:停调度器
```

3. 把 `app = FastAPI(title="Superstar Backend", version="0.1.0")` 改为:
   `app = FastAPI(title="Superstar Backend", version="0.1.0", lifespan=lifespan)`

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q`
Expected: PASS(全绿;原有 192 + 本次新增)

- [ ] **Step 6: Commit**

```bash
git -C /Users/shuangxingyang/Desktop/myspace/superstar add backend/app/agent/scheduler.py backend/app/api/main.py backend/tests/test_scheduler.py
git -C /Users/shuangxingyang/Desktop/myspace/superstar commit -m "feat(p5): APScheduler 后台调度 + FastAPI lifespan 挂载(默认关,enabled 才注册蒸馏 job)"
```

---

## 收尾(全部任务完成后)

- [ ] 更新 `HANDOFF.md`:第 5 节待办 5(记忆自动蒸馏)标记完成;技术栈补 APScheduler;新增 `POST /api/memory/distill` 接口说明。
- [ ] 手动验收(用户换非流式模型后):设 `distill.enabled=true` 或调 `POST /api/memory/distill`,确认日志被提炼进 MEMORY.md。

## Self-Review(计划自查记录)

- **Spec 覆盖**:config 分区(T1)、recent_log_days(T2)、distill_memory 核心含空日志/返空/异常三条兜底(T3)、手动接口(T4)、scheduler+lifespan(T5)。设计 §8 的 9 个测试点:1/3/4正常&返空&异常→T3;2空日志→T3;5 recent_log_days→T2;6 config默认→T1;7/8 scheduler→T5;9 手动接口→T4。全覆盖。✅
- **占位符**:无 TBD,每个代码步给完整代码。✅
- **类型一致**:`recent_log_days(n:int)->list[tuple[date,str]]` T2 定义、T3 调用一致;`distill_memory()->str` T3 定义、T4/T5 调用一致;`config["distill"]` 键名(enabled/interval_hours/scan_days)T1 定义、T3/T5 使用一致。✅
- **依赖顺序**:T1(config+依赖)→T2(memory)→T3(distill 用 T1+T2)→T4(接口用 T3)→T5(scheduler 用 T1+T3)。无逆序引用。✅
