# P2a 工具 + 只读安全 实现计划

> **执行方式**:用 executing-plans 逐任务实现。步骤用 `- [ ]` 勾选跟踪。设计见 `docs/specs/2026-07-06-p2a-tools-readonly-design.md`。

**目标:** 让 Agent 从「只会聊天」升级到「能看你的代码」——引入 function calling 循环 + 3 个只读工具(read_file / grep / glob)+ workspace 沙箱 + 前端可折叠工具卡片。全程只读,无写、无命令、无审批。

**架构:** `security.py` 是最底层沙箱守卫(路径先 `resolve()` 再判是否落在 workspace 根内);`agent/tools/` 包内 `__init__.py` 是 ToolRegistry(登记工具、生成 OpenAI schema、执行时三处自愈永不崩流),`fs.py`/`search.py` 是纯 Python 工具函数;`agent/loop.py` 是 function calling 流式循环(喂历史带 tools → 重组分片 tool_calls → 执行 → 结果喂回 → 再问),产 typed event;`chat.py` 退化成只把 event 转 SSE。前端消息流从「纯文本」扩成「文本 + 工具卡片」。

**技术栈:** Python 3.11 / FastAPI / pydantic v2 / pytest(后端 TDD,mock LLM);React + Vite + TS(前端,沿用 P0/P1,`npm run build` 类型通过 + 浏览器手验)。

## 全局约束(每个任务隐含遵守)

- **向后兼容「只加不删」**:`ChatEvent` 只新增 `tool_call`/`tool_result` 两种,不改 `session`/`text`/`done`/`error` 的结构/语义;纯聊天路径(模型不调工具)行为与 P1 完全一致。
- **日志**:关键节点(工具执行前后、循环入口、异常)打日志,带业务标识(`sid` / 工具 `name`),**绝不打印** api_key、消息全文、文件内容(只记 `result_len` 之类的长度/类型)。
- **安全**:碰文件的工具一律先过 `safe_path`;越界/未配置抛 `SecurityError`,被 `ToolRegistry.run` 捕获成 tool 结果喂回模型,**绝不 500、绝不崩流**。workspace 未配置时报错引导,不默认任何目录。
- **纯 Python 工具**:grep 用 `os.walk`+`re`,glob 用 `pathlib.glob`,不走 shell/subprocess(零注入面)。
- **超大结果截断**:read_file 限行数、grep 限命中数、glob 限文件数,截断处加「请缩小范围」提示。
- **中文注释**,风格与 P0/P1 已有文件一致(解释「为什么」而非「是什么」)。
- **测试从 backend/ 根解析 import**(pyproject 已配 `pythonpath=["."]`);每个后端任务 TDD:先写失败测试 → 跑挂 → 实现 → 跑过 → 提交。
- 命令约定:测试 `cd backend && uv run pytest -q`;git 从**仓库根**(`superstar/`)运行,路径带 `backend/` 前缀。

## 文件结构(本计划涉及)

**后端:**
- 新增 `backend/app/services/security.py` — 沙箱:`get_workspace` / `safe_path` / `SecurityError`。
- 改 `backend/app/agent/tools/__init__.py`(现为空包)— `Tool` + `ToolRegistry` + `registry` 单例;末尾登记三个工具。
- 新增 `backend/app/agent/tools/fs.py` — `ReadFileArgs` + `read_file`(只读)。
- 新增 `backend/app/agent/tools/search.py` — `GrepArgs`/`GlobArgs` + `grep`/`glob`(纯 Python)。
- 新增 `backend/app/agent/loop.py` — `run_agent_streaming`(function calling 流式循环)+ `_accumulate`(分片重组)。
- 改 `backend/app/api/routes/chat.py` — 改调 `loop.run_agent_streaming`,SSE 转发新增事件。
- 新增测试 `test_security.py`、`test_tools.py`、`test_loop.py`;改 `test_chat_routes.py`。

**为什么 registry 放在 `tools/__init__.py`**:Python 不允许同一个包下既有 `tools.py` 又有 `tools/` 目录(同名冲突)。设计文档把「注册表」和「工具函数」画成两层,落地时注册表就是这个包本身(`__init__.py`),`fs.py`/`search.py` 是包内模块。外部统一 `from app.agent.tools import registry`。

**前端:**
- 改 `frontend/src/lib/api.ts` — `ChatEvent` 加 `tool_call`/`tool_result`;`getSession` 返回原始存储消息 `StoredMessage[]`。
- 改 `frontend/src/hooks/useChatStream.ts` — 消息项从 `Message` 扩成 `ChatItem`(文本项 | 工具卡片项);流式插卡/填结果;历史回放还原卡片。
- 新增 `frontend/src/components/ToolCallCard.tsx` — 可折叠工具卡片。
- 改 `frontend/src/App.tsx` — 遍历 `ChatItem`,文本渲染气泡、工具项渲染 `<ToolCallCard>`。
- 改 `frontend/src/App.css` — 追加工具卡片样式。

---

## Task 1: `security.py` 沙箱守卫

**Files:**
- Create: `backend/app/services/security.py`
- Test: `backend/tests/test_security.py`

**Interfaces:**
- Consumes: `config_store.get()["security"]["workspace_dir"]`(P1 已有,默认 `""`);`app.config.settings.data_dir`。
- Produces:
  - `class SecurityError(Exception)`
  - `get_workspace() -> Path` — 读 workspace_dir,空则抛 `SecurityError`;非空返回 `Path(ws).resolve()`。
  - `safe_path(rel: str) -> Path` — 把 `rel` 钉进 workspace 根内,越界抛 `SecurityError`,合法返回真实绝对路径。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_security.py
"""security.safe_path:挡越界(../、绝对路径、软链接),放行合法相对路径;未配置报错。"""
import pytest

from app.config import settings
from app.services import config_store, security
from app.services.security import SecurityError


@pytest.fixture
def ws(tmp_path, monkeypatch):
    # 用真实临时目录当 workspace;data_dir 指向 tmp_path(其下没有 config.json → 从 DEFAULTS 起)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    config_store.update({"security": {"workspace_dir": str(proj)}})
    return proj


def test_legal_relative_path_ok(ws):
    (ws / "a.py").write_text("x", encoding="utf-8")
    assert security.safe_path("a.py") == (ws / "a.py").resolve()


def test_reject_parent_escape(ws):
    with pytest.raises(SecurityError):
        security.safe_path("../../etc/passwd")


