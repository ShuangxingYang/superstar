# P1 会话持久化 —— 设计文档

> 里程碑目标(DEVELOPMENT_PLAN L199):JSONL `session_store` + 多轮上下文 + 左栏会话侧边栏(新建/切换/删除/重命名)。
> 验证标准:多轮对话能接上文、切会话、**重启后端后历史仍在**(前后端都可见)。

P0 已打通「无状态单轮流式」。P1 让 Agent **记事**:会话能存/切/删/重命名、多轮接上下文、重开还在。全程零数据库,配合 JSONL 追加存储 + 一份 index.json 元数据缓存。

---

## 一、关键设计决策(逐条与用户压测后的定论)

1. **单条消息记录格式 = OpenAI messages 原样 + 薄信封。** 每行 `{"ts", "message": {...OpenAI 消息...}}`。用嵌套而非摊平,避免 `message` 未来长字段(`tool_calls`/`tool_call_id`)与信封字段撞名;喂模型时把整个 `message` 抠出即用,零转换。这直接兑现「人在环路=状态存在 messages 里」的决策:P2 的「未回答 tool_call」天然是消息的一部分,存储层零改动。
2. **会话列表 = index.json 元数据缓存(非扫目录)。** `.jsonl` 是消息的唯一真相;`index.json` 是可重建的元数据缓存,存 `[{id,title,created_at,updated_at}]`。好处:列表读一个文件、不依赖脆弱的 `mtime`、`.jsonl` 只剩纯消息行(不用 meta 行折叠技巧)。代价:双写一致性,用「写序防幽灵 + 锁 + 原子写 + 可重建」收住(见三)。
3. **默认标题 = 首条用户消息截断**(前 ~20 字),用户可手动重命名(改 index 条目)。零额外成本、即时;不引入 LLM 起名的额外调用与时机处理。
4. **多轮上下文 = 全量喂,先埋裁剪钩子。** 每轮把整个会话历史拼进 messages。留一个 `_fit_context(messages)` 占位函数(P1 原样返回),真超长了再接 M12,不在 P1 引入滑窗复杂度。
5. **会话创建 = 懒创建(首句时才落盘)。** 「新建」是纯前端动作(清空面板);首句 `/chat/stream` 不带 `session_id` → 后端 `create()` 落盘 + 生成 id → SSE 首个 `session` 事件回传 id + title。像 ChatGPT,不产生空会话。代价:SSE 协议新增一种 `session` 事件。

---

## 二、磁盘布局 & 记录格式

```
data/sessions/
  index.json                 # 元数据缓存:[{id,title,created_at,updated_at}]
  <session_id>.jsonl         # 每会话一个文件,只存 message 行,追加写
```

- `session_id`:`uuid4().hex`(文件名安全、不撞)。
- `.jsonl` 每行(只有一种):
  ```jsonl
  {"ts":"2026-07-03T13:20:01Z","message":{"role":"user","content":"帮我看下 utils.py"}}
  {"ts":"2026-07-03T13:20:03Z","message":{"role":"assistant","content":"好的,我先搜一下…"}}
  ```
- `index.json`:
  ```json
  [
    {"id":"ab12…","title":"帮我看下 utils.py…","created_at":"2026-07-03T13:20:01Z","updated_at":"2026-07-03T13:20:03Z"}
  ]
  ```
- 时间统一 ISO8601 UTC 字符串。

**为什么 `.jsonl` 是追加写、不用 config.json 那套 tmp+replace 原子写?**
JSONL 是逐行 `append`,风险从「写坏整个文件」降级为「最后一行可能写一半」;读时 `try/except` 跳过解析失败的行即可。而 `index.json` 是整体覆盖写,所以它需要 tmp+replace 原子写 + 锁(复用 config_store 心法)。

---

## 三、index.json 一致性策略(本里程碑头号难点)

**心法:`.jsonl` 是真相,`index.json` 是缓存。** 两条原则把双写风险收成「无害」:

1. **写序防幽灵**——让任何中途崩溃只退化成「孤儿文件」(存在但不在列表,看不见、无害),绝不留「幽灵条目」(列表有、文件没了,点进去 404):
   - **建会话**:先建 `.jsonl`,**最后**写 index 条目。
   - **删会话**:**先**删 index 条目,再删文件。
   - 记法:**index 条目 = 提交点,建时最后加、删时最先去。**
2. **可重建兜底**:`rebuild_index()` 扫 `data/sessions/*.jsonl` 重建 index(标题回退成首条 user 消息)。index.json 丢失/损坏时调用;自定义重命名会丢,个人自用可接受。

`index.json` 的读-改-写用 `threading.Lock` 串行 + tmp/`os.replace` 原子写,抽成共用 helper。

---

## 四、模块与接口

### `services/atomic_json.py`(新增,共用小工具)

把 config_store 里的原子写抽出来,两处复用(DRY):

```python
def read_json(path: Path, default): ...          # 不存在返回 default;解析失败也返回 default
def write_json_atomic(path: Path, data): ...      # tmp 写 + os.replace 覆盖;失败清理 tmp
```
> config_store 的 `update()` 顺带切到用该 helper(行为不变,去重)。

### `services/session_store.py`(新增,P1 核心,纯文件 IO,好测)

