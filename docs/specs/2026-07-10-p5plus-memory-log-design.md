# P5+ 每日日志层(memory log)+ SYSTEM_PROMPT 瘦身 设计

> 里程碑 P5 记忆增强:给 Agent 加**时效记忆**——按天分文件的每日日志(`data/memory/YYYY-MM-DD.md`,只追加),开会话自动注入「今天+昨天」,提供 `append_log` 工具让 Agent 记录当天发生的具体事。**顺带**修掉 SYSTEM_PROMPT 里「复述工具清单」的系统性重复。
> 状态:设计已评审通过,待拆实现计划。
> 前置:P5 第一版记忆(profile.md + soul.md,commit 28e02e3..079671e)已完成。
> 对标参考:`docs/research/2026-07-09-openclaw-memory-system.md`(本设计是其中「演进路线第①条:每日日志层」的落地,刻意不含索引/检索/衰减等重型部分)。

---

## 〇、项目阶段约定(重要)

superstar 处于**项目初期、个人自用、无外部用户**。本设计**豁免** api-compatibility「只加不删」约束:允许破坏性变更,优先代码精简,不写兼容分支。

---

## 一、背景与目标

第一版记忆(profile/soul)解决了「Agent 记得你是谁、偏好如何」,但缺**时效记忆**:昨天让它改的东西、它踩过的坑、临时上下文,今天新会话全忘。

P5+ 目标:引入**每日日志层**,补上「最近发生了什么」。设计对标 OpenClaw 的每日日志(`memory/YYYY-MM-DD.md` 只追加 + 今天/昨天滚动窗口),但**刻意只做这一层**——不做索引、混合检索、时间衰减、压缩前刷新(那些依赖「记忆量大到塞不进 context」或「已有 compaction」等前置条件,当前都不具备,详见 research 文档的演进路线)。

**顺带修复**:调研发现现有 `SYSTEM_PROMPT` 把「工具是什么」逐个复述了一遍(grep/glob/read_file...),而这些在 tool description 里已有、API 会自动喂给模型 —— 是纯重复(多花 token、增噪音)。业界共识(Claude Code / OpenAI / Anthropic 文档):**tool description 是「参考手册」说 what;system prompt 是「操作规程」说 when/why/优先级,两者互补不重复**。本设计一并瘦身。

### 三种记忆的定位区别

| 记忆 | 语义 | 工具 | 写入语义 |
| --- | --- | --- | --- |
| `profile.md` | 用户长期稳定画像(身份、偏好、常用项目) | `update_profile` | 全量覆盖 |
| `soul.md` | Agent 自身行为准则 | `update_soul` | 全量覆盖 |
| `memory/YYYY-MM-DD.md` | **今天发生的具体事**(操作、踩坑、临时上下文) | `append_log` | **追加** |

---

## 二、关键设计决策(评审已定)

1. **加载窗口:今天 + 昨天**。照 OpenClaw 滚动窗口,只自动注入这两天的非空日志。更久的不自动加载(本期也不做检索,就真不管)。
2. **写入方式:Agent 工具主动记**。加 `append_log` 工具,Agent 自主决定记什么。不做「会话结束自动摘要」(那要额外 LLM 调用且易记噪音)。
3. **分工边界:靠工具描述 + system prompt 划清**。profile=稳定事实、soul=自身准则、log=今天的事。
4. **审批:自动放行**。`append_log` 走 gate 默认 auto,不改 gate.py。
5. **追加落盘:用 `open(mode="a")`,不走原子写**。日志是流水账、会越来越长,每次「读全文→原子重写」不划算;追加最坏只坏最后一行(读时 `errors=replace` 兜),与 session_store 的取舍一致。
6. **SYSTEM_PROMPT 瘦身**:删工具清单复述,留策略/边界,记忆部分只留一句路由。

---

## 三、架构与文件布局

### 3.1 存储(新建 `data/memory/` 子目录)

