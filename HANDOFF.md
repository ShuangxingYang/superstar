# Superstar 交接文档

> 给「下一个会话」的接手说明:项目是什么、已经做到哪、还剩什么、怎么跑起来、有哪些坑。
> 写于 2026-07-08(P4 完成、P5 做了一半)。配套细节看 `DEVELOPMENT_PLAN.md`(总蓝图)和 `docs/`(每期 spec + plan)。

---

## 0. 一句话

Superstar 是一个**本地、单用户、无鉴权的「干活型」Agent**——对标 Claude Code 内核(工具调用 + 审批 + 会话持久化 + RAG),后端 FastAPI、前端 React,**零数据库**。它是 agent-study 学习项目的 M10 收官实战。

- 项目根目录:`/Users/shuangxingyang/Desktop/myspace/superstar`
- **是独立的 git 仓库**,和 hello-agents 那个仓库无关(别被环境里 hello-agents 的 git status 带偏)。
- 主分支:`main`(直接在 main 上开发,早期无用户、不背兼容包袱)。

---

## 1. 定位与设计原则(先读这段,避免过度设计)

个人自用 + 面试展示。

**设计原则(2026-07-17 升级):以业界标准做法为基线,在其上做减法。** 先按「专业项目该怎么做」立骨架,再砍掉本项目(单用户本地 + 未来轻量飞书并发)确实不需要的分支。**不是**「先搭玩具、缺什么补什么」——目标是代码经得起「这是不是专业实现」的审视,不是 toy 级。
> 旧原则是「用最小成本覆盖知识点、能省则省」,现已作废。举例:async 流式链路是业界标准骨架(必做);「存储/同步工具不改 async、用 to_thread 复用」是合理减法(对「单用户本地 async 零收益」的正确判断,不是偷懒)。做减法 ≠ 减掉有意设计——如子 Agent 并发上限,减错了就是行为回归(见第 4 节 async 重构终审)。

- ✅ 借鉴:极简 Agent 循环、工具统一接口、命令分级审批、人在环路、JSONL 会话、RAG 两阶段检索、流式 SSE、**async 流式链路**。
- ❌ 不做:monorepo、可插拔 sandbox、常驻网关、鉴权多租户、消息队列、CI/CD、性能压测。

两条硬简化:
1. **代码工作区用 grep/read,不建向量索引**;向量/RAG 只服务「文档知识库」。
2. **会话用 JSONL 追加存储,配置用 `data/config.json`,全后端零数据库**。

> ⚠️ **兼容性规则豁免**:superstar 处于早期、无线上用户,团队通用的「API 只加不删」向后兼容规范**不适用**这里。需要就大胆做破坏性变更,优先精简。

---

## 2. 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11 · FastAPI(**async 路由**)· uvicorn(管事件循环)· [uv](https://docs.astral.sh/uv/) 管依赖 · APScheduler(后台定时,仅记忆蒸馏用) |
| LLM 调用 | **OpenAI SDK**(function calling,非文字 ReAct)· 主链路+子Agent 用 **AsyncOpenAI**、蒸馏/测试连接用同步 OpenAI(见 `llm.py` 双 client) |
| 前端 | React 19 · Vite 6 · TypeScript · Tailwind + shadcn/ui |
| 流式 | SSE(`data: json\n\n` + StreamingResponse),前端 fetch + ReadableStream |
| 会话存储 | JSONL 文件(每会话一个 `.jsonl`,追加式可回放) |
| 应用配置 | `backend/data/config.json`(热生效,内存缓存) |
| 向量库 | Qdrant(Docker),collection 名 `superstar_kb`,1024 维,COSINE |
| embedding | 阿里云 DashScope `text-embedding-v3`(1024 维);对话模型走火山/tokenhub |

> embedding 用 DashScope 是因为火山那边没开 embedding 权限;对话用火山豆包 / 内网 tokenhub 网关。

---

## 3. 实际目录结构(注意:和 DEVELOPMENT_PLAN.md 里画的略有出入,以此为准)

计划里写的是 `backend/core/`,**实际落地时把内核并进了 `backend/app/`**:

