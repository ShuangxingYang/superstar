# P2b 写操作 + 安全审批 —— 设计文档

> 里程碑目标(P2「工具+安全」拆分后的后半):在 P2a 只读地基上,让 Agent **能动手改**——write_file(带 diff 预览)+ run_command(白/黑/灰三级),写操作与灰名单命令走**人在环路审批**(批准/拒绝),黑名单直接拒。
> 验证标准:配好 workspace 后,「把 foo 改名成 bar」→ 弹审批卡带 diff → 批准 → 写入成功;`ls` 白名单自动跑;`rm -rf build` 黑名单被拒且不崩流;`python demo.py` 灰名单弹审批,拒绝后模型改口;审批卡挂着时刷新浏览器仍在(状态落盘);越界写被拒。

P2a 让 Agent **能看**(function calling 循环 + read_file/grep/glob + 路径沙箱)。P2b 让 Agent **能改**:引入两个写操作工具,并在工具执行**之前**加一道「处置判定(gate)」——决定这次调用是直接跑、直接拒、还是停下来等人批准。审批的「暂停态」落到磁盘(pending sidecar),用一个独立的 `/resume` 请求接着跑。这正是 P2a 埋下的伏笔——「未回答的 tool_call = 回合边界暂停」——的兑现。

---

## 〇、承接 P2a

P2a 已铺好、P2b 直接复用的地基:

| 地基 | P2a 现状 | P2b 怎么接 |
|---|---|---|
| `security.safe_path` | 路径沙箱(resolve 后判祖先) | write_file 复用;新增 `classify_command` 判命令级别 |
| `config.json` | 已有 `cmd_whitelist` / `cmd_blacklist` | 灰名单 = 不在白也不在黑;直接复用,无需加配置 |
| `ToolRegistry.run()` | 永远返回字符串、三处自愈、不抛 | write_file / run_command 照同一模式登记 |
| `loop.py` 原子落盘 | 一轮工具「连续追加、中间不 yield」防意外悬空 | 审批 = **有意的悬空**,由 pending sidecar 标记与意外悬空区分 |
| `_prune_dangling_tool_calls` | 剪意外悬空(中断的流) | 残留 pending 的合法悬空由路由层先「自动拒绝」清理,不被误剪 |
| 前端 `tool` 卡片 | 名/参/结果、可折叠 | 加审批子状态(待批准 → diff/命令预览 + 批准/拒绝按钮) |

---

## 一、关键设计决策(与用户压测后的定论)

1. **范围 = 两工具 + 审批,不做只读模式开关。** write_file(带 diff)+ run_command(白/黑/灰),写操作与灰命令都走审批。用最小成本覆盖「能改代码 + 安全审批」这个核心难点;设置页的「只读总开关」YAGNI,不做。
2. **审批暂停/恢复 = 流结束 + 独立恢复请求(而非保持连接异步阻塞)。** 遇审批 → 落 pending 标记 + `return`(SSE 流正常结束)→ 用户点按钮 → `POST /api/chat/resume` 进入「恢复模式」接着跑。状态**全落盘**,零长连接、零内存态,刷新/重启不丢。教的是 agent 人在环路的正统模式——checkpoint + 从持久化状态恢复。对比「保持连接 + `await asyncio.Event`」:要内存注册表 + 超时 + 把同步 loop 改 async,连接一断状态全丢,与「个人项目最小成本」冲突,排除。
3. **命令分级 = 拆段判级(而非整条前缀匹配)。** 按 shell 操作符 `&& || ; |` 把命令拆成多段,逐段判定:任一段命中黑名单 → 整条 `black`;所有段都命中白名单 → `white`;其余 → `gray`。防住 `grep x && rm -rf /` 这类「白名单开头、危险尾巴」的拼接绕过。
4. **判级发生在 loop 调工具之前(gate),工具本身不判级。** 因为要先知道该不该停下等审批。工具(write_file/run_command)只负责执行;黑名单兜底可在工具内做 defense-in-depth,但主判定在 `gate.gate_tool_call`。
5. **审批只有「批准 / 拒绝」两态,没有「改了再批」。** 想改内容让 Agent 重来一轮。checkpoint / 回滚 / 编辑后批准都留二版。
6. **一次一审批。** 一轮里若有多个待批操作,前端逐个点、后端逐个 resolve(多数轮只有 1 个待批)。
7. **落盘原子性沿用 P2a 治本。** 一轮内所有 JSONL 追加在末尾**连续写、中间不 yield**;审批的「有意悬空」由 pending sidecar 显式标记,与「中断流的意外悬空」区分开。

