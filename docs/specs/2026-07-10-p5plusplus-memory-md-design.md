# P5++ 长期客观记忆(MEMORY.md)+ profile 收紧 设计

> 里程碑 P5 记忆增强(第三块):给记忆系统补上缺失的一层——`data/MEMORY.md` 长期**客观**记忆(项目约定、技术栈、既定决策等跟人无关的稳定事实),`update_memory` 工具整份覆盖、Agent 自主写、全量注入 system。同时**收紧 profile** 的语义,让它只记「特别确定是用户个人信息」的内容。
> 状态:设计已评审通过,待拆实现计划。
> 前置:P5 第一版记忆(profile/soul)+ P5+ 每日日志层(commit 395219c..31d5604)已完成。
> 对标背景:`docs/research/2026-07-09-openclaw-memory-system.md`(OpenClaw 的 MEMORY.md = 常青知识,不受时间衰减)。

---

## 〇、项目阶段约定(重要)

superstar 处于**项目初期、个人自用、无外部用户**。本设计**豁免** api-compatibility「只加不删」约束:允许破坏性变更(如收紧 update_profile 的语义/描述),优先代码精简,不写兼容分支。

---

## 一、背景与目标

前两期建成了三层记忆:`profile`(用户是谁)、`soul`(Agent 准则)、每日 `log`(今天流水)。但存在一个**层次断档**:

一件「重要、需长期记住,但既不是用户个人信息、也不是 Agent 准则」的**客观事实**——如「项目用 uv 管依赖」「测试跑 `uv run pytest`」「RAG 只服务文档不服务代码」——现在**无家可归**:塞进 profile 语义不符,写进 log 会随「今天+昨天」滚动窗口滑走(等于遗忘)。

同时,现有 `profile` 语义偏宽,容易把「项目约定」这类客观事实误塞进「用户画像」。

**目标**:
1. 新增 `MEMORY.md` 承接**客观、稳定、长期有用**的事实。对标 OpenClaw 的 `MEMORY.md`(常青知识)。
2. **收紧 profile**:只记「特别确定是用户个人信息」的内容,与 MEMORY 用「主观/客观」清晰分界。
3. 提炼靠**用户显式触发**(「整理下最近日志到长期记忆」);「定时自动蒸馏」登记为**待办**。

---

## 二、四种记忆的边界(本设计核心)

| 记忆 | 定义 | 判定标准 | ✅ 该记 | ❌ 不该记 |
|---|---|---|---|---|
| **profile** | 用户**个人信息**,跟人强相关 | **特别确定是个人信息**才记(门槛高,宁缺勿滥) | 叫小明 / 是前端不熟 Python / 偏好简洁 | 技术栈(→MEMORY)、今天做了啥(→log) |
| **MEMORY** | **客观事实**、需长期记录、跨会话稳定有用 | 客观 + 稳定 + 长期有用 | 项目用 uv / 测试 `uv run pytest` / RAG 只服务文档 / 已决定二期做飞书 | 用户偏好(→profile)、一次性的事(→log) |
| **soul** | Agent **自身**行为准则 | 关于「我该怎么做事」 | 用中文说人话 / 危险操作先确认 | (不变) |
| **log** | **今天**发生的具体事 | 一次性、时效 | 今天加了 MEMORY 层 / 踩了 X 坑 | 稳定结论(→MEMORY) |

**一句话记忆法**:profile=**主观**(关于这个人)/ MEMORY=**客观**(关于世界·项目)/ soul=**关于我**(Agent)/ log=**关于今天**。

### 关键措辞(进代码)