```
superstar/
├── DEVELOPMENT_PLAN.md          # 总蓝图(P0-P5 + 二期),仍是权威参考
├── HANDOFF.md                   # 本文件
├── README.md                    # 使用/部署说明(已完成)
├── docs/
│   ├── specs/                   # 每期设计文档(P1/P2a/P2b/P3/P4)
│   └── plans/                   # 每期实现计划(P0-P4)
├── backend/
│   ├── run.py                   # 启动入口(默认 127.0.0.1:8000)
│   ├── pyproject.toml           # uv
│   ├── config.example.json      # 业务配置模板(入库);复制成 data/config.json 再填 key
│   ├── .env.example             # 启动配置模板
│   ├── data/                    # 【全部 gitignore】config.json / sessions/ / kb/
│   ├── app/
│   │   ├── api/main.py          # FastAPI 实例 + CORS + 路由注册
│   │   ├── api/routes/          # settings / chat / session / kb 四个路由
│   │   │                        #   ⚠️ 没有独立 approval 路由:审批走 chat 的 resume
│   │   ├── agent/
│   │   │   ├── loop.py          # run_agent_streaming:流式 function calling 循环,产 typed event
│   │   │   ├── gate.py          # 命令/工具分级处置判定(白/黑/灰、是否需审批)
│   │   │   ├── pending.py       # 待审批 sidecar(把「未回答的 tool_call」落盘)
│   │   │   └── tools/           # fs / search / shell / rag / workspace + __init__(注册表)
│   │   ├── services/
│   │   │   ├── config_store.py  # data/config.json 读写 + 内存缓存 + 深合并
│   │   │   ├── llm.py           # 动态 OpenAI client(按当前配置建,可热切换)
│   │   │   ├── security.py      # 沙箱多根 safe_path + 命令分级
│   │   │   ├── session_store.py # JSONL 会话读写 + index.json
│   │   │   ├── rag_store.py     # RagStore:embed + Qdrant + 两阶段检索
│   │   │   ├── chunker.py       # 递归字符切块
│   │   │   ├── loaders.py       # pdf/md/txt 文档适配器
│   │   │   └── atomic_json.py   # 原子写 JSON(写 .tmp 再 rename,防写坏)
│   │   ├── models/schemas.py    # Pydantic 出入参 + key 脱敏
│   │   └── config.py            # pydantic-settings:启动必需项(端口/data_dir/qdrant_url)
│   └── tests/                   # 19 个测试文件,133 个 test 函数,当前全绿
└── frontend/
    └── src/
        ├── App.tsx              # 三栏布局 + 聊天气泡 + 思考过程块(ReasoningBlock)
        ├── components/          # SessionList / ContextPanel / KbManager /
        │                        #   SettingsPanel / ToolCallCard / ui(button,input)
        ├── hooks/useChatStream.ts  # 消费 SSE、维护 messages/sessions、审批 resume
        └── lib/api.ts           # 后端 API 封装
```

---

## 4. 已完成(P0-P4 全绿 + P5 大部完成)

### P0-P4 —— 第一版本地 Web 闭环,全部完成
- **P0 竖切**:config.json + 动态 LLM client + `POST /api/chat/stream`(SSE 单轮流式)+ 极简前端打字机。
- **P1 会话**:JSONL session_store + 多轮上下文 + 左栏会话侧边栏(新建/切换/删除/重命名),重启历史还在。
- **P2a 只读工具**:`read_file`/`grep`/`glob` + ToolRegistry(Pydantic 校验+自愈)+ 前端工具卡片。
- **P2b 写 + 审批**:`write_file`/`run_command` + security 三级名单 + **人在环路(回合边界机制)** + 前端审批弹窗 + diff 预览。
  - ⚠️ **2026-07-13 起 `write_file` 改为「允许目录内自动放行」**(硬改,不再弹审批/无 diff 预览);越界写仍被工具内 `safe_path` 拒。人在环路机制未删,现由 `run_command` 灰名单 + `add_workspace` 扩权承载(审批测试的触发样例也随之换成灰名单命令)。
