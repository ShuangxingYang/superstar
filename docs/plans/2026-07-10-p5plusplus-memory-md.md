# P5++ 长期客观记忆(MEMORY.md)+ profile 收紧 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `data/MEMORY.md` 长期客观记忆(`update_memory` 工具整份覆盖、Agent 自主写、全量注入 `## 长期记忆`),并收紧 profile 只记「特别确定是个人信息」的内容。

**Architecture:** 沿用前两期记忆分层,全部同构现有 profile/soul。`memory.py` 加 `read_memory`/`write_memory` + `build_memory_block` 插 MEMORY 段;`tools/memory.py` 加 `update_memory`;`tools/__init__.py` 注册 + 收紧 update_profile 描述;`loop.py` SYSTEM_PROMPT 四工具路由 + profile 收紧;待办登记进 HANDOFF + 演进路线文档。

**Tech Stack:** Python 3.11 · FastAPI · Pydantic · pytest · uv。设计详见 `docs/specs/2026-07-10-p5plusplus-memory-md-design.md`。

## Global Constraints

- 项目初期无外部用户:**豁免** api-compatibility 约束,允许破坏性变更(收紧 update_profile 语义/描述),优先精简,不写兼容分支。
- 工具函数**永不抛异常**,错误变返回值(由 `registry.run` 兜底)。
- 记忆文件存 `settings.data_dir` 下,路径从 `settings` **现取**。
- MEMORY.md **初始不自举**(同 profile,区别于 soul):不存在时 read 返回空串,不写模板。
- 整份覆盖写,原子落盘(`atomic_json.write_text_atomic`)。
- **注入顺序固定**(保 prompt cache):profile → **memory** → soul → 日志(今天+昨天)。
- 四种记忆边界(**主观/客观/关于我/关于今天**),措辞见 spec §2,须逐字用。
- 日志绝不打印记忆全文,只记 `len`。
- `update_memory` 自动放行(gate 默认 auto,**不改 gate.py**)。
- 命令在 `backend/` 下用 `uv run`。

---

### Task 1: `memory.py` 加 `read_memory`/`write_memory` + `build_memory_block` 插 MEMORY 段

**Files:**
- Modify: `backend/app/services/memory.py`(profile 函数附近加 memory 函数;改 build_memory_block)
- Test: `backend/tests/test_memory.py`(追加)

**Interfaces:**
- Consumes: `atomic_json.write_text_atomic`;`settings.data_dir`;现有 `read_profile`/`read_soul`/`recent_logs`。
- Produces: `memory.read_memory() -> str`、`memory.write_memory(content: str) -> None`;`build_memory_block()` 输出新增 `## 长期记忆` 段(供 Task 2 端到端接缝)。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_memory.py` **末尾**追加(顶部已有 `from datetime import date`、`pytest`、`memory`、`tmp_mem` fixture):

```python
# ============ P5++: 长期客观记忆 MEMORY.md ============
def test_read_memory_missing_returns_empty(tmp_mem):
    assert memory.read_memory() == ""                  # 不存在 → 空串,不自举


def test_write_then_read_memory_overwrites(tmp_mem):
    memory.write_memory("项目用 uv 管依赖")
    assert memory.read_memory() == "项目用 uv 管依赖"
    memory.write_memory("测试跑 uv run pytest")         # 整份覆盖
    assert memory.read_memory() == "测试跑 uv run pytest"


