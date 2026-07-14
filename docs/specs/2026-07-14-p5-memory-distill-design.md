# P5 记忆自动蒸馏 设计文档

- 日期:2026-07-14
- 状态:设计已确认,待实现
- 关联:`HANDOFF.md` 第 5 节待办 5(记忆自动蒸馏)、`docs/specs/2026-07-10-p5plusplus-memory-md-design.md`(MEMORY.md 长期客观记忆)

---

## 1. 目标

给记忆系统补上闭环:**定时(默认关)或手动**把最近若干天的日志流水,单次 LLM 提炼成客观事实,合并去重后覆盖写回 `MEMORY.md`。解决「用户忘了显式触发『整理日志到长期记忆』,客观事实就永不沉淀」的问题。对标 OpenClaw dreaming sweep。

---

## 2. 背景与动机

现状(P5++ 完成):`MEMORY.md`(长期客观记忆)靠用户**显式触发**提炼——用户得主动说「整理下最近日志到长期记忆」,Agent 才调 `update_memory`。日志(`data/memory/YYYY-MM-DD.md`)是流水,天天积累,但不会自动升华成 MEMORY。缺口:用户不主动说,日志里的客观事实(如「本项目测试用 uv run pytest」)就烂在流水里、不进长期记忆。

本功能加一个**后台蒸馏**:定时把日志提炼进 MEMORY,补上「无人触发」的洞。

### 2.1 为什么是「单次 LLM 提炼」而非「完整 Agent 循环」

蒸馏任务的**输入是确定的**——就是「最近 N 天日志 + 现有 MEMORY」,代码完全知道要喂什么,无需模型自主探索「该读什么」。走完整 Agent 循环(让模型自己决定调 read_log/read_memory/update_memory)是过度设计:多轮往返更慢更贵更多出错面,且后台无审批环境下让循环自主写盘风险更高。更关键——现有 `run_agent_streaming` 是为「前端会话」设计的(要 sid、读 JSONL、yield SSE、处理审批),后台无会话场景要复用它反而得套一层「假会话 + 假事件消费者 + 绕审批」的壳,那才是「更重」的来源。

所以:蒸馏是「输入已知 → 做一次文本转换」,用单次 `chat.completions.create` 正合适,独立成 `distill_memory()`,不复用会话循环。

---

## 3. 关键设计决策

| # | 决策 | 取值 | 理由 |
|---|---|---|---|
| D1 | 蒸馏方式 | **单次 LLM 提炼**,全量覆盖写回 | 输入确定,无需 Agent 自主决策;独立、简单 |
| D2 | 触发方式 | **后台定时调度器**(APScheduler)+ **手动 HTTP 接口** 双入口,共用 `distill_memory()` | 定时补「无人触发」洞;手动接口便于验证/随时触发 |
| D3 | 扫描范围 | **固定滑窗最近 N 天**日志,靠 prompt 让模型基于现有 MEMORY 合并去重 | 最简,不用维护「上次蒸馏水位」状态 |
| D4 | 默认开关 | config `distill.enabled` **默认 false** | 避免后端一起就自动烧 token / 撞网关约束;用户手动开 |
| D5 | 默认参数 | `interval_hours=72`(每 3 天一次)、`scan_days=3`(扫最近 3 天) | 个人自用低频足够;用户拍定 3 天 |
| D6 | LLM 调用 | **非流式**(与子 Agent 一致) | 单次转换,流式无收益;当前网关强制流式会 400,但失败被兜住不崩(见 §7) |
| D7 | 写盘保护 | **只有拿到非空新全文才 `write_memory`** | 模型返回空/异常都保留原记忆,绝不把 MEMORY 蒸没 |
| D8 | 空日志 | 最近 N 天无日志 → 短路,**不调 LLM** | 省 token、不触发网关 |
| D9 | 调度器挂载 | FastAPI `lifespan`:启动 start、关闭 stop | 现有 main.py 无生命周期钩子,需引入 lifespan |

---

## 4. 架构与数据流

```
后台调度器(APScheduler)──┐
                          ├──> distill_memory()   ← 核心蒸馏函数(纯逻辑,可单测)
POST /api/memory/distill ─┘         │
                                    ├─ memory.recent_log_days(N)  读最近 N 天非空日志(新增)
                                    ├─ memory.read_memory()       读现有 MEMORY(现成)
                                    ├─ llm.get_llm_client()        拿 client+model(现成)
                                    ├─ 单次 create()(非流式)     提炼
                                    └─ memory.write_memory(新全文) 覆盖写(现成)
```

数据流(定时触发那条):
```
调度器到点 → distill_memory()
  → recent_log_days(3) → [(日期,内容)...] → 空? → 返回"无日志",结束(不调 LLM)
  → 拼 prompt(system:记忆整理器 + user:现有MEMORY + 近3天日志)
  → create(非流式) → 失败? → catch,返回失败摘要,不写盘
  → 新 MEMORY 全文非空? → write_memory(全文) → 返回"已更新,长度 N"
                        → 空 → 不写,返回失败摘要(保留原记忆)
```

`distill_memory()` 是**纯函数**:不依赖 sid/会话/前端,输入是「配置 + 日志 + 现有记忆」,输出是人读的结果摘要串。定时器和 HTTP 接口都只是它的调用者。

---

## 5. 文件结构