---

## 二、架构与新增/改动文件

```
                         ┌─────────────────────────────────────┐
   loop 拿到 tool_call →  │  gate:这次调用怎么处置?             │
                         │   · read_file/grep/glob   → auto     │
                         │   · run_command 白名单     → auto     │
                         │   · run_command 黑名单     → deny     │
                         │   · run_command 灰名单     → approve  │
                         │   · write_file(越界)      → deny     │
                         │   · write_file(合法)      → approve  │
                         └─────────────────────────────────────┘
        auto    → 立即执行(P2a 老路)
        deny    → 写「被安全策略拒绝」当 tool 结果,喂回模型(不执行、不询问)
        approve → 落 pending 标记 + yield approval_required + 结束流,等 /resume
```

```
backend/app/
├── services/
│   └── security.py          【改】加 classify_command(拆段判白/黑/灰)+ SHELL_SEP
├── agent/
│   ├── gate.py             【新增】gate_tool_call:判 auto/deny/approve + 造预览(diff/命令)
│   ├── pending.py          【新增】pending sidecar 读/写/清(<sid>.pending.json)
│   ├── tools/
│   │   ├── fs.py           【改】加 write_file(WriteFileArgs)
│   │   ├── shell.py        【新增】run_command(subprocess,cwd=工作区,超时+截断)
│   │   └── __init__.py     【改】登记 write_file / run_command
│   └── loop.py             【改·核心】审批回合边界 + resume_streaming + reject_all_pending
├── api/routes/
│   ├── chat.py             【改】加 POST /api/chat/resume;/stream 开头兜「残留 pending」
│   └── session.py          【改】GET /api/sessions/{sid} 响应加 pending 字段
└── models/schemas.py        【改】加 ResumeRequest

frontend/src/
├── lib/api.ts               【改】ChatEvent 加 approval_required;加 resumeChat()
├── hooks/useChatStream.ts   【改】审批卡片状态机 + resume 调用 + 历史回放带 pending
├── components/ToolCallCard.tsx【改】待批准时渲染 diff/命令预览 + [✓批准][✗拒绝]
└── App.tsx                  【改】卡片多一种状态(基本沿用)
```

**分层职责(下→上):**
1. `security.classify_command` — 给命令串,拆段返回 `white/black/gray`。
2. `agent/tools/{fs,shell}.py` — 执行体,签名 `def f(args: PydanticModel) -> str`,审批通过后才被 `registry.run` 调到。
3. `agent/gate.py` — 处置判定:给一个 tool_call(名 + 参),返回 `(action, preview)`,`action ∈ {auto, deny, approve}`;approve 时顺带造好前端要展示的预览(write 的 diff / command 的命令串)。
4. `agent/pending.py` — pending sidecar 的读/写/清,把「暂停态」落到 `<sid>.pending.json`。
5. `agent/loop.py` — 循环引擎:拿到 tool_calls 后按 gate 分派;遇 approve 落 pending 并结束流;`resume_streaming` 恢复执行。
6. `routes/chat.py` — `/stream` 与 `/resume` 两个 SSE 端点 + 残留 pending 防护。

---

## 三、命令分级 —— `security.classify_command`(安全难点一)

```python
# services/security.py 追加
import re
SHELL_SEP = re.compile(r"&&|\|\||;|\|")   # && || ; |

def _segments(command: str) -> list[str]:
    return [s.strip() for s in SHELL_SEP.split(command) if s.strip()]

def classify_command(command: str) -> str:
    """把命令按 shell 操作符拆段,逐段判级。返回 'white' | 'black' | 'gray'。"""
    cfg = config_store.get()["security"]
    whitelist, blacklist = cfg["cmd_whitelist"], cfg["cmd_blacklist"]
    segs = _segments(command)
    if not segs:
        return "black"                                   # 空命令直接拒
    for seg in segs:                                     # 黑优先:任一段含黑名单词 → 整条拒
        if any(b in seg for b in blacklist):
            return "black"
    def seg_white(seg: str) -> bool:                     # token 边界:'grep' 配 'grep foo',不配 'grepx'
        return any(seg == w or seg.startswith(w + " ") for w in whitelist)
    if all(seg_white(seg) for seg in segs):              # 每段都白 → 自动跑
        return "white"
    return "gray"                                        # 其余 → 审批
```

