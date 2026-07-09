# P5 记忆 / 个性化(profile + soul)设计

> 里程碑 P5(收官期)第一块:给 Agent 加**跨会话长期记忆**——本地 markdown 的 `profile.md`(用户画像)+ `soul.md`(Agent 准则),开会话时注入 system prompt,并提供 `update_profile` / `update_soul` 两个工具让 Agent 自己沉淀。
> 状态:设计已评审通过,待拆实现计划。
> 前置:P0-P4(竖切闭环 / 会话 / 工具+安全+审批 / RAG / 设置页+多根工作区)已完成。

---

## 〇、项目阶段约定(重要)

superstar 处于**项目初期、个人自用、无外部用户**。本设计**豁免**全局的 api-compatibility「只加不删」约束:涉及已有字段/接口/逻辑的**破坏性变更是允许的**,优先**最终代码精简**——不写迁移、回退、双字段并存等兼容分支。

---

## 一、背景与目标

P0-P4 打通了「对话 → 工具 → 安全审批 → RAG → 多根工作区」的闭环,但 Agent **没有跨会话记忆**:每开一个新会话都是「白纸」,记不住用户是谁、常用哪些项目、偏好怎样的行为方式。

P5 目标:引入一层**极简的本地长期记忆**,对标 Claude Code / OpenClaw 的 memory 机制,但用最小成本落地——两份本地 markdown 文件 + 两个工具 + 一处 system prompt 注入。

**两份记忆的定位区别**:

| 文件 | 语义 | 谁来沉淀 | 初始状态 |
| --- | --- | --- | --- |
| `profile.md` | **用户画像**:关于用户的稳定事实(身份、偏好、常用项目) | Agent 通过 `update_profile` 逐渐沉淀 | 空(文件不存在) |
| `soul.md` | **Agent 准则**:Agent 自身的行为方式基线 | Agent 通过 `update_soul` 调整;也可手改文件 | 首次读取时自举一份默认模板 |

---

## 二、关键设计决策(评审已定)

以下 4 项在评审中逐条确认,是本设计的地基:

1. **写入权限:两份都只靠 Agent 工具写**。不做前端编辑 UI。用户要改,直接编辑 `data/*.md` 文件或让 Agent 重写。两文件对称,实现最简。
2. **写入语义:全量覆盖(overwrite)**。工具参数只有一个 `content`,Agent 负责「先读注入在 system 里的旧记忆 → 合并 → 写回整份」。好处:记忆不会无限膨胀(Agent 可自行精简);坏处:依赖模型不弄丢旧信息——通过工具描述里的明确指引缓解。
3. **注入范围:全局唯一份**。`profile.md` / `soul.md` 存 `data/` 下,与 `config.json`、`sessions/` 平级。每个会话开场注入**同一份**,符合「跨会话长期记忆」定位。
4. **审批策略:自动放行**。`update_profile` / `update_soul` 不走审批弹窗——gate 命中默认 `auto`,**无需改 gate.py**。Agent 沉淀记忆无感、体验顺畅;落盘后用户随时可翻 `data/*.md` 检查。

---

## 三、架构与文件布局

### 3.1 存储(全部在 `.gitignore` 覆盖的 `data/` 下)

```
backend/data/
├── config.json      # 已有
├── sessions/        # 已有
├── kb/              # 已有
├── profile.md       # 新增:用户画像,初始不存在,由 Agent 逐渐沉淀
└── soul.md          # 新增:Agent 准则,首次读取时若不存在,写入默认模板
```

> `data/` 整体已被 `.gitignore` 忽略,两份 markdown 天然不入库,与「记忆是个人本地数据」一致。

### 3.2 代码单元(沿用 P0-P4 分层:service 做逻辑 / tools 做适配 / loop 做编排)

| 文件 | 角色 | 动作 |
| --- | --- | --- |
| `app/services/memory.py` | service 层:profile/soul 的读、写、注入拼接 | **新建** |
| `app/agent/tools/memory.py` | tools 层:`update_profile` / `update_soul` 适配 | **新建** |
| `app/agent/tools/__init__.py` | 注册两个新工具 | 改 |
| `app/agent/loop.py` | `SYSTEM_PROMPT` 拼接改为叠加 `memory.build_memory_block()`;并补一句工具说明 | 改 |
| `app/services/atomic_json.py` | 新增 `write_text_atomic(path, text)`(原子写纯文本) | 改 |

