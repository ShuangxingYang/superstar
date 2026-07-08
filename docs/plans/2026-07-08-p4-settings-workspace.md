# P4 设置页 + 工作区权限模型 + 上下文面板 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工作区从「全局单目录」重构为「默认 cwd + 白名单目录组」,并补齐前端设置页与右栏上下文面板。

**Architecture:** 后端 `security` 从单根 `get_workspace()` 改为「`get_default_cwd()` + `get_allowed_roots()` + 多根 `safe_path()`」;新增 `add_workspace`/`remove_workspace` 工具(add 复用 P2b 审批链路)。前端新增 `SettingsPanel`(独立 view)与 `ContextPanel`(右栏),布局两栏→三栏。全局配置,不动 session_store。

**Tech Stack:** 后端 Python 3.11 + FastAPI + pytest(`uv run pytest -q`);前端 React 19 + Vite + TS(`npm run build`)。

## Global Constraints

- **项目初期无用户,破坏性变更允许,不写兼容/迁移/回退逻辑**(见 spec §〇)。直接删 `workspace_dir`、不留旧字段。
- **安全**:key 绝不进日志;API 出参 key 脱敏(`sk-***1234`);`safe_path` 先 `resolve()` 再判祖先(`root in target.parents`);`add_workspace` 审批预览展示 `expanduser().resolve()` 后**绝对路径**。
- **工具约定**:签名 `def f(args: XxxArgs) -> str`,入参用 Pydantic `BaseModel`,在 `app/agent/tools/__init__.py` 末尾 `register`。
- **审批链路复用 P2b**:`gate_tool_call` 返回 `("approve", preview)` → loop 发 `approval_required` + 写 pending → `/resume` 批准时 `registry.run` 真执行。不改 loop/resume/pending。
- **路径**:配置里可写 `~`,读取时 `expanduser()`。
- **测试不依赖真网络/真库**:mock OpenAI client、mock subprocess;工作区用 `tmp_path`。
- **中文注释**,匹配现有文件风格。
- 现有 `data/config.json` 的 `workspace_dir` 值,实现时**手动**迁到 `default_cwd`(运行时数据,一次性,代码不留迁移分支)。

---

## Task 1: 工作区多根重构(security + config_store + grep/glob + run_command 默认 cwd)

把「根」从单个变成一组。删 `get_workspace()`,新增 `get_default_cwd()` / `get_allowed_roots()`,`safe_path` 多根校验;同步改所有调用点(否则删了 `get_workspace` 会 import 崩)。

**Files:**
- Modify: `backend/app/services/config_store.py`(DEFAULTS security 段)
- Modify: `backend/app/services/security.py`
- Modify: `backend/app/agent/tools/search.py`(grep/glob 多根)
- Modify: `backend/app/agent/tools/shell.py`(run_command 默认 cwd)
- Test: `backend/tests/test_security.py`(已存在,追加/改)、`backend/tests/test_search.py`(已存在,改)

**Interfaces:**
- Produces:
  - `security.get_default_cwd() -> Path`(expanduser+resolve,不存在则 mkdir)
  - `security.get_allowed_roots() -> list[Path]`(default_cwd + allowed_dirs,各 expanduser+resolve,去重)
  - `security.safe_path(path: str) -> Path`(落在任一 allowed_root 内,否则 `SecurityError`)
  - `config` 的 `security` 段字段:`default_cwd: str`、`allowed_dirs: list[str]`(**删** `workspace_dir`)

- [ ] **Step 1: 改 config_store DEFAULTS**

`backend/app/services/config_store.py` 的 `DEFAULTS["security"]` 改为:

```python
    "security": {
        "default_cwd": "~/.superstar",
        "allowed_dirs": ["/Users/shuangxingyang/Desktop"],
        "kb_dir": "",
        "cmd_whitelist": ["grep", "ls", "cat", "git status", "find", "wc"],
        "cmd_blacklist": ["rm -rf", "sudo", "curl", "wget", "mkfs", "dd"],
    },
```

同时手动编辑 `backend/data/config.json`:把现有 `security.workspace_dir` 改名为 `default_cwd`(值保留),补一个 `"allowed_dirs": []`。

- [ ] **Step 2: 写失败测试(多根 safe_path)**

`backend/tests/test_security.py` 追加(先读文件头部看现有 fixture 怎么 monkeypatch config;下面假设用 `config_store.update` + `_reset_cache`):