**判定规则(顺序敏感)**:
1. **黑优先**:任一段包含任一黑名单词(子串匹配,`rm -rf`/`sudo`/`curl`…)→ `black`。`grep x && rm -rf /` 因第二段命中 `rm -rf` → 拒。这就是防拼接绕过的关键。
2. **全白才白**:每一段都以某白名单项开头(token 边界:等于该项,或以「该项 + 空格」开头)→ `white`。`git status` 这类短语白名单项也支持。
3. **其余灰**:→ 审批。

**诚实边界**:拆段是启发式,不是完整 shell 解析——带引号的 `echo "a && b"` 会被误拆成两段。个人项目可接受;严谨方案需 `shlex` 解析甚至沙箱容器,超出本期成本。黑名单是子串匹配,可能误伤(如命令里恰好含 `dd`),单用户自用可容忍,名单可在设置页调。

---

## 四、两个写操作工具

**`write_file`(加进 `agent/tools/fs.py`)**
```python
class WriteFileArgs(BaseModel):
    path: str
    content: str

def write_file(args: WriteFileArgs) -> str:
    target = security.safe_path(args.path)          # 复用 P2a 沙箱:越界抛 SecurityError
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(args.content, encoding="utf-8")
    logger.info("写文件: path=%s, len=%d", args.path, len(args.content))
    return f"已写入 {args.path}({len(args.content)} 字符)"
```
登记描述:`"把文本内容写入工作区内一个文件(相对路径);文件不存在则新建,存在则整体覆盖。此操作需用户审批。"`

**`run_command`(新增 `agent/tools/shell.py`)**
```python
import subprocess
from pydantic import BaseModel
from app.services import security

CMD_TIMEOUT = 30       # 秒
MAX_OUTPUT = 4000      # 字符

class RunCommandArgs(BaseModel):
    command: str

def run_command(args: RunCommandArgs) -> str:
    cwd = security.get_workspace()                  # 命令在工作区里跑
    logger.info("执行命令: %s", args.command)
    try:
        proc = subprocess.run(args.command, shell=True, cwd=cwd,
                               capture_output=True, text=True, timeout=CMD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"命令超时(>{CMD_TIMEOUT}s),已终止"
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + f"\n…(输出过长已截断,共 {len(out)} 字符)"
    return f"[exit {proc.returncode}]\n{out}".rstrip() or f"[exit {proc.returncode}](无输出)"
```
登记描述:`"在工作区目录下执行一条 shell 命令并返回输出(退出码 + stdout/stderr)。危险命令会被拒绝,其余命令需用户审批。"`

> 工具本身**不判级**,只执行。判级在 loop 调工具之前由 gate 完成。

---

## 五、gate —— 每个 tool_call 的处置判定(`agent/gate.py`)

```python
import difflib
from app.services import security
from app.services.security import SecurityError

def gate_tool_call(name: str, args: dict) -> tuple[str, dict | None]:
    """给一个 tool_call(名 + 已解析参数),返回 (action, preview)。
    action ∈ {'auto', 'deny', 'approve'};approve 时 preview 是前端要展示的预览。"""
    if name == "write_file":
        try:
            target = security.safe_path(args["path"])          # 越界 → 连审批都不给
        except (SecurityError, KeyError):
            return "deny", None
        old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        diff = "".join(difflib.unified_diff(
            old.splitlines(keepends=True),
            (args.get("content") or "").splitlines(keepends=True),
            fromfile=f"{args['path']} (原)", tofile=f"{args['path']} (新)"))
        return "approve", {"kind": "write", "path": args["path"], "diff": diff or "(无变化)"}
    if name == "run_command":
        level = security.classify_command(args.get("command", ""))
        if level == "white":
            return "auto", None
        if level == "black":
            return "deny", None
        return "approve", {"kind": "command", "command": args["command"], "level": "gray"}
    return "auto", None                                        # read_file/grep/glob 只读直接跑
```

