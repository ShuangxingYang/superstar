# P0 竖切最薄闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通最薄的端到端——浏览器输入一句话 → 后端用「可在配置页热切换的 LLM 配置」发起流式请求 → 前端逐字打字机显示回复。

**Architecture:** 后端 FastAPI 分层:`config_store`(读写 `data/config.json` + 内存缓存)→ `services/llm`(按当前配置动态建 OpenAI 客户端,配置变即重建)→ `routes/settings`(读写配置 + 测试连接)与 `routes/chat`(单轮 SSE 流式)。前端 Vite+React+TS,`useChatStream` 用 `fetch + ReadableStream` 消费 SSE。P0 **不含**工具、会话、RAG——只验证配置热生效 + 流式管道。

**Tech Stack:** Python 3.11 / FastAPI / uvicorn / openai(sync client, `stream=True`)/ pydantic v2 / uv;React + Vite + TypeScript;SSE(`data: {json}\n\n`)。

## Global Constraints

- **零数据库**:配置只存 `data/config.json`,不引入数据库/ORM。
- **API 向后兼容「只加不删」**:新增字段必须有默认值或允许为空;不删除、不改类型、不改语义已发布字段。
- **日志规范**:关键节点(接口入口/出口、外部调用前后、异常)加日志,带业务标识;**禁止记录 api_key/token 等敏感信息**;文案要有辨识度。
- **key 脱敏**:API 返回配置时 api_key 用 `sk-***1234` 形式;绝不明文返回、绝不打印进日志。
- **注释与沟通用中文**,技术术语/代码保留原文。
- **包管理用 uv**;Python ≥ 3.11(已在 `backend/pyproject.toml`)。
- **前端**:React+Vite+TS;P0 用默认/极简样式,美化点到为止。
- **现有基线**:`backend/app/config.py`(pydantic-settings:host/port/data_dir/qdrant_url)、`backend/app/api/main.py`(FastAPI 实例 + CORS 放行 5173 + `/health`)、`backend/run.py`(`uvicorn.run("app.api.main:app", reload=True)`)已就绪,勿重复创建。

---

## File Structure

**后端(`backend/`):**
- `app/services/config_store.py`(新)—— `data/config.json` 读写 + 内存缓存 + 深合并 + 默认值 + `is_llm_configured()`
- `app/models/schemas.py`(新)—— Pydantic 出入参模型 + `mask_key`/`to_masked_config` + `ChatRequest`
- `app/services/llm.py`(新)—— `get_llm_client() -> (OpenAI, model)`,按 `(base_url, api_key)` 缓存,变更即重建
- `app/api/routes/settings.py`(新)—— `GET/PUT /api/settings`、`POST /api/settings/test`
- `app/api/routes/chat.py`(新)—— `POST /api/chat/stream`(SSE 单轮)
- `app/api/main.py`(改)—— 注册上面两个 router
- `tests/test_config_store.py`、`tests/test_schemas.py`、`tests/test_llm.py`、`tests/test_settings_routes.py`(新)
- `pyproject.toml`(改)—— 加 dev 依赖 pytest

**前端(`frontend/`,`npm create vite` 脚手架生成):**
- `vite.config.ts`(改)—— 加 `/api` → `127.0.0.1:8000` 代理
- `src/lib/api.ts`(新)—— `streamChat(message, onEvent)` 消费 SSE
- `src/hooks/useChatStream.ts`(新)—— React hook,管理消息与流式拼接
- `src/App.tsx`(改)—— 输入框 + 消息流 + 打字机
- `src/App.css`(改)—— 极简样式

---

## Task 1: config_store —— 配置读写 + 缓存

**Files:**
- Create: `backend/app/services/config_store.py`
- Create: `backend/tests/test_config_store.py`
- Modify: `backend/pyproject.toml`(加 pytest)

**Interfaces:**
- Consumes: `app.config.settings`(读 `settings.data_dir`)
- Produces:
  - `DEFAULTS: dict`(四分组:llm/embedding/security/agent)
  - `load() -> dict` / `get() -> dict` / `update(partial: dict) -> dict` / `is_llm_configured() -> bool`
  - `_reset_cache() -> None`(测试用)

- [ ] **Step 1: 加 pytest 依赖**

