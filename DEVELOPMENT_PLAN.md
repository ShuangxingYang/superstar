# Superstar 开发计划

> 一个本地运行的"干活型" AI Agent —— 能读写文件、跑命令、检索知识库、加载技能、接入飞书。
> 定位:自研 **Claude Code 内核** + **OpenClaw 式 IM 接入**,个人自用,同时作为 Agent 开发能力的完整实践。

---

## 一、这是什么

Superstar 不是"问答机器人",而是一个**能替你做事**的本地 Agent:

- 💬 像 ChatGPT 一样跟它对话(本地 Web 端,流式打字机)
- 📂 让它**读写你的代码/文件**、跑 `grep`/`git`/测试等命令,真正修改项目
- 📚 检索你的**私有文档知识库**(RAG),基于你的资料回答
- 🧠 它会**记住你**(技术栈、偏好、工作准则),越用越懂你
- 🔌(二期)接**飞书**,在飞书里 @ 它派活;接 **MCP** 外部工具;加载 **Skill** 方法论

对标:内核像 **Claude Code**(读写文件/跑命令/子 Agent),接入像 **OpenClaw**(IM 里对话)。

### 设计原则(重要:控制复杂度)

本项目**个人自用 + 面试展示**,目标是**用最小成本覆盖 Agent 开发的核心知识点与难点**,不追求生产级完备。参考 Claude Code / OpenClaw 的调研结论,明确**只借鉴精华、不抄生产包袱**:

- ✅ 借鉴:极简 Agent 循环、工具统一接口、可中断(AbortSignal)、命令分级审批、JSONL 会话存储、Skill 渐进式披露、飞书三要素(长连接 + 快速 ACK + 卡片流式)
- ❌ 不做:monorepo、可插拔 sandbox backend、常驻网关、庞大钩子/安全审计体系、多账号多域名、鉴权多租户

两条关键简化(来自调研):
1. **代码工作区用 `grep`/`read` 即可,不建向量索引**(Claude Code 的做法:模型自己懂代码结构)。**向量/RAG 只服务"文档知识库"**,与代码工作区分离。
2. **会话用 JSONL 文件追加存储**(可回放、可 debug、比数据库轻),不上 ORM;仅"应用配置"用 SQLite 结构化存。

---

## 二、最终产品形态(用户能看到什么)

启动后浏览器打开本地页面,是一个**三栏布局**的聊天工作台:

```
┌──────────────┬────────────────────────────────────┬──────────────────┐
│  左栏 侧边栏  │            中栏 对话区              │   右栏 上下文面板 │
│              │                                    │                  │
│ [+ 新建会话] │  👤 帮我看下 utils.py 有没有没用到  │  当前工作区:     │
│              │     的函数,删掉                    │  ~/code/myproj   │
│ 会话列表:    │  🤖 好的,我先搜一下…               │  [切换目录]      │
│ • 重构utils  │   ┌─ 🔧 run_command ─────────────┐ │                  │
│ • 查报错     │   │ grep -rn "def " utils.py      │ │  知识库: 3 篇    │
│ • 周报草稿   │   │ ✓ 找到 8 个函数               │ │  [管理知识库]    │
│   ...        │   └───────────────────────────────┘ │                  │
│              │   ┌─ 🔧 read_file: utils.py ──────┐ │  Agent 记得你:   │
│ ─────────    │   └───────────────────────────────┘ │  • 用中文        │
│ ⚙️ 设置      │   找到 2 个无用函数,请确认删除:   │  • 会JS不会Java  │
│ 📚 知识库    │   ┌─ ✋ 待确认:写文件 ───────────┐ │  • 改前先diff    │
│              │   │ utils.py  [查看 diff ▾]        │ │  [编辑画像/准则] │
│              │   │  [✓ 批准]   [✗ 拒绝]          │ │                  │
│              │   └───────────────────────────────┘ │                  │
│              │  ┌──────────────────────────────┐   │                  │
│              │  │ 输入消息…              [发送] │   │                  │
│              │  └──────────────────────────────┘   │                  │
└──────────────┴────────────────────────────────────┴──────────────────┘
```

用户能看到 / 操作的元素:

1. **对话消息流**:用户气泡 + Agent 气泡,回答**逐字流式**冒出(打字机)。
2. **工具调用卡片**:每次调工具渲染成可折叠卡片(工具名/参数/结果),让用户**看见 Agent 在干什么**,不是黑盒。
3. **待确认卡片**(人在环路):写文件/跑非白名单命令时插入确认卡片。写文件附 **diff 预览**;命令显示完整命令 + 风险。不点就停着等。
4. **会话侧边栏**:新建/切换/删除/重命名,历史持久化,重开还在。
5. **上下文面板**:当前代码工作区路径([切换目录])、知识库文档数([管理])、**Agent 记得关于你的事**(画像+准则,可编辑)。
6. **设置页 ⚙️**:
   - **API 服务商配置**(核心需求,不写死):LLM 与 embedding 的 `base_url`/`api_key`/`model`,预置火山豆包/DashScope/OpenAI/DeepSeek/Ollama 下拉快选 + 自定义;[测试连接] 验证;[保存]**热生效不重启**。
   - **安全设置**:命令白/黑名单编辑、工作区根目录、只读模式开关。
   - **Agent 参数**:max_iters、温度等。