- **P3 RAG**:Qdrant + RagStore(切块/embed/灌库/增删查)+ 两阶段检索(向量召回 → rerank 精排)+ `search_kb`(带来源、反幻觉)+ 知识库管理页(上传/列表/删除/重建)。
- **P4 打磨**:工作区**多根**重构(`default_cwd` + `allowed_dirs` 白名单,grep/glob 搜所有根输出绝对路径)+ `add_workspace`/`remove_workspace` 工具(加目录到白名单,需审批)+ 设置页 SettingsPanel(LLM/embedding/安全/Agent 分区 + 测试连接)+ 右栏 ContextPanel(工作目录/白名单/知识库数)+ 首启未配引导 + 各种态。

### 已注册的工具(共 12 个)
`read_file`、`write_file`(2026-07-13 起自动放行)、`grep`、`glob`、`run_command`(三级)、`search_kb`、`add_workspace`(审批)/`remove_workspace`、`update_profile`/`update_memory`/`update_soul`/`append_log`(记忆,自动放行)、`dispatch_subagent`(派子 Agent,自动放行)。

### P5 已做的部分(收官期)
- ✅ **推理模型思考过程**:探测 → 前端可折叠「思考过程」块展示 → 落盘 JSONL 可回放。喂模型时用 `_strip_reasoning` 把 reasoning 剥掉再请求(reasoning 只存不喂)。
- ✅ **LLM 配置预设(llm_profiles)**:设置页存多套具名 LLM 连接快照,一键切换;切到推理模型的预设自动开 `reasoning_effort`,切回自动关。
- ✅ 一些前端修复:思考块与工具卡片间的**空白气泡**已修(`showBubble = !!content`)。
- ✅ **记忆 / 个性化(2026-07-09 完成)**:本地 markdown 长期记忆。
  - `data/profile.md`(用户画像,初始空,Agent 沉淀)+ `data/soul.md`(Agent 准则,首次读取自举默认模板)。
  - `app/services/memory.py`:读/写/`build_memory_block()` 注入拼接;每轮读盘不加内存缓存;全量覆盖 + 原子写(`atomic_json.write_text_atomic`)。
  - `app/agent/tools/memory.py`:`update_profile`/`update_soul` 两工具,**自动放行**(不走审批,gate 未改)。
  - `loop.py`:每轮把 `build_memory_block()` 叠加到 `SYSTEM_PROMPT` 之后(记忆为空时 system 等于原 prompt,保 prompt cache 前缀稳定)。
  - 设计/计划:`docs/specs/2026-07-09-p5-memory-design.md`、`docs/plans/2026-07-09-p5-memory.md`。测试 17 个(atomic 2 / memory 8 / tools 5 / loop 2),全量 150 绿。
  - ⚠️ 右栏 ContextPanel 里「Agent 记得你」那块**前端还没接**(后端记忆已通,只差 UI 展示,留可选)。
- ✅ **每日日志层 + SYSTEM_PROMPT 瘦身(2026-07-10 完成)**:给记忆加时效层 + 修 prompt 重复。
  - `data/memory/YYYY-MM-DD.md`(每日日志,只追加),`append_log` 工具让 Agent 记「今天发生的事」;开会话自动注入「今天+昨天」的非空日志。
  - `memory.py` 加 `_today`(可 monkeypatch)/`_log_dir`/`_log_path`/`append_log`/`read_log`/`recent_logs`;`build_memory_block()` 扩展拼日志段(小标题只用文件名日期、不含 HH:MM,保 prompt cache;HH:MM 只进文件内容)。多行 entry 会被 `" ".join(entry.split())` 折成单行(保「一条一行」)。
  - **SYSTEM_PROMPT 瘦身**:删掉「逐个复述工具是什么」的清单(grep/glob/read_file...),只留策略/边界 + 一句记忆路由。依据:tool description 说 what,system prompt 说 when/why(见 `docs/research/2026-07-09-openclaw-memory-system.md` 及本次调研)。
  - 三种记忆分工:profile=稳定事实 / soul=自身准则 / log=今天的事。
  - 设计/计划:`docs/specs/2026-07-10-p5plus-memory-log-design.md`、`docs/plans/2026-07-10-p5plus-memory-log.md`。全量 166 绿。
  - 对标调研存档:`docs/research/2026-07-09-openclaw-memory-system.md`(OpenClaw 记忆系统,含二期演进路线:每日日志✅→压缩前刷新→混合检索→时间衰减)。