- **`update_profile` 描述(收紧)**:「沉淀关于**用户本人**的个人信息(姓名、身份、职业、个人偏好等**跟人强相关**的稳定事实)。**只有特别确定是用户个人信息时才记**;项目/技术的客观事实用 update_memory,不要往这里塞。整份覆盖。」
- **`update_memory` 描述(新增)**:「沉淀需要长期记住的**客观事实与既定结论**(项目约定、技术栈、架构决策、重要背景等**跟人无关**的稳定知识)。区别于 profile(用户个人信息)、区别于日志(今天的流水)。整份覆盖:先基于 system 里已注入的现有长期记忆合并再写回。」
- **SYSTEM_PROMPT 四工具路由句**:「你有四种长期记忆,按内容归类:**用户个人信息**(姓名/偏好等跟人强相关的)用 update_profile;**客观事实与既定结论**(项目约定/技术栈/决策等跟人无关的)用 update_memory;**你自己的行为准则**用 update_soul;**今天发生的具体事**用 append_log。核心区分:主观个人信息进 profile,客观稳定事实进 memory,一次性的事进 log。」

---

## 三、关键设计决策(评审已定)

1. **写入:只加 `update_memory` 工具**(整份覆盖,同 update_profile)。Agent 自主写;提炼日志靠用户显式要求触发,不做专门前端入口。
2. **注入:全量注入 system**(与 profile/soul 并列 `## 长期记忆`)。客观事实稳定、量不大,全量注入简单又保 prompt cache。
3. **profile 收紧**:门槛提到「特别确定是个人信息」。改 update_profile 描述 + SYSTEM_PROMPT 措辞。
4. **审批:自动放行**(gate 默认 auto,不改 gate.py)。
5. **初始不自举**(同 profile,区别于 soul):MEMORY.md 不存在时 read 返回空串,不写默认模板。
6. **自动蒸馏 → 待办**(登记进 HANDOFF + 演进路线文档,本期不做)。

---

## 四、架构与文件布局

### 4.1 存储

```
backend/data/
├── profile.md          # 已有(语义收紧,文件本身不动)
├── soul.md             # 已有
├── MEMORY.md           # 新增:长期客观记忆,初始不存在(不自举)
└── memory/             # 已有(每日日志)
```

### 4.2 代码改动(方案 A:全部同构现有,不做过早抽象)

| 文件 | 改动 |
| --- | --- |
| `app/services/memory.py` | 加 `_memory_path`/`read_memory`/`write_memory`(照 profile 抄);`build_memory_block()` 加 `## 长期记忆` 段 |
| `app/agent/tools/memory.py` | 加 `UpdateMemoryArgs` + `update_memory` |
| `app/agent/tools/__init__.py` | 注册 `update_memory` + **改 update_profile 描述为收紧版** |
| `app/agent/loop.py` | SYSTEM_PROMPT 记忆路由从「三种」改「四种」+ profile 收紧措辞 |

gate.py 不改。

### 4.3 注入顺序(build_memory_block 固定次序,保 prompt cache)

```
## 关于用户        ← profile(收紧后:只个人信息)
## 长期记忆        ← MEMORY(新增:客观事实)
## 你的准则        ← soul
## 今天的日志(…)  ← log(易变,垫底)
## 昨天的日志(…)
```
> MEMORY 放 profile 之后、soul 之前——都是稳定内容排一起,日志垫底。

---

## 五、接口实现

### 5.1 `memory.py` 新增(照 profile 逐字同构)

```python
def _memory_path() -> Path:
    return Path(settings.data_dir) / "MEMORY.md"

def read_memory() -> str:
    """读 MEMORY.md。不存在 → 空串(不自举,同 profile)。errors=replace 防乱码。"""
    p = _memory_path()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")

def write_memory(content: str) -> None:
    """整份覆盖写 MEMORY.md(原子写)。"""
    atomic_json.write_text_atomic(_memory_path(), content)
    logger.info("已更新长期记忆(memory), len=%d", len(content))
```

### 5.2 `build_memory_block()` 加 MEMORY 段(profile 之后、soul 之前)