**gate.py 不改**——两个新工具走末尾默认 `auto`。

---

## 四、`memory.py` 接口

service 层暴露以下函数,职责单一、可独立测试:

```python
# app/services/memory.py

DEFAULT_SOUL = """\
# Agent 准则

- 用中文回答,说人话,不用翻译腔黑话。
- 动手改文件 / 跑命令前想清楚意图,危险操作先确认。
- 不确定就说不确定,别编造。
"""   # soul.md 首次不存在时写入的基线模板;profile 无默认(初始为空)

def _profile_path() -> Path:   # data_dir/profile.md,从 settings 现取(便于测试 monkeypatch)
def _soul_path() -> Path:      # data_dir/soul.md

def read_profile() -> str:
    """读 profile.md。不存在 → 返回空串 ""(不造模板)。read_text(errors="replace") 防乱码崩。"""

def read_soul() -> str:
    """读 soul.md。不存在 → 写入 DEFAULT_SOUL 并返回它(首次自举)。"""

def write_profile(content: str) -> None:
    """整份覆盖写 profile.md,原子写(atomic_json.write_text_atomic)。"""

def write_soul(content: str) -> None:
    """整份覆盖写 soul.md,原子写。"""

def build_memory_block() -> str:
    """把 profile + soul 拼成注入 system prompt 的一段文本;都为空 → 返回空串。
    内部整体 try/except:读记忆失败绝不让 agent 循环挂掉,退化成本轮不注入 + 记 warning。"""
```

**要点**:

- **原子写复用**:markdown 是纯文本,现有 `atomic_json.write_json_atomic` 只写 JSON。新增 `write_text_atomic(path, text)`(同样 `.tmp` → `rename`),让「原子写」能力集中在 `atomic_json.py` 一处。
- **soul 自举**:`read_soul()` 首次发现文件不存在,落一份 `DEFAULT_SOUL` 到磁盘并返回。用户第一次开会话即可在 `data/soul.md` 看到并手动编辑基线准则。profile **不自举**(初始空,纯靠 Agent 沉淀)。
- **不加内存缓存**:profile/soul 每次开会话读一次磁盘即可(非高频)。不做内存缓存,避免「改了磁盘不重启读到旧缓存」的一致性坑(参见交接文档 §7 config_store 的教训)。Agent 刚 `update_profile` 写完,下一轮 `build_memory_block()` 直接读盘就是最新的。
- **与 prompt caching 的关系**:「每轮读盘」不破坏 LLM provider 的 prompt cache——只要文件内容不变,拼出的字符串逐字节相同 → system 前缀不变 → 缓存照常命中。只有记忆**真被更新**时前缀才变、缓存才从变化点失效,这是应该失效且低频的。因此注入格式**必须稳定**(固定小标题、无时间戳 / 随机项)。

---

## 五、工具与注入

### 5.1 两个工具(`app/agent/tools/memory.py`,照 `workspace.py` 的样子)

```python
class UpdateProfileArgs(BaseModel):
    content: str = Field(description="用户画像的完整新内容(整份覆盖,不是追加)。"
                                     "先基于已注入在 system 里的现有画像,合并后写回完整内容。")

def update_profile(args: UpdateProfileArgs) -> str:
    memory.write_profile(args.content)
    return "已更新用户画像(profile)"

class UpdateSoulArgs(BaseModel):
    content: str = Field(description="Agent 准则的完整新内容(整份覆盖,不是追加)。"
                                     "先基于已注入在 system 里的现有准则,合并后写回完整内容。")

def update_soul(args: UpdateSoulArgs) -> str:
    memory.write_soul(args.content)
    return "已更新 Agent 准则(soul)"
```

**注册**(`tools/__init__.py` 末尾追加),描述里明确「整份覆盖、先读再写」语义。旧内容已在 system prompt 里注入,Agent 直接能看到,无需额外读工具。