- ✅ **长期客观记忆 MEMORY.md + profile 收紧(2026-07-10 完成)**:补齐记忆第四层,四层边界划清。
  - `data/MEMORY.md`(长期**客观**事实/既定结论:项目约定、技术栈、架构决策等跟人无关的稳定知识),初始不存在(不自举),`update_memory` 工具整份覆盖、Agent 自主写、全量注入 `## 长期记忆`(排在 profile 后、soul 前)。
  - **profile 收紧**:`update_profile` 描述改为「只有特别确定是用户个人信息时才记」——客观事实改走 update_memory。
  - **四种记忆边界**(system prompt + 工具描述划清):profile=用户个人信息(主观)/ MEMORY=客观稳定事实 / soul=Agent 自身准则 / log=今天流水。
  - 提炼靠**用户显式触发**(「整理下最近日志到长期记忆」);「定时自动蒸馏」已登记待办(见下第 5 节)。
  - 设计/计划:`docs/specs/2026-07-10-p5plusplus-memory-md-design.md`、`docs/plans/2026-07-10-p5plusplus-memory-md.md`。全量 178 绿。

### 后端 async 重构(2026-07-17 完成,纯重构·行为不变)
把后端流式链路从 sync 改成 async,为「中断生成」和未来飞书并发打地基。**性质:纯重构,行为完全不变**(所有测试断言逻辑一字未动,只 mock sync→async;全量 208→221 绿,增的全是新测试)。
- **架构 = async 壳 + 同步核**:主对话链路 + 子 Agent 全 async(`AsyncOpenAI` + `async for`);存储/config/memory/pending 及同步工具**源码零改动**,由调用方 `asyncio.to_thread`(阻塞磁盘 I/O)或 `registry.run_async`(工具执行)包装复用——这是「单用户本地 async 零收益」的减法。
- **改了什么**:`llm.py` 双 client(`get_llm_client`→AsyncOpenAI 主链路+子Agent、`get_sync_llm_client`→OpenAI 蒸馏/测试连接);`tools/__init__.py` 加 `run_async`(async 工具直 await、同步工具 to_thread)+ `Tool.is_async`(inspect 探测),旧 `run` 未动;`loop.py` 主循环全 async;`chat.py` 路由 async;`subagent.py` 子 Agent async + `dispatch_subagents` 用 `asyncio.gather`+`Semaphore(5)` 替代 ThreadPoolExecutor;`distill.py` 改用同步 client。
- **两个技术要点**:①async generator **不能 return 值**(`yield from` 也失效),`_accumulate` 改用可变容器 `out` 回传三元组;②`to_thread` 边界=**只包会阻塞的磁盘 I/O**,纯内存计算(`_prune`/`_strip`/`gate`)直接调(为微秒操作套线程调度是负优化)。
- **中断只预留、未实现**(第二步单独做):`run_agent_streaming(sid, cancel_event=None)` 加了可选参数 + 回合边界 TODO 注释。**设计约束(第二步遵守)**:中断只在两处生效——回合边界 + LLM 流读取;**正在执行的工具/命令不强杀**,靠操作原子性(write 原子写、run_command 外部脚本等它跑完)保证「被打断=安全状态」。
- **终审教训**:`dispatch_subagents` async 化时一度把并发上限 `_MAX_PARALLEL=5` 弄丢(gather 无限并发=行为回归),whole-branch review 逮住,已用 `Semaphore(5)` 修回 + 补并发上限测试。印证「减法 ≠ 减掉有意设计」。
- 手动冒烟(deepseek-v4-flash,真 uvicorn):普通对话流式 ✅、派子 Agent(async 工具直 await + 同步工具 to_thread)✅,端到端不卡。
- 设计/计划:`docs/specs/2026-07-17-async-refactor-design.md`、`docs/plans/2026-07-17-async-refactor.md`。

---

## 5. 还没做(待办)