```
backend/data/
├── profile.md          # 已有
├── soul.md             # 已有
└── memory/             # 新增子目录(整体在 gitignore 覆盖的 data/ 下)
    ├── 2026-07-09.md   # 每日日志,只追加
    └── 2026-07-10.md
```

### 3.2 代码改动(方案 A:日志逻辑内聚在 memory.py)

| 文件 | 改动 |
| --- | --- |
| `app/services/memory.py` | 加日志函数;`build_memory_block()` 扩展拼日志段 | 
| `app/agent/tools/memory.py` | 加 `append_log` 工具 |
| `app/agent/tools/__init__.py` | 注册 `append_log` |
| `app/agent/loop.py` | `SYSTEM_PROMPT` 瘦身 + 记忆路由句 |

gate.py 不改(`append_log` 走默认 auto)。

---

## 四、`memory.py` 日志接口

在现有 profile/soul 函数之后新增(日期通过 `_today()` 取,便于测试 monkeypatch):

```python
from datetime import date, datetime, timedelta   # 新增 import

def _today() -> date:
    """当前日期。独立成函数 → 测试可 monkeypatch 造'今天/昨天/跨天'场景。"""
    return date.today()

def _log_dir() -> Path:
    return Path(settings.data_dir) / "memory"

def _log_path(d: date) -> Path:
    return _log_dir() / f"{d.isoformat()}.md"        # 如 memory/2026-07-10.md

def append_log(entry: str) -> None:
    """把一条带时间戳的条目追加到今天的日志。目录/文件不存在则建。
    用 open(mode='a') 追加(最坏只坏最后一行,不必原子)。"""
    d = _today()
    path = _log_path(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H:%M")          # 条目内时间,仅进文件内容、不进 system 注入
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

**要点**:
- **日期可注入**(`_today`):测试造场景的关键,能验证「今天+昨天」窗口与跨天行为。
- **条目格式 `- HH:MM 内容`**:时间戳只进**文件内容**,**不进注入小标题**(小标题只用文件名日期)。保证 prompt cache 前缀稳定(HH:MM 每次变,绝不进注入前缀)。
- **追加用 `open("a")`**:不原子写。
- **`recent_logs` 只返回非空天**:昨天没写就不注入昨天段。

---

## 五、注入 + 工具 + SYSTEM_PROMPT

### 5.1 `build_memory_block()` 扩展

```python
def build_memory_block() -> str:
    try:
        profile = read_profile().strip()
        soul = read_soul().strip()
        logs = recent_logs()                          # 新增
    except Exception as e:  # noqa: BLE001
        logger.warning("读取记忆失败,本轮不注入: %s", type(e).__name__)
        return ""
    parts: list[str] = []
    if profile:
        parts.append(f"## 关于用户\n{profile}")
    if soul:
        parts.append(f"## 你的准则\n{soul}")
    for d, content in logs:                            # 新增:每天一段
        label = "今天" if d == _today() else "昨天"
        parts.append(f"## {label}的日志({d.isoformat()})\n{content}")
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)
```

注入产出示例:
```
## 关于用户
用户叫小明,常用 superstar 项目

## 今天的日志(2026-07-10)
- 09:30 帮用户加了每日日志层
- 14:20 修了 gate 的一个边界
```
> 小标题用文件名日期,不含 HH:MM;同一天没新写日志 → 逐字节不变 → prompt cache 命中。

### 5.2 `append_log` 工具(`tools/memory.py` 追加)

```python
class AppendLogArgs(BaseModel):
    entry: str = Field(description=(
        "要追加到今天日志的一条记录:今天发生的具体事、做过的操作、遇到的坑、"
        "临时的上下文。一句话或一小段。这是流水账,不是长期画像。"))

def append_log(args: AppendLogArgs) -> str:
    memory.append_log(args.entry)
    return "已记入今天的日志"