```python
def test_safe_path_multi_root(tmp_path, monkeypatch):
    from app.services import config_store, security
    a = tmp_path / "a"; b = tmp_path / "b"; a.mkdir(); b.mkdir()
    (a / "x.txt").write_text("hi")
    monkeypatch.setattr(config_store, "get", lambda: {
        "security": {"default_cwd": str(a), "allowed_dirs": [str(b)]}
    })
    assert security.safe_path(str(a / "x.txt")) == (a / "x.txt").resolve()   # 命中 default_cwd
    assert security.safe_path(str(b / "y.txt")) == (b / "y.txt").resolve()   # 命中 allowed_dirs
    import pytest
    with pytest.raises(security.SecurityError):
        security.safe_path(str(tmp_path / "outside.txt"))                    # 越界

def test_safe_path_blocks_traversal(tmp_path, monkeypatch):
    from app.services import config_store, security
    a = tmp_path / "a"; a.mkdir()
    monkeypatch.setattr(config_store, "get", lambda: {
        "security": {"default_cwd": str(a), "allowed_dirs": []}
    })
    import pytest
    with pytest.raises(security.SecurityError):
        security.safe_path("../../etc/passwd")
```

- [ ] **Step 3: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_security.py -q`
Expected: FAIL(`get_default_cwd`/多根逻辑还没有,或 `get_workspace` 相关旧断言)

- [ ] **Step 4: 改 security.py**

替换 `get_workspace` 为下面两个函数,并改 `safe_path`:

```python
def get_default_cwd() -> Path:
    """默认工作目录(命令 cwd + 相对基准)。不存在则创建。"""
    raw = config_store.get()["security"].get("default_cwd") or ""
    if not raw:
        roots = config_store.get()["security"].get("allowed_dirs") or []
        raw = roots[0] if roots else ""
    if not raw:
        raise SecurityError("未配置工作目录,请先在设置页指定")
    p = Path(raw).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_allowed_roots() -> list[Path]:
    """所有可访问根 = default_cwd + allowed_dirs(去重)。"""
    sec = config_store.get()["security"]
    raw = [sec.get("default_cwd") or "", *(sec.get("allowed_dirs") or [])]
    roots: list[Path] = []
    for r in raw:
        if not r:
            continue
        p = Path(r).expanduser().resolve()
        if p not in roots:
            roots.append(p)
    if not roots:
        raise SecurityError("未配置任何可访问目录,请先在设置页指定")
    return roots


def safe_path(path: str) -> Path:
    """把路径钉进任一允许根内;都不命中抛 SecurityError。先 resolve 再判祖先。"""
    roots = get_allowed_roots()
    for root in roots:
        target = (root / path).resolve()
        if target == root or root in target.parents:
            return target
    logger.warning("路径越界拦截: path=%s", path)
    raise SecurityError(f"路径越界,超出允许目录: {path}")
```

- [ ] **Step 5: 改 grep/glob 多根 + run_command 默认 cwd**

`search.py`:去掉 `from ... import get_workspace`,改成遍历所有根、输出**绝对路径**。

```python
from app.services.security import get_allowed_roots, safe_path

class GrepArgs(BaseModel):
    pattern: str = Field(description="正则表达式,按行匹配")
    path: str = Field(default="", description="搜索起点(绝对路径);留空搜所有允许目录")

