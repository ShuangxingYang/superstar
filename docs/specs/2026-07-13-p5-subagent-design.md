# P5 子 Agent 隔离 设计文档

- 日期:2026-07-13
- 状态:设计已确认,待实现
- 关联:`DEVELOPMENT_PLAN.md`(P5 第一版收尾)、`HANDOFF.md` 第 5 节待办 2

---

## 1. 目标

给 superstar 增加「子 Agent 隔离」能力:主 Agent 能派发一个**拥有独立上下文**的子 Agent 去完成一个具体子任务,子 Agent 跑完**只把最终结论回传**主 Agent。用途——把「要翻很多文件 / 大量检索 / 批量改动」的中间过程隔离到子上下文,避免这些噪音塞满主对话。

同一批次顺带调整审批策略:`write_file` 从「需审批」改为「允许目录内自动放行」(**硬改**,去掉 write 审批弹窗)。它一举两得:解除 agent 自主流程被频繁打断的痛点,并同时解开「子 Agent 在同步调用内无法暂停等待审批」的死结。

---

## 2. 背景与动机

### 2.1 审批机制:同步 CLI vs 异步 Web

Claude Code 是本地 CLI,审批是**进程内同步阻塞**——子 Agent 跑到需权限的工具就地卡住、把提示冒泡到终端、用户按 y/n 后就地续跑。任意调用深度都能自然暂停。

superstar 是 **Web**,审批是**跨 HTTP 请求的异步暂停-恢复**:一次 `/chat/stream` 跑到需审批的工具 → yield `approval_required` → SSE 流结束、状态落 `pending` sidecar → 用户前端点批准 → 另一条 `/resume` 请求从存档接着跑。像 serverless,无常驻连接。

子 Agent 若跑在主 Agent 的**一次同步 `registry.run` 内部**,它没有自己的「回合边界」把暂停点冒泡回前端——想暂停就得把整个深层调用栈状态序列化再重建,等于把 pending/resume 递归化,复杂度极高。

### 2.2 对标:OpenClaw 的处理

OpenClaw 子 Agent 技术上继承父的全部工具,靠 spawn 时 task 描述里的**软约束**(「危险操作别执行、列出来返回」)兜底;工程最佳实践是「**子 Agent 只准备、父 Agent 执行、审批永远在主会话发生**」,且不能派发孙 agent。

### 2.3 本设计的取舍

原本计划用「读做分离 + 只读硬白名单」规避审批死结(比 OpenClaw 的软约束更硬:从工具层就掐断,而非靠 prompt 自觉)。但用户提出「子 Agent 也要能 write、且 write 一律免审批」。这个诉求恰好**移除了死结的根因**:一旦 `write_file` 变成 `auto`,子 Agent 遇到它就同步执行、根本不进入暂停路径,于是把 `write_file` 纳入子 Agent 白名单即可,无需任何递归审批机制。

---

## 3. 关键设计决策

| # | 决策 | 取值 | 理由 / 放弃的替代 |
|---|---|---|---|
| D1 | 子 Agent 能力边界 | 能读、搜、**写**;不能跑命令、不能扩权、不能派孙 Agent | write 已改 auto,子 Agent 写无暂停问题;run_command 灰名单仍审批,子 Agent 处理不了暂停,故不给 |
| D2 | 审批策略 | `write_file` 允许目录内 → `auto`;越界仍 `deny`。**硬改,不加开关** | 满足「顺滑不打断」;沙箱(允许目录白名单)保留,防写系统任意文件 |
| D3 | 命令审批 | `run_command`(白/黑/灰)、`add_workspace`(扩权)**不变** | 用户只要求放行 write;命令/扩权风险更高,保留人在环路 |
| D4 | 可观测性 | **黑盒**:父只显示 `dispatch_subagent` 工具卡片 + 最终结论 | 零前端改动、隔离最纯;子 Agent 内部经后端 logger 可查,调试够用。放弃「折叠展示内部过程」(要改前端 event 协议 + 嵌套 UI) |
| D5 | 子 Agent 落盘 | **纯内存,跑完即焚** | 最简、隔离最纯;放弃「独立落盘回放」(YAGNI) |
| D6 | 子 Agent 循环实现 | 新增独立 `subagent.py`,主循环一行不改 | 主循环职责单一(管流式/审批/落盘);放弃「魔改主循环加模式开关」「借临时 session」 |
| D7 | 子 Agent LLM 调用 | **非流式**(`stream=False`) | 不往前端透传,流式无收益;非流式直接拿完整 `msg.tool_calls`,`subagent.py` 自足、不耦合主循环私有 helper |
| D8 | 递归防护 | `dispatch_subagent` 不在子 Agent 白名单 | 子 Agent 的工具 schema 里根本没有它 → 天然不派孙 Agent(对齐 OpenClaw),无需计数器 |
| D9 | 入参 | 仅一个 `task: str` | YAGNI,不搞可选上下文/工具指定 |
| D10 | 步数上限 | 复用 `config.agent.max_iters` | 不单开配置 |
| D11 | 子 Agent system prompt | 独立、精简,**不注入长期记忆** | 临时调研工,注入 profile/soul/memory/log 是噪音、膨胀 prompt、破坏隔离 |

