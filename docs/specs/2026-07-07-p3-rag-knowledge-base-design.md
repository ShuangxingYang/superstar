# P3 RAG 知识库设计

> 里程碑:给 Agent 装一只"查资料的手"。文档入库(loaders→切块→embed→Qdrant)+ `search_kb` 两阶段检索(向量召回→rerank 精排)带来源 + 反幻觉 + 前端知识库管理页。

## 一、目标与范围

**做什么**:让 Agent 能检索一个独立的文档知识库,基于检索到的真实片段回答、带来源、库里没有就老实说没有。

**范围**:后端 RAG 核心 + 前端知识库管理页,一步跑通完整使用故事(配 embedding → 拖入文档 → 对话里问库内/库外问题)。

**不做(留后面 / 二版)**:飞书 API 直连(先导出成 md/pdf 再入库)、OCR/扫描件 PDF、混合检索(BM25+向量 RRF)、真流式灌库进度、索引进度条、jieba 中文分词、父子分块、query 改写、**切块尾块合并(min_chunk_size)**——见 4.2 已知局限。

## 二、关键决策总账

| 维度 | 定案 | 理由 |
|---|---|---|
| 里程碑范围 | 后端核心 + 前端管理页 | 一步跑通使用故事 |
| 文档格式 | `.md/.txt/代码` 直读 + `.pdf` 走 pypdf | 覆盖技术文档/笔记/代码;PDF 纯代码抽取不依赖 LLM |
| 格式扩展 | loaders 适配器层(来源→统一 Document),预留飞书/OCR | 业界通行的适配器三层骨架,换来源只加一个 loader |
| 切块 | 递归字符切(`\n\n→\n→。→空格` 优先级找边界 + 重叠),size/overlap 可配 | M3 实测:按语义边界切召回优于纯定长斩断术语 |
| 向量库 | Qdrant;存在即复用**绝不自动删**;维度不一致**报错提示手动重建**;连不上返回**友好错误不崩流** | 数据安全洁癖 + 局部故障不扩散 |
| embedding | dashscope `text-embedding-v3`,1024 维(复用现有 config) | M3 沿用,和对话 LLM 分开鉴权 |
| search_kb | **只做检索**,返回 top-k 片段+来源;A+G 交主循环 LLM | 工具是"手"只取数据,生成是主循环大脑的事,避免 LLM 套 LLM |
| 反幻觉 | system prompt 约束"没有就说没有" + 返回带来源凭证 | M3 灵魂 prompt 的 Agent 版 |
| rerank | 两阶段:向量召回 top-N → dashscope `gte-rerank` 精排 top-k;**API 失败降级**到向量顺序 | 复用现有 dashscope key;rerank 是优化项挂了不该拖垮检索 |
| 前端 | 渐进引入 shadcn(装 Tailwind,新页用,老组件不动) | 面试展示观感;不铺战线重写老组件 |
| 上传 loading | 假进度条(匀速爬 90% + 回来跳 100%) | 纯前端,成本低,用户无感差异;真流式进度与 chat SSE 是同一知识点,边际收益低 |

## 三、架构分层

数据流:

```
灌库:  上传/指定 → loaders(取文本+来源) → chunker(递归切块) → embed → Qdrant upsert
检索:  query → embed → Qdrant 召回 top-N → rerank 精排 top-k → 带来源片段
```

**新增文件按"这段代码需不需要知道自己在 Agent 对话循环里"归位**(延续项目 services/ vs agent/ 判据):

| 文件 | 层 | 职责 |
|---|---|---|
| `services/loaders.py` | services | 来源 → `Document(text, source)`,按扩展名分发(`.pdf`→pypdf,其余 read_text) |
| `services/chunker.py` | services | 递归字符切块,纯函数 |
| `services/rag_store.py` | services | 收敛 embed + Qdrant 建/判/灌/删/召回 + rerank 精排 + 降级 |
| `agent/tools/rag.py` | agent | `search_kb` 工具:调 rag_store.search,拼来源返回;注册进 registry |
| `api/routes/kb.py` | api | 知识库 上传/列表/删除/重建/状态 路由 |

**依赖方向**(严格单向):`agent/tools/rag.py` → `services/rag_store.py` → `services/loaders.py` / `services/chunker.py`。services 三者都不反向依赖 agent。

**复用现有地基**:`config.py` 的 `qdrant_url`;`config_store` 的 `embedding` 配置和 `security.kb_dir`;`services/llm.py` 的"动态客户端工厂"模板(rag_store 的 embed/rerank 客户端照它建、按 (base_url,key) 缓存)。

**config 新增字段**(向后兼容,DEFAULTS 补齐):
- `embedding.dimension`(默认 1024)—— 建集合/维度校验用
- `rag.chunk_size`(默认 500)、`rag.overlap`(默认 80)
- `rag.top_n`(召回,默认 20)、`rag.top_k`(精排返回,默认 5)
- `rag.rerank_model`(默认 `gte-rerank`)—— 空则跳过 rerank 直接用向量 top_k

