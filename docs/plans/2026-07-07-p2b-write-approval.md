# P2b 写操作 + 安全审批 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans 或 subagent-driven-development 逐任务实现。步骤用 `- [ ]` 勾选跟踪。

**Goal:** 在 P2a 只读地基上,让 Agent 能 write_file(带 diff)/ run_command(白黑灰三级),写操作与灰命令走人在环路审批(批准/拒绝),黑名单直接拒。

**Architecture:** loop 拿到 tool_call 后先过 `gate` 判 auto/deny/approve;approve → 落 pending sidecar(`<sid>.pending.json`)+ yield `approval_required` + 结束流;用户点按钮 → `POST /api/chat/resume` 进入 `resume_streaming` 执行工具并继续循环。状态全落盘,零长连接。

**Tech Stack:** Python 3 / FastAPI / Pydantic / subprocess / difflib(后端);React + TS + Vite(前端)。测试:pytest(后端)、`npm run build` 类型门禁(前端)。

## Global Constraints

- **测试命令(后端)**:`cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q`(单测加 `path::name -v`)。
- **测试命令(前端)**:`cd /Users/shuangxingyang/Desktop/myspace/superstar/frontend && npm run build`(`tsc -b` + vite,类型门禁;`tsc --noEmit` 不编译 src,不算数)。
- **日志规范**:关键节点记业务标识(sid/path/命令串/退出码);**绝不打印 api_key**;命令输出只在 tool 结果里返回。日志文案有辨识度。
- **API 向后兼容(只加不删)**:新增字段必须有默认值或可为 null;不删已发布字段、不改类型/语义。`GET /api/sessions/{sid}` 新增 `pending` 字段属合规新增。
- **落盘原子性**:一轮内 `session_store.append_message` 连续调用、中间不 yield(沿用 P2a 治本);审批的「有意悬空」由 pending sidecar 显式标记。
- **工具自愈**:工具经 `ToolRegistry.run` 执行,错误变字符串喂回,从不崩流。
- **git 提交前务必 `cd backend` 再跑 pytest**(git 命令会把 cwd 漂到仓库根,导致 `Failed to spawn: pytest`)。
- **难以逆转的外向操作(写文件/跑命令)一律经用户显式批准**;黑名单直接拒。

---

### Task 1: `classify_command` 命令分级(拆段判级)

**Files:**
- Modify: `backend/app/services/security.py`(在文件末尾追加)
- Test: `backend/tests/test_security.py`(追加)

**Interfaces:**
- Consumes: `config_store.get()["security"]["cmd_whitelist" / "cmd_blacklist"]`(已存在,默认值见 config_store.DEFAULTS)。
- Produces: `security.classify_command(command: str) -> str`(返回 `"white"|"black"|"gray"`);`security.SHELL_SEP`;`security._segments(command: str) -> list[str]`。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_security.py` 末尾追加:

```python
# ============ P2b: classify_command 命令分级 ============
from app.services.security import classify_command


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    # 只需要 config_store 默认名单(不依赖 workspace);隔离到 tmp data_dir
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    return None


def test_classify_white(cfg):
    assert classify_command("grep foo") == "white"
    assert classify_command("git status") == "white"      # 短语白名单项


def test_classify_black_direct(cfg):
    assert classify_command("sudo reboot") == "black"      # sudo 命中


def test_classify_black_chained_bypass(cfg):
    # 白名单开头 + 危险尾巴:拆段后第二段命中 rm -rf → 整条 black(防绕过)
    assert classify_command("grep x && rm -rf /") == "black"


def test_classify_gray(cfg):
    assert classify_command("python demo.py") == "gray"


def test_classify_empty_is_black(cfg):
    assert classify_command("   ") == "black"


def test_classify_token_boundary(cfg):
    # grepx 不是 grep,不应算白名单
    assert classify_command("grepx foo") == "gray"
```

确认 `test_security.py` 顶部已 import `settings`、`config_store`(P2a 已有;若无则补 `from app.config import settings` / `from app.services import config_store`)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_security.py -q`
Expected: FAIL(`ImportError: cannot import name 'classify_command'`)。

- [ ] **Step 3: 实现**

在 `backend/app/services/security.py` 顶部 import 区加 `import re`(已有 `import logging`),然后在文件**末尾**追加:

```python
# ---- P2b: 命令分级(白/黑/灰),拆段判级防拼接绕过 ----
SHELL_SEP = re.compile(r"&&|\|\||;|\|")   # && || ; |


def _segments(command: str) -> list[str]:
    """按 shell 操作符拆成多段,去空白空段。"""
    return [s.strip() for s in SHELL_SEP.split(command) if s.strip()]


def classify_command(command: str) -> str:
    """返回 'white' | 'black' | 'gray'。

    规则(顺序敏感):
      1. 黑优先:任一段含黑名单词(子串)→ black。防 `grep x && rm -rf /` 绕过。
      2. 全白才白:每段都以某白名单项开头(token 边界)→ white。
      3. 其余 → gray(审批)。
    """
    cfg = config_store.get()["security"]
    whitelist, blacklist = cfg["cmd_whitelist"], cfg["cmd_blacklist"]
    segs = _segments(command)
    if not segs:
        return "black"                                   # 空命令直接拒
    for seg in segs:
        if any(b in seg for b in blacklist):
            logger.info("命令分级=black: seg=%s", seg)
            return "black"

    def seg_white(seg: str) -> bool:                     # 'grep' 配 'grep foo',不配 'grepx'
        return any(seg == w or seg.startswith(w + " ") for w in whitelist)

    if all(seg_white(seg) for seg in segs):
        return "white"
    return "gray"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_security.py -q`
Expected: PASS(含 P2a 老用例)。

- [ ] **Step 5: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/services/security.py backend/tests/test_security.py
git commit -m "feat(p2b): classify_command 命令分级(拆段判白黑灰)"
```

---

### Task 2: `write_file` 写文件工具 + 登记

**Files:**
- Modify: `backend/app/agent/tools/fs.py`(追加 write_file)
- Modify: `backend/app/agent/tools/__init__.py`(登记 write_file)
- Test: `backend/tests/test_tools.py`(追加)

**Interfaces:**
- Consumes: `security.safe_path(path) -> Path`(P2a 已有,越界抛 `SecurityError`)。
- Produces: `fs.WriteFileArgs(path: str, content: str)`;`fs.write_file(args) -> str`;全局 `registry` 新增登记 `write_file`。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_tools.py` 末尾追加:

