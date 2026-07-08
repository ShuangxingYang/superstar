# Superstar

本地、单用户、无鉴权的「干活型」Agent。参考 Claude Code 的内核思路(工具调用 + 审批 + 会话持久化),
后端 FastAPI,前端 React + Vite,零数据库(会话存 JSONL、配置存 `data/config.json`、RAG 走 Qdrant)。

## 能力

- **多轮对话**:流式输出(SSE),会话持久化到 `data/sessions/*.jsonl`
- **工具调用**:读文件 / grep / glob / 写文件 / 执行命令,写与执行走沙箱 + 三级命令名单(白自动 / 黑拒绝 / 灰审批)
- **审批回合**:灰名单命令暂停等前端批准,diff / 命令预览后再放行
- **RAG 知识库**:文档上传 → 切块 → embedding → Qdrant;检索两阶段(向量召回 → rerank 精排),回答带来源、反幻觉
- **设置页**:LLM / embedding 的 key、模型、安全名单在前端随时改,热生效

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.11 · FastAPI · [uv](https://docs.astral.sh/uv/) |
| 前端 | React 19 · Vite 6 · TypeScript · Tailwind + shadcn |
| 向量库 | Qdrant(Docker) |

## 本地跑起来

### 0. 前置

- Python 3.11+、[uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node 18+
- Docker(仅 RAG 知识库功能需要)

### 1. 后端

```bash
cd backend

# 装依赖
uv sync

# 生成业务配置:复制模板,然后填入你自己的 key
cp config.example.json data/config.json
#  → api_key / model 也可留空,启动后到前端「设置页」填写

# (可选)覆盖启动配置(端口 / 数据目录 / Qdrant 地址)
cp .env.example .env

# 启动(默认 http://127.0.0.1:8000)
uv run python run.py
```

### 2. 前端

```bash
cd frontend
npm install
npm run dev        # 默认 http://localhost:5173
```

### 3. Qdrant(用到知识库再起)

```bash
docker run -p 6333:6333 -v "$(pwd)/qdrant_storage:/qdrant/storage" qdrant/qdrant
```

## 配置说明

密钥类配置**不入库**,分两处:

- `data/config.json` —— 业务配置(LLM / embedding 的 `api_key`、模型、安全名单、RAG 参数),前端设置页可改、热生效。**已被 `.gitignore` 忽略**。模板见 `backend/config.example.json`。
- `backend/.env` —— 启动配置(端口 / 数据目录 / Qdrant 地址),**非敏感**,不填有默认值。模板见 `backend/.env.example`。

多端维护:新机器 `git clone` 后,按上面第 1 步 `cp` 两个模板再填 key 即可,密钥不会跟着仓库走。

## 测试

```bash
cd backend && uv run pytest -q        # 后端
cd frontend && npm run build          # 前端(tsc + vite)
```