def grep(args: GrepArgs) -> str:
    try:
        regex = re.compile(args.pattern)
    except re.error as e:
        return f"错误:正则表达式非法: {e}"
    bases = [safe_path(args.path)] if args.path else get_allowed_roots()
    hits: list[str] = []
    for base in bases:
        walk_root = base if base.is_dir() else base.parent
        for dirpath, dirnames, filenames in os.walk(walk_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    with open(fp, encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if regex.search(line):
                                hits.append(f"{fp}:{lineno}:{line.rstrip()}")
                                if len(hits) >= MAX_HITS:
                                    hits.append(f"…命中过多(≥{MAX_HITS}),请缩小 pattern 或 path")
                                    return "\n".join(hits)
                except OSError:
                    continue
    return "\n".join(hits) if hits else "(无匹配)"

class GlobArgs(BaseModel):
    pattern: str = Field(description="通配模式,如 **/*.py;在所有允许目录下匹配")

def glob(args: GlobArgs) -> str:
    matches: list[str] = []
    for root in get_allowed_roots():
        try:
            found = list(root.glob(args.pattern))
        except ValueError as e:
            return f"错误:glob 模式非法: {e}"
        for p in found:
            rel = p.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            matches.append(str(p))
            if len(matches) >= MAX_MATCHES:
                matches.append(f"…匹配过多(≥{MAX_MATCHES}),请缩小 pattern")
                return "\n".join(matches)
    return "\n".join(matches) if matches else "(无匹配)"
```

`shell.py`:`get_workspace` → `get_default_cwd`(仅默认 cwd,cwd 参数留给 Task 2):

```python
from app.services import security
# run_command 内:
    cwd = security.get_default_cwd()
```

同时更新 `tests/test_search.py`:mock `get_allowed_roots` 返回 `[tmp_path]`,断言输出为绝对路径。

- [ ] **Step 6: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_security.py tests/test_search.py -q`
Expected: PASS

- [ ] **Step 7: 全量回归(确认删 get_workspace 没漏改调用点)**

Run: `cd backend && uv run pytest -q`
Expected: PASS(若报 `get_workspace` ImportError,补齐漏改的 import)

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/config_store.py backend/app/services/security.py backend/app/agent/tools/search.py backend/app/agent/tools/shell.py backend/tests/test_security.py backend/tests/test_search.py backend/data/config.json
git commit -m "feat(p4): 工作区多根重构(default_cwd+allowed_dirs+多根safe_path,grep/glob搜所有根)"
```

---

## Task 2: run_command 加 cwd 参数

让 agent 指定命令在哪个允许目录跑(默认 default_cwd)。

**Files:**
- Modify: `backend/app/agent/tools/shell.py`
- Test: `backend/tests/test_shell.py`(已存在)

**Interfaces:**
- Consumes: `security.get_default_cwd()`、`security.safe_path()`(Task 1)
- Produces: `RunCommandArgs{command: str, cwd: str | None}`

- [ ] **Step 1: 失败测试**

```python
def test_run_command_custom_cwd(tmp_path, monkeypatch):
    from app.services import config_store
    from app.agent.tools import shell
    sub = tmp_path / "sub"; sub.mkdir()
    monkeypatch.setattr(config_store, "get", lambda: {
        "security": {"default_cwd": str(tmp_path), "allowed_dirs": []}
    })
    called = {}
    def fake_run(cmd, **kw):
        called["cwd"] = kw["cwd"]
        class R: returncode = 0; stdout = "ok"; stderr = ""
        return R()
    monkeypatch.setattr(shell.subprocess, "run", fake_run)
    shell.run_command(shell.RunCommandArgs(command="ls", cwd=str(sub)))
    assert str(called["cwd"]) == str(sub.resolve())

def test_run_command_cwd_out_of_bounds(tmp_path, monkeypatch):
    from app.services import config_store, security
    from app.agent.tools import shell
    monkeypatch.setattr(config_store, "get", lambda: {
        "security": {"default_cwd": str(tmp_path), "allowed_dirs": []}
    })
    import pytest
    with pytest.raises(security.SecurityError):
        shell.run_command(shell.RunCommandArgs(command="ls", cwd="/etc"))
```

- [ ] **Step 2: 运行确认失败** — `cd backend && uv run pytest tests/test_shell.py -q`(FAIL:`RunCommandArgs` 无 `cwd`)

- [ ] **Step 3: 实现**

```python
class RunCommandArgs(BaseModel):
    command: str = Field(description="要执行的 shell 命令")
    cwd: str | None = Field(default=None, description="命令工作目录(绝对路径,须在允许目录内);留空用默认工作目录")

def run_command(args: RunCommandArgs) -> str:
    cwd = security.safe_path(args.cwd) if args.cwd else security.get_default_cwd()
    # …余下 subprocess.run(..., cwd=cwd) 不变
```

> 注:`safe_path` 抛 `SecurityError` 会被 `registry.run` 兜成「安全拦截:…」喂回模型(见 tools/__init__.py),符合自愈约定;测试里直接调 `run_command` 故断言抛错。

- [ ] **Step 4: 运行确认通过** — `cd backend && uv run pytest tests/test_shell.py -q`(PASS)

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/tools/shell.py backend/tests/test_shell.py
git commit -m "feat(p4): run_command 加 cwd 参数(限允许目录内)"
```

---

## Task 3: workspace 工具(add_workspace / remove_workspace)+ 注册

**Files:**
- Create: `backend/app/agent/tools/workspace.py`
- Modify: `backend/app/agent/tools/__init__.py`(注册)
- Modify: `backend/app/agent/loop.py`(SYSTEM_PROMPT 补一句)
- Test: `backend/tests/test_workspace_tool.py`(新建)

**Interfaces:**
- Consumes: `config_store.get()` / `config_store.update()`
- Produces:
  - `add_workspace(args: AddWorkspaceArgs) -> str`,`AddWorkspaceArgs{path: str}`
  - `remove_workspace(args: RemoveWorkspaceArgs) -> str`,`RemoveWorkspaceArgs{path: str}`

- [ ] **Step 1: 失败测试**

```python
def test_add_workspace_appends_dedup(tmp_path, monkeypatch):
    from app.services import config_store
    from app.agent.tools import workspace
    store = {"security": {"default_cwd": str(tmp_path), "allowed_dirs": []}}
    monkeypatch.setattr(config_store, "get", lambda: store)
    def fake_update(partial):
        store["security"]["allowed_dirs"] = partial["security"]["allowed_dirs"]
        return store
    monkeypatch.setattr(config_store, "update", fake_update)
    d = tmp_path / "proj"; d.mkdir()
    workspace.add_workspace(workspace.AddWorkspaceArgs(path=str(d)))
    assert str(d.resolve()) in store["security"]["allowed_dirs"]
    workspace.add_workspace(workspace.AddWorkspaceArgs(path=str(d)))   # 去重
    assert store["security"]["allowed_dirs"].count(str(d.resolve())) == 1

def test_remove_workspace_idempotent(tmp_path, monkeypatch):
    from app.services import config_store
    from app.agent.tools import workspace
    d = tmp_path / "proj"; d.mkdir()
    store = {"security": {"default_cwd": str(tmp_path), "allowed_dirs": [str(d.resolve())]}}
    monkeypatch.setattr(config_store, "get", lambda: store)
    monkeypatch.setattr(config_store, "update",
        lambda p: store["security"].__setitem__("allowed_dirs", p["security"]["allowed_dirs"]) or store)
    workspace.remove_workspace(workspace.RemoveWorkspaceArgs(path=str(d)))
    assert str(d.resolve()) not in store["security"]["allowed_dirs"]
    workspace.remove_workspace(workspace.RemoveWorkspaceArgs(path=str(d)))  # 幂等,不炸
```

- [ ] **Step 2: 运行确认失败** — `cd backend && uv run pytest tests/test_workspace_tool.py -q`(FAIL:模块不存在)

- [ ] **Step 3: 实现 workspace.py**

```python
"""workspace.py —— agent 动态增删可访问目录(白名单)。
add 走审批(gate 判定),remove 自动放行(收权无害)。执行体只管读写 config。"""
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from app.services import config_store

logger = logging.getLogger(__name__)


class AddWorkspaceArgs(BaseModel):
    path: str = Field(description="要加入可访问白名单的目录(绝对路径)")


def add_workspace(args: AddWorkspaceArgs) -> str:
    abs_path = str(Path(args.path).expanduser().resolve())
    dirs = list(config_store.get()["security"].get("allowed_dirs") or [])
    if abs_path not in dirs:
        dirs.append(abs_path)
        config_store.update({"security": {"allowed_dirs": dirs}})
    logger.info("加入可访问目录: %s", abs_path)
    return f"已加入可访问目录:{abs_path}"


class RemoveWorkspaceArgs(BaseModel):
    path: str = Field(description="要从白名单移除的目录(绝对路径)")


def remove_workspace(args: RemoveWorkspaceArgs) -> str:
    abs_path = str(Path(args.path).expanduser().resolve())
    dirs = list(config_store.get()["security"].get("allowed_dirs") or [])
    if abs_path in dirs:
        dirs.remove(abs_path)
        config_store.update({"security": {"allowed_dirs": dirs}})
        logger.info("移除可访问目录: %s", abs_path)
        return f"已移除可访问目录:{abs_path}"
    return f"目录不在白名单中(无需移除):{abs_path}"
```

- [ ] **Step 4: 注册 + 补 system prompt**

`tools/__init__.py` 末尾追加:

```python
from app.agent.tools.workspace import (  # noqa: E402
    AddWorkspaceArgs, RemoveWorkspaceArgs, add_workspace, remove_workspace,
)

registry.register(
    "add_workspace", add_workspace, AddWorkspaceArgs,
    "把一个目录(绝对路径)加入可访问白名单,之后就能读写它。此操作需用户审批。",
)
registry.register(
    "remove_workspace", remove_workspace, RemoveWorkspaceArgs,
    "把一个目录从可访问白名单移除。",
)
```

`loop.py` 的 `SYSTEM_PROMPT` 末尾加一句:

```
"需要访问当前允许目录之外的文件时,用 add_workspace 申请加入该目录(绝对路径,需用户批准);不再需要时用 remove_workspace 移除。"
```

- [ ] **Step 5: 运行确认通过 + Commit**

```bash
cd backend && uv run pytest tests/test_workspace_tool.py -q
git add backend/app/agent/tools/workspace.py backend/app/agent/tools/__init__.py backend/app/agent/loop.py backend/tests/test_workspace_tool.py
git commit -m "feat(p4): add_workspace/remove_workspace 工具 + 注册"
```

---

## Task 4: gate 接 add_workspace 审批

**Files:**
- Modify: `backend/app/agent/gate.py`
- Test: `backend/tests/test_gate.py`(已存在)

**Interfaces:**
- Consumes: `AddWorkspaceArgs`(Task 3)
- Produces: `gate_tool_call("add_workspace", {"path": ...})` → `("approve", {"kind": "add_workspace", "path": <绝对路径>})`;`remove_workspace` → `("auto", None)`

- [ ] **Step 1: 失败测试**

```python
def test_gate_add_workspace_needs_approval():
    from app.agent.gate import gate_tool_call
    action, preview = gate_tool_call("add_workspace", {"path": "~/proj"})
    assert action == "approve"
    assert preview["kind"] == "add_workspace"
    assert preview["path"].startswith("/")          # 绝对路径
    assert "~" not in preview["path"]

def test_gate_remove_workspace_auto():
    from app.agent.gate import gate_tool_call
    assert gate_tool_call("remove_workspace", {"path": "/x"}) == ("auto", None)
```

- [ ] **Step 2: 运行确认失败** — `cd backend && uv run pytest tests/test_gate.py -q`(FAIL:add_workspace 走了默认 auto 分支)

- [ ] **Step 3: 实现(gate.py 加分支)**

在 `gate_tool_call` 的 `run_command` 分支之后、`return "auto", None` 之前插入:

```python
    if name == "add_workspace":
        abs_path = str(Path(args.get("path", "")).expanduser().resolve())
        return "approve", {"kind": "add_workspace", "path": abs_path}
```

文件顶部加 `from pathlib import Path`。

- [ ] **Step 4: 运行确认通过 + Commit**

```bash
cd backend && uv run pytest tests/test_gate.py -q
git add backend/app/agent/gate.py backend/tests/test_gate.py
git commit -m "feat(p4): gate 把 add_workspace 判为需审批(展示绝对路径)"
```

---

## Task 5: schemas(security 字段 + test kind)+ settings 路由 embedding 测连接

**Files:**
- Modify: `backend/app/models/schemas.py`
- Modify: `backend/app/api/routes/settings.py`
- Test: `backend/tests/test_settings_routes.py`(已存在)、`backend/tests/test_schemas.py`(已存在)

**Interfaces:**
- Produces:
  - `SecuritySettings{default_cwd: str, allowed_dirs: list[str], kb_dir: str, cmd_whitelist, cmd_blacklist}`(删 `workspace_dir`)
  - `SecurityUpdate` 同上字段全可选
  - `TestConnectionRequest{base_url, api_key, model, kind: Literal["llm","embedding"]}`

- [ ] **Step 1: 失败测试**

```python
def test_embedding_test_connection(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.main import app
    class FakeEmb:
        def create(self, **kw): return object()
    class FakeClient:
        embeddings = FakeEmb()
        def __init__(self, **kw): pass
    import app.api.routes.settings as s
    monkeypatch.setattr(s, "OpenAI", FakeClient)
    c = TestClient(app)
    r = c.post("/api/settings/test", json={
        "base_url": "u", "api_key": "sk-x", "model": "text-embedding-v3", "kind": "embedding"})
    assert r.json()["ok"] is True

def test_security_settings_roundtrip():
    from app.models import schemas
    cfg = schemas.to_masked_config({
        "llm": {"base_url": "u", "api_key": "sk-abcdef123456", "model": "m"},
        "embedding": {"base_url": "u2", "api_key": "sk-zzzz9999", "model": "e"},
        "security": {"default_cwd": "~/.superstar", "allowed_dirs": ["/tmp"],
                     "kb_dir": "", "cmd_whitelist": [], "cmd_blacklist": []},
        "agent": {"max_iters": 10, "temperature": 0.7},
        "rag": {"chunk_size": 500, "overlap": 80, "top_n": 20, "top_k": 5, "rerank_model": "gte-rerank"},
    })
    assert cfg.security.default_cwd == "~/.superstar"
    assert cfg.security.allowed_dirs == ["/tmp"]
```

- [ ] **Step 2: 运行确认失败** — `cd backend && uv run pytest tests/test_settings_routes.py tests/test_schemas.py -q`(FAIL)

- [ ] **Step 3: 改 schemas.py**

`SecuritySettings` 与 `SecurityUpdate` 把 `workspace_dir` 换成 `default_cwd` + `allowed_dirs`:

```python
class SecuritySettings(BaseModel):
    default_cwd: str = ""
    allowed_dirs: list[str] = []
    kb_dir: str = ""
    cmd_whitelist: list[str] = []
    cmd_blacklist: list[str] = []

class SecurityUpdate(BaseModel):
    default_cwd: str | None = None
    allowed_dirs: list[str] | None = None
    kb_dir: str | None = None
    cmd_whitelist: list[str] | None = None
    cmd_blacklist: list[str] | None = None
```

`TestConnectionRequest` 加 `kind`:

```python
class TestConnectionRequest(BaseModel):
    base_url: str
    api_key: str
    model: str
    kind: Literal["llm", "embedding"] = "llm"
```

- [ ] **Step 4: 改 settings.py test 路由按 kind 分流**

```python
@router.post("/test", response_model=schemas.TestConnectionResult)
def test_connection(req: schemas.TestConnectionRequest) -> schemas.TestConnectionResult:
    try:
        client = OpenAI(api_key=req.api_key, base_url=req.base_url or None, timeout=20)
        if req.kind == "embedding":
            client.embeddings.create(model=req.model, input="ping")
        else:
            client.chat.completions.create(
                model=req.model, messages=[{"role": "user", "content": "ping"}], max_tokens=1)
        return schemas.TestConnectionResult(ok=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("测试连接失败(%s): %s", req.kind, type(e).__name__)
        return schemas.TestConnectionResult(ok=False, error=str(e))
```

- [ ] **Step 5: 运行确认通过 + 全量回归**

```bash
cd backend && uv run pytest -q
```
Expected: PASS(若旧测试引用 `workspace_dir` 报错,一并改成 `default_cwd`)

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/schemas.py backend/app/api/routes/settings.py backend/tests/test_settings_routes.py backend/tests/test_schemas.py
git commit -m "feat(p4): schemas security 改 default_cwd+allowed_dirs;test 路由支持 embedding kind"
```

---

## Task 6: 前端 api.ts —— settings 封装 + ApprovalPreview 加 kind

**Files:**
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces: `getSettings()`、`updateSettings(partial)`、`testConnection(kind, body)`、类型 `AppConfig`/`ConfigUpdate`;`ApprovalPreview` 新增 `add_workspace` 分支

- [ ] **Step 1: 加类型 + 函数**(在 kb 段之后追加):

```ts
export type AppConfig = {
  llm: { base_url: string; api_key: string; model: string }
  embedding: { base_url: string; api_key: string; model: string }
  security: {
    default_cwd: string; allowed_dirs: string[]; kb_dir: string
    cmd_whitelist: string[]; cmd_blacklist: string[]
  }
  agent: { max_iters: number; temperature: number }
}
export type ConfigUpdate = {
  llm?: Partial<AppConfig['llm']>
  embedding?: Partial<AppConfig['embedding']>
  security?: Partial<AppConfig['security']>
  agent?: Partial<AppConfig['agent']>
}

export async function getSettings(): Promise<AppConfig> {
  const r = await fetch('/api/settings')
  if (!r.ok) throw new Error('拉取设置失败')
  return r.json()
}
export async function updateSettings(partial: ConfigUpdate): Promise<AppConfig> {
  const r = await fetch('/api/settings', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(partial),
  })
  if (!r.ok) throw new Error('保存设置失败')
  return r.json()
}
export async function testConnection(
  kind: 'llm' | 'embedding',
  body: { base_url: string; api_key: string; model: string },
): Promise<{ ok: boolean; error: string }> {
  const r = await fetch('/api/settings/test', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, kind }),
  })
  return r.json()
}
```

- [ ] **Step 2: `ApprovalPreview` 加分支**

```ts
export type ApprovalPreview =
  | { kind: 'write'; path: string; diff: string }
  | { kind: 'command'; command: string; level: string }
  | { kind: 'add_workspace'; path: string }