Run:
```bash
cd backend && uv add --dev pytest
```
Expected: `pyproject.toml` 出现 `[dependency-groups]` 含 `pytest`,`uv.lock` 更新。

- [ ] **Step 2: 写失败测试**

Create `backend/tests/test_config_store.py`:
```python
import json

import pytest

from app.config import settings
from app.services import config_store


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    # 把配置目录指到临时目录,互不污染
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    yield tmp_path
    config_store._reset_cache()


def test_get_returns_defaults_when_missing(tmp_config):
    cfg = config_store.get()
    assert cfg["llm"]["model"] == ""          # 默认 key/model 留空
    assert "grep" in cfg["security"]["cmd_whitelist"]


def test_update_deep_merges_and_persists(tmp_config):
    config_store.update({"llm": {"api_key": "sk-abc"}})
    cfg = config_store.get()
    # 只改 api_key,base_url 默认值应保留(深合并,不是整段替换)
    assert cfg["llm"]["api_key"] == "sk-abc"
    assert cfg["llm"]["base_url"] == config_store.DEFAULTS["llm"]["base_url"]
    # 已落盘
    saved = json.loads((tmp_config / "config.json").read_text(encoding="utf-8"))
    assert saved["llm"]["api_key"] == "sk-abc"


def test_is_llm_configured(tmp_config):
    assert config_store.is_llm_configured() is False
    config_store.update({"llm": {"api_key": "sk-abc", "model": "ep-1"}})
    assert config_store.is_llm_configured() is True
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_config_store.py -v`
Expected: FAIL(`ModuleNotFoundError: app.services.config_store` 或 AttributeError)。

- [ ] **Step 4: 写实现**

Create `backend/app/services/config_store.py`:
```python
"""
config_store.py —— 业务配置读写(存 data/config.json)

与 config.py 分工:
  - config.py = 启动必需、很少变(端口/data_dir/qdrant_url),从 .env 读
  - 本文件   = 业务配置、随时可改热生效(LLM/embedding 的 key、安全白黑名单、Agent 参数)

设计:
  - 内存缓存 _cache:首次 get() 从磁盘加载,之后读缓存
  - update(partial):深合并(只改传进来的字段)→ 写回磁盘 → 刷新缓存
  - 缺文件/缺字段用 DEFAULTS 兜底(向后兼容:以后加新字段,老 config.json 不会因缺字段报错)
"""

import json
import logging
import threading
from copy import deepcopy
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# 默认配置:api_key/model 留空 → is_llm_configured() 为 False,前端引导先进设置页
DEFAULTS: dict = {
    "llm": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": "",
        "model": "",
    },
    "embedding": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
        "model": "text-embedding-v3",
    },
    "security": {
        "workspace_dir": "",
        "kb_dir": "",
        "cmd_whitelist": ["grep", "ls", "cat", "git status", "find", "wc"],
        "cmd_blacklist": ["rm -rf", "sudo", "curl", "wget", "mkfs", "dd"],
    },
    "agent": {"max_iters": 10, "temperature": 0.7},
}

_cache: dict | None = None
_lock = threading.Lock()


def _config_path() -> Path:
    # 每次从 settings 现取,便于测试用 monkeypatch 换 data_dir
    return Path(settings.data_dir) / "config.json"


def _deep_merge(base: dict, patch: dict) -> dict:
    """深合并:嵌套 dict 递归合并,而非整段替换。
    类比 JS:要的是 lodash.merge,而不是 Object.assign / {...a,...b} 那种浅合并。"""
    result = deepcopy(base)
    for key, value in patch.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load() -> dict:
    """从磁盘读;不存在用 DEFAULTS。读到的再与 DEFAULTS 深合并,补齐缺失字段(向后兼容)。"""
    path = _config_path()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _deep_merge(DEFAULTS, raw)
    return deepcopy(DEFAULTS)


def get() -> dict:
    """返回当前配置(带缓存)。返回副本,防外部误改缓存。"""
    global _cache
    if _cache is None:
        _cache = load()
    return deepcopy(_cache)


def update(partial: dict) -> dict:
    """深合并 partial → 写回磁盘 → 刷新缓存。返回更新后的完整配置。
    整段加锁保证读-改-写原子(单用户也可能并发几个请求)。"""
    global _cache
    with _lock:
        current = _cache if _cache is not None else load()
        merged = _deep_merge(current, partial)
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        _cache = merged
        # 只记录改了哪些分组,绝不打印字段值(避免泄露 api_key)
        logger.info("配置更新: sections=%s", list(partial.keys()))
        return deepcopy(merged)


def is_llm_configured() -> bool:
    """LLM 三要素是否齐全 —— 前端首启引导用它判断要不要强制进设置页。"""
    llm = get()["llm"]
    return bool(llm.get("base_url") and llm.get("api_key") and llm.get("model"))


def _reset_cache() -> None:
    """仅测试用:清空缓存,让下次 get() 重新从磁盘加载。"""
    global _cache
    _cache = None
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_config_store.py -v`
Expected: 3 passed。