def test_reject_absolute_path(ws):
    with pytest.raises(SecurityError):
        security.safe_path("/etc/passwd")


def test_reject_symlink_escape(ws, tmp_path):
    # 工作区内造一个指向外部的软链接,resolve 后越界应被拒
    secret = tmp_path / "secret.txt"
    secret.write_text("top", encoding="utf-8")
    (ws / "link").symlink_to(secret)
    with pytest.raises(SecurityError):
        security.safe_path("link")


def test_unconfigured_workspace_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))  # 无 config.json → workspace_dir 为空
    config_store._reset_cache()
    with pytest.raises(SecurityError):
        security.get_workspace()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_security.py -q`
Expected: FAIL(`ModuleNotFoundError: app.services.security` 或 `AttributeError`)

- [ ] **Step 3: 写实现**

```python
# backend/app/services/security.py
"""
security.py —— 沙箱守卫(P2a 只读工具的最底层防线)

心法:光看路径字符串里有没有 `..` 防不住(软链接、绝对路径、深层 ../ 都能绕过)。
正确姿势:先 (root / rel) 再 resolve() 算出真实绝对路径,再判断它是不是 workspace 根的后代。
碰文件的工具(read_file/grep/glob)都必须先过 safe_path。
"""
import logging
from pathlib import Path

from app.services import config_store

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """安全拦截(越界 / 未配置工作区)。被工具执行层捕获成 tool 结果喂回模型,不崩流。"""


def get_workspace() -> Path:
    """读配置里的 workspace_dir;为空则报错引导(绝不默认任何目录乱翻)。"""
    ws = config_store.get()["security"]["workspace_dir"]
    if not ws:
        raise SecurityError("未配置工作区目录,请先在设置页指定 workspace_dir")
    return Path(ws).resolve()


def safe_path(rel: str) -> Path:
    """把工具传来的路径钉进 workspace 根内;越界抛 SecurityError。

    (root / rel) 再 resolve():
      - rel 相对路径 → 拼在 root 下
      - rel 绝对路径(如 /etc/passwd)→ Path 语义下 root / "/etc/passwd" == "/etc/passwd",
        resolve 后不在 root 内 → 拒
      - ../ 和软链接都被 resolve 解开成真实路径再判断
    用 `root in target.parents` 判祖先(比 startswith 稳,避开 /home/user-evil 冒充 /home/user)。
    """
    root = get_workspace()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        logger.warning("路径越界拦截: rel=%s", rel)
        raise SecurityError(f"路径越界,超出工作区: {rel}")
    return target
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_security.py -q`
Expected: PASS(5 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/security.py backend/tests/test_security.py
git commit -m "feat(p2a): security 沙箱 safe_path/get_workspace"
```

---

## Task 2: `ToolRegistry` 注册表 + 三处自愈

**Files:**
- Modify: `backend/app/agent/tools/__init__.py`(现为空文件)
- Test: `backend/tests/test_tools.py`

**Interfaces:**
- Consumes: `app.services.security.SecurityError`;`pydantic.BaseModel` / `ValidationError`。
- Produces:
  - `class Tool` — `name` / `func` / `args_model` / `description`。
  - `class ToolRegistry` — `register(name, func, args_model, description) -> None`;`to_openai_schema() -> list[dict]`;`run(name: str, raw_args: dict) -> str`(永远返回字符串,不抛)。
  - `registry = ToolRegistry()` — 全局单例(真实工具在 Task 3/4 登记)。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_tools.py
"""ToolRegistry:schema 生成 + 三处自愈(未知工具/参数错/执行异常都返回字符串,不抛)。"""
from pydantic import BaseModel

from app.agent.tools import ToolRegistry
from app.services.security import SecurityError


class _EchoArgs(BaseModel):
    text: str


def _echo(args: _EchoArgs) -> str:
    return f"echo:{args.text}"


def _boom(args: _EchoArgs) -> str:
    raise RuntimeError("炸了")


def _escape(args: _EchoArgs) -> str:
    raise SecurityError("越界")


def test_schema_shape():
    r = ToolRegistry()
    r.register("echo", _echo, _EchoArgs, "回声")
    fn = r.to_openai_schema()[0]
    assert fn["type"] == "function"
    assert fn["function"]["name"] == "echo"
    assert fn["function"]["description"] == "回声"
    assert "text" in fn["function"]["parameters"]["properties"]


def test_run_ok():
    r = ToolRegistry()
    r.register("echo", _echo, _EchoArgs, "回声")
    assert r.run("echo", {"text": "hi"}) == "echo:hi"


def test_selfheal_unknown_tool():
    assert ToolRegistry().run("nope", {}).startswith("错误:未知工具")


def test_selfheal_bad_args():
    r = ToolRegistry()
    r.register("echo", _echo, _EchoArgs, "回声")
    assert r.run("echo", {}).startswith("参数错误")           # 缺 text


def test_selfheal_security_error():
    r = ToolRegistry()
    r.register("bad", _escape, _EchoArgs, "x")
    assert r.run("bad", {"text": "a"}).startswith("安全拦截")


def test_selfheal_runtime_error():
    r = ToolRegistry()
    r.register("bad", _boom, _EchoArgs, "x")
    assert r.run("bad", {"text": "a"}).startswith("工具执行失败")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_tools.py -q`
Expected: FAIL(`ImportError: cannot import name 'ToolRegistry'`)

- [ ] **Step 3: 写实现**

```python
# backend/app/agent/tools/__init__.py
"""
agent/tools —— 工具注册表 + 具体工具的登记处。

为什么 registry 放在包的 __init__ 里:Python 不允许同一个包下既有 tools.py 又有 tools/ 目录
(同名冲突)。设计文档把「注册表」和「工具函数」画成两层,落地时注册表就是这个包本身,
fs.py / search.py 是包内模块。外部统一 `from app.agent.tools import registry`。

ToolRegistry 的三件事:
  1. register:登记「函数 + Pydantic 入参模型 + 描述」
  2. to_openai_schema:把入参模型转成 OpenAI function calling 的 JSON schema
  3. run:执行一次工具调用,三处自愈(未知工具/参数错/执行异常),永远返回字符串,绝不抛
"""
import logging
from typing import Callable

from pydantic import BaseModel, ValidationError

from app.services.security import SecurityError

logger = logging.getLogger(__name__)