---

## 4. 架构与文件结构

```
backend/app/agent/
├── subagent.py                 # 【新增】子 Agent 引擎:run_subagent(task) -> str
│                               #   + SUBAGENT_SYSTEM_PROMPT + SUBAGENT_TOOLS + _safe_json
├── tools/
│   ├── subagent.py             # 【新增】工具外壳:DispatchSubagentArgs + dispatch_subagent(args)
│   └── __init__.py             # 【改】① 注册 dispatch_subagent ② to_openai_schema 加子集过滤
└── gate.py                     # 【改】write_file → auto(越界仍 deny),删 difflib import
```

分层沿用现有惯例:`tools/subagent.py` 是**薄外壳**(Pydantic 入参 + 调引擎,像 `tools/memory.py`),`agent/subagent.py` 是**引擎**(真正的循环)。外壳被 registry 登记,引擎被外壳调用。

---

## 5. 详细设计

### 5.1 审批策略调整(`gate.py`)

```python
if name == "write_file":
    try:
        security.safe_path(args["path"])      # 沙箱保留:只做越界校验
    except (SecurityError, KeyError):
        return "deny", None                   # 越界(写系统目录等)仍拒
    return "auto", None                       # 允许目录内 → 自动放行,不再 approve
```

- `run_command`、`add_workspace` 分支**不变**。
- `difflib` 在 `gate.py` 里只被 write 的 diff 预览用到,该分支去掉后,删掉 `import difflib`。
- **副作用**:write 不再弹审批窗、不再有 diff 预览,变成普通工具卡片(前端照常显示 `tool_call`/`tool_result`,**前端代码无需改动**——auto 工具走的是既有通道)。

### 5.2 工具接口 `dispatch_subagent`(`tools/subagent.py`)

```python
from pydantic import BaseModel, Field


class DispatchSubagentArgs(BaseModel):
    task: str = Field(description="交给子 Agent 的子任务描述。子 Agent 看不到当前对话,"
                                  "所以要把背景、目标、要它产出什么都交代清楚、自足。")


def dispatch_subagent(args: DispatchSubagentArgs) -> str:
    from app.agent.subagent import run_subagent   # 函数内延迟 import,避开包加载期循环
    return run_subagent(args.task)
```

注册时的描述文案(给父模型看,决定它「何时」派子 Agent):

> 派发一个**子 Agent** 去独立完成一个子任务(搜代码、读文件、查知识库、写文件)。子 Agent 有独立上下文,只把最终结论返回给你——适合「要翻很多文件 / 大量检索 / 批量改动」的活,避免这些中间过程塞满当前对话。子 Agent 能读能写,但**不能跑命令、不能改目录权限**;需要跑命令时,它会把建议写进结论,你再自己执行。传入 `task`:自足的子任务描述(子 Agent 看不到当前对话)。

### 5.3 `ToolRegistry.to_openai_schema` 子集过滤(`tools/__init__.py`)

```python
def to_openai_schema(self, names: set[str] | None = None) -> list[dict]:
    tools = self._tools.values() if names is None else [
        self._tools[n] for n in names if n in self._tools
    ]
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.args_model.model_json_schema(),
            },
        }
        for t in tools
    ]
```

主循环照旧 `to_openai_schema()`(全量);子 Agent 循环调 `to_openai_schema(SUBAGENT_TOOLS)`(子集)。`registry.run` **完全不动**——子集限制由子 Agent 循环自己把关(见 5.4)。

### 5.4 子 Agent 引擎 `run_subagent`(`agent/subagent.py`)