7. **知识库管理页 📚**:拖拽上传 PDF/md/txt、文档列表、删除、重建索引、看索引进度。

### 一个完整使用故事

首次启动 → 弹设置页要求先配 API 服务商 → 选火山豆包填 key、embedding 选 DashScope → [测试连接] 通过 → 保存。右栏切换工作区到 `~/code/myproj`,拖入几篇文档到知识库。输入"删掉 utils.py 里没用到的函数" → Agent 流式回复:`run_command` grep(白名单自动跑)→ `read_file` → 分析出 2 个无用函数 → 弹待确认卡片带 diff → 批准 → `write_file` 改文件 → "已删除并跑测试确认没坏"。过程中你说"以后改代码先给我看 diff" → Agent `update_soul` 记下 → 右栏多一条,下次自动遵守。关掉重开,会话和画像都还在。

---

## 三、技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python + FastAPI + uvicorn | 复用 agent-study 的 common(build_client/ToolRegistry/run_agent)与 RAG |
| Agent 循环 | function calling(非文字 ReAct) | 流式版 run_agent,产 typed event |
| 前端 | React + Vite + TypeScript + shadcn/ui | 三栏聊天工作台 |
| 流式 | SSE(`data: json\n\n` + StreamingResponse) | 前端 fetch + ReadableStream 消费 |
| 会话存储 | JSONL 文件(追加式,可回放) | 每会话一个 .jsonl |
| 应用配置 | SQLite | API 服务商/安全/参数,可查询、热生效 |
| 向量库 | Qdrant(Docker) | 仅服务文档知识库,代码不建索引 |
| embedding | DashScope text-embedding-v3(1024维) | 配置可改 |
| IM(二期) | 飞书 lark-oapi 长连接 | 免公网,快速 ACK + 卡片流式 |

---

## 四、目录结构

```
superstar/
├── DEVELOPMENT_PLAN.md           # 本文件
├── README.md                     # (P6 写)使用与部署说明
├── docker-compose.yml            # (P6)后端 + 前端 + Qdrant
├── backend/
│   ├── pyproject.toml            # uv 管理
│   ├── .env.example              # 仅启动必需项(端口/DB路径/QDRANT_URL)
│   ├── run.py
│   ├── core/                     # 从 agent-study/common 移植的通用内核
│   │   ├── llm.py                # 动态客户端(按当前配置建,可热切换)
│   │   ├── tools.py              # Tool + ToolRegistry(Pydantic 校验+自愈)
│   │   └── loop.py               # run_agent + run_agent_streaming(流式 ReAct)
│   └── app/
│       ├── api/main.py           # FastAPI 实例、CORS、路由注册
│       ├── api/routes/
│       │   ├── chat.py           # POST /api/chat/stream (SSE)
│       │   ├── session.py        # 会话 CRUD
│       │   ├── kb.py             # 知识库 上传/列表/删除/重建
│       │   ├── approval.py       # 人在环路 批准/拒绝
│       │   └── settings.py       # 配置读写 + 测试连接
│       ├── agent/
│       │   ├── runtime.py        # 组装 system prompt(注入记忆)+ 跑流式循环 + 产 event
│       │   ├── tools/{fs,shell,rag}.py   # 工具实现
│       │   └── subagent.py       # 子 Agent 隔离(P6)
│       ├── services/
│       │   ├── rag_store.py      # RagStore:收敛 embed + Qdrant
│       │   ├── security.py       # 沙箱 + 命令分级 + 危险判定
│       │   ├── memory.py         # profile.md / soul.md 读写 + 注入
│       │   └── session_store.py  # JSONL 会话读写
│       ├── db/                   # SQLite(仅配置)：AppSetting 表
│       ├── models/schemas.py     # Pydantic 请求/响应 + SSE event
│       └── config.py             # pydantic-settings:启动必需项
├── frontend/
│   └── src/
│       ├── components/           # ChatPanel/MessageBubble/ToolCallCard/ApprovalDialog/
│       │                         #   DiffViewer/SessionList/ContextPanel/SettingsDialog/KbManager
│       ├── hooks/useChatStream.ts
│       ├── lib/api.ts
│       └── types.ts
└── data/                         # SQLite、会话 jsonl、profile.md、soul.md、上传文档
```