**为什么 diff 在 gate(审批前)造**:用户要在批准前看到「将要发生什么」。diff 用 `difflib.unified_diff` 对比磁盘旧内容与模型给的新内容;文件不存在则旧内容为空(全新增)。越界路径在这里就 `deny`,不进入审批。

---

## 六、pending sidecar(`agent/pending.py`)—— 暂停态落盘

`data/sessions/<sid>.pending.json`:
```json
{
  "tool_calls": [ { "id": "...", "type": "function",
                    "function": {"name": "write_file", "arguments": "{...}"} } ],
  "previews": { "<tool_call_id>": {"kind": "write", "path": "...", "diff": "..."} }
}
```

就三个函数(原子写,复用 `atomic_json`):
```python
def read(sid: str) -> dict | None      # 无文件 → None
def write(sid: str, tool_calls: list[dict], previews: dict) -> None
def clear(sid: str) -> None            # 删文件(missing_ok)
```

**为何用 sidecar 而非塞进 JSONL**:JSONL 是「消息日志」,应保持纯净且只追加;pending 是「可清除的临时暂停态」,读/写/删都独立。二者分离,`_prune_dangling_tool_calls` 也不必理解 pending。

---

## 七、loop 改造:审批回合边界(核心)

**不变量**:一轮内所有 `session_store.append_message` 在末尾**连续调用、中间不 yield**(沿用 P2a 治本);approve 的「有意悬空」由 pending sidecar 标记。

```python
# run_agent_streaming 拿到 tool_calls 后的一轮(替换 P2a 的执行段):
gated = []
for tc in tool_calls:
    parsed = _parse_args(tc)                                    # json.loads,失败 → {}
    action, preview = gate_tool_call(tc["function"]["name"], parsed)
    gated.append((tc, parsed, action, preview))

tool_results: list[tuple[str, str]] = []      # (tool_call_id, result) — auto/deny 的
pending: list[tuple[dict, dict]] = []         # (tool_call, preview) — approve 的
for tc, parsed, action, preview in gated:
    name = tc["function"]["name"]
    if action == "approve":
        yield {"type": "approval_required", "id": tc["id"], "name": name,
               "args": tc["function"]["arguments"], "preview": preview}
        pending.append((tc, preview))
    else:
        yield {"type": "tool_call", "id": tc["id"], "name": name, "args": tc["function"]["arguments"]}
        result = ("被安全策略拒绝(黑名单/越界)" if action == "deny"
                  else registry.run(name, parsed))              # 仅 auto 真执行
        yield {"type": "tool_result", "id": tc["id"], "result": result}
        tool_results.append((tc["id"], result))

# —— 到此才连续落盘(中间无 yield)——
session_store.append_message(sid, {
    "role": "assistant", "content": "".join(text_parts) or None, "tool_calls": tool_calls})
for tid, r in tool_results:
    session_store.append_message(sid, {"role": "tool", "tool_call_id": tid, "content": r})
if pending:
    pending_store.write(sid, [tc for tc, _ in pending], {tc["id"]: p for tc, p in pending})
    return                                     # ← 流结束,等 /resume
# 无 pending → 回 for 顶,带结果再问模型(全 auto/deny 的轮,与 P2a 行为一致)
```

**resume_streaming(恢复执行)**:
```python
def resume_streaming(sid: str, tool_call_id: str, decision: str):   # decision ∈ approve/reject
    pend = pending_store.read(sid)
    if not pend:
        yield {"type": "error", "message": "没有待审批的操作"}
        return
    tc = next((t for t in pend["tool_calls"] if t["id"] == tool_call_id), None)
    if tc is None:
        yield {"type": "error", "message": "待审批操作不存在或已处理"}
        return
    if decision == "approve":
        result = registry.run(tc["function"]["name"], _parse_args(tc))   # 真正执行
    else:
        result = "用户已拒绝此操作"
    yield {"type": "tool_result", "id": tool_call_id, "result": result}
    session_store.append_message(sid, {"role": "tool", "tool_call_id": tool_call_id, "content": result})

    remaining = [t for t in pend["tool_calls"] if t["id"] != tool_call_id]
    if remaining:                              # 本轮还有别的待批 → 结束,等下次点击
        prev = {k: v for k, v in pend["previews"].items() if k != tool_call_id}
        pending_store.write(sid, remaining, prev)
        return
    pending_store.clear(sid)                   # 全批完 → 继续正常循环,带新结果再问模型
    yield from run_agent_streaming(sid)
```