- [ ] **Step 6: 提交**

```bash
cd backend && git add app/services/config_store.py tests/test_config_store.py pyproject.toml uv.lock
git commit -m "feat(config): config_store 读写 data/config.json + 深合并 + 缓存"
```

---

## Task 2: schemas —— API 出入参模型 + key 脱敏

**Files:**
- Create: `backend/app/models/schemas.py`
- Create: `backend/tests/test_schemas.py`

**Interfaces:**
- Produces:
  - 出参:`AppConfig`(含 `LLMSettings/EmbeddingSettings/SecuritySettings/AgentSettings`)
  - 入参:`ConfigUpdate`(含 `LLMUpdate/EmbeddingUpdate/SecurityUpdate/AgentUpdate`,字段全可选)
  - `TestConnectionRequest{base_url,api_key,model}` / `TestConnectionResult{ok,error}`
  - `ChatRequest{message}`
  - `mask_key(key: str) -> str` / `to_masked_config(config: dict) -> AppConfig`

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_schemas.py`:
```python
from app.models import schemas


def test_mask_key():
    assert schemas.mask_key("") == ""
    assert schemas.mask_key("ab") == "****"
    assert schemas.mask_key("sk-abcdef123456") == "sk-***3456"


def test_to_masked_config_hides_keys():
    cfg = {
        "llm": {"base_url": "u", "api_key": "sk-abcdef123456", "model": "m"},
        "embedding": {"base_url": "u2", "api_key": "sk-zzzz9999", "model": "e"},
        "security": {"workspace_dir": "", "kb_dir": "", "cmd_whitelist": [], "cmd_blacklist": []},
        "agent": {"max_iters": 10, "temperature": 0.7},
    }
    out = schemas.to_masked_config(cfg)
    assert out.llm.api_key == "sk-***3456"
    assert out.embedding.api_key == "sk-***9999"
    assert out.llm.model == "m"          # 非 key 字段原样


def test_config_update_all_optional():
    # 只传一个字段应能通过校验(局部更新)
    u = schemas.ConfigUpdate(llm=schemas.LLMUpdate(model="ep-x"))
    dumped = u.model_dump(exclude_none=True)
    assert dumped == {"llm": {"model": "ep-x"}}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_schemas.py -v`
Expected: FAIL(`ModuleNotFoundError: app.models.schemas`)。

- [ ] **Step 3: 写实现**

Create `backend/app/models/schemas.py`:
```python
"""
schemas.py —— API 出入口的 Pydantic 模型(请求校验 + 响应塑形 + key 脱敏)

配置在 config_store 里是裸 dict(内部方便);API 边界要守门:
  - 入参:前端传来的更新请求,结构不对直接 422(Pydantic 自动校验)
  - 出参:返回配置时 api_key 必须脱敏(只回 sk-***1234),绝不吐明文
类比:这层就是给 HTTP 边界加了 TS interface + 运行时校验。
"""

from copy import deepcopy

from pydantic import BaseModel