---

## 五、核心设计

### Agent 循环(runtime.py)
流式 ReAct:`run_agent_streaming` 生成器,每步 yield typed event —— `text_chunk`(逐 token)/`tool_call`/`tool_result`/`approval_required`(命中危险操作,暂停)/`done`/`error`。**核心与输出通道解耦**:Web 端 SSE 透传;二期飞书适配器消费同样 event 渲染成卡片。工具调用沿用 M8-2 分片重组,执行走 ToolRegistry(复用 Pydantic 校验+自愈)。带 max_iters 防死循环。

### 工具(函数签名 `def f(args: XxxArgs) -> str`,注册进 ToolRegistry)
- `read_file` / `write_file`(先产 diff,写前触发 approval)/ `list_dir` / `glob`
- `run_command`(经 security 分级)
- `search_kb`(检索文档知识库,配反幻觉 prompt)
- `dispatch_subagent`(P6:独立上下文跑子任务)

### 安全(security.py,头号难点)
- **沙箱**:WORKSPACE_DIR / KB_DIR 两个允许根,`resolve()` 后必须落在根内(防 `../../` 穿越)。
- **命令三级**:白名单(grep/ls/git status/cat 只读)自动放行;黑名单(rm -rf/sudo/curl|sh)直接拒;灰名单 → `approval_required` 等确认。
- **人在环路**:approval_required → 前端弹窗(写文件带 diff)→ 批准 → 恢复执行。

### 配置动态化(不写死)
- 启动必需项(端口/DB/QDRANT_URL)走 `.env`;业务配置(LLM/embedding/白黑名单/工作区/参数)存 SQLite `AppSetting`,配置页 CRUD **热生效**。
- `core/llm.py` 提供 `get_llm_client()`,读当前配置建客户端、配置变更后重建缓存(替代写死读 env)。
- `POST /api/settings/test` 用填入配置发最小请求验证连通。
- key 存本地 SQLite;API 返回时脱敏(`sk-***1234`);日志绝不打印 key。

### 记忆/个性化(memory.py)
- `profile.md`(用户画像)+ `soul.md`(Agent 准则),本地 markdown。开会话注入 system prompt;`update_profile`/`update_soul` 工具让 Agent 沉淀。反思式自我完善(会话结束回顾)放二期。

### 会话存储(session_store.py)
- 每会话一个 JSONL 文件(消息逐行追加),可回放调试。会话索引 + 元数据存 SQLite 或一个 index.json。

---

## 六、分阶段计划

### 第一版(本地 Web 最小可用闭环)—— 6 个里程碑,逐个跑通再下一个

- **P1 骨架 + 配置**:FastAPI 分层 + SQLite AppSetting + settings 路由(读写 + 测试连接)+ 动态 llm_client + 单轮 `/api/chat`。**验证**:存 LLM 配置→测试连接通→单轮对话走该配置。
- **P2 会话**:JSONL session_store + 会话 CRUD + 多轮上下文。**验证**:建会话→多轮→落盘→重启还在。
- **P3 工具 + 安全**:fs/shell/rag 工具 + security(沙箱/白黑灰)+ approval(先命令行模拟)+ RagStore + 知识库上传检索。**验证**:grep 代码、读文件、写文件触发确认、`../../`越界与 rm -rf 被拒、检索带来源。
- **P4 流式**:run_agent_streaming + SSE + typed event。**验证**:`curl -N` 看逐 token + tool + approval 事件流。
- **P5 前端**:React+Vite+shadcn 全套(三栏 + 工具卡片 + 确认弹窗 + diff + 会话 + 设置页 + 知识库页)。**验证**:浏览器走通完整使用故事。
- **P6 收尾**:画像/soul 注入与更新 + 子 Agent 隔离 + M9 工具调用评测 + docker-compose + README。**验证**:多轮后"记得"偏好;跑评测出通过率;`docker compose up` 起得来。

### 第二版(增量,架构预留接口)
飞书长连接(快速 ACK + 卡片流式更新 + 按钮确认)、MCP 真实 server 接入(前缀路由防撞名)、Skill 渐进式披露、反思式自我完善、checkpoint/回滚(视工作量)。

---

## 七、覆盖的 Agent 知识点

RAG 全链路 / function calling 工具调用 / ReAct 循环 / 多轮对话与上下文管理 / 流式输出(SSE) / Prompt 工程(反幻觉) / 评测(工具调用正确率+回归) / 长期记忆与个性化 / 执行安全与人在环路 / 子 Agent 隔离 / (二期)MCP / Skill / IM 集成。

## 八、明确不覆盖(诚实边界)
高并发/性能压测、可观测性、消息队列、缓存层、CI/CD、鉴权多租户、模型训练/微调/推理部署。个人自用项目不需要,面试如实说明。