class Tool:
    """一个工具 = 名字 + 函数 + 入参模型 + 给模型看的描述。"""

    def __init__(
        self,
        name: str,
        func: Callable[[BaseModel], str],
        args_model: type[BaseModel],
        description: str,
    ):
        self.name = name
        self.func = func
        self.args_model = args_model
        self.description = description


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        func: Callable[[BaseModel], str],
        args_model: type[BaseModel],
        description: str,
    ) -> None:
        self._tools[name] = Tool(name, func, args_model, description)

    def to_openai_schema(self) -> list[dict]:
        """每个工具 → {type:'function', function:{name, description, parameters}}。
        parameters 直接用 Pydantic 的 model_json_schema()(标准 JSON Schema,OpenAI 认)。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.args_model.model_json_schema(),
                },
            }
            for t in self._tools.values()
        ]

    def run(self, name: str, raw_args: dict) -> str:
        """执行一次工具调用,统一兜错(自愈核心),永远返回字符串。

        在 function calling 协议里工具结果本就是一条 role:tool 文本消息;把错误也变成
        一种「正常返回值」喂回,模型看到即自我修正。run() 从不向上抛,循环层无需管工具会不会炸。
        """
        tool = self._tools.get(name)
        if tool is None:
            logger.warning("未知工具: name=%s", name)
            return f"错误:未知工具 {name}"                       # ① 模型幻觉的工具名
        try:
            args = tool.args_model(**raw_args)                     # Pydantic 校验
        except ValidationError as e:
            logger.info("工具参数校验失败: name=%s", name)
            return f"参数错误:{e}"                                 # ② 参数不对 → 喂回让模型改
        try:
            result = tool.func(args)
            logger.info("工具执行完成: name=%s, result_len=%d", name, len(result))
            return result
        except SecurityError as e:
            logger.warning("工具安全拦截: name=%s", name)
            return f"安全拦截:{e}"                                 # ③a 越界
        except Exception as e:  # noqa: BLE001
            logger.warning("工具执行失败: name=%s, err=%s", name, type(e).__name__)
            return f"工具执行失败:{e}"                             # ③b 任何异常 → 喂回,不崩流


# 全局单例:整个 Agent 共用一份注册表。真实工具在 fs.py / search.py 定义,
# 由本文件末尾在 Task 3/4 导入并登记(先定义 registry 再 import,天然避免循环引用)。
registry = ToolRegistry()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_tools.py -q`
Expected: PASS(6 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/tools/__init__.py backend/tests/test_tools.py
git commit -m "feat(p2a): ToolRegistry 注册表 + 三处自愈"
```

---

## Task 3: `read_file` 只读工具

**Files:**
- Create: `backend/app/agent/tools/fs.py`
- Modify: `backend/app/agent/tools/__init__.py`(末尾登记 read_file)
- Test: `backend/tests/test_tools.py`(追加)

**Interfaces:**
- Consumes: `app.services.security.safe_path`;`registry`(Task 2)。
- Produces:
  - `class ReadFileArgs(BaseModel)` — `path: str`。
  - `read_file(args: ReadFileArgs) -> str` — 过沙箱读文本,超 `MAX_LINES` 截断。
  - 全局 `registry` 新增已登记的 `"read_file"`。

- [ ] **Step 1: 追加失败测试**

```python
# backend/tests/test_tools.py 末尾追加
import pytest

from app.config import settings
from app.services import config_store
from app.agent.tools.fs import ReadFileArgs, read_file


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    config_store.update({"security": {"workspace_dir": str(proj)}})
    return proj


def test_read_file_ok(ws):
    (ws / "a.txt").write_text("hello\nworld", encoding="utf-8")
    assert read_file(ReadFileArgs(path="a.txt")) == "hello\nworld"


def test_read_file_missing(ws):
    assert read_file(ReadFileArgs(path="nope.txt")).startswith("错误:文件不存在")


def test_read_file_truncated(ws):
    (ws / "big.txt").write_text("\n".join(str(i) for i in range(1000)), encoding="utf-8")
    out = read_file(ReadFileArgs(path="big.txt"))
    assert "只显示前" in out


def test_read_file_escape_via_registry(ws):
    from app.agent.tools import registry
    # 经全局 registry.run 走自愈:越界返回「安全拦截」而不是抛(验证 read_file 已登记)
    assert registry.run("read_file", {"path": "../../etc/passwd"}).startswith("安全拦截")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_tools.py -q`
Expected: FAIL(`ModuleNotFoundError: app.agent.tools.fs`)

- [ ] **Step 3: 写实现 + 登记**

```python
# backend/app/agent/tools/fs.py
"""
fs.py —— 文件读取(只读工具)。写文件留给 P2b。