```python
# ============ P2b: write_file ============
from app.agent.tools.fs import WriteFileArgs, write_file


def test_write_file_ok(ws):
    assert write_file(WriteFileArgs(path="new.txt", content="hi")).startswith("已写入")
    assert (ws / "new.txt").read_text(encoding="utf-8") == "hi"


def test_write_file_creates_parent(ws):
    write_file(WriteFileArgs(path="sub/deep/x.txt", content="y"))
    assert (ws / "sub" / "deep" / "x.txt").read_text(encoding="utf-8") == "y"


def test_write_file_escape_via_registry(ws):
    from app.agent.tools import registry
    # 越界写经全局 registry 走自愈 → 「安全拦截」而非抛(验证已登记)
    assert registry.run("write_file", {"path": "../../tmp/x", "content": "z"}).startswith("安全拦截")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_tools.py -q`
Expected: FAIL(`ImportError: cannot import name 'write_file'`)。

- [ ] **Step 3: 实现**

在 `backend/app/agent/tools/fs.py` 末尾追加(顶部已 import `BaseModel, Field` 和 `safe_path`;补 `import logging` + `logger`):

```python
import logging

logger = logging.getLogger(__name__)


class WriteFileArgs(BaseModel):
    path: str = Field(description="相对工作区根目录的文件路径,如 src/main.py")
    content: str = Field(description="要写入的完整文本内容(整体覆盖原文件)")


def write_file(args: WriteFileArgs) -> str:
    target = safe_path(args.path)          # 越界抛 SecurityError,由 registry 兜
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(args.content, encoding="utf-8")
    logger.info("写文件完成: path=%s, len=%d", args.path, len(args.content))
    return f"已写入 {args.path}({len(args.content)} 字符)"
```

在 `backend/app/agent/tools/__init__.py` 末尾(read_file 登记之后)追加:

```python
from app.agent.tools.fs import WriteFileArgs, write_file  # noqa: E402

registry.register(
    "write_file", write_file, WriteFileArgs,
    "把文本内容写入工作区内一个文件(相对路径);不存在则新建,存在则整体覆盖。此操作需用户审批。",
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_tools.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/agent/tools/fs.py backend/app/agent/tools/__init__.py backend/tests/test_tools.py
git commit -m "feat(p2b): write_file 写文件工具(沙箱+登记)"
```

---

### Task 3: `run_command` 命令执行工具 + 登记

**Files:**
- Create: `backend/app/agent/tools/shell.py`
- Modify: `backend/app/agent/tools/__init__.py`(登记 run_command)
- Test: `backend/tests/test_tools.py`(追加)

**Interfaces:**
- Consumes: `security.get_workspace() -> Path`(P2a 已有)。
- Produces: `shell.RunCommandArgs(command: str)`;`shell.run_command(args) -> str`;`shell.CMD_TIMEOUT`;`shell.MAX_OUTPUT`;全局 `registry` 新增登记 `run_command`。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_tools.py` 末尾追加:

```python
# ============ P2b: run_command ============
from app.agent.tools.shell import RunCommandArgs, run_command


def test_run_command_ok(ws):
    out = run_command(RunCommandArgs(command="echo hi"))
    assert "[exit 0]" in out and "hi" in out


def test_run_command_cwd_is_workspace(ws):
    (ws / "marker.txt").write_text("", encoding="utf-8")
    out = run_command(RunCommandArgs(command="ls"))      # cwd=工作区 → 能看到 marker
    assert "marker.txt" in out


def test_run_command_truncates(ws):
    # 造超长输出(> MAX_OUTPUT),应截断并提示
    from app.agent.tools.shell import MAX_OUTPUT
    out = run_command(RunCommandArgs(command=f"python -c \"print('x'*{MAX_OUTPUT + 500})\""))
    assert "输出过长已截断" in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_tools.py -q`
Expected: FAIL(`ModuleNotFoundError: app.agent.tools.shell`)。

- [ ] **Step 3: 实现**

创建 `backend/app/agent/tools/shell.py`:

```python
"""
shell.py —— 执行 shell 命令(P2b 写操作之一)。

工具本身只负责执行:在工作区目录下跑命令、限时、截断输出。
命令是否放行(白/黑/灰)由 loop 调工具之前的 gate 判定,不在这里。
cwd=工作区 是弱边界(shell 命令本质能读写工作区外),主控制是三级名单。
"""
import logging
import subprocess

from pydantic import BaseModel, Field

from app.services import security

logger = logging.getLogger(__name__)

CMD_TIMEOUT = 30       # 秒:防命令挂死
MAX_OUTPUT = 4000      # 字符:防爆上下文


class RunCommandArgs(BaseModel):
    command: str = Field(description="要在工作区目录下执行的 shell 命令")