```

- [ ] **Step 3: build + Commit**

```bash
cd frontend && npm run build
git add frontend/src/lib/api.ts
git commit -m "feat(p4): 前端 settings API 封装 + ApprovalPreview 加 add_workspace"
```

---

## Task 7: ToolCallCard 支持 add_workspace 预览

**Files:**
- Modify: `frontend/src/components/ToolCallCard.tsx`

**Interfaces:**
- Consumes: `ApprovalPreview`(Task 6)

- [ ] **Step 1: summary 加分支**(现有三元:write / command,补 add_workspace)

把 `summary` 里 pending 部分改为:

```tsx
  const summary = pending
    ? approval.preview.kind === 'write'
      ? `待批准:写 ${approval.preview.path}`
      : approval.preview.kind === 'command'
        ? `待批准:运行 ${approval.preview.command}`
        : `待批准:加入工作区 ${approval.preview.path}`
    : running
      ? '运行中…'
      : (result ?? '').split('\n')[0] || '(空)'
```

- [ ] **Step 2: 展开区加分支**(在 command 分支后):

```tsx
          {pending && approval.preview.kind === 'add_workspace' && (
            <div className="mb-1.5 font-mono text-[10.5px] uppercase tracking-wide text-muted-foreground">
              将把以下目录加入可访问白名单:
              <pre className="mt-1.5 overflow-x-auto rounded-[10px] bg-background px-3 py-2.5 text-xs normal-case">
                {approval.preview.path}
              </pre>
            </div>
          )}
