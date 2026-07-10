# P5+ 每日日志层 + SYSTEM_PROMPT 瘦身 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Agent 加每日日志时效记忆(`data/memory/YYYY-MM-DD.md` 追加式 + `append_log` 工具 + 注入今天/昨天),并顺带瘦身 SYSTEM_PROMPT(删工具清单复述、只留策略)。

**Architecture:** 沿用 P5 分层。日志逻辑内聚进现有 `memory.py`(方案 A),日期用可 monkeypatch 的 `_today()`;`build_memory_block()` 扩展拼日志段;`tools/memory.py` 加 `append_log`;`loop.py` 瘦身 SYSTEM_PROMPT 并把记忆句改成一句路由。

**Tech Stack:** Python 3.11 · FastAPI · Pydantic · pytest · uv。设计详见 `docs/specs/2026-07-10-p5plus-memory-log-design.md`;对标背景见 `docs/research/2026-07-09-openclaw-memory-system.md`。

## Global Constraints

- 项目初期无外部用户:**豁免** api-compatibility 约束,允许破坏性变更,优先代码精简,不写兼容分支。
- 工具函数**永不抛异常**,错误变返回值喂回(由 `registry.run` 兜底)。
- 记忆文件存 `settings.data_dir` 下,路径从 `settings` **现取**(便于测试 monkeypatch)。
- 日志用 `open(mode="a")` 追加,**不走原子写**(流水账、会变长,最坏只坏最后一行,读时 `errors="replace"` 兜)。
- 日期通过 `memory._today()` 取(独立函数,测试可 monkeypatch 造今天/昨天/跨天场景)。
- 注入格式**稳定**:日志小标题只用**文件名日期**(如 `## 今天的日志(2026-07-10)`),**不含 HH:MM**(HH:MM 只进文件内容);同一天未新写日志 → 注入逐字节不变 → 保 prompt cache。
- **SYSTEM_PROMPT 原则**:tool description 说 what,system prompt 说 when/why —— 不复述工具清单。
- 日志绝不打印全文,只记 `date` + `len`。
- 命令在 `backend/` 下用 `uv run`。

---

### Task 1: `memory.py` 日志接口 + `build_memory_block()` 扩展

**Files:**
- Modify: `backend/app/services/memory.py`(顶部 import 加日期类型;末尾加日志函数;改 `build_memory_block`)
- Test: `backend/tests/test_memory.py`(追加日志相关测试)

**Interfaces:**
- Consumes: `settings.data_dir`;现有 `read_profile`/`read_soul`。
- Produces:
  - `memory._today() -> date`
  - `memory.append_log(entry: str) -> None`
  - `memory.read_log(d: date) -> str`
  - `memory.recent_logs() -> list[tuple[date, str]]`
  - `build_memory_block()` 输出新增日志段(供 Task 2 端到端接缝、Task 3 无关)

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_memory.py` **末尾**追加(文件顶部已有 `import pytest`、`from app.config import settings`、`from app.services import memory` 和 `tmp_mem` fixture,直接复用):

```python
# ============ P5+: 每日日志层 ============
from datetime import date