def run_command(args: RunCommandArgs) -> str:
    cwd = security.get_workspace()                  # 命令在工作区里跑
    logger.info("执行命令: %s", args.command)
    try:
        proc = subprocess.run(
            args.command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=CMD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.warning("命令超时: %s", args.command)
        return f"命令超时(>{CMD_TIMEOUT}s),已终止"
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + f"\n…(输出过长已截断,共 {len(out)} 字符)"
    logger.info("命令完成: exit=%d, out_len=%d", proc.returncode, len(out))
    body = f"[exit {proc.returncode}]\n{out}".rstrip()
    return body or f"[exit {proc.returncode}](无输出)"
```

在 `backend/app/agent/tools/__init__.py` 末尾追加:

```python
from app.agent.tools.shell import RunCommandArgs, run_command  # noqa: E402

registry.register(
    "run_command", run_command, RunCommandArgs,
    "在工作区目录下执行一条 shell 命令并返回输出(退出码 + stdout/stderr)。危险命令会被拒绝,其余需用户审批。",
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_tools.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/agent/tools/shell.py backend/app/agent/tools/__init__.py backend/tests/test_tools.py
git commit -m "feat(p2b): run_command 命令执行工具(subprocess+超时+截断)"
```

---

### Task 4: `gate.py` 处置判定(auto/deny/approve + 预览)

**Files:**
- Create: `backend/app/agent/gate.py`
- Test: `backend/tests/test_gate.py`

**Interfaces:**
- Consumes: `security.safe_path`、`security.classify_command`、`security.SecurityError`。
- Produces: `gate.gate_tool_call(name: str, args: dict) -> tuple[str, dict | None]`。`action ∈ {"auto","deny","approve"}`。approve 时 preview 为 `{"kind":"write","path","diff"}` 或 `{"kind":"command","command","level":"gray"}`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_gate.py`:

```python
"""gate_tool_call:每个 tool_call 的处置判定(auto/deny/approve + 预览)。"""
import pytest

from app.agent.gate import gate_tool_call
from app.config import settings
from app.services import config_store


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    config_store.update({"security": {"workspace_dir": str(proj)}})
    return proj


def test_gate_readonly_auto(ws):
    assert gate_tool_call("grep", {"pattern": "x"}) == ("auto", None)
    assert gate_tool_call("read_file", {"path": "a"})[0] == "auto"


def test_gate_write_approve_with_diff(ws):
    (ws / "a.txt").write_text("old\n", encoding="utf-8")
    action, preview = gate_tool_call("write_file", {"path": "a.txt", "content": "new\n"})
    assert action == "approve"
    assert preview["kind"] == "write" and preview["path"] == "a.txt"
    assert "old" in preview["diff"] and "new" in preview["diff"]


def test_gate_write_escape_deny(ws):
    assert gate_tool_call("write_file", {"path": "../../tmp/x", "content": "z"}) == ("deny", None)


def test_gate_command_white_auto(ws):
    assert gate_tool_call("run_command", {"command": "ls"}) == ("auto", None)


def test_gate_command_black_deny(ws):
    assert gate_tool_call("run_command", {"command": "rm -rf /"}) == ("deny", None)


def test_gate_command_gray_approve(ws):
    action, preview = gate_tool_call("run_command", {"command": "python demo.py"})
    assert action == "approve"
    assert preview["kind"] == "command" and preview["command"] == "python demo.py"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_gate.py -q`
Expected: FAIL(`ModuleNotFoundError: app.agent.gate`)。

- [ ] **Step 3: 实现**

创建 `backend/app/agent/gate.py`:

```python
"""
gate.py —— 处置判定:给一个 tool_call,决定「直接跑 / 直接拒 / 停下等审批」。

放在 loop 调工具之前:因为要先知道该不该停下等审批,不能等执行了才判。
  - write_file:越界 → deny;否则 approve(顺带造 diff 预览)
  - run_command:白 → auto,黑 → deny,灰 → approve(带命令预览)
  - 只读工具(read_file/grep/glob)→ auto
"""
import difflib
import logging

from app.services import security
from app.services.security import SecurityError

logger = logging.getLogger(__name__)


def gate_tool_call(name: str, args: dict) -> tuple[str, dict | None]:
    """返回 (action, preview)。action ∈ {'auto','deny','approve'}。"""
    if name == "write_file":
        try:
            target = security.safe_path(args["path"])          # 越界 → 连审批都不给
        except (SecurityError, KeyError):
            logger.info("gate: write_file 越界/缺参 → deny")
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
            logger.info("gate: run_command 黑名单 → deny")
            return "deny", None
        return "approve", {"kind": "command", "command": args.get("command", ""), "level": "gray"}

    return "auto", None                                        # 只读工具直接跑
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_gate.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/agent/gate.py backend/tests/test_gate.py
git commit -m "feat(p2b): gate 处置判定(auto/deny/approve + diff/命令预览)"
```

---

### Task 5: `pending.py` 暂停态 sidecar(读/写/清)

**Files:**
- Create: `backend/app/agent/pending.py`
- Test: `backend/tests/test_pending.py`

**Interfaces:**
- Consumes: `settings.data_dir`;`atomic_json.read_json` / `write_json_atomic`(P1 已有)。
- Produces: `pending.read(sid) -> dict | None`;`pending.write(sid, tool_calls: list[dict], previews: dict) -> None`;`pending.clear(sid) -> None`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_pending.py`:

```python
"""pending sidecar:审批暂停态落盘(读/写/清)。"""
import pytest

from app.agent import pending
from app.config import settings


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    return tmp_path


def test_read_missing_is_none(data):
    assert pending.read("nope") is None


def test_write_then_read_roundtrip(data):
    tcs = [{"id": "w1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]
    previews = {"w1": {"kind": "write", "path": "a.txt", "diff": "x"}}
    pending.write("s1", tcs, previews)
    got = pending.read("s1")
    assert got["tool_calls"] == tcs
    assert got["previews"]["w1"]["path"] == "a.txt"


def test_clear(data):
    pending.write("s1", [], {})
    pending.clear("s1")
    assert pending.read("s1") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_pending.py -q`
Expected: FAIL(`ModuleNotFoundError: app.agent.pending`)。

- [ ] **Step 3: 实现**

创建 `backend/app/agent/pending.py`:

```python
"""
pending.py —— 审批暂停态 sidecar(data/sessions/<sid>.pending.json)。

为何独立于 JSONL:JSONL 是「消息日志」,应纯净且只追加;pending 是「可清除的临时暂停态」,
读/写/删都独立。二者分离,_prune_dangling_tool_calls 也不必理解 pending。

结构:{ "tool_calls": [完整 tool_call...], "previews": {tool_call_id: 预览} }
"""
import logging
from pathlib import Path

from app.config import settings
from app.services import atomic_json

logger = logging.getLogger(__name__)


def _path(sid: str) -> Path:
    return Path(settings.data_dir) / "sessions" / f"{sid}.pending.json"


def read(sid: str) -> dict | None:
    """无文件 → None。"""
    return atomic_json.read_json(_path(sid), None)


def write(sid: str, tool_calls: list[dict], previews: dict) -> None:
    atomic_json.write_json_atomic(_path(sid), {"tool_calls": tool_calls, "previews": previews})
    logger.info("写 pending: sid=%s, 待审批=%d", sid, len(tool_calls))


def clear(sid: str) -> None:
    _path(sid).unlink(missing_ok=True)
    logger.info("清 pending: sid=%s", sid)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_pending.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/agent/pending.py backend/tests/test_pending.py
git commit -m "feat(p2b): pending sidecar 暂停态落盘(读/写/清)"
```

---

### Task 6: loop 审批回合边界(run_agent_streaming 改造)

**Files:**
- Modify: `backend/app/agent/loop.py`(改执行段 + 加 `_parse_args` helper + import)
- Test: `backend/tests/test_loop.py`(追加)

**Interfaces:**
- Consumes: `gate.gate_tool_call`、`pending.write`。
- Produces: 新增事件 `{"type":"approval_required","id","name","args","preview"}`;`loop._parse_args(tc: dict) -> dict`。行为:approve 分支落 assistant(tool_calls) + auto/deny 结果 + pending,然后 `return`(流结束)。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_loop.py` 末尾追加(复用文件顶部的 `_Chunk/_Delta/_TC/_Fn/_answer_stream`;新增一个**共享 client** 的 fixture,让 run + resume 跨调用共用同一个 calls 计数):

```python
# ============ P2b: 审批回合边界 + resume ============
from pathlib import Path

from app.agent import pending


def _write_tool_stream():
    yield _Chunk(_Delta(tool_calls=[_TC(0, id="w1", name="write_file",
        arguments='{"path": "out.txt", "content": "hello"}')]))


class _WriteThenAnswer:
    """第 1 次 create 要 write_file(触发审批),之后(resume 续跑)给终答。"""
    def __init__(self):
        self.calls = 0

    def create(self, model, messages, tools, stream):
        self.calls += 1
        return _write_tool_stream() if self.calls == 1 else _answer_stream()


class _WriteClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _WriteThenAnswer()})()


@pytest.fixture
def p2b_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    config_store.update({"security": {"workspace_dir": str(proj)}})
    client = _WriteClient()                                   # 共享实例:calls 跨 run+resume 累计
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    sid = session_store.create()
    session_store.append_message(sid, {"role": "user", "content": "写个文件"})
    return sid, proj


def test_write_file_pauses_for_approval(p2b_ready):
    sid, _ = p2b_ready
    events = list(loop.run_agent_streaming(sid))
    ar = next(e for e in events if e["type"] == "approval_required")
    assert ar["name"] == "write_file" and ar["preview"]["kind"] == "write"
    # 落盘:assistant(tool_calls) 有,但还没有任何 tool 结果(有意悬空)
    msgs = session_store.read_messages(sid)
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["tool_calls"]
    assert not any(m["role"] == "tool" for m in msgs)
    # pending sidecar 已写,文件还没被写
    assert pending.read(sid) is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_loop.py::test_write_file_pauses_for_approval -v`
Expected: FAIL(无 `approval_required` 事件 —— 现在的 loop 直接执行 write_file)。

- [ ] **Step 3: 实现**

在 `backend/app/agent/loop.py` 顶部 import 区加:

```python
from app.agent import pending as pending_store
from app.agent.gate import gate_tool_call
```

在 `_accumulate` 之后(或 `run_agent_streaming` 之前)加 helper:

```python
def _parse_args(tc: dict) -> dict:
    """解析一个 tool_call 的 arguments(JSON 字符串)→ dict;非法/空 → {}。"""
    raw = tc["function"]["arguments"]
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
```

把 `run_agent_streaming` 里「拿到 tool_calls 之后」那段(从 `if not tool_calls:` 之后的 tool 执行 + 落盘)替换为:

```python
            if not tool_calls:
                session_store.append_message(sid, {"role": "assistant", "content": "".join(text_parts)})
                yield {"type": "done"}
                return

            # 每个 tool_call 先过 gate 判处置
            tool_results: list[tuple[str, str]] = []   # (id, result) —— auto/deny 的
            pending_calls: list[dict] = []             # approve 的完整 tool_call
            previews: dict = {}                        # id -> 预览
            for tc in tool_calls:
                name = tc["function"]["name"]
                parsed = _parse_args(tc)
                action, preview = gate_tool_call(name, parsed)
                if action == "approve":
                    yield {"type": "approval_required", "id": tc["id"], "name": name,
                           "args": tc["function"]["arguments"], "preview": preview}
                    pending_calls.append(tc)
                    previews[tc["id"]] = preview
                else:
                    yield {"type": "tool_call", "id": tc["id"], "name": name,
                           "args": tc["function"]["arguments"]}
                    result = ("被安全策略拒绝(黑名单/越界)" if action == "deny"
                              else registry.run(name, parsed))     # 仅 auto 真执行
                    yield {"type": "tool_result", "id": tc["id"], "result": result}
                    tool_results.append((tc["id"], result))

            # —— 到此才连续落盘(中间无 yield),沿用 P2a 治本 ——
            session_store.append_message(sid, {
                "role": "assistant", "content": "".join(text_parts) or None, "tool_calls": tool_calls})
            for tid, r in tool_results:
                session_store.append_message(sid, {"role": "tool", "tool_call_id": tid, "content": r})
            if pending_calls:
                pending_store.write(sid, pending_calls, previews)
                logger.info("审批暂停: sid=%s, 待审批=%d", sid, len(pending_calls))
                return                                 # ← 流结束,等 /resume
            # 无 pending → 回 for 顶,带结果再问模型(全 auto/deny 的轮,与 P2a 一致)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_loop.py -q`
Expected: PASS(含 P2a 老用例;`test_grep_then_answer` 仍绿——grep 是 auto,行为不变)。

- [ ] **Step 5: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/agent/loop.py backend/tests/test_loop.py
git commit -m "feat(p2b): loop 审批回合边界(gate 分派 + 落 pending + 结束流)"
```

---

### Task 7: `resume_streaming` + `reject_all_pending`

**Files:**
- Modify: `backend/app/agent/loop.py`(追加两函数)
- Test: `backend/tests/test_loop.py`(追加)

**Interfaces:**
- Consumes: `pending_store.read/write/clear`、`registry.run`、`run_agent_streaming`。
- Produces: `loop.resume_streaming(sid: str, tool_call_id: str, decision: str)`(生成器,decision ∈ `"approve"|"reject"`);`loop.reject_all_pending(sid: str) -> None`。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_loop.py` 末尾追加:

```python
def test_resume_approve_executes_and_continues(p2b_ready):
    sid, proj = p2b_ready
    events = list(loop.run_agent_streaming(sid))            # 先跑到审批暂停
    ar = next(e for e in events if e["type"] == "approval_required")

    ev2 = list(loop.resume_streaming(sid, ar["id"], "approve"))
    assert any(e["type"] == "done" for e in ev2)            # 续跑拿到终答
    assert (proj / "out.txt").read_text(encoding="utf-8") == "hello"   # 真写了
    msgs = session_store.read_messages(sid)
    assert any(m["role"] == "tool" and m["tool_call_id"] == ar["id"] for m in msgs)
    assert pending.read(sid) is None                        # sidecar 已清


def test_resume_reject_records_and_continues(p2b_ready):
    sid, proj = p2b_ready
    events = list(loop.run_agent_streaming(sid))
    ar = next(e for e in events if e["type"] == "approval_required")

    ev2 = list(loop.resume_streaming(sid, ar["id"], "reject"))
    assert any(e["type"] == "done" for e in ev2)
    assert not (proj / "out.txt").exists()                  # 没写
    msgs = session_store.read_messages(sid)
    tool_msg = next(m for m in msgs if m["role"] == "tool" and m["tool_call_id"] == ar["id"])
    assert "拒绝" in tool_msg["content"]
    assert pending.read(sid) is None


def test_reject_all_pending_collapses(p2b_ready):
    sid, _ = p2b_ready
    list(loop.run_agent_streaming(sid))                     # 造出一个待审批
    loop.reject_all_pending(sid)
    assert pending.read(sid) is None
    msgs = session_store.read_messages(sid)
    assert any(m["role"] == "tool" and "拒绝" in m["content"] for m in msgs)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_loop.py::test_resume_approve_executes_and_continues -v`
Expected: FAIL(`AttributeError: module 'app.agent.loop' has no attribute 'resume_streaming'`)。

- [ ] **Step 3: 实现**

在 `backend/app/agent/loop.py` 末尾追加:

```python
def resume_streaming(sid: str, tool_call_id: str, decision: str):
    """恢复一个待审批的 tool_call。decision ∈ 'approve'|'reject'。
    批准 → 真执行;拒绝 → 落「已拒绝」。若本轮还有别的待批 → 结束等下次;全批完 → 继续正常循环。
    """
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
    logger.info("审批恢复: sid=%s, id=%s, decision=%s", sid, tool_call_id, decision)
    yield {"type": "tool_result", "id": tool_call_id, "result": result}
    session_store.append_message(sid, {"role": "tool", "tool_call_id": tool_call_id, "content": result})

    remaining = [t for t in pend["tool_calls"] if t["id"] != tool_call_id]
    if remaining:                              # 本轮还有别的待批 → 结束,等下次点击
        prev = {k: v for k, v in pend["previews"].items() if k != tool_call_id}
        pending_store.write(sid, remaining, prev)
        return
    pending_store.clear(sid)                   # 全批完 → 带新结果继续问模型
    yield from run_agent_streaming(sid)


def reject_all_pending(sid: str) -> None:
    """把某会话所有待批操作按拒绝落盘并清 sidecar(不 yield)。
    用于「审批未决、用户却发了新消息」:先把悬空协议合法地收尾,再走新消息。"""
    pend = pending_store.read(sid)
    if not pend:
        return
    for tc in pend["tool_calls"]:
        session_store.append_message(sid, {
            "role": "tool", "tool_call_id": tc["id"],
            "content": "用户已拒绝此操作(发起了新消息)"})
    pending_store.clear(sid)
    logger.info("残留 pending 自动拒绝: sid=%s, 数量=%d", sid, len(pend["tool_calls"]))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_loop.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/agent/loop.py backend/tests/test_loop.py
git commit -m "feat(p2b): resume_streaming 恢复执行 + reject_all_pending 残留收尾"
```

---

### Task 8: 路由 —— `/api/chat/resume` + `/stream` 残留防护 + ResumeRequest

**Files:**
- Modify: `backend/app/models/schemas.py`(加 ResumeRequest)
- Modify: `backend/app/api/routes/chat.py`(加 /resume + /stream 开头残留防护)
- Test: `backend/tests/test_chat_routes.py`(追加)

**Interfaces:**
- Consumes: `loop.resume_streaming`、`loop.reject_all_pending`、`pending_store.read`。
- Produces: `schemas.ResumeRequest(session_id: str, tool_call_id: str, decision: Literal["approve","reject"])`;`POST /api/chat/resume`(SSE)。

- [ ] **Step 1: 写失败测试**

先看现有 `backend/tests/test_chat_routes.py` 顶部的 fake LLM / fixture 写法,复用它。在文件末尾追加(下面用 `_sse_events(resp)` 小工具解析 SSE;若文件里已有等价解析则复用):

```python
# ============ P2b: /resume 端到端 ============
import json as _json

from app.agent import pending


def _parse_sse(text: str) -> list[dict]:
    out = []
    for part in text.split("\n\n"):
        line = part.strip()
        if line.startswith("data:"):
            out.append(_json.loads(line[len("data:"):].strip()))
    return out


def test_resume_endpoint_executes(client_ws):
    """client_ws:已配置 workspace + mock「先 write_file 再终答」的 TestClient(见下方 Step 3 说明)。"""
    client, sid = client_ws
    # 1) 发消息 → 触发审批,流里带 approval_required
    r1 = client.post("/api/chat/stream", json={"session_id": sid, "message": "写文件"})
    ev1 = _parse_sse(r1.text)
    ar = next(e for e in ev1 if e["type"] == "approval_required")
    assert pending.read(sid) is not None
    # 2) 批准 → /resume 续跑到 done
    r2 = client.post("/api/chat/resume",
                     json={"session_id": sid, "tool_call_id": ar["id"], "decision": "approve"})
    ev2 = _parse_sse(r2.text)
    assert any(e["type"] == "done" for e in ev2)
    assert pending.read(sid) is None
```

> 说明:`client_ws` fixture 用 FastAPI `TestClient` + monkeypatch `llm.get_llm_client` 返回「先 write_file 再终答」的共享 client(参照 `p2b_ready`),并 `config_store.update` 配好 workspace。把它加到 `test_chat_routes.py`(或 conftest)。若现有测试已有类似 `client` fixture,在其基础上扩展。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_chat_routes.py -q`
Expected: FAIL(404:`/api/chat/resume` 不存在)。

- [ ] **Step 3: 实现**

在 `backend/app/models/schemas.py` 末尾追加(顶部加 `from typing import Literal`):

```python
# ---- 审批恢复(P2b) ----
class ResumeRequest(BaseModel):
    session_id: str
    tool_call_id: str
    decision: Literal["approve", "reject"]
```

在 `backend/app/api/routes/chat.py`:import 区加 `from app.agent import pending as pending_store`。在 `chat_stream` 的 `event_stream` 里、`append_message(user)` **之前**加残留防护:

```python
        sid = req.session_id or session_store.create()
        try:
            # 残留 pending 防护:审批未决却发了新消息 → 先自动拒绝收尾,避免悬空毒死会话
            if req.session_id and pending_store.read(sid):
                loop.reject_all_pending(sid)
            session_store.append_message(sid, {"role": "user", "content": req.message})
            ...  # 其余不变
```

在文件末尾追加 `/resume` 路由:

```python
@router.post("/resume")
def chat_resume(req: schemas.ResumeRequest) -> StreamingResponse:
    logger.info("resume 请求: sid=%s, id=%s, decision=%s",
                req.session_id, req.tool_call_id, req.decision)

    def event_stream():
        try:
            for event in loop.resume_streaming(req.session_id, req.tool_call_id, req.decision):
                yield _sse(event)
        except Exception as e:  # noqa: BLE001
            logger.warning("resume 失败: sid=%s err=%s", req.session_id, type(e).__name__)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_chat_routes.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/models/schemas.py backend/app/api/routes/chat.py backend/tests/test_chat_routes.py
git commit -m "feat(p2b): /api/chat/resume 端点 + /stream 残留 pending 防护"
```

---

### Task 9: `GET /api/sessions/{sid}` 返回 `pending`(历史回放用)

**Files:**
- Modify: `backend/app/api/routes/session.py`(get_session 加 pending)
- Test: `backend/tests/test_session_routes.py`(追加;若文件不存在则创建)

**Interfaces:**
- Consumes: `pending_store.read(sid)`。
- Produces: `GET /api/sessions/{sid}` 响应体新增 `pending` 字段(sidecar 内容或 null)。**向后兼容新增**。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_session_routes.py` 末尾追加(若无此文件则新建,fixture 参照其它路由测试用 `TestClient`;下面假设已有 `client` + tmp data_dir fixture,命名 `api`):

```python
# ============ P2b: get_session 带 pending ============
from app.agent import pending


def test_get_session_includes_pending(api):
    client = api
    sid = session_store.create()
    session_store.append_message(sid, {"role": "user", "content": "hi"})
    # 无 pending → null
    assert client.get(f"/api/sessions/{sid}").json()["pending"] is None
    # 有 pending → 回内容
    pending.write(sid, [{"id": "w1", "type": "function",
                         "function": {"name": "write_file", "arguments": "{}"}}],
                  {"w1": {"kind": "write", "path": "a", "diff": "d"}})
    body = client.get(f"/api/sessions/{sid}").json()
    assert body["pending"]["tool_calls"][0]["id"] == "w1"
```

> 若项目还没有 `test_session_routes.py`,新建它:顶部 `from fastapi.testclient import TestClient` + `from app.api.main import app`(参照 test_chat_routes.py 的 app 导入),`api` fixture 用 tmp data_dir + `TestClient(app)`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_session_routes.py -q`
Expected: FAIL(`KeyError: 'pending'`)。

- [ ] **Step 3: 实现**

在 `backend/app/api/routes/session.py`:import 区加 `from app.agent import pending as pending_store`,改 `get_session`:

```python
@router.get("/{sid}")
def get_session(sid: str) -> dict:
    """切会话时前端拉历史铺进消息流;pending 用于还原「待批准」卡片(向后兼容新增字段)。"""
    try:
        messages = session_store.read_messages(sid)
    except session_store.SessionNotFound:
        raise HTTPException(status_code=404, detail="session not found")
    return {"messages": messages, "pending": pending_store.read(sid)}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_session_routes.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/api/routes/session.py backend/tests/test_session_routes.py
git commit -m "feat(p2b): GET /api/sessions/{sid} 返回 pending(历史回放待审批卡)"
```

---

### Task 10: 前端 `api.ts` —— approval_required 事件 + resumeChat + getSession pending

**Files:**
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces:`ChatEvent` union 新增 `approval_required`;`ApprovalPreview`、`PendingState` 类型;`resumeChat(sessionId, toolCallId, decision, onEvent)`;`getSession` 返回 `{ messages, pending }`。

- [ ] **Step 1: 改类型 + 抽公共读流 + 新增 resumeChat**

`frontend/src/lib/api.ts`:

1) `ChatEvent` union 加一支(放在 `tool_result` 之后):
```typescript
  | {
      type: 'approval_required'
      id: string
      name: string
      args: string
      preview: ApprovalPreview
    }
```

2) 新增类型(放 `StoredMessage` 附近):
```typescript
export type ApprovalPreview =
  | { kind: 'write'; path: string; diff: string }
  | { kind: 'command'; command: string; level: string }

export type PendingState = {
  tool_calls: { id: string; function: { name: string; arguments: string } }[]
  previews: Record<string, ApprovalPreview>
} | null
```

3) `getSession` 改为返回 messages + pending:
```typescript
export async function getSession(
  sid: string,
): Promise<{ messages: StoredMessage[]; pending: PendingState }> {
  const r = await fetch(`/api/sessions/${sid}`)
  if (!r.ok) throw new Error('拉取会话历史失败')
  const body = await r.json()
  return { messages: body.messages, pending: body.pending ?? null }
}
```

4) 把 `streamChat` 里读 SSE 的循环抽成公共函数 `readSSE(resp, onEvent)`,`streamChat` 复用它;再加 `resumeChat`:
```typescript
async function readSSE(resp: Response, onEvent: (e: ChatEvent) => void): Promise<void> {
  if (!resp.body) throw new Error('无响应体')
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''
    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data:')) continue
      const payload = line.slice(line.indexOf('data:') + 5).trim()
      if (payload) onEvent(JSON.parse(payload) as ChatEvent)
    }
  }
}

export async function resumeChat(
  sessionId: string,
  toolCallId: string,
  decision: 'approve' | 'reject',
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const resp = await fetch('/api/chat/resume', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, tool_call_id: toolCallId, decision }),
  })
  await readSSE(resp, onEvent)
}
```
`streamChat` 结尾改成 `await readSSE(resp, onEvent)`(删掉原重复的读循环)。

- [ ] **Step 2: 类型门禁**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/frontend && npm run build`
Expected: FAIL(`useChatStream.ts` 里 `getSession` 用法变了 —— 这是预期,下一 Task 修)。**只允许 useChatStream.ts 报错**;api.ts 本身不得报错。