## 四、组件详细设计

### 4.1 loaders.py —— 适配器层

```
Document = dataclass(text: str, source: str)      # 统一中间表示

load_document(path) -> Document
  内部 _LOADERS = {".pdf": _load_pdf}              # 扩展名 → 抽取器映射表
  命中走对应 loader;未命中默认 read_text(encoding, errors="replace")
  _load_pdf: pypdf 逐页 extract_text → 拼接 → 简单清洗(合并多余空行/去纯空白页)
  source = 相对 kb_dir 的路径
```

- **业界对标**:LangChain DocumentLoader / LlamaIndex Reader 的最小内核——适配器模式,每格式一 loader,吐统一 Document,下游与来源解耦。只做当下需要的格式。
- **飞书扩展位**:以后加"飞书 API loader"只是往映射表注册一项,切块/embed/检索不动。

### 4.2 chunker.py —— 递归字符切块

```
split(text, chunk_size, overlap) -> list[str]
  按分隔符优先级 ["\n\n", "\n", "。", " ", ""] 逐级找切点:
    尽量在段落边界切;段落仍超长则退到换行;再超长退到句号;最后硬切
  相邻块保留 overlap 字符重叠(防答案落在接缝被割裂)
```

- 最该密测:块数、重叠正确、不斩断自然边界、超长无边界文本能硬切、空文本返回空。

**已知局限(贪心切块的孤儿尾块)**:贪心累加「能塞就塞、塞不下开新块」,当 buf 已接近 chunk_size 时,末尾可能落下一个很短的尾块(如 buf=99% 时来个小 piece → 小 piece 独立成块)。这是递归字符切块(同 LangChain `RecursiveCharacterTextSplitter` 内核)的固有性质,非实现错误。当前不处理,理由:①`_apply_overlap` 会给该尾块补前一块尾部 overlap 字符(默认 80),使其仍带上下文、检索时不至于失去意义;②本项目文档量不大,冗余尾块影响边际很小。二版若需更干净的切块,可加 `min_chunk_size`:封箱时尾块小于下限则并回前一块(注意防「并回后超 chunk_size」「连续多个小块」两个边界),并补对应测试。overlap=0 时该兜底失效,是这条局限最明显的场景。

### 4.3 rag_store.py —— 检索设施(收敛散落 7 份 embed)

模块级函数(非 class:无跨调用共享内存状态,客户端按 llm.py 那套模块级缓存):

```
index_document(path) -> dict         # {source, chunks} 灌库结果
search(query, top_k=None) -> list[(text, source, score)]
list_documents() -> list[(source, chunk_count)]
delete_document(source) -> int       # 删除的块数
rebuild() -> dict                    # 清空集合重建(显式,前端按钮触发)
stats() -> dict                      # {documents, chunks, dimension}
```

**集合管理(三坑逐一堵死)**:
1. **建/判/复用**:`_ensure_collection` 内 `collection_exists` 判断,不存在才按 `(dimension, COSINE)` 建;**绝不 delete**(除非显式 rebuild)。
2. **维度漂移**:`_ensure_collection` 读集合现有维度,与 config 的 `embedding.dimension` 比,不一致抛 `RagStoreError("知识库是用 X 维建的,当前配置 Y 维,请到知识库页重建索引")`。维度以 config 写死为准(简单直接,同 M3 硬编码 1024),不做探针探测。
3. **Qdrant 连不上**:每个操作 catch 连接错 → 抛 `RagStoreError("知识库服务未启动,请先 docker start qdrant")`。

**point id**:`source + 块序号` 哈希成稳定 id。好处:重复灌同一文档 = 覆盖(upsert 语义)而非堆积;`delete_document` 按 source 精确删。payload 存 `{text, source}`。

**两阶段检索**:
```
search:
  embed(query) → qdrant.query_points(limit=top_n) 召回 top_n 候选
  若 rerank_model 非空:调 dashscope rerank(query, [候选text]) 重排 → 取 top_k
    rerank API 失败 → catch,降级用向量召回顺序取 top_k(不抛,检索照常返回)
  返回 [(text, source, score), ...]
```

**rerank 怎么调**(重要,与 embed 不同):rerank **不是 openai SDK 的标准端点**(OpenAI API 本身无 rerank 能力),只能走 dashscope 专用接口。为不引入整个 `dashscope` SDK(延续依赖越轻越好),用**标准库 HTTP POST** 打 dashscope rerank 端点(`.../api/v1/services/rerank/text-rerank/text-rerank`,body 传 model + query + documents,header 带 embedding 的 api_key),解析返回的 `results[].relevance_score` 重排。端点/model 从 config 的 `rag.rerank_model` + embedding 的 base_url 家族推导。这块封装在 rag_store 内部一个 `_rerank(query, docs)` 函数,失败即降级。