read_file:过 safe_path 沙箱 → 读文本 → 超大截断(限行数,防爆上下文/省 token)。
"""
from pydantic import BaseModel, Field

from app.services.security import safe_path

MAX_LINES = 400   # 单次最多回这么多行,超了截断并提示模型缩小范围


class ReadFileArgs(BaseModel):
    path: str = Field(description="相对工作区根目录的文件路径,如 src/main.py")


def read_file(args: ReadFileArgs) -> str:
    target = safe_path(args.path)          # 越界在这里抛 SecurityError,由 registry 兜
    if not target.is_file():
        return f"错误:文件不存在或不是文件: {args.path}"
    # errors="replace":遇到非 UTF-8 字节不炸,替换成占位符
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > MAX_LINES:
        head = "\n".join(lines[:MAX_LINES])
        return f"{head}\n…(共 {len(lines)} 行,只显示前 {MAX_LINES} 行,请缩小范围或指定区间)"
    return "\n".join(lines)
```

在 `backend/app/agent/tools/__init__.py` 末尾(`registry = ToolRegistry()` 之后)追加:

```python
# ---- 登记具体工具(放最后:此时 registry 已就绪,import 工具模块不会循环)----
from app.agent.tools.fs import ReadFileArgs, read_file  # noqa: E402

registry.register(
    "read_file", read_file, ReadFileArgs,
    "读取工作区内一个文件的文本内容(相对路径)。超大文件会自动截断。",
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_tools.py -q`
Expected: PASS(10 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/tools/fs.py backend/app/agent/tools/__init__.py backend/tests/test_tools.py
git commit -m "feat(p2a): read_file 只读工具 + 登记"
```

---

## Task 4: `grep` / `glob` 搜索工具(纯 Python)

**Files:**
- Create: `backend/app/agent/tools/search.py`
- Modify: `backend/app/agent/tools/__init__.py`(末尾登记 grep/glob)
- Test: `backend/tests/test_tools.py`(追加)

**Interfaces:**
- Consumes: `app.services.security.get_workspace` / `safe_path`;`registry`。
- Produces:
  - `class GrepArgs(BaseModel)` — `pattern: str`、`path: str = "."`。
  - `class GlobArgs(BaseModel)` — `pattern: str`。
  - `grep(args) -> str`、`glob(args) -> str`。
  - 全局 `registry` 新增 `"grep"`、`"glob"`。

- [ ] **Step 1: 追加失败测试**

```python
# backend/tests/test_tools.py 末尾追加(复用上面的 ws fixture)
from app.agent.tools.search import GlobArgs, GrepArgs, glob, grep


def test_grep_hit(ws):
    (ws / "a.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    out = grep(GrepArgs(pattern="def "))
    assert "a.py:1:def foo():" in out


def test_grep_no_hit(ws):
    (ws / "a.py").write_text("pass\n", encoding="utf-8")
    assert grep(GrepArgs(pattern="zzz")) == "(无匹配)"


def test_grep_truncated(ws):
    (ws / "big.py").write_text("\n".join("match" for _ in range(200)), encoding="utf-8")
    assert "命中过多" in grep(GrepArgs(pattern="match"))


def test_grep_skips_git_dir(ws):
    (ws / ".git").mkdir()
    (ws / ".git" / "x.py").write_text("secret", encoding="utf-8")
    (ws / "a.py").write_text("secret", encoding="utf-8")
    out = grep(GrepArgs(pattern="secret"))
    assert ".git" not in out and "a.py:1" in out


def test_glob_match(ws):
    (ws / "a.py").write_text("", encoding="utf-8")
    (ws / "b.txt").write_text("", encoding="utf-8")
    assert glob(GlobArgs(pattern="*.py")) == "a.py"


def test_glob_no_match(ws):
    assert glob(GlobArgs(pattern="*.rs")) == "(无匹配)"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_tools.py -q`
Expected: FAIL(`ModuleNotFoundError: app.agent.tools.search`)

- [ ] **Step 3: 写实现 + 登记**

```python
# backend/app/agent/tools/search.py
"""
search.py —— 代码搜索(只读,纯 Python,不走 shell)。

grep:os.walk 遍历工作区 + re 逐行匹配,返回「相对路径:行号:内容」。
glob:pathlib 按通配模式列文件名。
纯 Python 的理由:零外部依赖、跨平台、根本没有 shell 注入面(对比 Claude Code 打包 ripgrep
是为伺候巨型仓库;个人项目慢一点无感)。命中/匹配过多则截断,提示缩小范围。
"""
import os
import re

from pydantic import BaseModel, Field

from app.services.security import get_workspace, safe_path

MAX_HITS = 100          # grep 最多回这么多条命中
MAX_MATCHES = 200       # glob 最多回这么多个文件
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


class GrepArgs(BaseModel):
    pattern: str = Field(description="正则表达式,按行匹配")
    path: str = Field(default=".", description="搜索起点,相对工作区根,默认整个工作区")


def grep(args: GrepArgs) -> str:
    start = safe_path(args.path)                     # 起点也过沙箱
    try:
        regex = re.compile(args.pattern)
    except re.error as e:
        return f"错误:正则表达式非法: {e}"
    root = get_workspace()
    base = start if start.is_dir() else start.parent
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]   # 原地裁剪:不进这些目录
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            rel = os.path.relpath(fp, root)
                            hits.append(f"{rel}:{lineno}:{line.rstrip()}")
                            if len(hits) >= MAX_HITS:
                                hits.append(f"…命中过多(≥{MAX_HITS}),请缩小 pattern 或 path")
                                return "\n".join(hits)
            except OSError:
                continue     # 读不了的文件(权限/特殊文件)跳过,不影响整体
    return "\n".join(hits) if hits else "(无匹配)"


class GlobArgs(BaseModel):
    pattern: str = Field(description="通配模式,相对工作区根,如 **/*.py")


def glob(args: GlobArgs) -> str:
    root = get_workspace()
    try:
        found = list(root.glob(args.pattern))
    except ValueError as e:
        return f"错误:glob 模式非法: {e}"      # 绝对路径/含 .. 的模式 pathlib 会拒
    matches: list[str] = []
    for p in found:
        rel = p.relative_to(root)               # glob 结果天然在 root 下
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        matches.append(str(rel))
        if len(matches) >= MAX_MATCHES:
            matches.append(f"…匹配过多(≥{MAX_MATCHES}),请缩小 pattern")
            break
    return "\n".join(matches) if matches else "(无匹配)"
```

在 `backend/app/agent/tools/__init__.py` 末尾(read_file 登记之后)追加:

```python
from app.agent.tools.search import GlobArgs, GrepArgs, glob, grep  # noqa: E402

registry.register(
    "grep", grep, GrepArgs,
    "在工作区内按正则逐行搜索,返回 相对路径:行号:内容。命中过多会截断。",
)
registry.register(
    "glob", glob, GlobArgs,
    "按通配模式(如 **/*.py)列出工作区内匹配的文件路径。",
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_tools.py -q`
Expected: PASS(16 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/tools/search.py backend/app/agent/tools/__init__.py backend/tests/test_tools.py
git commit -m "feat(p2a): grep/glob 纯 Python 搜索工具 + 登记"
```

---

## Task 5: `loop.py` function calling 流式循环(核心引擎)

**Files:**
- Create: `backend/app/agent/loop.py`
- Test: `backend/tests/test_loop.py`

**Interfaces:**
- Consumes: `llm.get_llm_client() -> (client, model)`;`config_store.get()["agent"]["max_iters"]`;`session_store.read_messages/_fit_context/append_message`;`registry.to_openai_schema/run`(全局单例,含 3 个已登记工具)。
- Produces:
  - `run_agent_streaming(sid: str)` — 生成器,逐步 yield typed event:`{"type":"text","content"}` / `{"type":"tool_call","id","name","args"}` / `{"type":"tool_result","id","result"}` / `{"type":"done"}` / `{"type":"error","message"}`。
  - `_accumulate(stream)` — 生成器,边 yield text 事件边重组分片 tool_calls,`return (text_parts, tool_calls)`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_loop.py
"""loop.run_agent_streaming:mock「先 grep 再答」的假 LLM,断言事件序列 + 落盘四条。"""
import json

import pytest

from app.agent import loop
from app.config import settings
from app.services import config_store, llm, session_store


# --- 构造流式 chunk 的假对象(模仿 OpenAI SDK 的 delta 结构)---
class _Fn:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _TC:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.function = _Fn(name, arguments)


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Chunk:
    def __init__(self, delta):
        self.choices = [type("C", (), {"delta": delta})()]


def _tool_call_stream():
    # tool_call 分片:id/name 先到,arguments 的 JSON 分两片拼(考重组)
    yield _Chunk(_Delta(tool_calls=[_TC(0, id="call_1", name="grep")]))
    yield _Chunk(_Delta(tool_calls=[_TC(0, arguments='{"pattern"')]))
    yield _Chunk(_Delta(tool_calls=[_TC(0, arguments=': "def"}')]))


def _answer_stream():
    yield _Chunk(_Delta(content="找到"))
    yield _Chunk(_Delta(content="了"))


class _Completions:
    def __init__(self):
        self.calls = 0

    def create(self, model, messages, tools, stream):
        self.calls += 1
        return _tool_call_stream() if self.calls == 1 else _answer_stream()


class _Client:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _Completions()})()


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
    config_store.update({"security": {"workspace_dir": str(proj)}})
    monkeypatch.setattr(llm, "get_llm_client", lambda: (_Client(), "fake"))
    sid = session_store.create()
    session_store.append_message(sid, {"role": "user", "content": "搜一下 def"})
    return sid


def test_grep_then_answer(ready):
    events = list(loop.run_agent_streaming(ready))
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "text", "text", "done"]

    tc = next(e for e in events if e["type"] == "tool_call")
    assert tc["name"] == "grep"
    assert json.loads(tc["args"]) == {"pattern": "def"}       # 分片重组正确

    tr = next(e for e in events if e["type"] == "tool_result")
    assert "a.py:1:def foo" in tr["result"]                    # 真跑了 grep

    # 落盘四条:user, assistant(带 tool_calls), tool, assistant(终答)
    msgs = session_store.read_messages(ready)
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1]["tool_calls"][0]["function"]["name"] == "grep"
    assert msgs[2]["tool_call_id"] == "call_1"
    assert msgs[3]["content"] == "找到了"


# --- max_iters 用尽:模型永远只调工具、不给终答 ---
def _always_tool_stream():
    yield _Chunk(_Delta(tool_calls=[_TC(0, id="c", name="grep", arguments='{"pattern":"x"}')]))


class _AlwaysCompletions:
    def create(self, model, messages, tools, stream):
        return _always_tool_stream()


class _AlwaysClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _AlwaysCompletions()})()


def test_max_iters_exhausted(ready, monkeypatch):
    monkeypatch.setattr(llm, "get_llm_client", lambda: (_AlwaysClient(), "fake"))
    config_store.update({"agent": {"max_iters": 2}})
    events = list(loop.run_agent_streaming(ready))
    assert events[-1]["type"] == "error"
    assert "最大步数" in events[-1]["message"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_loop.py -q`
Expected: FAIL(`ModuleNotFoundError: app.agent.loop`)

- [ ] **Step 3: 写实现**

```python
# backend/app/agent/loop.py
"""
loop.py —— function calling 流式循环(P2a 引擎,Agent 的「大脑」)