```

- [ ] **Step 3: build + Commit**

```bash
cd frontend && npm run build
git add frontend/src/components/ToolCallCard.tsx
git commit -m "feat(p4): 审批卡支持 add_workspace 预览"
```

---

## Task 8: 设置页 SettingsPanel + App settings view + 侧栏 ⚙️ 可点

**Files:**
- Create: `frontend/src/components/SettingsPanel.tsx`
- Modify: `frontend/src/App.tsx`(view 加 'settings')
- Modify: `frontend/src/components/SessionList.tsx`(⚙️ onClick)

**Interfaces:**
- Consumes: `getSettings`/`updateSettings`/`testConnection`(Task 6)、`AppConfig`

**参照**:`KbManager.tsx` 的 shadcn + 液态玻璃风格(卡片 `rounded-2xl bg-card shadow-soft-md`、按钮 `grad-brand rounded-full`)。用 `Input`(`@/components/ui/input`)、`Button`(`@/components/ui/button`)。

- [ ] **Step 1: 写 SettingsPanel.tsx**

结构(单文件,`useEffect` 拉 `getSettings` 填表单,本地 state 编辑,分区渲染):

```tsx
import { useEffect, useState } from 'react'
import { getSettings, updateSettings, testConnection } from '../lib/api'
import type { AppConfig } from '../lib/api'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