```python
import json
import logging

from app.agent.tools import registry
from app.services import config_store, llm

logger = logging.getLogger(__name__)

SUBAGENT_TOOLS = {"read_file", "grep", "glob", "search_kb", "write_file"}

SUBAGENT_SYSTEM_PROMPT = (
    "你是一个子 Agent,被主 Agent 派来独立完成一个具体的子任务。"
    "你能读取、检索和写文件(read_file/grep/glob/search_kb/write_file),"
    "但不能跑命令,也不能改变可访问目录。"
    "专注完成任务,完成后用一段清晰、自足的文字把结论/所做改动汇总返回——"
    "这段汇总会直接交回主 Agent,它看不到你的中间过程,所以要说清你查到了什么、改了哪些文件,"
    "带上关键路径/行号/来源。"
    "如果任务需要跑命令,你没有这个工具,不要尝试;把「建议执行什么命令」写进结论,交给主 Agent。"
)


def _safe_json(raw: str | None) -> dict:
    """解析 tool_call arguments;非法/空 → {}(交给 registry.run 的 Pydantic 校验自愈)。"""
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def run_subagent(task: str) -> str:
    """独立上下文跑一个子任务,同步跑到底,返回一段给主 Agent 的结论字符串。绝不向上抛。"""
    try:
        client, model = llm.get_llm_client()
        max_iters = config_store.get()["agent"]["max_iters"]     # 复用父的步数上限
        schema = registry.to_openai_schema(SUBAGENT_TOOLS)       # 只给读写子集
        messages = [
            {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
            {"role": "user", "content": task},                   # 独立上下文:只有 task,看不到父会话
        ]
        logger.info("子 Agent 开始: task_len=%d, max_iters=%d", len(task), max_iters)
        for i in range(max_iters):
            resp = client.chat.completions.create(model=model, messages=messages, tools=schema)
            msg = resp.choices[0].message
            if not msg.tool_calls:                               # 不调工具了 → 这就是结论
                result = (msg.content or "").strip()
                logger.info("子 Agent 完成: 迭代=%d, 结论长=%d", i + 1, len(result))
                return result or "(子 Agent 未产出结论)"
            messages.append(msg.model_dump(exclude_none=True))   # 完整回灌 assistant(含 tool_calls)
            for tc in msg.tool_calls:
                name = tc.function.name
                if name in SUBAGENT_TOOLS:
                    out = registry.run(name, _safe_json(tc.function.arguments))
                else:                                            # 双保险:幻觉调越权工具 → 喂回自愈
                    out = f"错误:子 Agent 只能用工具 {sorted(SUBAGENT_TOOLS)},不能调用 {name}"
                    logger.warning("子 Agent 越权工具: name=%s", name)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})
        logger.info("子 Agent 达到 max_iters: task_len=%d", len(task))
        return "(子 Agent 达到最大步数仍未得出结论,收集的信息可能不完整)"
    except Exception as e:  # noqa: BLE001 - 子 Agent 任何失败都收敛成给父的字符串,父循环永不崩
        logger.warning("子 Agent 执行失败: err=%s", type(e).__name__)
        return f"(子 Agent 执行失败:{e})"
```

### 5.5 白名单与递归防护

- `SUBAGENT_TOOLS = {read_file, grep, glob, search_kb, write_file}`。
- **不含** `run_command`(灰名单要审批,子 Agent 无暂停能力)、`add_workspace`(扩权)、`dispatch_subagent`(防递归)。
- 递归防护是**结构性**的:`dispatch_subagent` 不在白名单 → 子 Agent 的工具 schema 里没有它 → 模型看不到、不会派孙 Agent。即使模型幻觉硬造该调用,5.4 的 `else` 分支也会拦下并喂回错误。双保险,无需计数器。
- **沙箱兜底**(重要):子 Agent 循环直接调 `registry.run`、**不经过 gate**(gate 只服务主循环的审批判定)。子 Agent 写越界文件由 `write_file` **函数内部**的 `safe_path`(`fs.py:40`)拦截——越界抛 `SecurityError`,被 `registry.run` 兜成「安全拦截」串喂回子 Agent;`read_file`/`grep`/`glob` 同理各自在工具内 `safe_path`。因此沙箱(允许目录白名单)对子 Agent **依然有效**,与是否过 gate 无关。gate 里对 write 的 `safe_path` 只是「免进审批流」的冗余前置,执行期真正的防线在工具函数内部。

### 5.6 防循环 import

加载期链路:`tools/__init__.py` 定义 `registry` 后,在末尾 `from app.agent.tools.subagent import ...` 并注册。`tools/subagent.py` 顶部只 import pydantic;它对 `agent/subagent.py` 的 import 放在 `dispatch_subagent` **函数体内**(延迟)。于是加载 `tools` 包时不会触发 `agent/subagent.py` 加载,无循环。运行期首次调用 `dispatch_subagent` 才 import 引擎,此时 `tools` 包(含 `registry`)已就绪。

---

## 6. 数据流