### P5 剩余(第一版收尾)
1. **记忆 / 个性化(✅ 已完成 2026-07-09)**:见上第 4 节。唯一遗留:右栏 ContextPanel 的「Agent 记得你」UI 未接(后端已通,纯前端展示,可选)。
2. **子 Agent 隔离(✅ 已完成 2026-07-13)**:`dispatch_subagent` 工具 → `agent/subagent.py` 的 `run_subagent(task)`。主 Agent 派发独立上下文的子 Agent 干只读+写子任务,只回传结论(黑盒、纯内存、非流式、跑到底、整体兜底)。子 Agent 白名单 `{read_file,grep,glob,search_kb,write_file}`——不含 run_command/add_workspace/dispatch_subagent(结构性防孙 Agent)。同批把 `write_file` 改自动放行(解开「子 Agent 同步调用内无法等审批」的死结)。设计/计划:`docs/specs/2026-07-13-p5-subagent-design.md`、`docs/plans/2026-07-13-p5-subagent.md`。全量 192 绿。⚠️ 前端未接子 Agent 专门 UI(走既有工具卡片通道,够用)。
3. **docker-compose(未做)**:根目录还没有 `docker-compose.yml`。目前 Qdrant 是手动 `docker run` 起的。
4. **README**:已完成 ✅。
5. **记忆自动蒸馏(✅ 已完成 2026-07-14)**:定时(默认关)或手动把最近 N 天日志单次 LLM 提炼进 MEMORY.md。`services/distill.py` 的 `distill_memory()`(读最近 scan_days 天日志 + 现有 MEMORY → 单次非流式提炼 → **只有非空新全文才覆盖写**,空日志短路不调 LLM、失败/返空/异常全兜住不毁记忆)。两个入口:`agent/scheduler.py`(APScheduler 后台定时,挂 FastAPI lifespan,`config.distill.enabled` 默认 false 才注册 job)+ `POST /api/memory/distill`(手动触发)。config 加 `distill` 分区(enabled=false / interval_hours=72 / scan_days=3)。设计/计划:`docs/specs/2026-07-14-p5-memory-distill-design.md`、`docs/plans/2026-07-14-p5-memory-distill.md`。全量 202 绿。⚠️ 非流式,当前 tokenhub 网关强制流式会 400 → 恒返回失败摘要;**须换非流式模型才真正生效**(与子 Agent 同约束)。⚠️ 前端未接蒸馏 UI(配置走 config.json/设置页,手动触发走 API)。

### 明确「推迟」的
- **给 superstar 做一套新的端到端评测**:用户认为 M9 那套评测太简单,要给 superstar 做新的,但**决定推迟到二期做完之后**再做。别现在动。

### 二期(增量,还没开始)
飞书长连接(快速 ACK + 卡片流式 + 按钮确认)、MCP 真实 server 接入、Skill 渐进式披露、反思式自我完善、checkpoint/回滚、**Anthropic 格式兼容**(2026-07-15 提出,当前无硬需求——在用模型全是 OpenAI 兼容格式,故不做;LLM 调用已隔离在 `llm.py` 一层,将来真要接只支持 Anthropic 原生格式的 API 时,在此加协议适配器即可,不动上层)。

### 可选的体验优化(提过,还没做)
- `GET /api/kb/stats` 在 Qdrant 没起时目前直接抛 500。改成返回一个可读的「RAG 未就绪」状态更友好。
- LLM 连不上时,后端抛 `APIConnectionError`,前端只显示字面量 `⚠️ Connection error.`。可以 catch 后给可读提示(「连不上模型服务,请检查网络/VPN 或换个模型预设」)。

### 待办:子 Agent 并行派发(✅ 已完成 2026-07-16)
`dispatch_subagents`(复数)工具:一次传 `tasks` 列表,内部 `ThreadPoolExecutor` 并发跑 N 个 `run_subagent`(它本就同步、OpenAI client 线程安全),`ex.map` 保序、`max_workers=min(len,5)` 封顶、某个失败返失败串不影响其余、空列表短路。主循环未动(方案 A,并行封装在工具内)。不进 `SUBAGENT_TOOLS` 白名单(防子 Agent 并行派孙 Agent)。单数 `dispatch_subagent` 保留。计划:`docs/plans/2026-07-16-p5-subagent-parallel.md`。全量 208 绿。

---

## 6. ⚠️ 接手前必须知道的决策待定项