```python
def build_memory_block() -> str:
    try:
        profile = read_profile().strip()
        memory_ = read_memory().strip()          # 新增
        soul = read_soul().strip()
        logs = recent_logs()
    except Exception as e:  # noqa: BLE001
        logger.warning("读取记忆失败,本轮不注入: %s", type(e).__name__)
        return ""
    parts: list[str] = []
    if profile:
        parts.append(f"## 关于用户\n{profile}")
    if memory_:                                   # 新增
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

### 5.3 `update_memory` 工具(`tools/memory.py`)

```python
class UpdateMemoryArgs(BaseModel):
    content: str = Field(description=(
        "长期记忆的完整新内容(整份覆盖)。"
        "先基于 system 里已注入的现有长期记忆合并,再写回完整内容。"))

def update_memory(args: UpdateMemoryArgs) -> str:
    memory.write_memory(args.content)
    return "已更新长期记忆(memory)"
```

### 5.4 注册 + 收紧 update_profile(`tools/__init__.py`)

- 加 `update_memory` 注册(描述用 §2 措辞)。
- **改现有 `update_profile` 的 register 描述字符串**为 §2 收紧版。

---

## 六、待办登记(自动蒸馏)

**必须登记,不能让「显式触发」变成隐性技术债**:

1. **HANDOFF.md**:待办区加「记忆自动蒸馏(未做)」。
2. **`docs/research/2026-07-09-openclaw-memory-system.md` 演进路线**:MEMORY.md 标为完成;明确下一候选 =「定时任务自动从日志蒸馏进 MEMORY」(对标 OpenClaw dreaming sweep),说明动机(减少「用户忘了触发就永不沉淀」)。

---

## 七、错误处理

| 场景 | 处理 |
|---|---|
| MEMORY.md 不存在 | `read_memory` 空串。不报错。 |
| 写盘失败 | 抛异常 → `registry.run` 兜住 → 「工具执行失败」喂回。 |
| 乱码 | `read_text(errors="replace")`。 |
| `read_memory` 异常 | 在 `build_memory_block` 整体 try/except 内,退化成不注入。 |
| 空 content | 允许(等于清空),不特殊拦截。 |

---

## 八、测试

monkeypatch `settings.data_dir`。

1. **`test_memory.py`** 追加:
   - `read_memory` 不存在返回空串;
   - `write_memory` 覆盖写、读回一致;
   - `build_memory_block` 含 MEMORY:有内容 → 出现 `## 长期记忆`;无 → 不出现;
   - **注入顺序**:profile+memory+soul 都有时,断言 `## 关于用户` 位置 < `## 长期记忆` 位置 < `## 你的准则` 位置(用 `block.index(...)` 比较);
   - 前缀稳定性:同内容两次调用逐字节相同。
2. **`test_tools_memory.py`** 追加:
   - `update_memory` 经 `registry.run` 写盘生效、返回预期字符串;缺 content → 参数错误自愈;
   - `update_memory` 已注册;
   - 端到端接缝:`registry.run("update_memory", ...)` 后 `build_memory_block()` 能反映。
3. **`test_loop.py`** 追加:
   - SYSTEM_PROMPT 含 `update_memory`;含 profile 收紧关键词(如「个人信息」);
   - 现有 loop 测试全绿。

**gate 无需新增测试**。

---

## 九、验收标准

- 现有全部测试 + 新增测试全绿。
- 手动端到端:让 Agent 记一条客观事实(如「本项目测试用 uv run pytest」)→ Agent 调 `update_memory` → `data/MEMORY.md` 落盘 → 新开会话 Agent 记得。
- 边界验证:让 Agent 分别处理「我叫小明」(应进 profile)和「项目用 uv」(应进 MEMORY),观察是否归类正确。
- 显式提炼:说「把最近日志里重要的客观事实整理进长期记忆」→ Agent 读日志→提炼→调 update_memory。

---

## 十、不做(YAGNI)

- ❌ **自动蒸馏**(定时从日志提炼)—— 登记为待办,本期不做。
- ❌ 把 profile/soul/memory 三个整份覆盖函数抽象成通用函数(现在才 3 个,过早抽象;等 5-6 个再说)。
- ❌ MEMORY 的索引/检索/衰减(量小,全量注入够)。
- ❌ 前端 MEMORY 展示/编辑 UI。
- ❌ MEMORY 初始模板自举(同 profile,初始空)。