```
backend/
├── pyproject.toml                    #【改】加依赖 apscheduler
├── app/
│   ├── services/
│   │   ├── memory.py                 #【改】加 recent_log_days(n)
│   │   ├── distill.py                #【新增】distill_memory() + DISTILL_SYSTEM_PROMPT
│   │   └── config_store.py           #【改】DEFAULTS 加 distill 分区
│   ├── agent/
│   │   └── scheduler.py              #【新增】APScheduler 封装 start/stop
│   └── api/
│       ├── main.py                   #【改】加 lifespan
│       └── routes/memory.py          #【新增】POST /api/memory/distill
```

---

## 6. 详细设计

### 6.1 config 新增 distill 分区(`config_store.py` DEFAULTS)

```python
"distill": {
    "enabled": False,       # 默认关:后端起不自动跑
    "interval_hours": 72,   # 定时频率:每 3 天一次
    "scan_days": 3,         # 每次扫最近 3 天日志
},
```

### 6.2 memory.py 新增 `recent_log_days`

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

### 6.3 distill.py 核心

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

### 6.4 scheduler.py(APScheduler 封装)

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

### 6.5 main.py 加 lifespan

```python
from contextlib import asynccontextmanager
from app.agent import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start_scheduler()   # 启动:按 config 决定是否注册 job
    yield
    scheduler.stop_scheduler()    # 关闭:停调度器


app = FastAPI(title="Superstar Backend", version="0.1.0", lifespan=lifespan)
```

并注册新路由:`app.include_router(memory_routes.router)`。

> ⚠️ uvicorn `--reload`(现启动带 watch)下 lifespan 随热重载重启,调度器跟着停/起——正常,单实例无重复 job 风险(默认关时更无所谓)。

### 6.6 routes/memory.py(手动触发)

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

---

## 7. 错误处理

核心原则:**绝不崩调度器、绝不毁 MEMORY**。

| 失败点 | 处理 |
|---|---|
| 最近 N 天无日志 | 短路返回「无日志」摘要,不调 LLM |
| LLM 调用失败(网关拒/网络/超时) | `distill_memory` try/except → 返回失败摘要,不写盘 |
| 模型返回空内容 | 不 `write_memory`,保留原记忆,返回失败摘要 |
| 调度器 job 抛异常 | `distill_memory` 自己兜住不上抛 → APScheduler 线程不死 |
| config distill 分区缺失 | DEFAULTS 深合并补齐(老 config.json 兼容) |

> 注:当前配置的网关(tokenhub gpt-5.5)强制 `stream=true`,非流式蒸馏会 400 → 被兜成失败摘要,不影响后端其余功能。用户换非流式模型后即可正常蒸馏(与子 Agent 同一约束)。

---

## 8. 测试计划

pytest + mock LLM(非流式 mock 参照 test_subagent 的 `_Msg`/`_Resp`):

1. **正常蒸馏**:造 2 天日志 + mock 返回新 MEMORY 全文 → 断言 `write_memory` 被调、内容=模型输出。
2. **空日志短路**:无日志 → 断言返回「无日志」摘要、**未调 LLM**(mock client 断言未被调用)。
3. **模型返回空不覆盖**:mock 返回空串 → 断言原 MEMORY 未被改、返回失败摘要。
4. **LLM 异常兜底**:mock client 抛错 → 断言返回失败摘要、不抛、原记忆不动。
5. **`recent_log_days(n)`**:造今天/前2天/前5天日志,n=3 → 只返回最近 3 天非空、今天在前;n=0 → 空。
6. **config distill 默认值**:`get()["distill"]` 有 enabled=False / interval_hours=72 / scan_days=3。
7. **scheduler enabled=false 不启动**:mock config enabled=false → `start_scheduler` 后 `_scheduler is None`。
8. **scheduler enabled=true 注册 job**:enabled=true → 启动后有 id="distill" 的 job;`stop_scheduler` 后 `_scheduler is None`。(真实起 BackgroundScheduler,测完 stop 干净)
9. **手动接口**:`POST /api/memory/distill`(monkeypatch `distill_memory` 返回固定串)→ 断言 200 + `{"result": ...}`。

---

## 9. 影响面 / 需同步更新

- **`pyproject.toml`**:加 `apscheduler` 依赖(需 `uv sync`/`uv add`)。
- **`HANDOFF.md`**:第 5 节待办 5(记忆自动蒸馏)标记完成;技术栈/依赖补 APScheduler;新增 `POST /api/memory/distill` 接口说明。
- **config.json(线上运行的)**:深合并会自动补 distill 分区,无需手改;想开蒸馏则设 `distill.enabled=true`(改盘要重启,或走设置页 API 改)。
- **前端**:本期不接蒸馏 UI(手动触发走 API,配置走 config.json/设置页现有通道)。

---

## 10. 明确不做(YAGNI)

- 蒸馏历史/审计留档。
- 增量水位追踪(已选固定滑窗)。
- 蒸馏走完整 Agent 循环。
- 前端蒸馏配置/触发 UI。
- 蒸馏结果推送通知。
- 蒸馏并发锁(单用户、低频,APScheduler 默认 `max_instances=1` 已防同一 job 叠跑;手动接口与定时器理论上可同时跑,但覆盖写幂等、且概率极低,不额外加锁)。