```
主 Agent 循环(run_agent_streaming)
  └─ 模型产出 tool_call: dispatch_subagent(task="...")
       └─ gate → auto(dispatch_subagent 无特判,落默认 auto)
       └─ registry.run("dispatch_subagent", {task})
            └─ dispatch_subagent(args) → run_subagent(task)
                 ├─ 独立 messages = [system(子Agent), user(task)]   # 隔离:看不到父会话
                 ├─ 循环:非流式请求 → 若有 tool_call 且在白名单 → registry.run 执行 → 喂回
                 └─ 模型不再调工具 → 返回结论字符串
       └─ 结论作为 role:tool 结果落盘 + yield tool_result 给前端
  └─ 主 Agent 带着这段结论继续对话
```

主 Agent 只看到 `dispatch_subagent` 的 `tool_call`(带 task)和最终 `tool_result`(结论);子 Agent 翻了多少文件、调了多少次工具,全在子上下文内、不进主会话。

---

## 7. 错误处理

核心原则:子 Agent 的任何失败都收敛成「给父的一段字符串」,父循环永不崩。

| 失败点 | 处理 |
|---|---|
| 子 Agent 内部工具执行失败 | `registry.run` 已兜底返回错误串,子 Agent 喂回自愈(现成机制) |
| 子 Agent 越权调工具(run_command 等) | 5.4 `else` 分支返回拒绝串喂回,子 Agent 自愈 |
| 子 Agent 内部 LLM 调用抛异常(网络/超时/配置) | `run_subagent` 整体 try/except → 返回 `(子 Agent 执行失败:…)` |
| 双保险 | 万一 `run_subagent` 漏兜抛出,执行 `dispatch_subagent` 的外层 `registry.run` 再 catch → 父绝不挂 |
| 达到 max_iters | 返回明确超限提示串 |
| tool_call args 非法 JSON | `_safe_json` 返回 `{}`,交 `registry.run` 的 Pydantic 校验自愈 |

---

## 8. 测试计划

pytest + mock LLM(monkeypatch `llm.get_llm_client` 返回假 client,按脚本吐 tool_call / 文本):

1. **正常闭环**:mock「先返回 read_file 调用 → 再返回纯文本结论」→ 断言拿到结论、中途调了工具。
2. **能 write**:mock 返回 write_file 调用 → 断言 write 被执行(auto,无暂停)、结论正常返回。
3. **越权拦截**:mock 返回 run_command / dispatch_subagent 调用 → 断言返回拒绝串、未执行、随后能自愈继续。
4. **递归防护**:断言 `dispatch_subagent not in SUBAGENT_TOOLS`;断言 `to_openai_schema(SUBAGENT_TOOLS)` 结果里没有 dispatch_subagent。
5. **max_iters 超限**:mock 一直返回 tool_call → 断言到上限返回超限提示串。
6. **LLM 抛异常**:mock client 抛错 → 断言 `run_subagent` 返回失败串而非抛出。
7. **子集过滤**:`to_openai_schema({"read_file"})` 只返回 read_file 的 schema。
8. **gate 改动**:`write_file` 越界 → `deny`;允许目录内 → `auto`(**更新原有 gate write_file 测试**,原来断言的是 approve+diff)。
9. **工具外壳**:`dispatch_subagent(args)` 正确把 `task` 透传给 `run_subagent`(monkeypatch `run_subagent` 验证入参)。
10. **dispatch gate**:`dispatch_subagent` 经 `gate_tool_call` → `("auto", None)`。
11. **子 Agent 沙箱**:子 Agent 对越界路径调 write_file(mock 越界 path)→ 断言返回「安全拦截」串、未真正写盘(验证不过 gate 也被工具内 `safe_path` 拦)。

---

## 9. 影响面 / 需同步更新

- **原有 gate 测试**:write_file 行为从 approve+diff 变 auto,相关断言需更新(见测试 8)。
- **前端**:write 不再弹审批窗 + diff 预览;前端代码无需改动,但**行为变化需知晓**(write 变普通工具卡片)。
- **`HANDOFF.md`**:工具数 11 → 12;P2b「写 + 审批」说明更新为「write 已改自动放行(2026-07-13)」;P5 第 5 节待办 2(子 Agent 隔离)标记完成。
- **人在环路特性**:`write_file` 不再触发审批;`run_command` 灰名单、`add_workspace` 扩权**仍保留**审批(该展示点未完全丢失)。

---

## 10. 明确不做(YAGNI)

- 递归异步审批(让子 Agent 直接跑需审批的操作,如灰名单命令)。
- 子 Agent 过程流式透传前端 / 折叠嵌套展示。
- 子 Agent 独立落盘回放。
- 审批配置开关(用户明确选了硬改)。
- `run_command` 纳入子 Agent 白名单。
- 子 Agent 注入长期记忆(profile/soul/memory/log)。