**reject_all_pending(残留 pending 防护用)**:把某会话所有待批操作按拒绝落盘、清 sidecar(不 yield),供 `/stream` 开头调用:
```python
def reject_all_pending(sid: str) -> None:
    pend = pending_store.read(sid)
    if not pend:
        return
    for tc in pend["tool_calls"]:
        session_store.append_message(sid, {"role": "tool", "tool_call_id": tc["id"],
                                           "content": "用户已拒绝此操作(发起了新消息)"})
    pending_store.clear(sid)
```

**为什么要残留防护**:若审批卡挂着、用户没点按钮就发了新消息,历史里会留一条 assistant(tool_calls) 没有对应 tool 结果。若不处理,要么被 `_prune` 误删(丢上下文),要么 provider 400。用「自动拒绝」把它协议合法地收尾,新消息再照常走。

---

## 八、路由 + 前端

### 后端路由(`chat.py` / `session.py` / `schemas.py`)

```python
# schemas.py
class ResumeRequest(BaseModel):
    session_id: str
    tool_call_id: str
    decision: Literal["approve", "reject"]

# chat.py
@router.post("/resume")
def chat_resume(req: ResumeRequest) -> StreamingResponse:
    def event_stream():
        try:
            for event in loop.resume_streaming(req.session_id, req.tool_call_id, req.decision):
                yield _sse(event)
        except Exception as e:      # noqa: BLE001
            yield _sse({"type": "error", "message": str(e)})
    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

`/stream` 开头加残留 pending 防护(在落 user 消息之前):
```python
sid = req.session_id or session_store.create()
if req.session_id and pending_store.read(sid):
    loop.reject_all_pending(sid)               # 自动拒绝残留,收尾悬空
session_store.append_message(sid, {"role": "user", "content": req.message})
...
```

`GET /api/sessions/{sid}` 响应体新增 `pending` 字段(读 sidecar,可为 null):向后兼容——**新增字段、老客户端忽略即可**(遵守「只加不删」)。前端用它在刷新后还原「待批准」卡片。

### 前端

`api.ts` — `ChatEvent` union 新增(向后兼容,不删旧类型):
```typescript
| { type: 'approval_required'; id: string; name: string; args: string;
    preview: { kind: 'write'; path: string; diff: string }
           | { kind: 'command'; command: string; level: string } }
```
新增 `resumeChat(sessionId, toolCallId, decision, onEvent)`:`POST /api/chat/resume`,读 SSE 的逻辑与 `streamChat` 相同(可抽公共读流函数)。`getSession` 返回类型加可选 `pending`。

`useChatStream.ts` — 复用现有 `tool` 卡片,加审批子状态,**不新增 ChatItem 种类**:
```typescript
| { kind: 'tool'; id: string; name: string; args: string; result?: string;
    approval?: { preview: ApprovalPreview; status: 'pending' | 'approved' | 'rejected' } }