```

注册(`tools/__init__.py` 末尾):
```python
registry.register(
    "append_log", append_log, AppendLogArgs,
    "把今天发生的具体事/操作/踩的坑追加到当天日志(流水账,带时间戳)。"
    "开会话时会自动看到今天+昨天的日志。记'今天的事'用它,记'长期稳定画像'用 update_profile。",
)
```

### 5.3 SYSTEM_PROMPT 瘦身(`loop.py`)

- **删**:开头「grep(按正则搜索)、glob(...)、read_file(...)、write_file(...)、run_command(...)、search_kb(...)、add_workspace/remove_workspace(...)」这一串工具清单复述。
- **留**:允许目录边界、绝对路径偏好、审批反馈说明、search_kb 反幻觉策略。
- **改开头**:「可以调用工具查看/修改用户电脑上的文件、检索知识库、管理可访问目录」(概括能力,不列清单)。
- **记忆路由**(替代原来逐个复述的记忆句):「你有长期记忆——用户的稳定事实用 update_profile,你自己的准则用 update_soul,今天发生的具体事用 append_log(开会话会自动看到今天+昨天的日志)。各工具怎么用见其说明。」

> 原则:tool description 说 what,system prompt 说 when/why。见 research 文档「其他产品怎么做」小节。

---

## 六、错误处理

| 场景 | 处理 |
| --- | --- |
| 日志目录/文件不存在 | `append_log` 建目录再写;`read_log` 空串。不报错。 |
| 追加写失败(磁盘满/权限) | 抛异常 → `registry.run` 兜住 → 「工具执行失败」喂回。 |
| 日志读到乱码 | `read_text(errors="replace")`。 |
| `recent_logs()` 读盘异常 | 在 `build_memory_block` 整体 try/except 内,退化成不注入日志段。 |
| 空 entry | 允许(strip 后写空条目,无害),不特殊拦截。 |

---

## 七、测试

monkeypatch `settings.data_dir` + `memory._today` 造场景。

1. **`test_memory.py`** 追加:
   - `append_log` 写入今天文件、含时间戳条目、可读回;
   - 连写两条 → 追加不覆盖(两条都在);
   - `read_log` 不存在返回空串;
   - `recent_logs`:monkeypatch `_today`,造「今天有/昨天有/前天有」→ 只返回今天+昨天、今天在前、前天不出现、空天跳过;
   - `build_memory_block` 含日志段:有今天日志 → 出现 `## 今天的日志(日期)`;无日志 → 不出现日志段;
   - 前缀稳定性:同内容同一天两次调用 `build_memory_block` 结果逐字节相同。
2. **`test_tools_memory.py`** 追加:
   - `append_log` 经 `registry.run` 写盘生效、返回预期字符串;缺 `entry` → 参数错误自愈;
   - `append_log` 已注册;
   - 端到端接缝:`registry.run("append_log", ...)` 后 `build_memory_block()` 能反映。
3. **`test_loop.py`** 追加(瘦身回归):
   - SYSTEM_PROMPT **不再包含**「grep(按正则搜索)」这类清单串(防止回退);
   - 仍包含策略关键句(如「允许目录」);
   - 已有 loop 测试全绿(注入逻辑改动不破坏现有行为)。

**gate 无需新增测试**(未改 gate.py)。

---

## 八、验收标准

- 现有全部测试 + 新增测试全绿。
- 手动端到端:让 Agent「记一下今天做了 X」→ Agent 调 `append_log` → `data/memory/今天.md` 落盘 → 新开会话 Agent 能看到今天日志。
- SYSTEM_PROMPT 瘦身后,现有工具行为(读写/审批/RAG)不受影响(靠 tool description 驱动)。

---

## 九、不做(YAGNI)

- ❌ 日志索引 / 混合检索 / 语义搜索(记忆短,注入今天+昨天够用;要检索是「记忆撑爆 context」才需要,留二期)。
- ❌ 时间衰减 / 常青区分(依赖按日期堆积的记忆先成规模)。
- ❌ 压缩前记忆刷新(依赖 compaction/M12,当前 `_fit_context` 还没裁剪)。
- ❌ 自动摘要写入日志(额外 LLM 调用、易噪音)。
- ❌ 超过「今天+昨天」的自动加载 / 日志归档 / 保留策略(量小,先不管)。
- ❌ 前端日志展示 UI。
