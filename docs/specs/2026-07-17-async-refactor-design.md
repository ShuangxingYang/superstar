# Superstar async 化改造说明(业界标准 · 做减法)

- 日期:2026-07-17
- 状态:改造设计,待用户审阅
- 性质:**方向升级 + 大重构**。把后端流式链路从 sync 改成 async,为「中断生成」和「未来飞书并发」打地基。

---

## 0. 原则升级(替换 HANDOFF 第 1 节旧原则)

**旧原则**:最小成本覆盖知识点、不追求生产级、能省则省。

**新原则**:**以业界标准做法为基线,在其上做减法**——先按「专业项目该怎么做」立骨架,再砍掉本项目(单用户本地 + 未来轻量飞书并发)确实不需要的分支。**不是**「先搭玩具、缺什么补什么」。目标:代码经得起「这是不是专业实现」的审视,不是 toy 级。

> 落地到本次:async 流式链路是业界标准骨架(必做);"存储/工具不改 async、用 to_thread 复用"是合理减法(不是偷懒,是对"单用户本地 async 零收益"的正确判断)。

---

## 1. 目标与非目标

### 目标
1. 后端**流式链路 async 化**:chat/resume 路由 + `run_agent_streaming` + `_accumulate` + LLM client(`AsyncOpenAI`)。
2. 引入**取消令牌**(`asyncio` cancellation / `AbortSignal` 的 Python 对应),传到**两处**:回合边界、LLM 流式读取。
3. 为下一步「中断生成」功能铺好地基(本说明只做重构,**中断功能是第二步、单独做**)。
4. **行为完全不变**:重构后所有现有测试继续绿,对话/工具/审批/子 Agent/蒸馏功能一律无回归。

### 非目标(明确不做 —— 这就是"减法")
- **不改** session_store / config_store / atomic_json / rag_store 为 async I/O(理由见 §4)。
- **不改**同步工具实现(fs/search/shell/rag/workspace/memory)为 async——经 `registry.run_async` 自动走 to_thread(理由见 §4)。**例外**:子 Agent 引擎 `run_subagent` 与 dispatch 工具外壳**改 async**(用户决策:子 Agent 全 async,§3.6)。
- **不做**「强杀正在执行的工具/命令」——中断只在回合边界 + LLM 流读取生效(理由见 §5)。
- **不做**中断功能本身(第二步)。
- **不引入** aiofiles、async 数据库等(单用户本地零收益)。

---

## 2. 原理与取舍(为什么这么改)

### 2.1 为什么流式链路必须 async
async 的收益是「等 I/O 时让出 CPU 干别的」。流式链路的两个场景都吃这个收益:
- **中断**:async 里 `task.cancel()` 能让挂在 `await` 上的协程抛 `CancelledError`,当场停——这是「JS AbortSignal 免费」的 Python 对应。同步阻塞调用做不到(线程霸占着死等,无法自省"该取消了")。
- **飞书并发**:多个会话同时等各自 LLM 流式响应时,async 让它们共享一个线程、互不阻塞。这是"轻量并发"的正解。

### 2.2 为什么存储/工具不改 async(减法的依据)
- **存储**:读写本地小文件(几 KB),SSD 上是微秒级。async 化的调度开销比 I/O 本身还大 → **负优化**。且已工作良好(原子写/锁/索引全测过)。
- **工具**:多为秒级操作。为「中途打断一个 3 秒的 grep」把取消令牌灌进每个工具函数 → **投入产出比极低**。
- **标准姿势**:async 世界里调稳定的同步库,用 `await asyncio.to_thread(同步函数, ...)` 扔线程池——同步代码**一行不改**,事件循环不阻塞。这是业界处理「async 壳 + 同步核」的标准做法,不是妥协。

### 2.3 JS signal 免费 vs Python 手动注入
- JS:I/O 出厂即非阻塞 + 生态统一接受 signal → 传个参数即可,底层中止逻辑别人写好了。
- Python 同步:阻塞调用霸占线程、无法内部响应取消 → 取消只能发生在**操作之间的缝隙**(回合边界手动检查)。
- 我们的策略:**LLM 流读取买 async**(取消在这层变"免费",当场停吐字);**工具/存储保持同步 + 回合边界手动检查取消**(接受 Python 同步的固有限制)。

---

## 3. 受影响文件与改法

### 3.1 `app/services/llm.py` —— 提供 async + sync 两个 client 工厂
- **`get_llm_client()`** → 返回 `(AsyncOpenAI, model)`:主对话链路 + **子 Agent** 用(可中断、可并发)。
- **`get_sync_llm_client()`** → 返回 `(OpenAI, model)`:**蒸馏 + 测试连接**用(后台/非交互,不需中断,同步最简)。
- 两者各自按 (base_url, api_key) 缓存实例,互不干扰。缓存逻辑与现状一致。
- `AsyncOpenAI` 在事件循环里创建/使用;按需创建模式兼容。