# ---- 出参:GET /api/settings(key 已脱敏) ----
class LLMSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class EmbeddingSettings(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class SecuritySettings(BaseModel):
    workspace_dir: str = ""
    kb_dir: str = ""
    cmd_whitelist: list[str] = []
    cmd_blacklist: list[str] = []


class AgentSettings(BaseModel):
    max_iters: int = 10
    temperature: float = 0.7


class AppConfig(BaseModel):
    llm: LLMSettings
    embedding: EmbeddingSettings
    security: SecuritySettings
    agent: AgentSettings


# ---- 入参:PUT /api/settings 局部更新(字段全可选,None=不改) ----
class LLMUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class EmbeddingUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class SecurityUpdate(BaseModel):
    workspace_dir: str | None = None
    kb_dir: str | None = None
    cmd_whitelist: list[str] | None = None
    cmd_blacklist: list[str] | None = None


class AgentUpdate(BaseModel):
    max_iters: int | None = None
    temperature: float | None = None


class ConfigUpdate(BaseModel):
    llm: LLMUpdate | None = None
    embedding: EmbeddingUpdate | None = None
    security: SecurityUpdate | None = None
    agent: AgentUpdate | None = None


# ---- 测试连接 ----
class TestConnectionRequest(BaseModel):
    base_url: str
    api_key: str
    model: str


class TestConnectionResult(BaseModel):
    ok: bool
    error: str = ""


# ---- 对话(P0 单轮;session 在 P1 引入) ----
class ChatRequest(BaseModel):
    message: str


# ---- key 脱敏 ----
def mask_key(key: str) -> str:
    """只保留末 4 位:sk-abcdef123456 -> sk-***3456;空串→空串;过短(≤4)→ ****。"""
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return f"{key[:3]}***{key[-4:]}"


def to_masked_config(config: dict) -> AppConfig:
    """内部 dict → 响应模型,并对两个 api_key 脱敏。"""
    masked = deepcopy(config)
    masked["llm"]["api_key"] = mask_key(config["llm"].get("api_key", ""))
    masked["embedding"]["api_key"] = mask_key(config["embedding"].get("api_key", ""))
    return AppConfig(**masked)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_schemas.py -v`
Expected: 3 passed。

- [ ] **Step 5: 提交**

```bash
cd backend && git add app/models/schemas.py tests/test_schemas.py
git commit -m "feat(schemas): 配置出入参模型 + api_key 脱敏"
```

---

## Task 3: services/llm —— 动态 LLM 客户端(可热切换)

**Files:**
- Create: `backend/app/services/llm.py`
- Create: `backend/tests/test_llm.py`

**Interfaces:**
- Consumes: `config_store.get()["llm"]`(base_url/api_key/model)
- Produces: `get_llm_client() -> tuple[OpenAI, str]`(返回 client 与 model);`_reset_client() -> None`(测试用)

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_llm.py`:
```python
import pytest

from app.config import settings
from app.services import config_store, llm


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    llm._reset_client()
    yield tmp_path
    config_store._reset_cache()
    llm._reset_client()


def test_raises_when_not_configured(tmp_config):
    with pytest.raises(RuntimeError):
        llm.get_llm_client()


def test_rebuilds_client_on_key_change(tmp_config):
    config_store.update({"llm": {"api_key": "sk-1", "model": "m1", "base_url": "http://a"}})
    c1, m1 = llm.get_llm_client()
    assert m1 == "m1"
    # 同配置再取 → 命中缓存,同一个 client
    c2, _ = llm.get_llm_client()
    assert c2 is c1
    # 改 key → 重建(不同对象)
    config_store.update({"llm": {"api_key": "sk-2"}})
    c3, _ = llm.get_llm_client()
    assert c3 is not c1
    assert c3.api_key == "sk-2"
    # 只改 model → 不重建(同 client),但返回新 model
    config_store.update({"llm": {"model": "m2"}})
    c4, m4 = llm.get_llm_client()
    assert c4 is c3
    assert m4 == "m2"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_llm.py -v`
Expected: FAIL(`ModuleNotFoundError: app.services.llm`)。

- [ ] **Step 3: 写实现**

Create `backend/app/services/llm.py`:
```python
"""
services/llm.py —— 动态 LLM 客户端工厂(替代 agent-study 写死读 env 的 build_client)

关键:base_url/api_key/model 来自 config_store 的当前配置,配置页改完即时生效。
客户端按 (base_url, api_key) 缓存:只有它俩变了才重建;换 model 无需重建(model 每次现取)。
"""

import logging

from openai import OpenAI

from app.services import config_store

logger = logging.getLogger(__name__)

_client: OpenAI | None = None
_client_key: tuple[str, str] | None = None


def get_llm_client() -> tuple[OpenAI, str]:
    """读当前配置返回 (client, model)。未配置直接报错,别让后面莫名失败。"""
    llm = config_store.get()["llm"]
    base_url = llm.get("base_url") or ""
    api_key = llm.get("api_key") or ""
    model = llm.get("model") or ""
    if not api_key or not model:
        raise RuntimeError("LLM 未配置:请在设置页填写 api_key 与 model")

    global _client, _client_key
    key = (base_url, api_key)
    if _client is None or _client_key != key:
        _client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=60)
        _client_key = key
        logger.info("重建 LLM 客户端: base_url=%s", base_url or "(默认)")  # 不打印 key
    return _client, model


def _reset_client() -> None:
    """仅测试用:清空客户端缓存。"""
    global _client, _client_key
    _client = None
    _client_key = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_llm.py -v`
Expected: 2 passed。

- [ ] **Step 5: 提交**

```bash
cd backend && git add app/services/llm.py tests/test_llm.py
git commit -m "feat(llm): 动态 LLM 客户端,按配置热重建"
```

---

## Task 4: routes/settings —— 配置读写 + 测试连接

**Files:**
- Create: `backend/app/api/routes/settings.py`
- Modify: `backend/app/api/main.py`(注册 router)
- Create: `backend/tests/test_settings_routes.py`

**Interfaces:**
- Consumes: `config_store.get()/update()`、`schemas.{AppConfig,ConfigUpdate,TestConnectionRequest,TestConnectionResult,to_masked_config}`
- Produces: `router`(前缀 `/api/settings`,含 `GET ""` / `PUT ""` / `POST "/test"`)

- [ ] **Step 1: 写失败测试(GET/PUT 不打网络)**

Create `backend/tests/test_settings_routes.py`:
```python
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import config_store
from app.api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    yield TestClient(app)
    config_store._reset_cache()


def test_get_settings_masks_key(client):
    config_store.update({"llm": {"api_key": "sk-abcdef123456"}})
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["llm"]["api_key"] == "sk-***3456"


def test_put_settings_updates(client):
    r = client.put("/api/settings", json={"llm": {"model": "ep-x"}})
    assert r.status_code == 200
    assert r.json()["llm"]["model"] == "ep-x"


def test_put_ignores_masked_key(client):
    # 前端把脱敏 key 原样回传时,不能用掩码覆盖真 key
    config_store.update({"llm": {"api_key": "sk-realkey9999"}})
    client.put("/api/settings", json={"llm": {"api_key": "sk-***9999"}})
    assert config_store.get()["llm"]["api_key"] == "sk-realkey9999"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_settings_routes.py -v`
Expected: FAIL(404,router 还没挂 / 模块不存在)。

- [ ] **Step 3: 写 router**

Create `backend/app/api/routes/settings.py`:
```python
"""
routes/settings.py —— 配置读写 + 测试连接

GET  /api/settings        读当前配置(key 脱敏)
PUT  /api/settings        局部更新(只传要改的字段),返回更新后配置(脱敏)
POST /api/settings/test   用传入的 LLM 配置发一次最小请求,验证连通(存之前先验)
"""

import logging

from fastapi import APIRouter
from openai import OpenAI

from app.models import schemas
from app.services import config_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


def _drop_masked_keys(partial: dict) -> dict:
    """前端可能把脱敏后的 key(含 ***)原样回传;含 *** 视为未改,丢弃,避免用掩码覆盖真 key。"""
    for section in ("llm", "embedding"):
        sec = partial.get(section)
        if isinstance(sec, dict) and isinstance(sec.get("api_key"), str) and "***" in sec["api_key"]:
            sec.pop("api_key")
    return partial


@router.get("", response_model=schemas.AppConfig)
def get_settings() -> schemas.AppConfig:
    return schemas.to_masked_config(config_store.get())


@router.put("", response_model=schemas.AppConfig)
def update_settings(update: schemas.ConfigUpdate) -> schemas.AppConfig:
    partial = update.model_dump(exclude_none=True)   # 丢掉没传的字段 → 天然是局部更新
    partial = _drop_masked_keys(partial)
    merged = config_store.update(partial)
    logger.info("配置已更新: sections=%s", list(partial.keys()))  # 只记分组名
    return schemas.to_masked_config(merged)


@router.post("/test", response_model=schemas.TestConnectionResult)
def test_connection(req: schemas.TestConnectionRequest) -> schemas.TestConnectionResult:
    """临时建客户端发 1 token 请求,验证 base_url/key/model 是否可用。"""
    try:
        client = OpenAI(api_key=req.api_key, base_url=req.base_url or None, timeout=20)
        client.chat.completions.create(
            model=req.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        return schemas.TestConnectionResult(ok=True)
    except Exception as e:  # noqa: BLE001 - 错误信息透传给前端展示
        logger.warning("测试连接失败: %s", type(e).__name__)  # 不打印 key
        return schemas.TestConnectionResult(ok=False, error=str(e))
```

- [ ] **Step 4: 在 main.py 注册 router**

Modify `backend/app/api/main.py` —— 在 `@app.get("/health")` 定义**之前**加:
```python
from app.api.routes import settings as settings_routes

app.include_router(settings_routes.router)
```
(import 放文件顶部其余 import 旁;`include_router` 放在 `app = FastAPI(...)` 与 CORS 之后。)

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_settings_routes.py -v`
Expected: 3 passed。

- [ ] **Step 6: 手动验证(真实起服务)**

Run:
```bash
cd backend && uv run run.py
```
另开一个终端:
```bash
curl -s localhost:8000/api/settings | python3 -m json.tool
curl -s -X PUT localhost:8000/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"llm":{"api_key":"sk-REAL-KEY","model":"YOUR-MODEL"}}' | python3 -m json.tool
```
Expected: GET 返回四分组、api_key 为 `sk-***…`;PUT 后 `data/config.json` 里出现真 key、返回体里是脱敏值。用你**真实的** base_url/key/model 再 PUT 一次(P0 后续 chat 要用)。
可选测连接:
```bash
curl -s -X POST localhost:8000/api/settings/test -H 'Content-Type: application/json' \
  -d '{"base_url":"<你的base_url>","api_key":"<你的key>","model":"<你的model>"}'