职责:喂会话历史(带工具 schema)给模型 → 流式收 delta → 重组分片的 tool_calls →
调注册表执行工具 → 结果喂回 → 再问,直到模型不再调工具(给终答)或到达 max_iters。
产 typed event(text/tool_call/tool_result/done/error),与输出通道解耦——
chat 路由把 event 原样转 SSE,二期飞书适配器可消费同样的 event。
"""
import json
import logging

from app.agent.tools import registry
from app.services import config_store, llm, session_store

logger = logging.getLogger(__name__)

# 极简 system:告诉模型有工具、大致职责。完整画像/soul 注入留 P5。
SYSTEM_PROMPT = (
    "你是一个本地编码助手,可以调用工具查看用户工作区里的代码:"
    "grep(按正则搜索)、glob(按通配列文件)、read_file(读文件)。"
    "需要看代码再作答时就调用工具;能直接回答的问题不必调用。"
)


def _accumulate(stream):
    """消费一次流式响应:普通文字 yield text 事件;tool_calls 分片按 index 重组。
    return (text_parts, tool_calls) —— tool_calls 是 OpenAI 兼容结构,可直接回灌历史。

    面试难点:流式下 tool_calls 是「碎着吐」的——id/name 先到,arguments 的 JSON
    字符串分几个 chunk 拼。用 delta.tool_calls[].index 把碎片按槽位累积。
    """
    text_parts: list[str] = []
    acc: dict[int, dict] = {}          # index -> {id, name, arguments}
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if getattr(delta, "content", None):
            text_parts.append(delta.content)
            yield {"type": "text", "content": delta.content}
        for tc in getattr(delta, "tool_calls", None) or []:
            slot = acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            if tc.id:
                slot["id"] = tc.id
            if tc.function and tc.function.name:
                slot["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                slot["arguments"] += tc.function.arguments
    tool_calls = [
        {
            "id": s["id"],
            "type": "function",
            "function": {"name": s["name"], "arguments": s["arguments"]},
        }
        for _, s in sorted(acc.items())
    ]
    return text_parts, tool_calls


def run_agent_streaming(sid: str):
    """喂该会话历史,跑 function calling 循环,逐步 yield typed event。"""
    client, model = llm.get_llm_client()
    max_iters = config_store.get()["agent"]["max_iters"]
    logger.info("agent 循环开始: sid=%s, max_iters=%d", sid, max_iters)
    try:
        for _ in range(max_iters):
            history = session_store._fit_context(session_store.read_messages(sid))
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=registry.to_openai_schema(),
                stream=True,
            )
            # yield from:把 _accumulate 里的 text 事件原样透传给外层消费者,
            # 同时用它的 return 值拿到重组好的 (text_parts, tool_calls)。
            text_parts, tool_calls = yield from _accumulate(stream)

            if not tool_calls:
                session_store.append_message(
                    sid, {"role": "assistant", "content": "".join(text_parts)}
                )
                yield {"type": "done"}
                return

            # 协议要求:先落一条带 tool_calls 的 assistant 消息(content 可为 None)。
            # 「未回答的 tool_call」天然表示暂停 —— P2b 审批的回合边界正落在这。
            session_store.append_message(
                sid,
                {
                    "role": "assistant",
                    "content": "".join(text_parts) or None,
                    "tool_calls": tool_calls,
                },
            )
            for tc in tool_calls:
                name = tc["function"]["name"]
                raw = tc["function"]["arguments"]
                yield {"type": "tool_call", "id": tc["id"], "name": name, "args": raw}
                try:
                    parsed = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    parsed = {}          # 参数不是合法 JSON → 交给 registry 自愈(返回参数错误)
                result = registry.run(name, parsed)
                yield {"type": "tool_result", "id": tc["id"], "result": result}
                session_store.append_message(
                    sid, {"role": "tool", "tool_call_id": tc["id"], "content": result}
                )
            # 回到 for 顶:带工具结果再问模型
        logger.info("agent 循环到达 max_iters: sid=%s", sid)
        yield {"type": "error", "message": "达到最大步数,已停止"}
    except Exception as e:  # noqa: BLE001 - 未预期异常 → 兜成 error 事件,已流出的内容保留
        logger.warning("agent 循环失败: sid=%s err=%s", sid, type(e).__name__)
        yield {"type": "error", "message": str(e)}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_loop.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/loop.py backend/tests/test_loop.py
git commit -m "feat(p2a): loop function calling 流式循环 + 分片重组"
```

---

## Task 6: `chat.py` 改调 loop + 端到端测试

**Files:**
- Modify: `backend/app/api/routes/chat.py`
- Test: `backend/tests/test_chat_routes.py`(改现有 fake + 加带工具用例)

**Interfaces:**
- Consumes: `loop.run_agent_streaming(sid)`;`session_store.create/append_message/list_sessions`。
- Produces:`POST /api/chat/stream` 的 SSE 里,除 `session`/`text`/`done`/`error` 外,带工具时透传 `tool_call`/`tool_result`。

- [ ] **Step 1: 改测试(先让它对齐新行为 → 跑挂)**

现有 `_Completions.create` 缺 `tools` 参数、且直调 LLM 的老路径已不存在;改成走 loop。整体替换 `backend/tests/test_chat_routes.py` 为:

```python
"""chat 走 loop:纯聊天向后兼容 + 带工具端到端。mock 掉 LLM 流。"""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import config_store, llm, session_store


# ---- 纯文本假流(向后兼容:模型不调工具)----
class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Chunk:
    def __init__(self, delta):
        self.choices = [type("C", (), {"delta": delta})()]


class _TextCompletions:
    def create(self, model, messages, tools=None, stream=True):
        for c in ["你", "好"]:
            yield _Chunk(_Delta(content=c))


class _TextClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _TextCompletions()})()


def _events(resp_text):
    out = []
    for part in resp_text.split("\n\n"):
        part = part.strip()
        if part.startswith("data:"):
            out.append(json.loads(part[len("data:"):].strip()))
    return out


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    monkeypatch.setattr(llm, "get_llm_client", lambda: (_TextClient(), "fake-model"))
    from app.api.main import app
    return TestClient(app)


def test_lazy_create_and_persist(client):
    # 首句不带 session_id → 懒创建;纯聊天路径行为与 P1 一致
    r = client.post("/api/chat/stream", json={"message": "我叫小明"})
    evs = _events(r.text)
    assert evs[0]["type"] == "session"
    sid = evs[0]["session_id"]
    assert evs[0]["title"].startswith("我叫小明")
    assert any(e["type"] == "text" for e in evs)
    assert evs[-1]["type"] == "done"

    msgs = session_store.read_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "你好"

    r2 = client.post("/api/chat/stream", json={"session_id": sid, "message": "我叫啥"})
    assert _events(r2.text)[0]["session_id"] == sid
    assert [m["role"] for m in session_store.read_messages(sid)] == [
        "user", "assistant", "user", "assistant",
    ]


# ---- 带工具假流:第一轮 glob,第二轮文字 ----
class _Fn:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _TC:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.function = _Fn(name, arguments)


class _ToolCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, model, messages, tools=None, stream=True):
        self.calls += 1
        if self.calls == 1:
            yield _Chunk(_Delta(tool_calls=[_TC(0, id="c1", name="glob", arguments='{"pattern": "*.py"}')]))
        else:
            yield _Chunk(_Delta(content="有一个文件"))


class _ToolClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _ToolCompletions()})()


def test_chat_with_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "x.py").write_text("", encoding="utf-8")
    config_store.update({"security": {"workspace_dir": str(proj)}})
    monkeypatch.setattr(llm, "get_llm_client", lambda: (_ToolClient(), "fake"))
    from app.api.main import app
    c = TestClient(app)

    r = c.post("/api/chat/stream", json={"message": "有哪些 py 文件"})
    evs = _events(r.text)
    types = [e["type"] for e in evs]
    assert "tool_call" in types and "tool_result" in types
    assert next(e for e in evs if e["type"] == "tool_call")["name"] == "glob"
    assert "x.py" in next(e for e in evs if e["type"] == "tool_result")["result"]
    assert types[-1] == "done"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_chat_routes.py -q`
Expected: FAIL(现有 chat.py 直调 LLM、无 `tool_call` 事件,`test_chat_with_tool` 挂)

- [ ] **Step 3: 改实现**

整体替换 `backend/app/api/routes/chat.py` 为:

```python
"""
routes/chat.py —— 流式对话(P2a:退化成「定 sid + 落 user + 转发 loop 事件」)