| 函数 | 职责 | 一致性要点 |
|---|---|---|
| `create() -> str` | 生成 id、建空 `.jsonl`、**最后**加 index 条目(title 暂空、时间戳) | 提交点最后写 |
| `append_message(sid, message: dict) -> None` | 追加 `{ts,message}` 行;首条 user 消息时给 index 落 title;每次 bump `updated_at` | index 写走锁 |
| `read_messages(sid) -> list[dict]` | 读所有 `message`(去信封),跳过坏行;喂模型用 | — |
| `list_sessions() -> list[dict]` | 读 index.json,按 `updated_at` 倒序 | 读缓存 |
| `rename(sid, title) -> None` | 改 index 条目 title | index 写走锁 |
| `delete(sid) -> None` | **先**删 index 条目,再删 `.jsonl` | 提交点最先去 |
| `rebuild_index() -> None` | 扫目录重建 index(兜底) | — |
| `_fit_context(messages) -> list[dict]` | 裁剪钩子,P1 原样返回,占位给 M12 | — |

- 单会话不存在时 `read_messages`/`rename`/`delete` 抛可映射成 404 的错误(如 `SessionNotFound`)。
- `_sessions_dir()` 从 `settings.data_dir` 现取(便于测试 monkeypatch)。

### `models/schemas.py`(改)

- `ChatRequest`:加 `session_id: str | None = None`(**向后兼容:默认 None,不传=懒创建**)。
- 新增出参:`SessionMeta{id,title,created_at,updated_at}`。
- 新增入参:`RenameRequest{title: str}`。
- SSE 事件模型(文档/类型用,运行时仍是 dict → JSON):`session` 事件 `{type:"session", session_id, title}`。

### `api/routes/session.py`(新增)

| 方法 | 路径 | 入 | 出 |
|---|---|---|---|
| `GET` | `/api/sessions` | — | `list[SessionMeta]` |
| `GET` | `/api/sessions/{sid}` | — | `{messages: list[dict]}`(切会话加载历史) |
| `PATCH` | `/api/sessions/{sid}` | `RenameRequest` | `SessionMeta` |
| `DELETE` | `/api/sessions/{sid}` | — | `204` |

- `sid` 不存在 → 404。

### `api/routes/chat.py`(改,接 session)

`POST /api/chat/stream` body → `{session_id?: str, message}`,`event_stream` 时序:

1. `sid = req.session_id or session_store.create()`。
2. `session_store.append_message(sid, {"role":"user","content":req.message})`(**收到即落盘**,首条会顺带落 title)。
3. `yield _sse({"type":"session","session_id":sid,"title":<该会话当前 title>})`(text 之前发)。
4. `history = _fit_context(session_store.read_messages(sid))` → 喂模型 `stream=True`。
5. 逐 delta `yield text` 事件,同时**累积** assistant 全文。
6. 正常收尾:`append_message(sid, {"role":"assistant","content":<累积全文>})` → `yield done`。
7. 中途异常:`yield error`,assistant **不落盘**(下轮重发即可,避免存半截)。

### `api/main.py`(改)

注册 `session_routes.router`。

### 前端(改/增)

- `lib/api.ts`:加 `listSessions/getSession/renameSession/deleteSession`;`streamChat` 入参加 `sessionId?`,`ChatEvent` union 加 `session` 类型。
- `hooks/useChatStream.ts`:维护 `currentSessionId`;处理 `session` 事件(记住 sid、把会话插入/更新到列表);暴露 `newSession()`(纯清空)、`switchSession(sid)`(拉历史铺进消息流)。
- `components/SessionList.tsx`(新增):列表 + 新建 + 切换高亮 + 重命名(inline)+ 删除(带确认)。
- `App.tsx`:左栏挂 `SessionList`,中栏沿用 P0 打字机;两栏布局(右栏上下文面板留到 P4)。

---

## 五、验证方式(对应「重启后还在」)

**curl(后端全通)**
1. `POST /api/chat/stream {message:"我叫小明"}`(不带 sid)→ SSE 首个 `session` 事件拿到 `sid`。
2. `POST /api/chat/stream {session_id:sid, message:"我叫什么?"}` → 回答含「小明」(**多轮记住上文**)。
3. `GET /api/sessions` → 看到该会话,title=「我叫小明」。
4. **重启后端** → `GET /api/sessions/{sid}` → 历史仍在。
5. `PATCH /api/sessions/{sid} {title:"自我介绍"}` → 列表标题变;`DELETE` → 列表消失、文件删除。
6. 一致性:构造「删 index 条目后崩溃」→ 确认只剩无害孤儿文件、可 `rebuild_index()` 找回。

**浏览器(前后端都可见)**
发消息 → 左栏冒出会话 → 新建另一条 → 来回切换历史正确 → 重命名 → 删除 → 刷新页面列表还在。

---

## 六、显式取舍(诚实边界)

- **硬删除**,不做回收站/软删(个人自用无需 undo)。
- index.json 是**缓存**:极端并发下时间戳可能短暂滞后(无伤大雅);损坏可 `rebuild_index()` 重建。
- 上下文**不裁剪**,超长留给 M12;P1 只埋钩子。
- 标题**不 LLM 起名**,首句截断 + 手动重命名。
- 前端**右栏上下文面板不在 P1**(留 P4),P1 只做左栏会话侧边栏。