```
Expected: `{"ok":true,"error":""}`。

- [ ] **Step 7: 提交**

```bash
cd backend && git add app/api/routes/settings.py app/api/main.py tests/test_settings_routes.py
git commit -m "feat(settings): 配置读写 API + 测试连接 + key 脱敏"
```

---

## Task 5: routes/chat —— 单轮 SSE 流式对话

**Files:**
- Create: `backend/app/api/routes/chat.py`
- Modify: `backend/app/api/main.py`(注册 router)

**Interfaces:**
- Consumes: `llm.get_llm_client()`、`schemas.ChatRequest`
- Produces: `router`(前缀 `/api/chat`,含 `POST "/stream"`,返回 `StreamingResponse` text/event-stream)
- 事件协议(每行 `data: {json}\n\n`):`{"type":"text","content":"…"}` / `{"type":"done"}` / `{"type":"error","message":"…"}`

- [ ] **Step 1: 写 router**

Create `backend/app/api/routes/chat.py`:
```python
"""
routes/chat.py —— 单轮流式对话(P0;工具/会话在后续里程碑加)

POST /api/chat/stream  body {message}
返回 SSE:每行 `data: {json}\n\n`,事件类型 text / done / error。
core 与输出通道解耦的雏形:这里只把 openai 的流转成 typed event,后面 P2 再加 tool_call 等。
"""