### 3.2 `app/agent/loop.py` —— 主循环 async 化(核心)
- `run_agent_streaming(sid)`:同步 generator → **async generator**(`async def` + `yield`)。
- `_accumulate(stream)`:`for chunk in stream` → `async for chunk in stream`;它也变 async generator。
- LLM 调用:`client.chat.completions.create(...)` → `await client.chat.completions.create(...)`(async client 返回的 stream 用 `async for` 消费)。
- **工具执行改 `registry.run_async`**:`await registry.run_async(name, args)`(见 §3.6)——async 工具(dispatch_subagent/dispatch_subagents)直接 `await`,同步工具内部走 `to_thread`。**替代**原先「`await asyncio.to_thread(registry.run,...)`」的写法。
- **其余同步调用包 to_thread**:循环里调的 `session_store.read_messages/append_message`、`memory.build_memory_block`、`config_store.get` 等,`await asyncio.to_thread(原函数, 参数...)`。
- `resume_streaming`、`reject_all_pending` 同样 async 化。
- **取消令牌**:见 §5(本次重构**预留接口**,实际中断逻辑第二步做)。

### 3.3 `app/api/routes/chat.py` —— 路由 async 化
- `chat_stream` / `chat_resume`:`def` → `async def`;`event_stream()` 内部 generator → async generator。
- `StreamingResponse` 接受 async generator,原生支持。
- `for event in loop.run_agent_streaming(sid)` → `async for event in loop.run_agent_streaming(sid)`。
- 路由里调的同步 `session_store.create/append_message`、`pending_store.read` 等 → `await asyncio.to_thread(...)`。

### 3.4 存储/config/rag/工具 —— **不改**,由调用方 to_thread 包
- `session_store.py`、`config_store.py`、`atomic_json.py`、`rag_store.py`、`pending.py`、`memory.py` 及各工具:**源码零改动**。
- 谁在 async 上下文里调它们,谁负责 `asyncio.to_thread` 包一层。

### 3.5 `app/api/routes/settings.py` / `session.py` / `kb.py` —— 评估是否需要 async
- 这些是**非流式**普通 CRUD 路由。FastAPI 对同步路由本就自动丢线程池,**可以保持同步不改**(减法)。
- 例外:`settings/test`(测试连接)内部触发 LLM 调用——它用**独立同步 client**(见 §3.6),路由保持同步即可。

### 3.6 子 Agent / 蒸馏 —— 子 Agent 全 async;蒸馏/测试连接同步
子 Agent 定为**全 async**(用户决策 2026-07-17):贴业界标准,让子 Agent 的 LLM 调用可中断、并行时可整体取消。蒸馏与测试连接不在交互链路、不需要中断,保持同步。

**子 Agent(`app/agent/subagent.py`)全 async:**
- `run_subagent(task, cancel_event=None)`:`def` → **`async def`**;内部 `create()` → `await client.chat.completions.create(...)`(async client),流式则 `async for`。
- 用**主链路同款 async client**(`get_llm_client()` 返回的 `AsyncOpenAI`)。
- 子 Agent 内部**调工具**仍走同步 `registry.run`,用 `await asyncio.to_thread(registry.run, ...)` 包(工具不改 async 的减法不变)。
- **并行工具 `dispatch_subagents`**:目前用 `ThreadPoolExecutor` 跑同步 `run_subagent`。改为 **`asyncio.gather(*[run_subagent(t) for t in tasks])`** —— async 原生并发,取消时 `gather` 可整体取消所有子 Agent。保序由 `gather` 保证(返回顺序 = 传入顺序)。
- **调用链连带 async 化**:`dispatch_subagent` / `dispatch_subagents` 工具外壳 → `async def`;但它们由 `registry.run`(同步)调用……**这产生一个真实的结构问题,见下。**

**结构问题:工具执行链是同步的(`registry.run`),但子 Agent 现在是 async 的。**
- 现状:主循环 `await asyncio.to_thread(registry.run, name, args)` 把工具丢线程池。`registry.run` 同步调工具函数。若 `dispatch_subagent` 工具函数内要 `await run_subagent(...)`,它就得是 async,但它跑在 `to_thread` 的**子线程**里——子线程里 `await` 需要自己的事件循环。
- **解法(标准做法)**:`registry` 增加对 **async 工具**的支持——`Tool` 记录 func 是否 async;`registry` 提供 `async def run_async(name, args)`:async 工具直接 `await func(args)`,同步工具 `await asyncio.to_thread(func, args)`。主循环对工具统一 `await registry.run_async(...)`。
  - 这样:`dispatch_subagent`/`dispatch_subagents` 注册为 **async 工具**,主循环 `await registry.run_async("dispatch_subagents", ...)` → 直接在主事件循环里 `await run_subagent`,无子线程套娃。
  - 其余同步工具(fs/search/shell/rag/workspace/memory)经 `run_async` 自动走 `to_thread`,**源码零改动**。
- **影响面**:`registry`(`ToolRegistry`)要加 `run_async` + async 工具标记;主循环工具执行从 `to_thread(registry.run,...)` 改为 `await registry.run_async(...)`。这是本次比"子 Agent 同步"方案多出的核心改动。