export default function SettingsPanel() {
  const [cfg, setCfg] = useState<AppConfig | null>(null)
  const [saved, setSaved] = useState(false)
  const [test, setTest] = useState<Record<string, string>>({}) // 'llm'|'embedding' -> 状态文案

  useEffect(() => { getSettings().then(setCfg) }, [])
  if (!cfg) return <div className="p-6 text-muted-foreground">加载中…</div>

  const patch = (section: keyof AppConfig, key: string, val: unknown) =>
    setCfg({ ...cfg, [section]: { ...cfg[section], [key]: val } })

  const onSave = async () => {
    const next = await updateSettings(cfg)   // 脱敏 key 未改则原样回传,后端 _drop_masked_keys 丢弃
    setCfg(next); setSaved(true); setTimeout(() => setSaved(false), 2000)
  }
  const onTest = async (kind: 'llm' | 'embedding') => {
    setTest((t) => ({ ...t, [kind]: '测试中…' }))
    const r = await testConnection(kind, cfg[kind])
    setTest((t) => ({ ...t, [kind]: r.ok ? '✓ 连接成功' : `✗ ${r.error}` }))
  }

  // 渲染:LLM 卡 / embedding 卡(各 base_url+api_key+model+[测试连接]+状态文案)
  //       安全卡(default_cwd 输入;allowed_dirs 列表增删;kb_dir 输入;cmd_whitelist/blacklist 列表增删)
  //       Agent 卡(max_iters/temperature number 输入)
  //       底部 [保存] 按钮 + saved 提示
  // 列表增删用一个小的 StringList 子组件(输入框 + 加号 + 每项后跟删除)
  return (/* 参照 KbManager 的卡片布局,mx-auto max-w-2xl 分区堆叠 */)
}
```

> 列表字段(`allowed_dirs` / `cmd_whitelist` / `cmd_blacklist`)用一个内联 `StringList` 子组件:`items: string[]` + `onChange`,渲染每项一行(`Input` + 删除按钮)+ 底部"添加"输入。api_key 输入框 `placeholder` 显示当前脱敏值,用户不改就原样带回。

- [ ] **Step 2: App.tsx 接入 settings view**

`type View` 加 `'settings'`(改 `useState<'chat' | 'kb'>` → `'chat' | 'kb' | 'settings'`);主区渲染加分支:

```tsx
{view === 'settings' ? (
  <div className="flex-1 overflow-y-auto"><SettingsPanel /></div>
) : view === 'kb' ? ( /* 现有 */ ) : ( /* 现有聊天 */ )}
```

`SessionList` 传 `onOpenSettings={() => setView('settings')}`,并把 `activeView` 类型放宽到含 `'settings'`。

- [ ] **Step 3: SessionList ⚙️ 可点**

`Props` 加 `onOpenSettings: () => void`;把设置 `NavIcon` 的 `disabled` 去掉,`active={activeView === 'settings'}`、`onClick={onOpenSettings}`,label 去掉"敬请期待"。

- [ ] **Step 4: build + 手动验证 + Commit**

```bash
cd frontend && npm run build
```
手动:⚙️ 切到设置页;改 model、点测试连接看状态;改 allowed_dirs 增删;保存后刷新仍在。

```bash
git add frontend/src/components/SettingsPanel.tsx frontend/src/App.tsx frontend/src/components/SessionList.tsx
git commit -m "feat(p4): 设置页 SettingsPanel + ⚙️ 入口"
```

---

## Task 9: 右栏上下文面板 ContextPanel + 三栏布局

**Files:**
- Create: `frontend/src/components/ContextPanel.tsx`
- Modify: `frontend/src/App.tsx`(聊天视图右侧加栏)

**Interfaces:**
- Consumes: `getSettings`、`kbStats`(现有)

- [ ] **Step 1: 写 ContextPanel.tsx**

```tsx
import { useEffect, useState } from 'react'
import { BookOpen, FolderOpen, Settings } from 'lucide-react'
import { getSettings, kbStats } from '../lib/api'