- [ ] **Step 3: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add frontend/src/lib/api.ts
git commit -m "feat(p2b): api.ts 加 approval_required 事件 + resumeChat + getSession pending"
```

---

### Task 11: 前端 `useChatStream.ts` —— 审批卡状态机 + resume + 回放

**Files:**
- Modify: `frontend/src/hooks/useChatStream.ts`

**Interfaces:**
- Consumes: `resumeChat`、`getSession`(返回 `{messages, pending}`)、`ApprovalPreview`。
- Produces:`ChatItem` 的 `tool` 项加 `approval?`;hook 导出 `approve(toolCallId, decision)` 与 `hasPending`。

- [ ] **Step 1: 改 ChatItem + 抽公共事件处理 + approve + 回放带 pending**

`frontend/src/hooks/useChatStream.ts`:

1) import 补 `resumeChat`、`ApprovalPreview`、`PendingState`(从 `../lib/api`)。

2) `ChatItem` 的 `tool` 支路加审批子状态:
```typescript
export type ChatItem =
  | { kind: 'msg'; role: 'user' | 'assistant'; content: string }
  | {
      kind: 'tool'
      id: string
      name: string
      args: string
      result?: string
      approval?: { preview: ApprovalPreview; status: 'pending' | 'approved' | 'rejected' }
    }