```
- 收 `approval_required` → 插一张卡,`approval.status='pending'`(带预览,不显示「运行中」)。
- 点 [✓批准]/[✗拒绝] → 调 `resumeChat` → 本卡 `status` 改 approved/rejected;resume 流回来的 `tool_result`/`text` 照常按 `id` 填结果、续答。**审批期间输入框锁住**(`streaming` 或「有 pending 卡」时禁用发送)。
- 历史回放 `messagesToItems`:某 tool_call_id 若在 `getSession().pending` 名单里 → 该卡 `approval.status='pending'`(带 sidecar 的 preview);已有 tool 结果的 → 正常完成卡。

`ToolCallCard.tsx` — `approval.status==='pending'` 时卡片体渲染:write → diff(`+`/`-` 行红绿高亮)+ 路径;command → 命令全文 + ⚠️ 灰名单提示;底部 [✓ 批准] [✗ 拒绝]。非 pending 时就是 P2a 老样子。

---

## 九、测试策略(TDD,mock LLM)

**后端**:
- `test_security.py` 补 `classify_command`:白(`grep foo`、`git status`)、黑(`sudo rm`、拼接 `grep x && rm -rf /` 命中第二段)、灰(`python x.py`)、空命令 → black、token 边界(`grepx` 不算白)。
- `test_gate.py`(新增):write_file 合法 → `approve` 且 preview 含 diff;越界 write → `deny`;白命令 → `auto`;黑 → `deny`;灰 → `approve` 且 preview 含 command。
- `test_tools.py` 补:write_file 正常写入 / 越界抛 SecurityError(经 registry → 「安全拦截」字符串);run_command 正常输出 / 超时 / 输出超长截断。
- `test_loop.py` 补(最关键):
  - mock「模型要 write_file」→ 断言事件序列出现 `approval_required`;落盘为 assistant(tool_calls) 但**无对应 tool 结果**;pending sidecar 已写;流结束(生成器耗尽)。
  - 接 `resume_streaming(sid, id, "approve")` → 断言真执行(文件被写)、落了 tool 结果、清了 sidecar、继续拿到终答(`done`)。
  - `resume_streaming(sid, id, "reject")` → tool 结果为「用户已拒绝」、模型改口。
  - 残留 pending + 走 `/stream` 新消息 → `reject_all_pending` 收尾,历史协议合法。
- `test_chat_routes.py` 补:`/resume` 端到端 SSE(mock LLM),断言出现 `tool_result` 与后续 `done`。

**前端**:沿用 P0/P1/P2a,无单测;`npm run build`(`tsc -b`)类型通过 + 浏览器手验。

---

## 十、验证方式(浏览器端到端)

先在设置页 / config.json 配好 `security.workspace_dir` 指向一个可写测试目录。

1. 「把 utils.py 里的 foo 改名成 bar」→ 弹审批卡带 diff → [批准] → 写入成功,模型确认。
2. 「跑一下 `ls`」→ 白名单,自动执行,无审批卡。
3. 「`rm -rf build`」→ 黑名单,模型收到「被拒」,道歉,不执行。
4. 「跑 `python demo.py`」→ 灰名单,弹审批 → [拒绝] → 模型收到「用户已拒绝」,改口。
5. 审批卡挂着时刷新浏览器 → 卡片仍在(pending 落盘 + getSession 回放)。
6. 「写 `../../etc/passwd`」→ gate `deny`,不崩流。
7. 纯聊天 / 只读工具(grep/glob/read_file)→ 行为与 P2a 一致(向后兼容)。

---

## 十一、显式取舍(诚实边界)

- **命令沙箱靠三级名单 + cwd,不做命令内路径提取校验**:shell 命令本质能读写工作区外(`cat /etc/passwd`);名单是主控制,诚实标注此边界。
- **拆段判级是启发式**:带引号的复合命令(`echo "a && b"`)可能误拆;黑名单子串匹配可能误伤。名单可在设置页调。
- **一次一审批**:一轮多个待批操作逐个点(多数轮仅 1 个)。
- **崩溃恢复弱一致**:落 assistant 后、写 sidecar 前若进程被杀,该轮悬空会被后续 `_prune` 丢弃(轮丢失,用户重问),个人项目可接受。
- **审批仅批准/拒绝**,无「编辑后批准」;checkpoint / 回滚留二版。
- **RAG / KB_DIR 第二沙箱根**仍留 P3;write_file 单根(workspace)。

---

## 十二、日志与安全(遵守项目规范)

- 日志含业务标识(sid、path、命令串、退出码),**绝不打印 api_key**;命令输出只在 tool 结果里返回,日志只记命令串与长度级信息。
- `GET /api/sessions/{sid}` 加 `pending` 字段属**向后兼容新增**(只加不删,老客户端忽略)。
- 审批是**外向、难撤销**操作的闸门:写文件 / 跑命令一律经用户显式批准(黑名单直接拒),符合「对难以逆转的操作先确认」的原则。