### 4.4 agent/tools/rag.py —— search_kb 工具

```
SearchKbArgs(query: str, top_k: int = 默认)
search_kb(args) -> str:
  results = rag_store.search(query, top_k)
  空 → "知识库里没有相关内容"
  否则拼: 【片段N】{text}\n[来源: {source}]  以空行分隔
  RagStoreError 由 ToolRegistry 自愈兜成 tool_result 喂回(不崩流)
```

注册进 registry,描述引导模型"需要引用文档/知识库内容时用它检索"。

**反幻觉(system prompt 追加)**:
> 当你用 search_kb 查资料时:只依据检索到的片段回答;片段里没有的,明确说"知识库里没有相关内容",不要编;回答时带上来源。

### 4.5 api/routes/kb.py —— 知识库路由

| 路由 | 行为 |
|---|---|
| `POST /api/kb/upload` | 存文件到 kb_dir → index_document → 返回 {source, chunks} |
| `GET /api/kb/list` | list_documents |
| `DELETE /api/kb/{source}` | delete_document + 删 kb_dir 下文件 |
| `POST /api/kb/rebuild` | rebuild(重扫 kb_dir 全部文件重灌) |
| `GET /api/kb/stats` | stats |

`RagStoreError` → 返回 503 + 明确 message 给前端提示。

### 4.6 前端知识库管理页

- 渐进引入 shadcn:装 Tailwind + 初始化 shadcn + Vite 路径别名 `@/`;新增知识库页用 shadcn 组件;老组件(App.css/SessionList/ToolCallCard)维持手写 CSS 不动。
- `KbManager` 组件:上传区(拖拽/选择 .md/.txt/代码/.pdf)+ 已入库文档列表(source + 块数 + 删除)+ 重建索引按钮 + 状态(篇数/块数)。
- 上传假进度条:匀速爬到 90%,请求回来跳 100%,纯前端 `useState`,不改后端接口。
- `lib/api.ts` 加 `uploadKb / listKb / deleteKb / rebuildKb / kbStats`。入口挂侧边栏「📚 知识库」。

## 五、错误处理(错误变返回值、局部故障不扩散)

| 出错点 | 处理 | 去向 |
|---|---|---|
| Qdrant 连不上 | `RagStoreError` | 检索→ToolRegistry 自愈成 tool_result;灌库→路由 503 |
| embedding 维度漂移 | `_ensure_collection` 抛明确错(提示重建) | 同上分流 |
| PDF 抽取失败/空文本 | loaders 返回空,index_document 跳过并回报"没抽到文本" | 路由如实告诉前端 |
| rerank API 失败 | **降级**:catch,用向量召回顺序取 top_k | 检索照常返回,不失败 |
| embed API 失败 | 抛错(硬依赖) | 自愈/路由分流 |

**RAG 挂不影响读写文件/跑命令**——它们不依赖 Qdrant/embedding。

## 六、测试策略(TDD,不依赖真网络/真库)

1. **纯函数直接测**:
   - `chunker.split`:块数/重叠/不斩断边界/超长硬切/空文本。最密。
   - `loaders.load_document`:临时 .md/.txt 断言文本+source;小 fixture PDF 断言能抽出字。
2. **外部依赖 mock**:
   - `rag_store`:monkeypatch embed/rerank/Qdrant client。测灌库调用顺序、维度不一致抛错、Qdrant 连不上抛 RagStoreError、rerank 失败降级到向量顺序、point id 稳定(重灌覆盖)。
   - `search_kb`:mock rag_store.search,断言拼接带来源、空结果文案。
   - `kb` 路由:mock rag_store,断言各路由行为 + RagStoreError→503。
3. **手动冒烟脚本**(不进 pytest):真起 Qdrant + 真 key,灌一篇、检索一次,验证端到端。对标各里程碑 curl 验证。

## 七、验证方式(端到端使用故事)

- 设置页配 embedding key(dashscope)→ 知识库页拖入一篇技术文档 → 列表显示 source+块数。
- 对话问**库内**问题:Agent 调 search_kb,回答带来源、内容准确。
- 对话问**库外**问题:Agent 老实说"知识库里没有相关内容",不编。
- 换 embedding 到不同维度模型 → 检索/灌库报维度不一致,提示重建;点重建后恢复。
- 停掉 Qdrant → RAG 操作友好报错,但读写文件/跑命令照常。
- rerank key 失效 → 检索降级仍返回向量结果,不崩。

## 八、依赖变更

- 后端 pyproject 加:`qdrant-client`、`pypdf`。
  - **embedding** 复用现有 openai SDK(dashscope 的 embedding 走 openai 兼容接口),无需新增。
  - **rerank** 不走 openai SDK(OpenAI API 无 rerank 能力),用**标准库 HTTP POST** 打 dashscope rerank 端点,不引 `dashscope` SDK。
- 前端加:`tailwindcss` + shadcn 相关(渐进,仅新页用)。