```

3) `messagesToItems` 加第二参 `pending`,回放时把待审批的卡标成 pending:
```typescript
function messagesToItems(msgs: StoredMessage[], pending: PendingState): ChatItem[] {
  const items: ChatItem[] = []
  const toolIndex: Record<string, number> = {}
  for (const m of msgs) {
    if (m.role === 'user') {
      items.push({ kind: 'msg', role: 'user', content: m.content ?? '' })
    } else if (m.role === 'assistant') {
      if (m.content) items.push({ kind: 'msg', role: 'assistant', content: m.content })
      for (const tc of m.tool_calls ?? []) {
        toolIndex[tc.id] = items.length
        items.push({ kind: 'tool', id: tc.id, name: tc.function.name, args: tc.function.arguments })
      }
    } else if (m.role === 'tool' && m.tool_call_id != null) {
      const idx = toolIndex[m.tool_call_id]
      const item = idx != null ? items[idx] : undefined
      if (item && item.kind === 'tool') item.result = m.content ?? ''
    }
  }
  // 还原「待审批」卡片:pending 里的 tool_call 还没有结果,挂上 approval 预览
  for (const tc of pending?.tool_calls ?? []) {
    const idx = toolIndex[tc.id]
    const item = idx != null ? items[idx] : undefined
    if (item && item.kind === 'tool') {
      item.approval = { preview: pending!.previews[tc.id], status: 'pending' }
    }
  }
  return items
}
```

4) 把 send 里的 onEvent 抽成共享 `onEvent`(useCallback),加 `approval_required` 分支:
```typescript
  const onEvent = useCallback((e: ChatEvent) => {
    if (e.type === 'session') {
      setCurrentId(e.session_id)
    } else if (e.type === 'approval_required') {
      setMessages((m) => [
        ...m,
        { kind: 'tool', id: e.id, name: e.name, args: e.args,
          approval: { preview: e.preview, status: 'pending' } },
      ])
    } else if (e.type === 'tool_call') {
      setMessages((m) => [...m, { kind: 'tool', id: e.id, name: e.name, args: e.args }])
    } else if (e.type === 'tool_result') {
      setMessages((m) =>
        m.map((it) => (it.kind === 'tool' && it.id === e.id ? { ...it, result: e.result } : it)),
      )
    } else if (e.type === 'text') {
      setMessages((m) => {
        const next = [...m]
        const last = next[next.length - 1]
        if (last && last.kind === 'msg' && last.role === 'assistant') {
          next[next.length - 1] = { ...last, content: last.content + e.content }
        } else {
          next.push({ kind: 'msg', role: 'assistant', content: e.content })
        }
        return next
      })
    } else if (e.type === 'error') {
      setMessages((m) => [...m, { kind: 'msg', role: 'assistant', content: `⚠️ ${e.message}` }])
    }
  }, [])