def test_build_block_includes_memory(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_soul("")                              # 排除 soul 干扰
    memory.write_memory("项目用 uv")
    block = memory.build_memory_block()
    assert "## 长期记忆" in block and "项目用 uv" in block


def test_build_block_no_memory_section_when_empty(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_soul("")
    block = memory.build_memory_block()
    assert "## 长期记忆" not in block


def test_build_block_injection_order(tmp_mem, monkeypatch):
    # 注入顺序:profile → memory → soul(都稳定,排一起;日志垫底)
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_profile("用户叫小明")
    memory.write_memory("项目用 uv")
    memory.write_soul("用中文")
    block = memory.build_memory_block()
    assert block.index("## 关于用户") < block.index("## 长期记忆") < block.index("## 你的准则")


def test_build_block_memory_prefix_stable(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_memory("项目用 uv")
    assert memory.build_memory_block() == memory.build_memory_block()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_memory.py::test_read_memory_missing_returns_empty -v`
Expected: FAIL —— `AttributeError: module 'app.services.memory' has no attribute 'read_memory'`

- [ ] **Step 3: 实现 —— 加 memory 读写函数**

在 `backend/app/services/memory.py` 里,现有 `write_soul` 函数之后(或 profile 函数附近)插入:

```python
def _memory_path() -> Path:
    return Path(settings.data_dir) / "MEMORY.md"


def read_memory() -> str:
    """读 MEMORY.md(长期客观记忆)。不存在 → 空串(不自举,同 profile)。errors=replace 防乱码。"""
    p = _memory_path()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def write_memory(content: str) -> None:
    """整份覆盖写 MEMORY.md(原子写)。"""
    atomic_json.write_text_atomic(_memory_path(), content)
    logger.info("已更新长期记忆(memory), len=%d", len(content))
```

- [ ] **Step 4: 实现 —— `build_memory_block` 插 MEMORY 段**

把 `build_memory_block` 改为(在 profile 之后、soul 之前加 memory):

```python
def build_memory_block() -> str:
    """拼成注入 system prompt 的一段文本;都空 → 空串。
    整体兜错:读记忆失败绝不让 agent 循环挂掉,退化成本轮不注入 + 记 warning。
    注入顺序固定(profile→memory→soul→日志),保 prompt cache 前缀稳定。"""
    try:
        profile = read_profile().strip()
        memory_ = read_memory().strip()
        soul = read_soul().strip()
        logs = recent_logs()
    except Exception as e:  # noqa: BLE001 - 关键路径,任何异常都退化成"不注入"
        logger.warning("读取记忆失败,本轮不注入: %s", type(e).__name__)
        return ""
    parts: list[str] = []
    if profile:
        parts.append(f"## 关于用户\n{profile}")
    if memory_:
        parts.append(f"## 长期记忆\n{memory_}")
    if soul:
        parts.append(f"## 你的准则\n{soul}")
    for d, content in logs:
        label = "今天" if d == _today() else "昨天"
        parts.append(f"## {label}的日志({d.isoformat()})\n{content}")
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)
```

> 注意:只在 profile 与 soul 之间插入 memory 段,其余(日志段、try/except、空返回)保持不变。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_memory.py -v`
Expected: PASS(原有 17 个 + 新增 6 个 = 23 个全绿)

- [ ] **Step 6: 提交**

```bash
git add app/services/memory.py tests/test_memory.py
git commit -m "feat(p5++): memory 加 MEMORY.md 长期客观记忆(read/write_memory)+ 注入 ## 长期记忆"
```

---

### Task 2: `update_memory` 工具 + 注册 + profile 收紧 + SYSTEM_PROMPT + 待办登记

**Files:**
- Modify: `backend/app/agent/tools/memory.py`(加 update_memory)
- Modify: `backend/app/agent/tools/__init__.py`(注册 update_memory + 收紧 update_profile 描述 + 更新 append_log 描述末句)
- Modify: `backend/app/agent/loop.py`(SYSTEM_PROMPT 记忆路由四工具 + profile 收紧)
- Modify: `HANDOFF.md`(待办登记)+ `docs/research/2026-07-09-openclaw-memory-system.md`(演进路线更新)
- Test: `backend/tests/test_tools_memory.py`、`backend/tests/test_loop.py`(追加)

**Interfaces:**
- Consumes: `memory.write_memory`(Task 1);`registry`;`memory.build_memory_block`(端到端测试)。
- Produces: 注册表新增 `update_memory`;SYSTEM_PROMPT 含四工具路由。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_tools_memory.py` **末尾**追加:

```python
def test_update_memory_writes(tmp_mem):
    out = registry.run("update_memory", {"content": "项目用 uv 管依赖"})
    assert "memory" in out or "长期记忆" in out
    assert memory.read_memory() == "项目用 uv 管依赖"


def test_update_memory_missing_content_self_heals(tmp_mem):
    out = registry.run("update_memory", {})
    assert "参数错误" in out


def test_update_memory_registered():
    names = {s["function"]["name"] for s in registry.to_openai_schema()}
    assert "update_memory" in names


def test_update_memory_then_reinject_roundtrip(tmp_mem):
    registry.run("update_memory", {"content": "测试跑 uv run pytest"})
    block = memory.build_memory_block()
    assert "## 长期记忆" in block and "测试跑 uv run pytest" in block
```

在 `backend/tests/test_loop.py` **末尾**追加:

```python
# ============ P5++: 四工具记忆路由 + profile 收紧 ============
def test_system_prompt_has_update_memory():
    assert "update_memory" in loop.SYSTEM_PROMPT


def test_system_prompt_profile_tightened():
    # 收紧后 SYSTEM_PROMPT 应体现"个人信息"这一 profile 边界措辞
    assert "个人信息" in loop.SYSTEM_PROMPT
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_tools_memory.py::test_update_memory_writes tests/test_loop.py::test_system_prompt_has_update_memory -v`
Expected: 两者 FAIL(未知工具 update_memory / SYSTEM_PROMPT 无 update_memory)。

- [ ] **Step 3: 实现工具函数**

在 `backend/app/agent/tools/memory.py` **末尾**(append_log 之后)追加:

```python
class UpdateMemoryArgs(BaseModel):
    content: str = Field(description=(
        "长期记忆的完整新内容(整份覆盖)。"
        "先基于 system 里已注入的现有长期记忆合并,再写回完整内容。"))


def update_memory(args: UpdateMemoryArgs) -> str:
    memory.write_memory(args.content)
    return "已更新长期记忆(memory)"
```

- [ ] **Step 4: 注册 update_memory + 收紧 update_profile + 更新 append_log 描述**

在 `backend/app/agent/tools/__init__.py`:

**(a)** 把现有 `update_profile` 的注册描述(第 157 行那句)**替换**为收紧版:

```python
registry.register(
    "update_profile", update_profile, UpdateProfileArgs,
    "沉淀关于用户本人的个人信息(姓名、身份、职业、个人偏好等跟人强相关的稳定事实)。"
    "只有特别确定是用户个人信息时才记;项目/技术的客观事实用 update_memory,不要往这里塞。"
    "整份覆盖:先基于 system 里已注入的现有画像合并,再写回完整内容。",
)
```

**(b)** 把现有 `append_log` 注册描述末句(「记'长期稳定画像'用 update_profile」)**更新**为区分 profile/memory:

```python
registry.register(
    "append_log", append_log, AppendLogArgs,
    "把今天发生的具体事/操作/踩的坑追加到当天日志(流水账,带时间戳)。"
    "开会话时会自动看到今天+昨天的日志。记'今天的事'用它;"
    "用户个人信息用 update_profile,项目客观事实用 update_memory。",
)
```

**(c)** 在文件**末尾**追加 update_memory 的 import + 注册:

```python
from app.agent.tools.memory import UpdateMemoryArgs, update_memory  # noqa: E402

registry.register(
    "update_memory", update_memory, UpdateMemoryArgs,
    "沉淀需要长期记住的客观事实与既定结论(项目约定、技术栈、架构决策、重要背景等跟人无关的稳定知识)。"
    "区别于 profile(用户个人信息)、区别于日志(今天的流水)。"
    "整份覆盖:先基于 system 里已注入的现有长期记忆合并,再写回完整内容。",
)
```

- [ ] **Step 5: 实现 —— SYSTEM_PROMPT 四工具路由 + profile 收紧**

在 `backend/app/agent/loop.py` 的 SYSTEM_PROMPT 常量里,把现有记忆路由那句(P5+ 瘦身后写的「你有长期记忆:用户的稳定事实用 update_profile;...append_log...」)**替换**为四工具版:

```python
    "你有四种长期记忆,按内容归类:用户个人信息(姓名、偏好等跟人强相关的)用 update_profile;"
    "客观事实与既定结论(项目约定、技术栈、决策等跟人无关的)用 update_memory;"
    "你自己的行为准则用 update_soul;今天发生的具体事用 append_log"
    "(开会话会自动看到今天+昨天的日志)。"
    "核心区分:主观个人信息进 profile,客观稳定事实进 memory,一次性的事进 log。"
```

> 只替换记忆路由那几行,SYSTEM_PROMPT 其余(工具能力概括、允许目录、审批、search_kb 反幻觉)保持 P5+ 瘦身后的样子不动。

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/test_tools_memory.py tests/test_loop.py -v`
Expected: PASS(tools_memory 原 10 + 新 4 = 14;loop 原 20 + 新 2 = 22)

- [ ] **Step 7: 待办登记(HANDOFF + 演进路线)**

**(a)** `HANDOFF.md` 待办区(P5 剩余那节)加一条:

```markdown
- **记忆自动蒸馏(未做,待办)**:当前 MEMORY.md 靠用户显式触发提炼(「整理下最近日志到长期记忆」)。待办是做定时任务,自动扫近期日志→提炼客观事实→更新 MEMORY,减少「用户忘了触发就永不沉淀」。对标 OpenClaw dreaming sweep。
```

**(b)** `docs/research/2026-07-09-openclaw-memory-system.md` 的演进路线小节:把「MEMORY.md 长期记忆层」标为 ✅ 已完成(2026-07-10),并在其后明确下一候选 =「定时自动从日志蒸馏进 MEMORY(dreaming sweep)」。

> 具体行文由实现者按文档现有风格补;要点:MEMORY 完成、自动蒸馏是下一步、动机是减少手动触发遗漏。

- [ ] **Step 8: 全量回归**

Run: `uv run pytest -q`
Expected: 全绿。基线 166 + 本计划新增(memory 6 + tools 4 + loop 2 = 12)= 178 passed(可能 1 个 fastapi/testclient 第三方 warning,与本期无关)。

- [ ] **Step 9: 提交**

```bash
git add app/agent/tools/memory.py app/agent/tools/__init__.py app/agent/loop.py tests/test_tools_memory.py tests/test_loop.py docs/research/2026-07-09-openclaw-memory-system.md
git commit -m "feat(p5++): update_memory 工具 + profile 收紧 + 四工具路由 + 自动蒸馏待办登记"
```

> HANDOFF.md 一直未跟踪(不入库),改动保留在磁盘不 commit(与前几期一致)。

---

## 自审记录

- **Spec 覆盖**:§2 边界措辞 → Task 2 Step 4(三处描述)+ Step 5(SYSTEM_PROMPT);§5.1 read/write_memory → Task 1 Step 3;§5.2 build_memory_block 插 MEMORY → Task 1 Step 4;§5.3 update_memory 工具 → Task 2 Step 3;§5.4 注册+收紧 profile → Task 2 Step 4;§6 待办登记 → Task 2 Step 7;§7 错误处理(write 失败靠 registry.run → Task 2 缺参测试;read_memory 异常在 build try/except 内,沿用现有);§8 测试三组全覆盖(注入顺序 test_build_block_injection_order、profile 收紧 test_system_prompt_profile_tightened);§9 验收(Task 2 Step 8 全量 + 下方手动闭环)。
- **占位符扫描**:仅 Step 7(b) 演进路线行文留给实现者按现有文风补——但要点已明确列出(完成标记 + 下一候选 + 动机),非空泛占位。其余 code step 均完整可粘贴。
- **类型一致性**:`read_memory`/`write_memory`/`_memory_path` 在 Task 1 定义,Task 2 端到端测试引用一致;`UpdateMemoryArgs`/`update_memory` 工具名在 Task 2 定义与测试/SYSTEM_PROMPT 引用一致;注入小标题 `## 长期记忆` 在 Task 1 实现、Task 1/Task 2 测试断言一致。

## 手动验收(全部 Task 完成后)

1. 重启后端。会话说「记住:本项目测试用 uv run pytest」→ 观察 Agent 调 `update_memory`(不弹审批)→ 确认 `backend/data/MEMORY.md` 落盘。
2. **新开会话**问「这项目测试怎么跑」→ Agent 应从注入的长期记忆答出。
3. 边界验证:分别说「我叫小明」(应进 profile)和「项目用 uv」(应进 MEMORY),看归类是否正确。
4. 显式提炼:「把最近日志里重要的客观事实整理进长期记忆」→ Agent 读日志→提炼→调 update_memory。