import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.models import schemas
from app.services import llm

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/stream")
def chat_stream(req: schemas.ChatRequest) -> StreamingResponse:
    logger.info("chat 请求: msg_len=%d", len(req.message))  # 只记长度,不打全文

    def event_stream():
        try:
            client, model = llm.get_llm_client()
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": req.message}],
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield _sse({"type": "text", "content": delta})
            yield _sse({"type": "done"})
        except Exception as e:  # noqa: BLE001 - 把错误当事件发给前端展示
            logger.warning("chat 失败: %s", type(e).__name__)
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 2: 在 main.py 注册 router**

Modify `backend/app/api/main.py` —— 在 settings router 注册旁加:
```python
from app.api.routes import chat as chat_routes

app.include_router(chat_routes.router)
```

- [ ] **Step 3: 手动验证流式(真实 key 已在 config.json)**

确保 Task 4 已 PUT 进真实 base_url/key/model。起服务 `cd backend && uv run run.py`,另开终端:
```bash
curl -N -X POST localhost:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"用一句话介绍你自己"}'
```
Expected: 看到一串逐步冒出的 `data: {"type":"text","content":"…"}`,最后 `data: {"type":"done"}`。
故意验证错误分支:临时把 model PUT 成一个不存在的值,再 `curl -N`,应收到 `data: {"type":"error","message":"…"}` 而不是崩栈。验证完把 model 改回。