```
`send` 里改用这个 `onEvent`(删掉原内联版本),依赖数组加 `onEvent`。

5) `switchSession` 用新返回结构:
```typescript
  const switchSession = useCallback(async (sid: string) => {
    setCurrentId(sid)
    const { messages, pending } = await getSession(sid)
    setMessages(messagesToItems(messages, pending))
  }, [])
```

6) 新增 `approve` 与 `hasPending`,并 return 出去:
```typescript
  const approve = useCallback(
    async (toolCallId: string, decision: 'approve' | 'reject') => {
      if (!currentId) return
      setMessages((m) =>
        m.map((it) =>
          it.kind === 'tool' && it.id === toolCallId && it.approval
            ? { ...it, approval: { ...it.approval, status: decision === 'approve' ? 'approved' : 'rejected' } }
            : it,
        ),
      )
      setStreaming(true)
      try {
        await resumeChat(currentId, toolCallId, decision, onEvent)
      } finally {
        setStreaming(false)
        void refreshSessions()
      }
    },
    [currentId, onEvent, refreshSessions],
  )

  const hasPending = messages.some(
    (it) => it.kind === 'tool' && it.approval?.status === 'pending',
  )
```
`return { ... }` 里加 `approve` 和 `hasPending`。

- [ ] **Step 2: 类型门禁(此时应通过)**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/frontend && npm run build`
Expected: FAIL —— 但只应剩 `App.tsx` / `ToolCallCard.tsx` 相关报错(它们还没用新字段);hook 与 api 不得报错。若 hook 自身报错,修到 hook 干净。

