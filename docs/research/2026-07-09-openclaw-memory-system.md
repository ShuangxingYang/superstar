# OpenClaw 记忆系统调研 —— 二期增强参考

> 类型:调研 / 参考(非 spec、非 plan)。写于 2026-07-09,配合 P5 第一版记忆(profile.md + soul.md)完成后的技术探讨。
> 用途:第一版记忆是「极简够用型」,本文档记录 OpenClaw(小龙虾)记忆系统的完整设计,作为**二期记忆增强**的对标参考与演进路线依据。
> ⚠️ 资料来源为公开文档/社区深度分析(见文末),非一手源码;个别细节标注了版本差异。真要动手时以当时 OpenClaw 最新文档/源码为准。

---

## 0. 一句话对比

我们第一版:**2 个 markdown(profile/soul)+ 每轮全量注入 system prompt**。
OpenClaw:**一组分层 markdown + SQLite 索引 + 向量/关键词混合检索 + 时间衰减 + 压缩前刷新 + 多级降级**。

差别不是「谁更好」,而是**场景不同**:OpenClaw 的复杂度是被「多渠道、7×24 长运行、海量历史、上下文会遗忘」逼出来的;我们是本地单用户、记忆短、还没上下文压缩,极简方案恰好匹配。

---

## 1. OpenClaw 记忆系统的五层设计

核心哲学与我们一致:**文件即记忆,模型只记住写在磁盘上的东西,没有隐藏状态**。在此之上叠了五层工程。

### ① 分层文件结构(类比人类记忆)

| 文件 | 作用 | 加载策略 | 我们的对应 |
|---|---|---|---|
| `MEMORY.md` | 长期稳定事实/偏好 | 会话开始**总是加载**;默认不存在需手动建 | ≈ `profile.md` |
| `SOUL.md` | Agent 人格准则 | 会话开始总是加载 | ≈ `soul.md` |
| `memory/YYYY-MM-DD.md` | **每日日志,只追加** | 只自动加载**今天+昨天**;更久靠检索 | 无(第一版没有日志层) |
| `DREAMS.md`(可选) | 供人审阅的摘要/回填 | 按需 | 无 |

类人三层:**Context(工作记忆)→ Compaction(短期记忆)→ Memory Files(长期记忆)**。

### ② SQLite 索引 + 混合检索(与我们最大的分野)

把 markdown 和历史会话(JSONL)切块、embedding,存进**每个 agent 独立的 SQLite**(`sqlite-vec` 扩展),提供两个工具:

- `memory_search` —— 语义检索(**向量 + BM25 关键词混合**),返回片段 + 文件行号。
- `memory_get` —— 按文件 + 行范围精确读(通常在 search 之后用)。

### ③ 时间衰减 + 常青记忆(最精巧的一处)

借遗忘曲线:`衰减得分 = 原始得分 × e^(-λ×天数)`,默认半衰期 30 天。但区分两类:

- **常青知识**(`MEMORY.md`、`patterns.md`):承载长期核心认知,**不衰减**。
- **时效信息**(`2024-01-15.md`):某天的具体细节,**正常衰减**。

模拟人类双重记忆:「你忘了上周二午饭吃啥,但没忘怎么骑车」。

### ④ 压缩前的「记忆刷新」

上下文窗口快满会触发 compaction(把早期对话摘要成一段,细节永久丢失)。OpenClaw 在压缩**之前**,悄悄发一条无声提示给 Agent:「把重要的写进 `memory/今天.md`」。用户无感,但信息被抢救下来。

触发阈值示例:200K 窗口、默认配置(20K reserve、4K soft threshold)下,约在 **176K token** 触发。

### ⑤ 多级降级 + 隐私边界

- **降级链**:没 API key / 没本地模型 / 没向量扩展 → 退到 **SQLite FTS5 全文检索**,绝不崩。
- **隐私**:`MEMORY.md` **只在私聊加载,群聊永不加载**(实测群里问服务器 IP 说不知道,私聊立刻答)。这是**多渠道**场景才需要的威胁模型。