def test_append_log_writes_today(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.append_log("帮用户加了日志层")
    content = memory.read_log(date(2026, 7, 10))
    assert "帮用户加了日志层" in content
    assert content.startswith("- ")                 # 带 "- HH:MM " 前缀的条目


def test_append_log_appends_not_overwrites(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.append_log("第一条")
    memory.append_log("第二条")
    content = memory.read_log(date(2026, 7, 10))
    assert "第一条" in content and "第二条" in content   # 追加,不覆盖
    assert content.count("\n") == 2                       # 两行条目


def test_read_log_missing_returns_empty(tmp_mem):
    assert memory.read_log(date(2026, 1, 1)) == ""


def test_recent_logs_today_and_yesterday_only(tmp_mem, monkeypatch):
    # 造:今天(07-10)、昨天(07-09)、前天(07-08)各写一条,前天不该出现
    for d, text in [(date(2026, 7, 10), "今天事"),
                    (date(2026, 7, 9), "昨天事"),
                    (date(2026, 7, 8), "前天事")]:
        monkeypatch.setattr(memory, "_today", lambda d=d: d)
        memory.append_log(text)
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    logs = memory.recent_logs()
    assert [d for d, _ in logs] == [date(2026, 7, 10), date(2026, 7, 9)]  # 今天在前,只两天
    joined = " ".join(c for _, c in logs)
    assert "今天事" in joined and "昨天事" in joined and "前天事" not in joined


def test_recent_logs_skips_empty_days(tmp_mem, monkeypatch):
    # 只有今天有日志,昨天没有 → recent_logs 只返回今天
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.append_log("只有今天")
    logs = memory.recent_logs()
    assert [d for d, _ in logs] == [date(2026, 7, 10)]


def test_build_block_includes_today_log(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_soul("")                            # 排除 soul 干扰
    memory.append_log("加了日志层")
    block = memory.build_memory_block()
    assert "## 今天的日志(2026-07-10)" in block
    assert "加了日志层" in block


def test_build_block_no_log_section_when_empty(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_soul("")
    block = memory.build_memory_block()
    assert "的日志(" not in block                     # 无日志 → 无日志段


def test_build_block_prefix_stable_same_day(tmp_mem, monkeypatch):
    # 前缀稳定性:同一天、内容不变,两次调用逐字节相同(保 prompt cache)
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_profile("用户叫小明")
    memory.append_log("干了活")
    assert memory.build_memory_block() == memory.build_memory_block()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_memory.py::test_append_log_writes_today -v`
Expected: FAIL —— `AttributeError: module 'app.services.memory' has no attribute '_today'`(或 `append_log`）

- [ ] **Step 3: 实现 —— 加日期 import + 日志函数**

改 `backend/app/services/memory.py`:

**(a)** 顶部 import 区(现有 `from pathlib import Path` 下面)加:

```python
from datetime import date, datetime, timedelta
```

**(b)** 在现有 `write_soul` 函数之后、`build_memory_block` 之前,插入日志函数:

```python
def _today() -> date:
    """当前日期。独立成函数 → 测试可 monkeypatch 造'今天/昨天/跨天'场景。"""
    return date.today()


def _log_dir() -> Path:
    return Path(settings.data_dir) / "memory"


def _log_path(d: date) -> Path:
    return _log_dir() / f"{d.isoformat()}.md"          # 如 memory/2026-07-10.md


def append_log(entry: str) -> None:
    """把一条带时间戳的条目追加到今天的日志。目录/文件不存在则建。
    用 open(mode='a') 追加(最坏只坏最后一行,不必原子)。"""
    d = _today()
    path = _log_path(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H:%M")           # 条目内时间,仅进文件、不进 system 注入
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {stamp} {entry.strip()}\n")
    logger.info("已追加日志: date=%s, len=%d", d.isoformat(), len(entry))


def read_log(d: date) -> str:
    """读某天日志。不存在 → 空串。errors=replace 防乱码。"""
    p = _log_path(d)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def recent_logs() -> list[tuple[date, str]]:
    """返回'今天+昨天'里非空的 (日期, 内容),今天在前。供 build_memory_block 用。"""
    today = _today()
    out: list[tuple[date, str]] = []
    for d in (today, today - timedelta(days=1)):
        content = read_log(d).strip()
        if content:
            out.append((d, content))
    return out
```

- [ ] **Step 4: 实现 —— `build_memory_block` 拼日志段**

把 `build_memory_block` 改成(在现有 profile/soul 拼接基础上加 logs):

```python
def build_memory_block() -> str:
    """拼成注入 system prompt 的一段文本;都空 → 空串。
    整体兜错:读记忆失败绝不让 agent 循环挂掉,退化成本轮不注入 + 记 warning。
    格式固定(日志小标题只用文件名日期,无 HH:MM/随机项),保 prompt cache 前缀稳定。"""
    try:
        profile = read_profile().strip()
        soul = read_soul().strip()
        logs = recent_logs()
    except Exception as e:  # noqa: BLE001 - 关键路径,任何异常都退化成"不注入"
        logger.warning("读取记忆失败,本轮不注入: %s", type(e).__name__)
        return ""
    parts: list[str] = []
    if profile:
        parts.append(f"## 关于用户\n{profile}")
    if soul:
        parts.append(f"## 你的准则\n{soul}")
    for d, content in logs:
        label = "今天" if d == _today() else "昨天"
        parts.append(f"## {label}的日志({d.isoformat()})\n{content}")
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_memory.py -v`
Expected: PASS(原有 8 个 + 新增 8 个 = 16 个全绿)

- [ ] **Step 6: 提交**

```bash
git add app/services/memory.py tests/test_memory.py
git commit -m "feat(p5+): memory 加每日日志层(append_log/read_log/recent_logs)+ 注入今天昨天"
```

---

### Task 2: `append_log` 工具 + 注册

**Files:**
- Modify: `backend/app/agent/tools/memory.py`(追加工具)
- Modify: `backend/app/agent/tools/__init__.py`(末尾注册)
- Test: `backend/tests/test_tools_memory.py`(追加)

**Interfaces:**
- Consumes: `memory.append_log`(Task 1);`registry`。
- Produces: 注册表新增 `append_log` 工具名。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_tools_memory.py` **末尾**追加(顶部已有 `from app.config import settings`、`from app.services import memory`、`from app.agent.tools import registry`、`tmp_mem` fixture):

```python
def test_append_log_tool_writes(tmp_mem):
    out = registry.run("append_log", {"entry": "今天加了日志工具"})
    assert "日志" in out
    from app.services import memory as _m
    assert "今天加了日志工具" in _m.read_log(_m._today())


def test_append_log_missing_entry_self_heals(tmp_mem):
    out = registry.run("append_log", {})
    assert "参数错误" in out


def test_append_log_registered():
    names = {s["function"]["name"] for s in registry.to_openai_schema()}
    assert "append_log" in names


def test_append_log_then_reinject_roundtrip(tmp_mem):
    # 端到端接缝:Agent 调 append_log → build_memory_block 能反映出来
    registry.run("append_log", {"entry": "端到端验证条目"})
    block = memory.build_memory_block()
    assert "的日志(" in block
    assert "端到端验证条目" in block
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_tools_memory.py::test_append_log_tool_writes -v`
Expected: FAIL —— 返回「错误:未知工具 append_log」,断言不通过。

- [ ] **Step 3: 实现工具函数**

在 `backend/app/agent/tools/memory.py` **末尾**追加(现有 `update_soul` 之后):

```python
class AppendLogArgs(BaseModel):
    entry: str = Field(description=(
        "要追加到今天日志的一条记录:今天发生的具体事、做过的操作、遇到的坑、"
        "临时的上下文。一句话或一小段。这是流水账,不是长期画像。"))


def append_log(args: AppendLogArgs) -> str:
    memory.append_log(args.entry)
    return "已记入今天的日志"
```

- [ ] **Step 4: 注册工具**

在 `backend/app/agent/tools/__init__.py` **末尾**(现有 `update_soul` 注册之后)追加。把现有的 memory 工具 import 行扩展为一并导入 `append_log`,或新增一行 import,然后 register:

```python
from app.agent.tools.memory import AppendLogArgs, append_log  # noqa: E402

registry.register(
    "append_log", append_log, AppendLogArgs,
    "把今天发生的具体事/操作/踩的坑追加到当天日志(流水账,带时间戳)。"
    "开会话时会自动看到今天+昨天的日志。记'今天的事'用它,记'长期稳定画像'用 update_profile。",
)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_tools_memory.py -v`
Expected: PASS(原有 5 个 + 新增 4 个 = 9 个全绿)

- [ ] **Step 6: 提交**

```bash
git add app/agent/tools/memory.py app/agent/tools/__init__.py tests/test_tools_memory.py
git commit -m "feat(p5+): append_log 工具 + 注册(自动放行)"
```

---

### Task 3: SYSTEM_PROMPT 瘦身(删工具清单复述 + 记忆改路由句)

**Files:**
- Modify: `backend/app/agent/loop.py`(`SYSTEM_PROMPT` 常量 20-36 行 + 上方注释 19-20 行)
- Test: `backend/tests/test_loop.py`(追加瘦身回归测试)

**Interfaces:**
- Consumes: 无(纯文本重构)。
- Produces: 无(终点任务)。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_loop.py` **末尾**追加:

```python
# ============ P5+: SYSTEM_PROMPT 瘦身回归 ============
def test_system_prompt_no_tool_list_recital():
    # 瘦身后不应再逐个复述工具是什么(tool description 已负责 what)
    assert "grep(按正则搜索)" not in loop.SYSTEM_PROMPT
    assert "glob(按通配列文件)" not in loop.SYSTEM_PROMPT


def test_system_prompt_keeps_policy():
    # 策略/边界必须保留(tool schema 传达不了的)
    assert "允许目录" in loop.SYSTEM_PROMPT           # 沙箱边界
    assert "来源" in loop.SYSTEM_PROMPT                # search_kb 反幻觉


def test_system_prompt_keeps_memory_routing():
    # 三种记忆的路由要在(哪种事记哪),但不逐个复述工具机制
    assert "update_profile" in loop.SYSTEM_PROMPT
    assert "append_log" in loop.SYSTEM_PROMPT
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_loop.py::test_system_prompt_no_tool_list_recital tests/test_loop.py::test_system_prompt_keeps_memory_routing -v`
Expected: `test_system_prompt_no_tool_list_recital` FAIL(当前 SYSTEM_PROMPT 仍含「grep(按正则搜索)」);`test_system_prompt_keeps_memory_routing` FAIL(当前无 `append_log`)。

- [ ] **Step 3: 实现 —— 重写 SYSTEM_PROMPT**

把 `backend/app/agent/loop.py` 第 19-36 行整段(注释 + `SYSTEM_PROMPT = (...)`)替换为:

```python
# system 基座:概括职责与策略,不复述工具清单(每个工具的 what 由其 tool description 负责,
# API 会自动喂给模型;这里只说 when/why/边界/优先级)。
# 每轮循环会把 memory.build_memory_block()(profile/soul/日志)拼在这段之后(见下方循环)。
SYSTEM_PROMPT = (
    "你是一个本地助手,可以调用工具查看/修改用户电脑上的文件、检索文档知识库、管理可访问目录。"
    "你只能访问「允许目录」内的文件(默认工作目录 + 白名单目录);路径优先用绝对路径。"
    "需要访问允许目录之外的文件时,先申请把该目录加入白名单(需用户批准);不再需要时移除。"
    "需要看/改文件再作答时就调用工具;能直接回答的问题不必调用。"
    "写文件和跑命令可能需要用户审批,危险命令会被拒绝,你会在结果里看到反馈。"
    "查资料时只依据检索到的片段回答;片段里没有的,明确说「知识库里没有相关内容」,"
    "不要编造;回答时带上来源。"
    "你有长期记忆:用户的稳定事实(身份、偏好、常用项目)用 update_profile;"
    "你自己的行为准则用 update_soul;今天发生的具体事、操作、踩的坑用 append_log"
    "(开会话会自动看到今天+昨天的日志)。分清:稳定事实进 profile,一次性/时效的事进 log。"
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_loop.py -v`
Expected: PASS(原有 loop 测试 —— 含 P5 的 `test_memory_injected_into_system`/`test_system_unchanged_when_memory_empty` —— + 新增 3 个全绿)

> 注意:P5 的 `test_system_unchanged_when_memory_empty` 断言「记忆为空时 system == SYSTEM_PROMPT」,它比较的是**常量本身**,瘦身改的是常量内容、不改「为空时等于常量」的逻辑,故仍通过。

- [ ] **Step 5: 全量回归**

Run: `uv run pytest -q`
Expected: 全绿。基线 150 + 本计划新增(memory 8 + tools 4 + loop 3 = 15)= 165 passed(可能有 1 个来自 fastapi/testclient 的第三方弃用 warning,与本期无关)。

- [ ] **Step 6: 提交**

```bash
git add app/agent/loop.py tests/test_loop.py
git commit -m "refactor(p5+): SYSTEM_PROMPT 瘦身——删工具清单复述,记忆改路由句,加 append_log 边界"
```

---

## 自审记录

- **Spec 覆盖**:§4 日志接口 → Task 1 全部函数;§5.1 build_memory_block 扩展 → Task 1 Step 4;§5.2 append_log 工具 → Task 2;§5.3 SYSTEM_PROMPT 瘦身 → Task 3;§6 错误处理(append_log 抛错靠 registry.run 兜 → Task 2 缺参测试;recent_logs 异常在 build_memory_block try/except 内 → 沿用 P5 已有的 `test_build_block_survives_read_error`,本期不重复);§7 测试三组全覆盖;§8 验收(Task 3 Step 5 全量回归 + 下方手动闭环);§9 YAGNI(计划不含索引/检索/衰减/摘要,一致)。
- **占位符扫描**:无 TBD/TODO,每个 code step 都是完整可粘贴代码。
- **类型一致性**:`_today`/`append_log`/`read_log`/`recent_logs` 在 Task 1 定义,Task 2 端到端测试用 `_m._today()` 引用一致;`AppendLogArgs`/`append_log` 工具名在 Task 2 定义与 Task 3 SYSTEM_PROMPT/测试引用一致。日志小标题格式 `## {label}的日志({d.isoformat()})` 在 Task 1 实现与 Task 1/Task 2 测试断言一致(`## 今天的日志(2026-07-10)` / `的日志(`）。

## 手动验收(全部 Task 完成后)

1. 重启后端(加载新代码)。前端开会话说「记一下:今天我们加了每日日志层」→ 观察 Agent 调 `append_log`(不弹审批)→ 确认 `backend/data/memory/<今天>.md` 落盘,内容是带时间戳的条目。
2. **新开会话**,问「今天都干了啥」→ Agent 应能从注入的今天日志答出。
3. 确认瘦身后常规操作(读文件、grep、写文件审批、search_kb)仍正常——工具行为由各自 description 驱动,不受 SYSTEM_PROMPT 瘦身影响。