- [ ] **Step 3: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add frontend/src/hooks/useChatStream.ts
git commit -m "feat(p2b): useChatStream 审批卡状态机 + approve + 回放待审批"
```

---

### Task 12: 前端 `ToolCallCard` 审批 UI + `App` 接线 + CSS

**Files:**
- Modify: `frontend/src/components/ToolCallCard.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: hook 的 `approve`、`hasPending`;`ApprovalPreview`。
- Produces:审批卡片渲染 diff/命令预览 + [批准]/[拒绝];输入框在 `streaming || hasPending` 时禁用。

- [ ] **Step 1: 改 ToolCallCard(加审批分支)**

`frontend/src/components/ToolCallCard.tsx` 全量替换:

```typescript
import { useState } from 'react'

import type { ApprovalPreview } from '../lib/api'

type Props = {
  name: string
  args: string
  result?: string
  approval?: { preview: ApprovalPreview; status: 'pending' | 'approved' | 'rejected' }
  onDecision?: (decision: 'approve' | 'reject') => void
}

// diff 文本按行着色:+ 绿、- 红、@@ 蓝、其余默认
function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="diff">
      {diff.split('\n').map((line, i) => {
        let cls = 'diff-ctx'
        if (line.startsWith('+')) cls = 'diff-add'
        else if (line.startsWith('-')) cls = 'diff-del'
        else if (line.startsWith('@@')) cls = 'diff-hunk'
        return (
          <div key={i} className={cls}>
            {line || ' '}
          </div>
        )
      })}
    </pre>
  )
}

export default function ToolCallCard({ name, args, result, approval, onDecision }: Props) {
  const [open, setOpen] = useState(approval?.status === 'pending')
  const prettyArgs = (() => {
    try {
      return JSON.stringify(JSON.parse(args), null, 2)
    } catch {
      return args
    }
  })()

  const pending = approval?.status === 'pending'
  const running = result === undefined && !approval
  const summary = pending
    ? approval.preview.kind === 'write'
      ? `待批准:写 ${approval.preview.path}`
      : `待批准:运行 ${approval.preview.command}`
    : running
      ? '运行中…'
      : (result ?? '').split('\n')[0] || '(空)'

  return (
    <div className={`tool-card${pending ? ' tool-card-pending' : ''}`}>
      <div className="tool-head" onClick={() => setOpen((o) => !o)}>
        <span className="tool-name">🔧 {name}</span>
        <span className="tool-summary">
          {pending ? '✋ ' : running ? '⏳ ' : '✓ '}
          {summary}
        </span>
        <span className="tool-toggle">{open ? '▾' : '▸'}</span>
      </div>
      {open && (
        <div className="tool-body">
          {pending && approval.preview.kind === 'write' && (
            <>
              <div className="tool-label">将写入 {approval.preview.path}</div>
              <DiffView diff={approval.preview.diff} />
            </>
          )}
          {pending && approval.preview.kind === 'command' && (
            <>
              <div className="tool-label">将执行命令 ⚠️ 灰名单</div>
              <pre>{approval.preview.command}</pre>
            </>
          )}
          {!pending && (
            <>
              <div className="tool-label">参数</div>
              <pre>{prettyArgs}</pre>
              <div className="tool-label">结果</div>
              <pre>{running ? '运行中…' : result}</pre>
            </>
          )}
          {pending && (
            <div className="approval-actions">
              <button className="btn-approve" onClick={() => onDecision?.('approve')}>
                ✓ 批准
              </button>
              <button className="btn-reject" onClick={() => onDecision?.('reject')}>
                ✗ 拒绝
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: 改 App.tsx(传 approval + onDecision;输入框禁用条件)**

`frontend/src/App.tsx`:

1) 从 hook 解构加 `approve, hasPending`:
```typescript
  const { messages, sessions, currentId, streaming, send, newSession, switchSession,
    removeSession, rename, approve, hasPending } = useChatStream()