- [ ] **Step 4: 提交**

```bash
cd backend && git add app/api/routes/chat.py app/api/main.py
git commit -m "feat(chat): 单轮 SSE 流式对话端点"
```

---

## Task 6: 前端 —— 极简聊天页(打字机)

**Files:**
- 脚手架生成 `frontend/`
- Modify: `frontend/vite.config.ts`(加 `/api` 代理)
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/hooks/useChatStream.ts`
- Modify: `frontend/src/App.tsx`、`frontend/src/App.css`

**Interfaces:**
- Consumes: 后端 `POST /api/chat/stream`(经 vite `/api` 代理,免跨域)
- Produces: `streamChat(message, onEvent)`、`useChatStream() -> {messages, streaming, send}`

- [ ] **Step 1: 脚手架生成前端**

Run(在 `superstar/` 根目录):
```bash
npm create vite@latest frontend -- --template react-ts
cd frontend && npm install
```
Expected: 生成 `frontend/`,`npm install` 成功。

- [ ] **Step 2: 配 `/api` 代理**

Overwrite `frontend/vite.config.ts`:
```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期把 /api 转发到后端,前端用相对路径 fetch,免跨域
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
```

- [ ] **Step 3: 写 SSE 消费**

Create `frontend/src/lib/api.ts`:
```ts
// 与后端 chat.py 的事件协议对齐
export type ChatEvent =
  | { type: 'text'; content: string }
  | { type: 'done' }
  | { type: 'error'; message: string }

// fetch + ReadableStream 读 SSE:按空行切事件、剥掉 data: 前缀、JSON.parse
export async function streamChat(
  message: string,
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  })
  if (!resp.body) throw new Error('无响应体')

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')   // SSE 事件以空行分隔
    buffer = parts.pop() ?? ''           // 最后一段可能不完整,留到下次
    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data:')) continue
      const payload = line.slice(line.indexOf('data:') + 5).trim()
      if (payload) onEvent(JSON.parse(payload) as ChatEvent)
    }
  }
}
```

- [ ] **Step 4: 写 hook**

Create `frontend/src/hooks/useChatStream.ts`:
```ts
import { useState, useCallback } from 'react'

import { streamChat, type ChatEvent } from '../lib/api'

export type Message = { role: 'user' | 'assistant'; content: string }

export function useChatStream() {
  const [messages, setMessages] = useState<Message[]>([])
  const [streaming, setStreaming] = useState(false)

  const send = useCallback(async (text: string) => {
    // 先塞入用户消息 + 一个空的 assistant 占位,后续把 token 往占位里追加
    setMessages((m) => [
      ...m,
      { role: 'user', content: text },
      { role: 'assistant', content: '' },
    ])
    setStreaming(true)
    try {
      await streamChat(text, (e: ChatEvent) => {
        if (e.type === 'text') {
          setMessages((m) => {
            const next = [...m]
            const last = next[next.length - 1]
            next[next.length - 1] = { role: 'assistant', content: last.content + e.content }
            return next
          })
        } else if (e.type === 'error') {
          setMessages((m) => {
            const next = [...m]
            next[next.length - 1] = { role: 'assistant', content: `⚠️ ${e.message}` }
            return next
          })
        }
      })
    } finally {
      setStreaming(false)
    }
  }, [])

  return { messages, streaming, send }
}
```

- [ ] **Step 5: 写页面**

Overwrite `frontend/src/App.tsx`:
```tsx
import { useState } from 'react'