---

## 2. `memory_search` 到底怎么实现的(不是纯向量)

**是「向量 + 关键词」混合检索(hybrid search)**,两条腿:

1. **向量腿**:query 做 embedding,用 **cosine 相似度**在 `chunks_vec`(vec0 虚拟表)找语义最近的 chunk。
2. **关键词腿**:SQLite 原生 **FTS5 + BM25**,精确关键词匹配(支持 CJK trigram 分词)。

融合与后处理链:

```
向量分 + 关键词分 → 加权合并(vectorWeight + textWeight 归一到 1.0)
                  → 时间衰减(temporal decay)
                  → 排序 → MMR 去冗余 → Top-K
```

**为什么要两条腿**:纯向量搜不到 `REDIS_PASSWORD` 与「redis 密码配置」的关联(语义上不像);纯关键词又搞不定换个说法问同一件事。

**检索时的双路径**:
- **Fast path**:`sqlite-vec` 在库内 `chunks_vec v JOIN chunks c`,用 `vec_distance_cosine` 排序取 Top-K。
- **Safe path**:`sqlite-vec` 加载失败 → 从 SQLite 捞候选,**纯 JS 在内存暴力算 cosine**。慢但不崩。

> 对比我们:`search_kb` 是**纯向量召回 + rerank 精排**(Qdrant),没有 BM25 关键词腿。

---

## 3. 历史日志「什么时候」被向量化(关键时机)

**不是写入即刻向量化,而是「文件监听 → 打脏标记 → 防抖 → 异步后台 sync」。**

### 触发时机(四个触发点)

1. **文件 watcher**(chokidar 监听 `memory/`、`MEMORY.md`):文件一变 → 标记 **dirty**,**防抖 1.5s**。
2. **会话转录(JSONL)** 用 **delta 阈值**触发后台 sync:攒够约 **100KB 或 50 条消息**才触发(会话进行中防抖 5s)。← 这就是「历史对话何时向量化」的答案:**攒够一批再后台索引,不是每句都 embed**。
3. **会话启动时** sync 一次。
4. **search 触发时** + **定时间隔**。

### 索引流水线(触发后)

```
标记 dirty → 防抖 → 后台异步 sync
  → 切块(400 token/块,80 token 重叠)
  → 批量 embedding(并发 4,每批最多 8000 token,失败重试 3 次)
  → 写入 chunks_vec(向量)+ chunks_fts(关键词)
```

### 两个聪明的工程细节

- **`files` 表记 mtime/size/内容哈希** → 没变的文件**跳过重索引**,只增量处理变动的。
- **原子重建**:新索引先建临时文件,建好再整体 swap,避免重建到一半损坏。

### 何时「全量重建」

索引存指纹(embedding provider + model + endpoint + 切块参数)。这些一变旧向量不兼容 → 新版**暂停向量搜索 + 报「索引身份」警告**,手动 `openclaw memory index --force` 重建(老版自动重建)。
> 与我们交接文档 §7「换 embedding 模型维度变了要重建 Qdrant」同理。

### SQLite 存储结构

`~/.openclaw/memory/{agentId}.sqlite`,四个核心表 + 两个可选虚拟表:
- `files`:mtime/size/哈希(跳过未变文件)
- `chunks`:真相源——文本 + 行范围 + JSON 序列化的 embedding
- `chunks_vec`(虚拟,vec0):二进制浮点向量,`sqlite-vec` 在时启用
- `chunks_fts`(虚拟):FTS5 关键词索引

---

## 4. 逐维度对比

| 维度 | Superstar 第一版 | OpenClaw |
|---|---|---|
| 存储 | 2 个 markdown | 一组 markdown + SQLite 索引 |
| 检索 | 全量注入(记忆短,直接进 system) | 向量+BM25 混合检索,按需召回 |
| 会话历史 | 靠工具 grep/read 现查 | 切块 embed 进索引,可语义回忆 |
| 时间维度 | 无(全量覆盖) | 指数衰减 + 常青区分 |
| 压缩保护 | 无(还没做 compaction) | 压缩前记忆刷新 |
| 分层 | profile / soul 两层 | 长期 / 每日日志 / 人格 / 梦境多层 |
| 向量化时机 | 无(不向量化记忆) | watcher 打脏 → 防抖 → 后台批量 embed |