export default function ContextPanel({ onOpenKb, onOpenSettings }: {
  onOpenKb: () => void; onOpenSettings: () => void
}) {
  const [cwd, setCwd] = useState(''); const [dirs, setDirs] = useState<string[]>([])
  const [docs, setDocs] = useState<number | null>(null)
  useEffect(() => {
    getSettings().then((c) => { setCwd(c.security.default_cwd); setDirs(c.security.allowed_dirs) })
    kbStats().then((s) => setDocs(s.documents)).catch(() => setDocs(null))
  }, [])
  // 渲染:glass 右栏(w-64 border-l),三块小卡:
  //   「工作目录」cwd + 允许目录列表(FolderOpen 图标)
  //   「知识库」docs 篇 + [管理] 按钮(onOpenKb)
  //   底部 [设置] 按钮(onOpenSettings)
  return (/* 参照液态玻璃:glass / shadow-soft-* / text-muted-foreground */)
}
```

- [ ] **Step 2: App.tsx 三栏**

聊天视图(`view === 'chat'`)在 `<main>` 之后加 `<ContextPanel onOpenKb={() => setView('kb')} onOpenSettings={() => setView('settings')} />`。外层已是 `flex h-screen`,右栏 `shrink-0` 即可(kb/settings 视图下不渲染右栏,保持宽敞)。

- [ ] **Step 3: build + 手动 + Commit**

```bash
cd frontend && npm run build
git add frontend/src/components/ContextPanel.tsx frontend/src/App.tsx
git commit -m "feat(p4): 右栏上下文面板(工作目录/白名单/知识库数)+ 三栏布局"
```

---

## Task 10: 各态收尾 + 全量回归验收

**Files:**
- Modify: `frontend/src/App.tsx`(首启引导)、`SettingsPanel.tsx`(态微调)

- [ ] **Step 1: 首启未配 LLM 引导**

App 挂载时拉一次 `getSettings`,若 `llm.api_key`/`model` 为空(脱敏后空串),聊天空态里加一句"还没配模型,点这里去设置 →"(按钮 `setView('settings')`)。

- [ ] **Step 2: 态微调**

确认已覆盖:测连接「测试中/成功/失败」文案(Task 8 已做)、保存成功提示(Task 8 已做)、知识库 0 篇空态、白名单空列表提示。补缺失的。

- [ ] **Step 3: 后端全量回归**

Run: `cd backend && uv run pytest -q`
Expected: PASS(全绿)

- [ ] **Step 4: 前端 build + 端到端手动验收**

```bash
cd frontend && npm run build
```
起后端 `cd backend && uv run python run.py` + 前端 `npm run dev`,过一遍:
- 设置页配 LLM/embedding → 双测连接;
- 安全设置改 default_cwd / 增删 allowed_dirs → 保存热生效;
- 对话里让 agent `add_workspace` 一个新目录 → 弹审批卡(展示绝对路径)→ 批准 → 白名单多一条;
- 右栏显示工作目录 / 白名单 / 知识库数,入口可跳转。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/SettingsPanel.tsx
git commit -m "feat(p4): 首启引导 + 各态收尾"
```

---

## 附:自检备注

- **Spec 覆盖**:§二工作区模型→Task1-4;§3.7/3.8 schemas+embedding测连→Task5;§四设置页→Task6/8;右栏→Task9;各态→Task10。全覆盖。
- **破坏性重构**:Task1 删 `workspace_dir`/`get_workspace`,Task5 删 schemas `workspace_dir`——一次到位,不留兼容(符合 §〇)。
- **审批复用**:Task3/4 只加工具 + gate 分支,loop/resume/pending 零改动。