```
2) tool 分支传新 props:
```typescript
            it.kind === 'tool' ? (
              <ToolCallCard
                key={i}
                name={it.name}
                args={it.args}
                result={it.result}
                approval={it.approval}
                onDecision={(d) => approve(it.id, d)}
              />
            ) : (
```
3) `onSend` 守卫与按钮/输入禁用把 `streaming` 换成 `streaming || hasPending`:
```typescript
  const onSend = () => {
    const text = input.trim()
    if (!text || streaming || hasPending) return
    setInput('')
    void send(text)
  }
```
```typescript
          <input ... disabled={streaming || hasPending}
            placeholder={hasPending ? '请先处理待批准的操作…' : '说点什么…'} />
          <button onClick={onSend} disabled={streaming || hasPending}>发送</button>
```
(input 原本没 disabled,新增之。)

- [ ] **Step 3: 追加 CSS**

在 `frontend/src/App.css` 末尾追加:

```css
/* P2b 审批卡片 */
.tool-card-pending { border-color: #d29922; background: #fff8e6; }
.approval-actions { display: flex; gap: 8px; margin-top: 8px; }
.approval-actions button { padding: 5px 14px; border: 0; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn-approve { background: #1a7f37; color: #fff; }
.btn-reject { background: #cf222e; color: #fff; }
.diff { margin: 0; font-family: ui-monospace, monospace; font-size: 12px; background: #fff; border: 1px solid #eaeef2; border-radius: 4px; max-height: 320px; overflow: auto; }
.diff > div { padding: 0 8px; white-space: pre-wrap; word-break: break-all; }
.diff-add { background: #e6ffec; color: #1a7f37; }
.diff-del { background: #ffebe9; color: #cf222e; }
.diff-hunk { background: #ddf4ff; color: #0969da; }
.diff-ctx { color: #57606a; }
```

- [ ] **Step 4: 类型门禁 + 构建(应全绿)**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/frontend && npm run build`
Expected: PASS(`tsc -b` 无错 + vite 产物生成)。

- [ ] **Step 5: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add frontend/src/components/ToolCallCard.tsx frontend/src/App.tsx frontend/src/App.css
git commit -m "feat(p2b): 审批卡片 UI(diff/命令预览 + 批准/拒绝)+ 输入锁"
```

---

## 收尾:端到端手验(浏览器)

全部任务完成后,重启后端 + 前端,按 spec 第十节走一遍:
1. 「把某文件里的 X 改成 Y」→ 审批卡带 diff → 批准 → 写入成功。
2. `ls` → 白名单自动跑。
3. `rm -rf xxx` → 黑名单被拒,模型改口。
4. `python xxx.py` → 灰名单弹审批 → 拒绝 → 模型改口。
5. 审批卡挂着时刷新浏览器 → 卡片仍在。
6. `../../etc/passwd` 写越界 → deny,不崩流。
7. 纯聊天 / grep / glob → 与 P2a 一致。

---

## 自检记录(计划 vs spec)

- **spec 覆盖**:命令分级→T1;write_file→T2;run_command→T3;gate→T4;pending→T5;loop 回合边界→T6;resume+reject_all→T7;路由→T8;session pending→T9;前端 api/hook/card→T10-12。全覆盖。
- **占位符**:无 TBD/TODO;每个改代码步骤都给了完整代码。
- **类型一致**:`classify_command`/`gate_tool_call`/`pending.read|write|clear`/`resume_streaming`/`reject_all_pending`/`_parse_args`/`ResumeRequest`/`ApprovalPreview`/`PendingState` 全程同名同签名。前端 `getSession` 返回结构变更在 T10 声明、T11 消费,一致。
- **已知裁剪**:命令沙箱靠名单+cwd(不做路径提取校验)、拆段是启发式、一次一审批、崩溃弱一致 —— 均在 spec 第十一节记录。