---

## 5. 演进路线:如果二期要「往 OpenClaw 靠」

按性价比排序,**什么时候该加什么**:

1. **每日日志 `memory/YYYY-MM-DD.md`(只追加)** —— ✅ **已完成(2026-07-10)**。成本最低、收益明显。让 Agent 记「今天做了啥、观察到啥」,开会话加载今天+昨天。这一层是引出后面检索/衰减的前提。
   - 接入点:我们没有 `/new`/`/reset`,最自然是在 **`session_store.create()` 新建会话时**加载,或每会话开始定一次(**别每轮读**,日志会随对话变)。
   - 滚动窗口:本地单用户日志增长慢,「今天+昨天」或「最近 N 条」都行,不必照搬。
2. **`MEMORY.md` 长期记忆层(与 profile 分家)** —— ✅ **已完成(2026-07-10)**。把「跟人无关的客观事实/既定结论」(项目约定、技术栈、架构决策)从 profile(用户个人信息)里拆出来,单独放 `data/MEMORY.md`,Agent 用 `update_memory` 沉淀、开会话总是注入。四工具边界:profile=用户个人信息 / memory=客观稳定事实 / soul=自身准则 / log=今天的流水。
   - **下一候选 =「定时自动从日志蒸馏进 MEMORY(dreaming sweep)」**:当前 MEMORY 只靠用户显式触发提炼(「整理下最近日志到长期记忆」),用户忘了触发就永不沉淀。下一步做定时任务,自动扫近期日志→提炼客观事实→更新 MEMORY,减少手动触发的遗漏。这是 OpenClaw「压缩前记忆刷新」的轻量近亲(我们还没 compaction,故先用定时扫代替压缩前钩子)。
3. **压缩前记忆刷新** —— **前提是先有 compaction(M12)**。顺序上应等上下文裁剪做了再说。
4. **混合检索(BM25 + 向量)** —— 只有当记忆多到「全量注入撑爆 context」时才需要。我们**已有 RAG 基建(Qdrant)可复用**,不必像 OpenClaw 引 SQLite;但要补 BM25 关键词腿才算「混合」。
5. **时间衰减** —— 最后考虑,且依赖①先有按日期堆积的记忆。

**核心判断**:第一版记忆的极简是**恰当**的,不是欠债。上述每一层都应在「对应的痛点真实出现」时才加(日志堆积了才要衰减、context 撑爆了才要检索、有压缩了才要压缩前刷新),避免过度工程。

---

## 参考来源

- [OpenClaw 记忆系统架构深度解析:从 Markdown 到混合检索 - 知乎](https://zhuanlan.zhihu.com/p/2005943466006438841)
- [Memory overview · OpenClaw 官方文档](https://docs.openclaw.ai/concepts/memory)
- [Builtin memory engine · OpenClaw](https://docs.openclaw.ai/concepts/memory-builtin)
- [Memory configuration reference · OpenClaw](https://docs.openclaw.ai/reference/memory-config)
- [Local-First RAG: Using SQLite for AI Agent Memory with OpenClaw - PingCAP](https://www.pingcap.com/blog/local-first-rag-using-sqlite-ai-agent-memory-openclaw/)
- [Deep Dive: How OpenClaw's Memory System Works - Study Notes](https://snowan.gitbook.io/study-notes/ai-blogs/openclaw-memory-system-deep-dive)
- [Memory & Search - DeepWiki](https://deepwiki.com/openclaw/openclaw/3.4.3-memory-and-search)
- [6.3 记忆机制:写入、检索与失效 - OpenClaw Guide](https://yeasy.gitbook.io/openclaw_guide/di-er-bu-fen-jin-jie-shi-yong/06_context_memory/6.3_memory_mechanism)