### 5.2 gate 处置

两个工具**不在 gate.py 的特判里** → 命中末尾 `return "auto"` → **自动放行**。无需改 gate.py。

### 5.3 注入(`loop.py` 改动)

```python
# loop.py 循环内,SYSTEM_PROMPT 常量保持,拼接改成:
memory_block = memory.build_memory_block()   # 每轮读盘,内容不变则前缀稳定
system_content = SYSTEM_PROMPT + memory_block
messages = [{"role": "system", "content": system_content}, *history]
```

`build_memory_block()` 产出(内容非空才拼对应段,格式固定):

```
（profile 和 soul 都空时返回空串，system prompt 与现状完全一致）

## 关于用户
<profile.md 内容>

## 你的准则
<soul.md 内容>
```

### 5.4 让 Agent 知道自己有记忆能力

在 `SYSTEM_PROMPT` 常量里补一句工具说明(与现有工具介绍并列):

> "你有长期记忆:`update_profile`(沉淀关于用户的画像)、`update_soul`(调整你自己的行为准则)。发现关于用户的稳定事实(偏好、身份、常用项目)时,主动用 update_profile 记下来;整份覆盖,先基于上面已注入的记忆合并再写回。"

---

## 六、错误处理

沿用项目「工具永不抛、错误变返回值喂回模型」的心法:

| 场景 | 处理 |
| --- | --- |
| profile.md / soul.md 不存在 | `read_profile` → 空串;`read_soul` → 写默认模板并返回。不报错。 |
| 写盘失败(磁盘满 / 权限) | `write_profile/soul` 抛异常 → 被 `registry.run` 的 `except Exception` 兜住 → 返回「工具执行失败:…」喂回模型。不崩流。 |
| 文件读到乱码 | `read_text(errors="replace")`,不崩。 |
| `build_memory_block()` 读盘异常 | **必须兜住**——它在每轮循环关键路径上。内部 try/except,异常时返回空串 + 记 warning,退化成本轮不注入记忆,对话照常。 |
| content 为空串 | 允许(等于清空该记忆),不特殊拦截。 |

---

## 七、测试

照 `tests/` 现有风格(pytest + monkeypatch `data_dir` 到临时目录):

1. **`test_memory.py`** —— service 层:
   - `read_profile` 文件不存在返回空串;
   - `read_soul` 首次自举写入默认模板、二次读到已存在内容;
   - `write_profile/soul` 覆盖写、再读回一致;
   - `build_memory_block`:双空→空串、只有 profile、只有 soul、两者都有 四种拼接;
   - `write_text_atomic` 覆盖写正确、不留 `.tmp`。
2. **`test_tools_memory.py`** —— 工具层:
   - `update_profile` / `update_soul` 经 `registry.run` 调用,写盘生效、返回预期字符串;
   - 参数缺 `content` → 走 registry 的 Pydantic 自愈,返回「参数错误」不抛。
3. **loop 注入验证**(补进现有 loop 测试或新增):
   - profile/soul 有内容时,`build_memory_block` 结果出现在喂模型的 system 里;
   - 都空时 system 等于原 `SYSTEM_PROMPT`(prompt 前缀稳定性回归)。

**gate 无需新增测试**(未改 gate.py)。

---

## 八、验收标准

- 现有全部测试(133 个)+ 新增测试全绿。
- 手动端到端闭环:开会话说「我叫 X、常用 Y 项目」→ Agent 调 `update_profile` → `data/profile.md` 落盘 → **新开会话** Agent 记得。
- `data/soul.md` 首次开会话后存在且为默认模板,手动编辑后新会话生效。

---

## 九、不做(YAGNI)

- ❌ 不做前端记忆编辑 UI(右栏 ContextPanel「Agent 记得你」那块本期**不接**,留后续)。
- ❌ 不做记忆的向量化 / RAG 化检索(记忆是短文本,全量注入即可)。
- ❌ 不做按会话隔离的记忆。
- ❌ 不做记忆版本历史 / 回滚(markdown 文件本身可被用户手动备份)。