POST /api/chat/stream  body {session_id?, message}
SSE 事件:session / text / tool_call / tool_result / done / error。
时序:定 sid(无则懒创建)→ 落 user 消息 → 发 session 事件 → 把 loop 产的 event 原样转 SSE。
真正的 function calling 循环、工具执行、落盘都在 agent/loop.py;路由只做通道适配。
"""
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.agent import loop
from app.models import schemas
from app.services import session_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/stream")
def chat_stream(req: schemas.ChatRequest) -> StreamingResponse:
    logger.info("chat 请求: msg_len=%d, has_sid=%s", len(req.message), bool(req.session_id))

    def event_stream():
        # 定 sid:带 sid 则续写,不带则懒创建(首句到达才落盘,不产生空会话)
        sid = req.session_id or session_store.create()
        try:
            # 先落用户消息:哪怕模型挂了也不丢输入(首条会顺带生成标题)
            session_store.append_message(sid, {"role": "user", "content": req.message})
            title = next((s["title"] for s in session_store.list_sessions() if s["id"] == sid), "")
            # session 事件必须在 text 之前:前端据此记住新 sid、刷新列表标题
            yield _sse({"type": "session", "session_id": sid, "title": title})
            # 循环产啥,原样转 SSE(text/tool_call/tool_result/done/error 都自动透传)
            for event in loop.run_agent_streaming(sid):
                yield _sse(event)
        except Exception as e:  # noqa: BLE001 - 兜底:错误也当事件发给前端展示
            logger.warning("chat 失败: sid=%s err=%s", sid, type(e).__name__)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: 跑全量后端测试确认通过**

Run: `cd backend && uv run pytest -q`
Expected: PASS(P0/P1 老测试 + P2a 全绿)

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/routes/chat.py backend/tests/test_chat_routes.py
git commit -m "feat(p2a): chat 路由改调 loop,SSE 透传工具事件"
```

---

## Task 7: 前端 `api.ts` + `useChatStream`(类型 + 流式插卡 + 历史回放)

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/hooks/useChatStream.ts`

**Interfaces:**
- Produces:
  - `api.ts`:`ChatEvent` 加 `tool_call`/`tool_result`;`StoredMessage` 类型;`getSession(sid) -> Promise<StoredMessage[]>`。
  - `useChatStream.ts`:`ChatItem` 联合类型(`{kind:'msg',role,content}` | `{kind:'tool',id,name,args,result?}`);`messages: ChatItem[]`;流式收 `tool_call` 插卡、`tool_result` 填结果;`switchSession` 用 `messagesToItems` 还原历史。

- [ ] **Step 1: 改 `api.ts`**

替换 `frontend/src/lib/api.ts` 顶部类型区(`ChatEvent`、`ChatMessage`、`getSession`)为:

```typescript
// 与后端 chat.py / loop.py / session.py 的协议对齐
export type ChatEvent =
  | { type: 'session'; session_id: string; title: string }
  | { type: 'text'; content: string }
  | { type: 'tool_call'; id: string; name: string; args: string }
  | { type: 'tool_result'; id: string; result: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

export type SessionMeta = {
  id: string
  title: string
  created_at: string
  updated_at: string
}

// 后端 JSONL 里存的原始消息形状(历史回放要按它还原工具卡片)
export type StoredMessage = {
  role: 'user' | 'assistant' | 'tool'
  content: string | null
  tool_calls?: { id: string; function: { name: string; arguments: string } }[]
  tool_call_id?: string
}
```

把原 `getSession` 的返回类型与 body 改成:

```typescript
export async function getSession(sid: string): Promise<StoredMessage[]> {
  const r = await fetch(`/api/sessions/${sid}`)
  if (!r.ok) throw new Error('拉取会话历史失败')
  return (await r.json()).messages
}
```

(`listSessions` / `renameSession` / `deleteSession` / `streamChat` 不变。删除原来的 `ChatMessage` 类型——已被 `StoredMessage` 取代。)

- [ ] **Step 2: 改 `useChatStream.ts`**

替换 `frontend/src/hooks/useChatStream.ts` 为:

```typescript
import { useCallback, useEffect, useState } from 'react'

import {
  deleteSession,
  getSession,
  listSessions,
  renameSession,
  streamChat,
  type ChatEvent,
  type SessionMeta,
  type StoredMessage,
} from '../lib/api'

// 消息流的一项:要么一条文本消息,要么一张工具卡片
export type ChatItem =
  | { kind: 'msg'; role: 'user' | 'assistant'; content: string }
  | { kind: 'tool'; id: string; name: string; args: string; result?: string }

// 历史回放:把后端存的原始消息还原成 ChatItem[](assistant 的 tool_calls → 卡片,
// role:tool 消息按 tool_call_id 回填对应卡片的 result)
function messagesToItems(msgs: StoredMessage[]): ChatItem[] {
  const items: ChatItem[] = []
  const toolIndex: Record<string, number> = {} // tool_call_id -> items 下标
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
  return items
}

export function useChatStream() {
  const [messages, setMessages] = useState<ChatItem[]>([])
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)

  useEffect(() => {
    void listSessions().then(setSessions).catch(() => {})
  }, [])

  const refreshSessions = useCallback(async () => {
    setSessions(await listSessions())
  }, [])

  const newSession = useCallback(() => {
    setCurrentId(null)
    setMessages([])
  }, [])

  const switchSession = useCallback(async (sid: string) => {
    setCurrentId(sid)
    setMessages(messagesToItems(await getSession(sid)))
  }, [])

  const removeSession = useCallback(
    async (sid: string) => {
      await deleteSession(sid)
      if (sid === currentId) {
        setCurrentId(null)
        setMessages([])
      }
      await refreshSessions()
    },
    [currentId, refreshSessions],
  )

  const rename = useCallback(
    async (sid: string, title: string) => {
      await renameSession(sid, title)
      await refreshSessions()
    },
    [refreshSessions],
  )

  const send = useCallback(
    async (text: string) => {
      setMessages((m) => [...m, { kind: 'msg', role: 'user', content: text }])
      setStreaming(true)
      try {
        await streamChat(
          text,
          (e: ChatEvent) => {
            if (e.type === 'session') {
              setCurrentId(e.session_id)
            } else if (e.type === 'tool_call') {
              // 新工具调用 → 插一张「运行中」卡片(result 未定义 = 运行中)
              setMessages((m) => [...m, { kind: 'tool', id: e.id, name: e.name, args: e.args }])
            } else if (e.type === 'tool_result') {
              // 同 id 卡片填结果
              setMessages((m) =>
                m.map((it) => (it.kind === 'tool' && it.id === e.id ? { ...it, result: e.result } : it)),
              )
            } else if (e.type === 'text') {
              // 追加到「最后一条 assistant 文本」;若上一项是工具卡片/用户消息,则新起一条
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
          },
          currentId ?? undefined,
        )
      } finally {
        setStreaming(false)
        void refreshSessions()
      }
    },
    [currentId, refreshSessions],
  )

  return {
    messages,
    sessions,
    currentId,
    streaming,
    send,
    newSession,
    switchSession,
    removeSession,
    rename,
  }
}
```

- [ ] **Step 3: 类型检查(此时 App.tsx 还在用旧 `m.role`,预期 App 报错——下一 Task 修)**

Run: `cd frontend && npx tsc --noEmit`
Expected: 仅 `src/App.tsx` 因 `messages` 形状变化报类型错(api.ts / useChatStream.ts 本身无错)。这是预期的,Task 8 修复。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/lib/api.ts frontend/src/hooks/useChatStream.ts
git commit -m "feat(p2a): 前端协议加工具事件,消息流支持工具卡片项 + 历史回放"
```

---

## Task 8: `ToolCallCard` 组件 + `App.tsx` 渲染 + 样式

**Files:**
- Create: `frontend/src/components/ToolCallCard.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: `ChatItem`(Task 7);`useChatStream`。
- Produces: 浏览器可见的可折叠工具卡片;`npm run build` 类型通过。

- [ ] **Step 1: 新建 `ToolCallCard.tsx`**

```tsx
// frontend/src/components/ToolCallCard.tsx
import { useState } from 'react'

type Props = { name: string; args: string; result?: string }

export default function ToolCallCard({ name, args, result }: Props) {
  const [open, setOpen] = useState(false)
  // args 是模型给的 JSON 字符串,尝试美化;parse 失败原样显示
  const prettyArgs = (() => {
    try {
      return JSON.stringify(JSON.parse(args), null, 2)
    } catch {
      return args
    }
  })()
  const running = result === undefined
  const summary = running ? '运行中…' : result.split('\n')[0] || '(空)'

  return (
    <div className="tool-card">
      <div className="tool-head" onClick={() => setOpen((o) => !o)}>
        <span className="tool-name">🔧 {name}</span>
        <span className="tool-summary">
          {running ? '⏳ ' : '✓ '}
          {summary}
        </span>
        <span className="tool-toggle">{open ? '▾' : '▸'}</span>
      </div>
      {open && (
        <div className="tool-body">
          <div className="tool-label">参数</div>
          <pre>{prettyArgs}</pre>
          <div className="tool-label">结果</div>
          <pre>{running ? '运行中…' : result}</pre>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: 改 `App.tsx`**

替换 `frontend/src/App.tsx` 的消息渲染区(`import` 加 ToolCallCard;`.messages` 内的 `map` 改为按 `it.kind` 分支):

```tsx
import { useState } from 'react'

import SessionList from './components/SessionList'
import ToolCallCard from './components/ToolCallCard'
import { useChatStream } from './hooks/useChatStream'
import './App.css'

export default function App() {
  const {
    messages,
    sessions,
    currentId,
    streaming,
    send,
    newSession,
    switchSession,
    removeSession,
    rename,
  } = useChatStream()
  const [input, setInput] = useState('')

  const onSend = () => {
    const text = input.trim()
    if (!text || streaming) return
    setInput('')
    void send(text)
  }

  return (
    <div className="layout">
      <SessionList
        sessions={sessions}
        currentId={currentId}
        onNew={newSession}
        onSwitch={switchSession}
        onDelete={removeSession}
        onRename={rename}
      />
      <div className="app">
        <h1>Superstar</h1>
        <div className="messages">
          {messages.map((it, i) =>
            it.kind === 'tool' ? (
              <ToolCallCard key={i} name={it.name} args={it.args} result={it.result} />
            ) : (
              <div key={i} className={`msg ${it.role}`}>
                <b>{it.role === 'user' ? '你' : 'AI'}:</b> {it.content}
                {streaming && i === messages.length - 1 && it.role === 'assistant' ? ' ▋' : ''}
              </div>
            ),
          )}
        </div>
        <div className="composer">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && onSend()}
            placeholder="说点什么…"
          />
          <button onClick={onSend} disabled={streaming}>
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: 追加 `App.css` 工具卡片样式**

在 `frontend/src/App.css` 末尾追加:

```css
/* 工具调用卡片:让用户「看见 Agent 在干什么」,默认折叠只显示摘要 */
.tool-card {
  border: 1px solid #d0d7de;
  border-radius: 6px;
  margin: 6px 0;
  background: #f6f8fa;
  font-size: 13px;
  overflow: hidden;
}
.tool-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  cursor: pointer;
  user-select: none;
}
.tool-name {
  font-weight: 600;
  white-space: nowrap;
}
.tool-summary {
  flex: 1;
  color: #57606a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tool-toggle {
  color: #57606a;
}
.tool-body {
  border-top: 1px solid #d0d7de;
  padding: 8px 10px;
}
.tool-label {
  font-weight: 600;
  color: #57606a;
  margin: 4px 0 2px;
}
.tool-body pre {
  margin: 0;
  padding: 6px 8px;
  background: #fff;
  border: 1px solid #eaeef2;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow: auto;
}
```

- [ ] **Step 4: 构建 + 类型检查通过**

Run: `cd frontend && npm run build`
Expected: 构建成功,无类型错误。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/ToolCallCard.tsx frontend/src/App.tsx frontend/src/App.css
git commit -m "feat(p2a): 前端工具卡片组件 + 消息流渲染"
```

---

## 端到端手动验证(全部任务完成后)

先在 `data/config.json` 的 `security.workspace_dir` 填一个真实代码目录,再启前后端,浏览器验证:

1. 问「工作区里有哪些 .py 文件?」→ Agent 调 `glob` → 卡片显示匹配 → 终答列文件。
2. 问「grep 一下 def」→ Agent 调 `grep` → 卡片显示命中 → 终答。
3. 问「读一下 xxx.py」→ Agent 调 `read_file` → 卡片显示内容(超大截断)→ 终答。
4. 诱导越界(「读 ../../etc/passwd」)→ 工具结果回显「路径越界/安全拦截」,模型道歉,**不崩流**。
5. 纯聊天(「你好」)→ 模型不调工具,直接答(向后兼容)。
6. 切到含工具调用的旧会话 → 历史正确还原成卡片(参数 + 结果)。

后端回归:`cd backend && uv run pytest -q` 全绿。

---

## 自检(计划 vs spec)

- **spec 覆盖**:function calling 循环(T5)、read_file(T3)、grep/glob(T4)、沙箱(T1)、ToolRegistry 三处自愈(T2)、chat 转发(T6)、前端卡片 + 历史回放(T7/T8)、截断(T3/T4)、max_iters→error(T5)、系统提示(T5)——逐条有任务。
- **落地冲突**:spec 的 `tools.py` + `tools/` 冲突已在「文件结构」显式解决(registry 入 `tools/__init__.py`)。
- **类型一致**:后端事件字段(`tool_call{id,name,args}`、`tool_result{id,result}`)前后端(`api.ts` union)对齐;`tool_call_id` 贯穿落盘/回放;工具函数签名 `def f(args: XxxArgs) -> str` 统一。
- **向后兼容**:纯聊天路径事件序列与 P1 一致(T6 `test_lazy_create_and_persist` 守住);`ChatEvent` 只加不改。