import { useChatStream } from './hooks/useChatStream'
import './App.css'

export default function App() {
  const { messages, streaming, send } = useChatStream()
  const [input, setInput] = useState('')

  const onSend = () => {
    const text = input.trim()
    if (!text || streaming) return
    setInput('')
    void send(text)
  }

  return (
    <div className="app">
      <h1>Superstar</h1>
      <div className="messages">
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <b>{m.role === 'user' ? '你' : 'AI'}:</b> {m.content}
            {streaming && i === messages.length - 1 && m.role === 'assistant' ? ' ▋' : ''}
          </div>
        ))}
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
  )
}
```

Overwrite `frontend/src/App.css`:
```css
.app { max-width: 720px; margin: 0 auto; padding: 24px; font-family: system-ui, sans-serif; }
.messages { display: flex; flex-direction: column; gap: 12px; margin: 16px 0; min-height: 300px; }
.msg { line-height: 1.6; white-space: pre-wrap; }
.msg.user { color: #1a1a1a; }
.msg.assistant { color: #0b6; }
.composer { display: flex; gap: 8px; }
.composer input { flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
.composer button { padding: 10px 20px; border: 0; border-radius: 8px; background: #0b6; color: #fff; cursor: pointer; }
.composer button:disabled { background: #aaa; cursor: not-allowed; }
```

- [ ] **Step 6: 手动验证端到端**

两个终端分别起后端与前端:
```bash
# 终端 A
cd backend && uv run run.py
# 终端 B
cd frontend && npm run dev
```
浏览器开 `http://localhost:5173`,输入"用一句话介绍你自己" → 回车。
Expected: AI 气泡里文字**逐字冒出**(打字机),末尾光标 ▋,结束后消失。改一个不存在的 model(用 curl PUT)再发一句,页面显示 `⚠️ …` 而非白屏。
**热生效验证**:服务不重启,用 Task 4 的 curl PUT 把 `llm.model` 换成另一个可用模型,前端再发一句 → 走的是新模型(证明 `get_llm_client` 热切换生效)。

- [ ] **Step 7: 提交**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add frontend
git commit -m "feat(frontend): 极简聊天页 + SSE 打字机(P0 竖切打通)"
```

---

## Self-Review(对照 P0 spec)

**1. Spec coverage**（DEVELOPMENT_PLAN.md 的 P0 = config.json + 动态 llm_client + POST /api/chat/stream + 极简前端 + 打通联调/SSE/CORS/热重载/配置热生效):
- config.json 读写 → Task 1 ✓
- key 脱敏 / 配置 API → Task 2 + Task 4 ✓
- 动态 llm_client(热切换) → Task 3(缓存按 key 失效) + Task 6 Step 6 热生效验证 ✓
- 测试连接 → Task 4 `POST /test` ✓
- `POST /api/chat/stream`(SSE) → Task 5 ✓
- 极简前端打字机 → Task 6 ✓
- CORS → 已存在于 main.py(基线) + vite `/api` 代理(Task 6 双保险) ✓
- 热重载 → run.py `reload=True`(基线) ✓
- 配置热生效 → Task 3 + Task 6 Step 6 ✓

**2. Placeholder scan:** 无 TBD/TODO;所有代码步骤含完整代码;所有命令含预期输出。✓

**3. Type consistency:**
- `get_llm_client() -> (OpenAI, str)` 在 Task 3 定义、Task 5 消费,签名一致。✓
- `config_store.get()/update()/_reset_cache()` 在 Task 1 定义,Task 3/4 消费,名称一致。✓
- 前端 `ChatEvent` 三型(text/done/error)与后端 `chat.py` 产出的事件类型逐一对齐。✓
- `schemas.to_masked_config` / `ConfigUpdate.model_dump(exclude_none=True)` 在 Task 2 定义、Task 4 消费,一致。✓

**边界备注:** `POST /api/settings/test` 与 `chat/stream` 需真实 LLM key + 网络,故用手动验证(curl/浏览器),不写自动化单测——避免为 mock 网络写无学习价值的测试。纯逻辑(config_store/schemas/llm 缓存/settings GET-PUT)均有自动化单测覆盖。