**二期方向没定,动手前先问用户。** 用户说过「二期还有很多内容」,但计划里「二期」指的是飞书/MCP/Skill。需要确认:是**先补第一版 P5 的记忆 + 子 Agent 收尾**,还是**直接跳到第二版飞书/MCP/Skill**?这决定接下来拆什么任务。

---

## 7. 关键机制(接手要理解的几处「非直觉」设计)

- **人在环路 = 回合边界,状态存在 messages 里**:命中灰名单/写文件时,`run_agent_streaming` yield `approval_required` 然后**本轮流结束**——此刻 messages 末条 assistant 有个「没被回答的 tool_call」(落在 `pending.py` sidecar)。用户点批准 → `POST` resume **只记录 yes/no**(不执行)→ 前端**再开一条流**续跑 → 循环检测到「未回答 tool_call + 已批准」→ 才真正执行工具。**工具执行只有一处(在循环里)**,断线/重启不丢。
- **配置热生效但有内存缓存**:`config_store` 首次 `get()` 从磁盘加载后走内存缓存。**直接手改 `data/config.json` 不重启后端会读到旧缓存**——验证配置改动必须重启后端,或走设置页 API 改(API 改会刷新缓存)。
- **深合并只补缺失键,不覆盖已存在的空串**:`config_store` 的 DEFAULTS 只填 config.json 里**缺失**的键。如果 live config.json 里某键已经是空串 `""`,新加的 default 不会覆盖它——这类得直接改磁盘 + 重启。
- **推理模型的流式形状**:推理模型(gpt-5 系经 tokenhub)常「憋一会儿再整段吐」,这是思考期特性、**不是 bug**;换非推理模型即恢复逐字流。
- **reasoning 网关噪音**:tokenhub 的 `reasoning_content` 里常夹 markdown 注释残片 `<!-- -->` 和 `**`,前端 ReasoningBlock 已做清洗。

---

## 8. 怎么跑起来 / 当前运行状态

**当前状态(交接时)**:后端(:8000)、前端(:5173)、Qdrant 容器(名 `qdrant`,:6333)都在跑。知识库 collection `superstar_kb` 里有历史测试数据。

### 后端
```bash
cd backend
uv sync                                  # 装依赖
cp config.example.json data/config.json  # 首次:复制模板再填 key(api_key/model 也可留空,启动后设置页填)
uv run python run.py                      # 默认 http://127.0.0.1:8000
```

### 前端
```bash
cd frontend
npm install
npm run dev                               # 默认 http://localhost:5173(vite proxy 转 /api 到后端)
```

### Qdrant(用到知识库再起)
```bash
# 起(数据挂到 ./qdrant_storage,该目录已 gitignore)
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 \
  -v "$(pwd)/qdrant_storage:/qdrant/storage" qdrant/qdrant

# Mac 上 docker 没起时先:open -a Docker,再等就绪:
#   until docker info >/dev/null 2>&1; do sleep 2; done

# 看库里存了啥:浏览器开 http://localhost:6333/dashboard,collection 名 superstar_kb
```

### 测试
```bash
cd backend && uv run pytest -q     # 133 个,当前全绿
cd frontend && npm run build       # tsc -b && vite build
```

---

## 9. 安全红线(改动时务必守住)

- `backend/data/config.json` 装的是**真实 API 凭证**,已 gitignore,**绝不能提交**。改配置的实验只留在磁盘,不要 `git add` 它。
- `qdrant_storage/`(Qdrant Docker 挂载卷,二进制)已 gitignore,**绝不入库**。
- 日志**绝不打印 api_key**;`config_store.update` 只记录改了哪些分组名。
- API 返回配置时 key 处理:本地自用场景 key **明文回传**(前端默认密文展示、可点眼睛看),测试连接直接用真 key;`_drop_masked_keys` 仍保留做防御。
- **提交/推送只在用户明确要求时做**。

---

## 10. 沟通约定

- 用**中文**,说人话,别用「啊哈时刻」这类翻译腔黑话。
- 用户会 JS、不熟 Java/Python 细节;讲语法时用 JS 类比。
- 收官项目模式:脚手架生成样板、Agent 核心手写、边讲边开发,用户全程参与。