**蒸馏(`app/services/distill.py`)/ 测试连接:保持同步 + 独立同步 client。**
- `distill_memory()` 仍同步,用 `llm.get_sync_llm_client()` 拿一个同步 `OpenAI` client。它由 APScheduler 后台线程 / 手动接口调用,不需要中断,同步最简。
- `settings/test` 同理用同步 client。
- `llm.py` 因此提供**两个** client 工厂:`get_llm_client()`→`AsyncOpenAI`(主链路+子 Agent),`get_sync_llm_client()`→`OpenAI`(蒸馏+测试连接)。二者各自按 base_url+api_key 缓存。

---

## 4. `asyncio.to_thread` 包装点清单(明确哪些同步调用被包)

| 调用点 | 位置 | 包装 |
|---|---|---|
| `session_store.read_messages/append_message` | loop.py / chat 路由 | `await asyncio.to_thread(...)` |
| `session_store.create` | chat 路由 | `await asyncio.to_thread(...)` |
| `registry.run(name, args)` | loop.py(工具执行) | **改用 `await registry.run_async(...)`**(见 §3.6):async 工具直 await、同步工具内部 to_thread |
| `memory.build_memory_block` | loop.py | `await asyncio.to_thread(...)` |
| `config_store.get` | loop.py | `await asyncio.to_thread(...)` |
| `pending_store.read/write/clear` | loop.py / chat 路由 | `await asyncio.to_thread(...)` |
| `gate_tool_call` | loop.py | 纯计算,快,可不包(直接调) |

> 原则:**磁盘 I/O / 可能阻塞的同步调用**才包;纯内存计算(gate 判定、字符串处理)直接调,别为微秒操作徒增线程调度。

---

## 5. 取消令牌:本次只预留,不实现中断逻辑

**本次重构是"换底子",中断功能是第二步。** 但为让第二步顺滑,本次**预留接口**:

- `run_agent_streaming(sid, cancel_event: asyncio.Event | None = None)` 加一个可选取消参数(默认 None = 现有行为)。
- **两个检查点**(本次只留 TODO 注释 + 参数,不写实际中断逻辑):
  - **回合边界**:`for _ in range(max_iters)` 每轮开头,`if cancel_event and cancel_event.is_set(): 干净退出`。
  - **LLM 流读取**:`async for chunk in stream` 循环里,响应取消(async 里 `task.cancel()` 会让 `async for` 抛 `CancelledError`,或检查 cancel_event)。
- **设计约束(写进代码注释 + HANDOFF)**:中断只在这两处生效;**正在执行的工具/命令不强杀**,依赖操作原子性(write 用原子写;run_command 外部脚本等它跑完)保证"被打断 = 安全状态"。

> 为什么现在不实现中断:重构 + 加功能揉一起,出问题难定位(你已同意分两步)。本次目标是"async 化后行为不变、测试全绿"。

---

## 6. 测试策略调整

- 引入 **`pytest-asyncio`**(`uv add --dev pytest-asyncio`),`pytest.ini` 配 `asyncio_mode = auto`。
- **现有流式测试**(test_loop.py / test_chat_routes.py)的 mock LLM 要改:
  - 同步 mock 的 `create()` 返回同步迭代器 → 改成 **async**:`create()` 变 `async def`,返回 async 迭代器(`async def __aiter__` 或 async generator)。
  - 消费改 `async for`。
  - 测试函数加 `async def` + `await`。
- **非流式测试**(test_memory / test_config_store / test_gate / test_subagent / test_distill 等)**基本不动**——它们测的是同步代码,子 Agent/蒸馏仍同步。
- **验收铁律**:重构后 `uv run pytest -q` **全绿**,数量不减(除 mock 结构变化外,断言逻辑不变)。行为无回归 = 重构成功的唯一标准。

---

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| async generator + `yield from` 不兼容 | `yield from` 在 async gen 里不可用,改 `async for ... yield`。逐个改,测试兜底 |
| async client 被同步代码误调 | §3.6 明确隔离:主链路 async client、子 Agent/蒸馏同步 client |
| to_thread 包漏了某个阻塞调用 → 阻塞事件循环 | §4 清单逐一核对;code review 专项检查"async 函数里有没有裸的同步磁盘 I/O" |
| 测试 mock 从同步改 async 出错 | 先改一个跑通形成范式,再套用其余 |
| 中途引入行为变化(不是纯重构) | 铁律:本步不加功能、不改行为,只换 async 底子。任何行为变化都是 bug |

---

## 8. 实现节奏(两步走,本说明只覆盖第一步)

- **第一步(本说明)**:async 重构。行为不变、测试全绿 → 提交干净的重构 commit(s)。
- **第二步(单独 spec)**:中断生成功能。前端 AbortController 断流 + 后端取消令牌在两处生效 + 被中断的部分回答落盘 + 前端「停止」按钮。

---

## 9. 需同步更新

- **HANDOFF.md**:第 1 节原则升级(§0);技术栈标注"流式链路 async / 存储工具同步 + to_thread";记录"中断只在回合边界+LLM流生效、不强杀工具"的设计约束。
- **`pyproject.toml`**:加 `pytest-asyncio` dev 依赖。
