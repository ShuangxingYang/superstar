# P3 RAG 知识库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Agent 装一只"查资料的手"——文档入库(loaders→切块→embed→Qdrant)+ `search_kb` 两阶段检索(向量召回→rerank 精排)带来源 + 反幻觉 + 前端知识库管理页。

**Architecture:** 三层分层。`services/`(loaders 适配器 / chunker 递归切块 / rag_store 收敛 embed+Qdrant+rerank)是通用检索设施,不知 Agent 循环存在;`agent/tools/rag.py` 是"手"(search_kb 工具,注册进 registry);`api/routes/kb.py` 是 HTTP 入口。依赖严格单向:tools/rag → rag_store → loaders/chunker。前端渐进引入 shadcn,只在新增知识库页用。

**Tech Stack:** Python 3.11 / FastAPI / pydantic / qdrant-client / pypdf / openai SDK(embedding 复用) / 标准库 urllib(rerank HTTP) / pytest。前端 React+Vite+TS + Tailwind + shadcn(渐进)。

## Global Constraints

- 依赖越轻越好:后端仅新增 `qdrant-client`、`pypdf`;rerank 用标准库 HTTP POST 打 dashscope 端点,**不引 dashscope SDK**;embedding 复用现有 openai SDK。
- 错误变返回值、绝不崩流:工具层错误由 ToolRegistry 自愈成 tool_result;RAG 挂不影响读写文件/跑命令。
- 数据安全洁癖:Qdrant 集合存在即复用**绝不自动删**(除非显式 rebuild);维度不一致**报错提示手动重建**,不偷删。
- 日志绝不打印 api_key;RAG 日志只记 source/块数/退出信息,不记文档正文。
- API 向后兼容"只加不删":config 新增字段必须有默认值,DEFAULTS 补齐(老 config.json 不因缺字段报错)。
- embedding: dashscope `text-embedding-v3`,维度以 config `embedding.dimension` 为准(默认 1024)。
- 测试不依赖真网络/真库:embed/rerank/Qdrant client 一律 mock;纯函数(chunker/loaders)直接测。
- 测试运行前务必 `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend`(git 命令会把 cwd 切回 repo 根,导致 "Failed to spawn: pytest")。
- 前端 shadcn 渐进引入:只新页用,老组件(App.css/SessionList/ToolCallCard)维持手写 CSS 不动。

---

## 文件结构

**后端新增:**
- `backend/app/services/loaders.py` — `Document` dataclass + `load_document(path)`,扩展名分发(.pdf→pypdf,其余 read_text)
- `backend/app/services/chunker.py` — `split(text, chunk_size, overlap)` 递归字符切块
- `backend/app/services/rag_store.py` — `RagStoreError` + embed/Qdrant 建判灌删召回 + rerank + index_document/search/list_documents/delete_document/rebuild/stats
- `backend/app/agent/tools/rag.py` — `SearchKbArgs` + `search_kb(args)`
- `backend/app/api/routes/kb.py` — 上传/列表/删除/重建/状态 路由

**后端修改:**
- `backend/app/services/config_store.py` — DEFAULTS 加 `embedding.dimension` + `rag` 段
- `backend/app/agent/tools/__init__.py` — 注册 search_kb
- `backend/app/agent/loop.py` — SYSTEM_PROMPT 追加 search_kb + 反幻觉
- `backend/app/api/main.py` — 挂 kb 路由
- `backend/pyproject.toml` — 加 qdrant-client、pypdf

**前端新增/修改:**
- `frontend/` — 装 Tailwind + shadcn 初始化(Task 10)
- `frontend/src/lib/api.ts` — 加 kb API 函数
- `frontend/src/components/KbManager.tsx` — 知识库管理页
- `frontend/src/App.tsx` / `SessionList.tsx` — 挂入口

**任务顺序:** Task 1(config 地基)→ 2(chunker)→ 3(loaders)→ 4-6(rag_store 分三刀)→ 7(search_kb 工具+注册+prompt)→ 8(kb 路由)→ 9(依赖+冒烟脚本)→ 10(shadcn 接入)→ 11(api.ts)→ 12(KbManager+入口)。后端 1-9 每 task 独立 TDD;前端 10-12 因类型/构建交错,10 单独,11-12 视情况合并提交。

---

### Task 1: config_store 加 embedding.dimension + rag 段

**Files:**
- Modify: `backend/app/services/config_store.py:25-43`(DEFAULTS)
- Test: `backend/tests/test_config_store.py`(追加)

**Interfaces:**
- Produces: `config_store.get()["embedding"]["dimension"]`(int,默认 1024);`config_store.get()["rag"]` = `{chunk_size:500, overlap:80, top_n:20, top_k:5, rerank_model:"gte-rerank"}`

- [ ] **Step 1: Write the failing test**

在 `tests/test_config_store.py` 末尾追加:

```python
def test_defaults_have_rag_section(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    cfg = config_store.get()
    assert cfg["embedding"]["dimension"] == 1024
    assert cfg["rag"]["chunk_size"] == 500
    assert cfg["rag"]["overlap"] == 80
    assert cfg["rag"]["top_n"] == 20
    assert cfg["rag"]["top_k"] == 5
    assert cfg["rag"]["rerank_model"] == "gte-rerank"
    config_store._reset_cache()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_config_store.py::test_defaults_have_rag_section -q`
Expected: FAIL (KeyError: 'dimension')

- [ ] **Step 3: 改 DEFAULTS**

在 `config_store.py` 的 DEFAULTS 里,给 `embedding` 加 `dimension`,并新增 `rag` 段:

```python
    "embedding": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
        "model": "text-embedding-v3",
        "dimension": 1024,
    },
    "security": {
        "workspace_dir": "",
        "kb_dir": "",
        "cmd_whitelist": ["grep", "ls", "cat", "git status", "find", "wc"],
        "cmd_blacklist": ["rm -rf", "sudo", "curl", "wget", "mkfs", "dd"],
    },
    "agent": {"max_iters": 10, "temperature": 0.7},
    "rag": {
        "chunk_size": 500,
        "overlap": 80,
        "top_n": 20,
        "top_k": 5,
        "rerank_model": "gte-rerank",
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_config_store.py -q`
Expected: PASS(全部,含老用例——深合并保证老 config 补齐新字段)

- [ ] **Step 5: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/services/config_store.py backend/tests/test_config_store.py
git commit -m "feat(p3): config 加 embedding.dimension + rag 段(切块/召回/精排参数)"
```

---

### Task 2: chunker 递归字符切块

**Files:**
- Create: `backend/app/services/chunker.py`
- Test: `backend/tests/test_chunker.py`

**Interfaces:**
- Produces: `chunker.split(text: str, chunk_size: int, overlap: int) -> list[str]`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_chunker.py`:

```python
from app.services import chunker


def test_short_text_single_chunk():
    # 短于 chunk_size → 整段一块
    assert chunker.split("hello world", 100, 10) == ["hello world"]


def test_empty_text_returns_empty():
    assert chunker.split("", 100, 10) == []
    assert chunker.split("   ", 100, 10) == []


def test_splits_on_paragraph_boundary():
    # 两段各 ~30 字符,chunk_size=40 → 应在段落边界(\n\n)切开,不硬切
    p1 = "第一段" * 10   # 30 字符
    p2 = "第二段" * 10
    text = f"{p1}\n\n{p2}"
    chunks = chunker.split(text, 40, 5)
    assert len(chunks) >= 2
    # 每块不超过 chunk_size 太多(边界切,允许略超但不离谱)
    assert all(len(c) <= 40 + 5 for c in chunks)


def test_overlap_between_chunks():
    # 无自然边界的长文本 → 硬切 + 重叠。相邻块尾首应有重叠
    text = "A" * 100
    chunks = chunker.split(text, 30, 10)
    assert len(chunks) > 1
    # 每块长度约 30
    assert all(len(c) <= 30 for c in chunks)
    # 重叠:第 2 块起点应回退 overlap,总覆盖 = 完整还原
    assert "".join([chunks[0]] + [c[10:] for c in chunks[1:]]) == text


def test_long_no_boundary_hard_split():
    # 一个超长无分隔符 token 也能被硬切,不死循环
    chunks = chunker.split("X" * 250, 100, 0)
    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [100, 100, 50]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_chunker.py -q`
Expected: FAIL (ModuleNotFoundError: app.services.chunker)

- [ ] **Step 3: 实现 chunker**

创建 `backend/app/services/chunker.py`:

```python
"""
chunker.py —— 递归字符切块(RAG 头号变量)

思路(同 LangChain RecursiveCharacterTextSplitter 内核):按分隔符优先级
["\\n\\n", "\\n", "。", " ", ""] 逐级找切点——尽量在段落边界切,切不动才退到
换行/句号/空格,最后("")硬切。相邻块保留 overlap 重叠,防答案落在接缝被割裂。

为什么不纯定长:M3 实测纯定长会把术语从中间斩断(召回仅 40%);按语义边界切召回明显更好。
"""

SEPARATORS = ["\n\n", "\n", "。", " ", ""]


def split(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    return _recursive_split(text, chunk_size, overlap, SEPARATORS)


def _recursive_split(text: str, chunk_size: int, overlap: int, seps: list[str]) -> list[str]:
    """用当前分隔符把 text 切成"段",段太大就用下一级分隔符继续切,再把段贪心合并成块。"""
    sep = seps[0]
    rest = seps[1:]
    # 按当前分隔符切;sep=="" 表示无分隔符,退化为逐字符
    if sep == "":
        pieces = list(text)
    else:
        parts = text.split(sep)
        # 切完把分隔符补回(除最后一段),保留原文可还原性
        pieces = [p + sep for p in parts[:-1]] + [parts[-1]]

    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        # 单个 piece 就超长:先冲掉 buf,再用下一级分隔符拆这个 piece
        if len(piece) > chunk_size:
            if buf:
                chunks.append(buf)
                buf = ""
            if rest:
                chunks.extend(_recursive_split(piece, chunk_size, overlap, rest))
            else:
                chunks.extend(_hard_split(piece, chunk_size, overlap))
            continue
        # 贪心累加:加上这个 piece 不超 chunk_size 就并入,否则先冲 buf
        if len(buf) + len(piece) <= chunk_size:
            buf += piece
        else:
            if buf:
                chunks.append(buf)
            buf = piece
    if buf:
        chunks.append(buf)
    return _apply_overlap(chunks, overlap)


def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    """无任何分隔符的长串:按 (chunk_size - overlap) 步长滑窗硬切。"""
    step = max(1, chunk_size - overlap)
    return [text[i:i + chunk_size] for i in range(0, len(text), step)]


def _apply_overlap(chunks: list[str], overlap: int) -> list[str]:
    """给相邻块加重叠:每块开头补上前一块末尾 overlap 字符。"""
    if overlap <= 0 or len(chunks) <= 1:
        return chunks
    out = [chunks[0]]
    for prev, cur in zip(chunks, chunks[1:]):
        out.append(prev[-overlap:] + cur)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_chunker.py -q`
Expected: PASS(5 个用例)

若 `test_overlap_between_chunks` 的还原断言因 `_hard_split` 已自带重叠、`_apply_overlap` 又叠一次而失败:`_hard_split` 已产生重叠,不应再过 `_apply_overlap`。改法——`_recursive_split` 里调 `_hard_split` 的结果直接 extend 进 chunks(它们已重叠),最后 `_apply_overlap` 只作用于"贪心合并产生的块"。为简化且可测,统一策略:`_hard_split` 不自带重叠(`step=chunk_size`),重叠一律由 `_apply_overlap` 兜底:

```python
def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
```

此时 `test_long_no_boundary_hard_split`(overlap=0)得 `[100,100,50]` ✓;`test_overlap_between_chunks` 的还原式对"无边界文本"成立(先 hard_split 成 `["A"*30...]` 再 apply_overlap)。重跑确认全绿。

- [ ] **Step 5: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/services/chunker.py backend/tests/test_chunker.py
git commit -m "feat(p3): chunker 递归字符切块(边界优先+重叠)"
```

---

### Task 3: loaders 适配器层

**Files:**
- Create: `backend/app/services/loaders.py`
- Test: `backend/tests/test_loaders.py`

**Interfaces:**
- Consumes: `security.safe_path` 不用(loaders 收绝对/相对路径由调用方保证);此处直接收 Path。
- Produces: `loaders.Document`(dataclass: `text: str`, `source: str`);`loaders.load_document(path: Path, source: str) -> Document`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_loaders.py`:

```python
from pathlib import Path

from app.services import loaders


def test_load_txt(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("纯文本内容", encoding="utf-8")
    doc = loaders.load_document(p, source="a.txt")
    assert doc.text == "纯文本内容"
    assert doc.source == "a.txt"


def test_load_md(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("# 标题\n正文", encoding="utf-8")
    doc = loaders.load_document(p, source="b.md")
    assert "正文" in doc.text


def test_load_unknown_ext_as_text(tmp_path):
    # 代码文件等未登记扩展名 → 默认 read_text
    p = tmp_path / "c.py"
    p.write_text("print('hi')", encoding="utf-8")
    doc = loaders.load_document(p, source="c.py")
    assert "print" in doc.text


def test_pdf_dispatches_to_pdf_loader(tmp_path, monkeypatch):
    # 不造真 PDF:mock _load_pdf,只验证 .pdf 走到它
    p = tmp_path / "d.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(loaders, "_load_pdf", lambda path: "PDF抽出的文字")
    doc = loaders.load_document(p, source="d.pdf")
    assert doc.text == "PDF抽出的文字"
    assert doc.source == "d.pdf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_loaders.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: 实现 loaders**

创建 `backend/app/services/loaders.py`:

```python
"""
loaders.py —— 文档加载适配器层(业界通行的三层骨架之第一层)

每种来源一个 loader,统一吐 Document(text + source),下游(切块/embed/检索)
与来源彻底解耦。只做当下需要的格式:.pdf 走 pypdf 抽文字层,其余 read_text。
以后加飞书 API loader / OCR,只在 _LOADERS 映射表注册一项,下游不动。
"""
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Document:
    text: str
    source: str


def _load_pdf(path: Path) -> str:
    """纯代码抽 PDF 文字层(不依赖 LLM)。扫描件/图片型 PDF 抽不出字,返回空。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = "\n".join(pages)
    # 简单清洗:压掉连续 3+ 空行为 2 行,去首尾空白
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# 扩展名 → 抽取器映射;未命中默认 read_text
_LOADERS = {".pdf": _load_pdf}


def load_document(path: Path, source: str) -> Document:
    loader = _LOADERS.get(path.suffix.lower())
    if loader is not None:
        text = loader(path)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    logger.info("加载文档: source=%s, chars=%d", source, len(text))
    return Document(text=text, source=source)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_loaders.py -q`
Expected: FAIL(test_pdf 那条会因 `from pypdf import` 在 mock 前就 import 失败?不会——mock 替换了 `_load_pdf` 整个函数,内部 import 不执行)。若因 pypdf 未装导致**其他**收集期错误,先跳过 pdf 用例;pypdf 在 Task 9 装。这里改为:

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_loaders.py -q`
Expected: PASS(4 个用例;`_load_pdf` 被 mock,不触发真 pypdf import)

- [ ] **Step 5: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/services/loaders.py backend/tests/test_loaders.py
git commit -m "feat(p3): loaders 适配器层(Document + 扩展名分发,pdf 走 pypdf)"
```

---

### Task 4: rag_store 客户端 + embed + 集合管理(建/判/维度校验)

**Files:**
- Create: `backend/app/services/rag_store.py`
- Test: `backend/tests/test_rag_store.py`

**Interfaces:**
- Consumes: `config_store.get()`(embedding/rag/qdrant);`Document`(未直接用,Task 5 用)
- Produces: `RagStoreError`(Exception);`rag_store._embed(text) -> list[float]`;`rag_store._get_qdrant()`;`rag_store._ensure_collection()`;`rag_store.COLLECTION`(str);`rag_store._reset()`(测试用清缓存)

- [ ] **Step 1: Write the failing test**

创建 `tests/test_rag_store.py`:

```python
import pytest

from app.config import settings
from app.services import config_store, rag_store


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    rag_store._reset()
    config_store.update({"embedding": {"api_key": "sk-x", "model": "text-embedding-v3", "dimension": 1024}})
    yield
    config_store._reset_cache()
    rag_store._reset()


class _FakeQdrant:
    """假 Qdrant:记录调用,内存存点。"""
    def __init__(self):
        self.collections = {}   # name -> dim
        self.points = {}        # name -> list[point-like]
        self.upserted = []
    def collection_exists(self, name): return name in self.collections
    def create_collection(self, collection_name, vectors_config):
        self.collections[collection_name] = vectors_config.size
        self.points[collection_name] = []
    def get_collection(self, name):
        dim = self.collections[name]
        class C:  # 仿 qdrant 返回结构 config.params.vectors.size
            class config:
                class params:
                    class vectors:
                        size = dim
        C.config.params.vectors.size = dim
        return C


def test_ensure_collection_creates_when_absent(cfg, monkeypatch):
    fake = _FakeQdrant()
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    rag_store._ensure_collection()
    assert rag_store.COLLECTION in fake.collections
    assert fake.collections[rag_store.COLLECTION] == 1024


def test_ensure_collection_dimension_mismatch_raises(cfg, monkeypatch):
    fake = _FakeQdrant()
    fake.collections[rag_store.COLLECTION] = 768   # 已存在且 768 维
    fake.points[rag_store.COLLECTION] = []
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    config_store.update({"embedding": {"dimension": 1024}})  # 当前配置 1024
    with pytest.raises(rag_store.RagStoreError, match="维"):
        rag_store._ensure_collection()


def test_qdrant_connection_error_wrapped(cfg, monkeypatch):
    def boom():
        raise ConnectionError("refused")
    monkeypatch.setattr(rag_store, "_get_qdrant", boom)
    with pytest.raises(rag_store.RagStoreError, match="未启动"):
        rag_store._ensure_collection()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_rag_store.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: 实现 rag_store 第一刀(客户端+集合)**

创建 `backend/app/services/rag_store.py`:

```python
"""
rag_store.py —— 检索设施(收敛 M3 散落 7 份的 embed + Qdrant + rerank)

模块级函数(非 class:无跨调用共享内存状态)。客户端按 llm.py 那套模块级缓存。
集合管理三坑:建/判复用(绝不自动删)、维度漂移报错(不偷删)、连不上包装成 RagStoreError。
"""
import logging

from openai import OpenAI

from app.config import settings
from app.services import config_store

logger = logging.getLogger(__name__)

COLLECTION = "superstar_kb"


class RagStoreError(Exception):
    """RAG 相关的可预期错误(连不上/维度不一致等),给用户友好提示用。"""


# ---- embedding 客户端(照 llm.py:按 (base_url, api_key) 缓存)----
_embed_client: OpenAI | None = None
_embed_key: tuple[str, str] | None = None


def _get_embed_client() -> tuple[OpenAI, str]:
    emb = config_store.get()["embedding"]
    base_url, api_key, model = emb.get("base_url") or "", emb.get("api_key") or "", emb.get("model") or ""
    if not api_key or not model:
        raise RagStoreError("embedding 未配置:请在设置页填写 embedding 的 api_key 与 model")
    global _embed_client, _embed_key
    key = (base_url, api_key)
    if _embed_client is None or _embed_key != key:
        _embed_client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=60)
        _embed_key = key
        logger.info("重建 embedding 客户端: base_url=%s", base_url or "(默认)")
    return _embed_client, model


def _embed(text: str) -> list[float]:
    client, model = _get_embed_client()
    resp = client.embeddings.create(model=model, input=[text], encoding_format="float")
    return resp.data[0].embedding


# ---- Qdrant 客户端 ----
def _get_qdrant():
    from qdrant_client import QdrantClient
    return QdrantClient(url=settings.qdrant_url, timeout=10)


def _dimension() -> int:
    return int(config_store.get()["embedding"]["dimension"])


def _ensure_collection() -> None:
    """集合不存在则按 (dimension, COSINE) 建;存在则校验维度一致;连不上包装报错。"""
    from qdrant_client.models import Distance, VectorParams
    try:
        client = _get_qdrant()
        exists = client.collection_exists(COLLECTION)
    except (ConnectionError, OSError) as e:
        raise RagStoreError("知识库服务未启动,请先 docker start qdrant") from e
    except Exception as e:  # noqa: BLE001  qdrant 连接类异常五花八门,统一兜
        raise RagStoreError(f"连接知识库服务失败:{e}") from e

    want = _dimension()
    if not exists:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=want, distance=Distance.COSINE),
        )
        logger.info("建集合: %s (dim=%d, COSINE)", COLLECTION, want)
        return
    have = client.get_collection(COLLECTION).config.params.vectors.size
    if have != want:
        raise RagStoreError(
            f"知识库是用 {have} 维建的,当前 embedding 配置 {want} 维,不匹配。"
            f"请在设置页确认 embedding,或到知识库页「重建索引」。"
        )


def _reset() -> None:
    """仅测试用:清 embedding 客户端缓存。"""
    global _embed_client, _embed_key
    _embed_client = None
    _embed_key = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_rag_store.py -q`
Expected: PASS(3 个用例)。注意 `test_qdrant_connection_error_wrapped` 里 `_get_qdrant` 被换成抛 ConnectionError,但实现中 `_get_qdrant()` 在 try 内调用——ConnectionError 被 catch 包装成 RagStoreError ✓。

- [ ] **Step 5: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/services/rag_store.py backend/tests/test_rag_store.py
git commit -m "feat(p3): rag_store 客户端+集合管理(建/判/维度校验/连不上包装)"
```

---

### Task 5: rag_store 灌库 + 增删查(index_document/list/delete/rebuild/stats)

**Files:**
- Modify: `backend/app/services/rag_store.py`(追加)
- Test: `backend/tests/test_rag_store.py`(追加)

**Interfaces:**
- Consumes: `loaders.load_document`、`chunker.split`、`_ensure_collection`、`_embed`、`_get_qdrant`、`config_store`
- Produces: `index_document(path, source) -> dict{source, chunks}`;`list_documents() -> list[dict{source, chunks}]`;`delete_document(source) -> int`;`rebuild() -> dict{documents, chunks}`;`stats() -> dict{documents, chunks, dimension}`;`_point_id(source, idx) -> int`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_rag_store.py`:

```python
def test_point_id_stable_and_distinct():
    a = rag_store._point_id("a.md", 0)
    assert a == rag_store._point_id("a.md", 0)          # 稳定:重灌同文档同块 → 同 id(覆盖非堆积)
    assert a != rag_store._point_id("a.md", 1)          # 不同块不同 id
    assert a != rag_store._point_id("b.md", 0)          # 不同文档不同 id


def test_index_document_flow(cfg, tmp_path, monkeypatch):
    fake = _FakeQdrant()
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    monkeypatch.setattr(rag_store, "_embed", lambda t: [0.1] * 1024)
    # 捕获 upsert
    def fake_upsert(collection_name, points):
        fake.points.setdefault(collection_name, []).extend(points)
    fake.upsert = fake_upsert
    p = tmp_path / "doc.md"
    p.write_text("第一段" * 60 + "\n\n" + "第二段" * 60, encoding="utf-8")  # 会切成多块
    result = rag_store.index_document(p, source="doc.md")
    assert result["source"] == "doc.md"
    assert result["chunks"] >= 2
    assert len(fake.points[rag_store.COLLECTION]) == result["chunks"]
    # 每个 point 的 payload 带 text + source
    pt = fake.points[rag_store.COLLECTION][0]
    assert pt.payload["source"] == "doc.md"
    assert "text" in pt.payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_rag_store.py -k "point_id or index_document" -q`
Expected: FAIL (AttributeError: _point_id / index_document)

- [ ] **Step 3: 实现灌库+增删查**

追加到 `rag_store.py`(在 `_reset` 之前):

```python
import hashlib

from app.services import chunker, loaders


def _point_id(source: str, idx: int) -> int:
    """source+块序号 → 稳定正整数 id。重灌同文档同块=同 id(upsert 覆盖,不堆积)。"""
    h = hashlib.md5(f"{source}#{idx}".encode()).hexdigest()
    return int(h[:15], 16)   # 取 60 bit,稳妥落在 Qdrant 支持的无符号整数范围


def index_document(path, source: str) -> dict:
    """loaders 取文本 → chunker 切块 → embed 每块 → upsert。返回 {source, chunks}。"""
    from qdrant_client.models import PointStruct

    _ensure_collection()
    doc = loaders.load_document(path, source)
    if not doc.text.strip():
        logger.warning("文档没抽到文本: source=%s", source)
        return {"source": source, "chunks": 0}
    rag = config_store.get()["rag"]
    pieces = chunker.split(doc.text, rag["chunk_size"], rag["overlap"])
    points = [
        PointStruct(id=_point_id(source, i), vector=_embed(piece),
                    payload={"text": piece, "source": source})
        for i, piece in enumerate(pieces)
    ]
    _get_qdrant().upsert(collection_name=COLLECTION, points=points)
    logger.info("灌库完成: source=%s, chunks=%d", source, len(points))
    return {"source": source, "chunks": len(points)}


def _scroll_all() -> list:
    """拉集合里全部点(payload,不要向量)。文档量小,一次 scroll 够。"""
    client = _get_qdrant()
    if not client.collection_exists(COLLECTION):
        return []
    points, _ = client.scroll(collection_name=COLLECTION, limit=10000, with_payload=True, with_vectors=False)
    return points


def list_documents() -> list[dict]:
    """按 source 聚合已灌文档 → [{source, chunks}]。"""
    counts: dict[str, int] = {}
    for pt in _scroll_all():
        src = pt.payload.get("source", "?")
        counts[src] = counts.get(src, 0) + 1
    return [{"source": s, "chunks": n} for s, n in sorted(counts.items())]


def delete_document(source: str) -> int:
    """删掉某 source 的所有块,返回删除数。"""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    ids = [pt.id for pt in _scroll_all() if pt.payload.get("source") == source]
    if ids:
        _get_qdrant().delete(collection_name=COLLECTION, points_selector=ids)
    logger.info("删除文档: source=%s, chunks=%d", source, len(ids))
    return len(ids)


def rebuild() -> dict:
    """显式清空重建:删集合 → 重扫 kb_dir 全部文件重灌。返回汇总。"""
    from pathlib import Path

    client = _get_qdrant()
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    _ensure_collection()
    kb_dir = config_store.get()["security"].get("kb_dir") or ""
    docs = chunks = 0
    if kb_dir:
        root = Path(kb_dir)
        for fp in sorted(root.rglob("*")):
            if fp.is_file():
                r = index_document(fp, source=str(fp.relative_to(root)))
                docs += 1
                chunks += r["chunks"]
    logger.info("重建完成: documents=%d, chunks=%d", docs, chunks)
    return {"documents": docs, "chunks": chunks}


def stats() -> dict:
    docs = list_documents()
    return {"documents": len(docs), "chunks": sum(d["chunks"] for d in docs), "dimension": _dimension()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_rag_store.py -q`
Expected: PASS(全部)

- [ ] **Step 5: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/services/rag_store.py backend/tests/test_rag_store.py
git commit -m "feat(p3): rag_store 灌库+增删查(稳定id/upsert覆盖/按source聚合/重建)"
```

---

### Task 6: rag_store 检索 + rerank(两阶段 + 失败降级)

**Files:**
- Modify: `backend/app/services/rag_store.py`(追加)
- Test: `backend/tests/test_rag_store.py`(追加)

**Interfaces:**
- Consumes: `_embed`、`_get_qdrant`、`config_store`、`_ensure_collection`
- Produces: `search(query, top_k=None) -> list[tuple[str, str, float]]`(text, source, score);`_rerank(query, docs) -> list[int]`(返回按相关性重排后的原索引顺序)

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_rag_store.py`:

```python
class _Hit:
    def __init__(self, text, source, score):
        self.payload = {"text": text, "source": source}
        self.score = score


def test_search_vector_only_when_no_rerank(cfg, monkeypatch):
    config_store.update({"rag": {"rerank_model": ""}})   # 关掉 rerank
    fake = _FakeQdrant()
    class _R:
        points = [_Hit("片段A", "a.md", 0.9), _Hit("片段B", "b.md", 0.8)]
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    monkeypatch.setattr(rag_store, "_ensure_collection", lambda: None)
    monkeypatch.setattr(rag_store, "_embed", lambda t: [0.1] * 1024)
    fake.query_points = lambda collection_name, query, limit: _R()
    out = rag_store.search("q", top_k=2)
    assert out == [("片段A", "a.md", 0.9), ("片段B", "b.md", 0.8)]


def test_search_reranks_and_trims(cfg, monkeypatch):
    fake = _FakeQdrant()
    class _R:
        points = [_Hit("片段A", "a.md", 0.9), _Hit("片段B", "b.md", 0.8), _Hit("片段C", "c.md", 0.7)]
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    monkeypatch.setattr(rag_store, "_ensure_collection", lambda: None)
    monkeypatch.setattr(rag_store, "_embed", lambda t: [0.1] * 1024)
    fake.query_points = lambda collection_name, query, limit: _R()
    # rerank 把顺序翻成 C,A,B
    monkeypatch.setattr(rag_store, "_rerank", lambda query, docs: [2, 0, 1])
    out = rag_store.search("q", top_k=2)
    assert [t[0] for t in out] == ["片段C", "片段A"]   # 精排后取 top_k=2


def test_search_rerank_failure_degrades(cfg, monkeypatch):
    fake = _FakeQdrant()
    class _R:
        points = [_Hit("片段A", "a.md", 0.9), _Hit("片段B", "b.md", 0.8)]
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    monkeypatch.setattr(rag_store, "_ensure_collection", lambda: None)
    monkeypatch.setattr(rag_store, "_embed", lambda t: [0.1] * 1024)
    fake.query_points = lambda collection_name, query, limit: _R()
    def boom(query, docs):
        raise RuntimeError("rerank api down")
    monkeypatch.setattr(rag_store, "_rerank", boom)
    out = rag_store.search("q", top_k=2)   # rerank 挂 → 降级用向量顺序
    assert [t[0] for t in out] == ["片段A", "片段B"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_rag_store.py -k search -q`
Expected: FAIL (AttributeError: search)

- [ ] **Step 3: 实现检索+rerank**

追加到 `rag_store.py`:

```python
import json
import urllib.request


def search(query: str, top_k: int | None = None) -> list[tuple[str, str, float]]:
    """两阶段:向量召回 top_n → rerank 精排 top_k。rerank 失败降级用向量顺序。"""
    _ensure_collection()
    rag = config_store.get()["rag"]
    top_n = rag["top_n"]
    k = top_k or rag["top_k"]
    hits = _get_qdrant().query_points(collection_name=COLLECTION, query=_embed(query), limit=top_n).points
    candidates = [(h.payload.get("text", ""), h.payload.get("source", "?"), h.score) for h in hits]
    if not candidates:
        return []
    rerank_model = rag.get("rerank_model") or ""
    if rerank_model:
        try:
            order = _rerank(query, [c[0] for c in candidates])
            candidates = [candidates[i] for i in order]
        except Exception as e:  # noqa: BLE001  rerank 是优化项,挂了降级不拖垮检索
            logger.warning("rerank 失败,降级用向量顺序: %s", type(e).__name__)
    return candidates[:k]


def _rerank(query: str, docs: list[str]) -> list[int]:
    """调 dashscope rerank(HTTP,不引 dashscope SDK)。返回按相关性重排后的原索引顺序。"""
    emb = config_store.get()["embedding"]
    model = config_store.get()["rag"]["rerank_model"]
    api_key = emb.get("api_key") or ""
    # dashscope rerank 端点(与 embedding 同一 dashscope 账号 key)
    url = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    body = json.dumps({
        "model": model,
        "input": {"query": query, "documents": docs},
        "parameters": {"return_documents": False, "top_n": len(docs)},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    # 返回 output.results[].index 按相关性降序
    results = data["output"]["results"]
    return [r["index"] for r in results]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_rag_store.py -q`
Expected: PASS(全部)

- [ ] **Step 5: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/services/rag_store.py backend/tests/test_rag_store.py
git commit -m "feat(p3): rag_store 两阶段检索(向量召回→dashscope rerank 精排,失败降级)"
```

---

### Task 7: search_kb 工具 + 注册 + 反幻觉 prompt

**Files:**
- Create: `backend/app/agent/tools/rag.py`
- Modify: `backend/app/agent/tools/__init__.py`(追加注册)
- Modify: `backend/app/agent/loop.py:20-26`(SYSTEM_PROMPT)
- Test: `backend/tests/test_tools.py`(追加)

**Interfaces:**
- Consumes: `rag_store.search`、`rag_store.RagStoreError`
- Produces: `rag.SearchKbArgs`(query: str, top_k: int=5);`rag.search_kb(args) -> str`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_tools.py`(文件顶部若无 import 补上 `from app.agent.tools import rag`):

```python
def test_search_kb_formats_with_source(monkeypatch):
    from app.agent.tools import rag
    monkeypatch.setattr(rag.rag_store, "search",
                        lambda query, top_k=5: [("ReAct 是推理+行动", "m3.md", 0.9),
                                                 ("CoT 是思维链", "m2.md", 0.8)])
    out = rag.search_kb(rag.SearchKbArgs(query="什么是ReAct"))
    assert "ReAct 是推理+行动" in out
    assert "[来源: m3.md]" in out
    assert "[来源: m2.md]" in out


def test_search_kb_empty(monkeypatch):
    from app.agent.tools import rag
    monkeypatch.setattr(rag.rag_store, "search", lambda query, top_k=5: [])
    out = rag.search_kb(rag.SearchKbArgs(query="库外问题"))
    assert "没有相关内容" in out


def test_search_kb_registered():
    from app.agent.tools import registry
    schema = registry.to_openai_schema()
    names = {s["function"]["name"] for s in schema}
    assert "search_kb" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_tools.py -k search_kb -q`
Expected: FAIL (ModuleNotFoundError: app.agent.tools.rag)

- [ ] **Step 3: 实现工具 + 注册 + 改 prompt**

创建 `backend/app/agent/tools/rag.py`:

```python
"""
rag.py —— search_kb 工具(Agent 查资料的手)

只做检索:调 rag_store.search 拿 top-k 片段,拼成带来源的文本喂回。
A+G(基于片段生成答案)交主循环 LLM——工具是"手"只取数据,不自己调 LLM。
RagStoreError(连不上/维度不一致)由 ToolRegistry 自愈兜成 tool_result,不崩流。
"""
from pydantic import BaseModel, Field

from app.services import rag_store


class SearchKbArgs(BaseModel):
    query: str = Field(description="要在知识库里检索的问题或关键词")
    top_k: int = Field(default=5, description="返回最相关的前几条片段")


def search_kb(args: SearchKbArgs) -> str:
    results = rag_store.search(args.query, top_k=args.top_k)
    if not results:
        return "知识库里没有相关内容。"
    blocks = [f"【片段{i}】{text}\n[来源: {source}]" for i, (text, source, _score) in enumerate(results, 1)]
    return "\n\n".join(blocks)
```

在 `backend/app/agent/tools/__init__.py` 末尾追加:

```python
from app.agent.tools.rag import SearchKbArgs, search_kb  # noqa: E402

registry.register(
    "search_kb", search_kb, SearchKbArgs,
    "在文档知识库里语义检索,返回最相关的片段和来源。需要引用资料/文档内容回答时用它。",
)
```

改 `backend/app/agent/loop.py` 的 SYSTEM_PROMPT(第 20-26 行)为:

```python
SYSTEM_PROMPT = (
    "你是一个本地编码助手,可以调用工具查看并修改用户工作区里的代码:"
    "grep(按正则搜索)、glob(按通配列文件)、read_file(读文件)、"
    "write_file(写文件)、run_command(跑 shell 命令)、search_kb(检索文档知识库)。"
    "需要看/改代码再作答时就调用工具;能直接回答的问题不必调用。"
    "写文件和跑命令可能需要用户审批,危险命令会被拒绝,你会在结果里看到反馈。"
    "用 search_kb 查资料时:只依据检索到的片段回答;片段里没有的,"
    "明确说「知识库里没有相关内容」,不要编造;回答时带上来源。"
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_tools.py -q`
Expected: PASS(含新增 3 条 + 原有全过)

- [ ] **Step 5: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/agent/tools/rag.py backend/app/agent/tools/__init__.py backend/app/agent/loop.py backend/tests/test_tools.py
git commit -m "feat(p3): search_kb 工具+注册+反幻觉 system prompt"
```

---

### Task 8: kb 路由(上传/列表/删除/重建/状态)

**Files:**
- Create: `backend/app/api/routes/kb.py`
- Modify: `backend/app/api/main.py`(挂路由)
- Test: `backend/tests/test_kb_routes.py`

**Interfaces:**
- Consumes: `rag_store`(index_document/list_documents/delete_document/rebuild/stats + RagStoreError);`config_store`(kb_dir)
- Produces: 路由 `POST /api/kb/upload`、`GET /api/kb/list`、`DELETE /api/kb/{source}`、`POST /api/kb/rebuild`、`GET /api/kb/stats`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_kb_routes.py`:

```python
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import config_store, rag_store
from app.api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    kb = tmp_path / "kb"
    kb.mkdir()
    config_store._reset_cache()
    config_store.update({"security": {"kb_dir": str(kb)}})
    yield TestClient(app)
    config_store._reset_cache()


def test_list(client, monkeypatch):
    monkeypatch.setattr(rag_store, "list_documents", lambda: [{"source": "a.md", "chunks": 3}])
    r = client.get("/api/kb/list")
    assert r.status_code == 200
    assert r.json() == [{"source": "a.md", "chunks": 3}]


def test_stats(client, monkeypatch):
    monkeypatch.setattr(rag_store, "stats", lambda: {"documents": 1, "chunks": 3, "dimension": 1024})
    r = client.get("/api/kb/stats")
    assert r.json()["dimension"] == 1024


def test_upload_indexes(client, monkeypatch):
    captured = {}
    def fake_index(path, source):
        captured["source"] = source
        return {"source": source, "chunks": 2}
    monkeypatch.setattr(rag_store, "index_document", fake_index)
    r = client.post("/api/kb/upload", files={"file": ("note.md", b"hello", "text/markdown")})
    assert r.status_code == 200
    assert r.json()["chunks"] == 2
    assert captured["source"] == "note.md"


def test_delete(client, monkeypatch):
    monkeypatch.setattr(rag_store, "delete_document", lambda source: 3)
    r = client.request("DELETE", "/api/kb/a.md")
    assert r.status_code == 200
    assert r.json()["deleted"] == 3


def test_rebuild(client, monkeypatch):
    monkeypatch.setattr(rag_store, "rebuild", lambda: {"documents": 2, "chunks": 10})
    r = client.post("/api/kb/rebuild")
    assert r.json()["chunks"] == 10


def test_ragstore_error_returns_503(client, monkeypatch):
    def boom():
        raise rag_store.RagStoreError("知识库服务未启动")
    monkeypatch.setattr(rag_store, "stats", boom)
    r = client.get("/api/kb/stats")
    assert r.status_code == 503
    assert "未启动" in r.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_kb_routes.py -q`
Expected: FAIL (404,路由未挂)

- [ ] **Step 3: 实现路由 + 挂载**

创建 `backend/app/api/routes/kb.py`:

```python
"""
kb.py —— 知识库管理路由(上传/列表/删除/重建/状态)

上传:存文件到 kb_dir → rag_store.index_document。
RagStoreError(连不上/维度不一致)→ 503 + 明确 message,前端提示用户。
"""
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from app.services import config_store, rag_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kb", tags=["kb"])


def _kb_dir() -> Path:
    kb = config_store.get()["security"].get("kb_dir") or ""
    if not kb:
        raise HTTPException(status_code=400, detail="未配置知识库目录(kb_dir),请到设置页填写")
    p = Path(kb)
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.post("/upload")
async def upload(file: UploadFile):
    root = _kb_dir()
    source = file.filename or "unnamed"
    dest = root / source
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    try:
        return rag_store.index_document(dest, source=source)
    except rag_store.RagStoreError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/list")
def kb_list():
    try:
        return rag_store.list_documents()
    except rag_store.RagStoreError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.delete("/{source:path}")
def kb_delete(source: str):
    try:
        n = rag_store.delete_document(source)
    except rag_store.RagStoreError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    # 删磁盘文件(存在才删)
    fp = _kb_dir() / source
    if fp.is_file():
        fp.unlink()
    return {"deleted": n}


@router.post("/rebuild")
def kb_rebuild():
    try:
        return rag_store.rebuild()
    except rag_store.RagStoreError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/stats")
def kb_stats():
    try:
        return rag_store.stats()
    except rag_store.RagStoreError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
```

在 `backend/app/api/main.py` 加 import 和挂载:

```python
from app.api.routes import kb as kb_routes
```
```python
app.include_router(kb_routes.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest tests/test_kb_routes.py -q`
Expected: PASS(6 个用例)

- [ ] **Step 5: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/app/api/routes/kb.py backend/app/api/main.py backend/tests/test_kb_routes.py
git commit -m "feat(p3): kb 路由(上传/列表/删除/重建/状态,RagStoreError→503)"
```

---

### Task 9: 加依赖 + 全量回归 + 冒烟脚本

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/scripts/smoke_rag.py`

**Interfaces:**
- Consumes: 全部 rag_store 接口
- Produces: 手动冒烟脚本(不进 pytest)

- [ ] **Step 1: 加依赖**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/backend
uv add qdrant-client pypdf
```
Expected: pyproject dependencies 出现 `qdrant-client`、`pypdf`,uv.lock 更新。

- [ ] **Step 2: 全量回归(确认加依赖没破坏、真 pypdf import 不炸)**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/backend && uv run pytest -q`
Expected: PASS(全部,含之前所有里程碑用例)

- [ ] **Step 3: 写冒烟脚本**

创建 `backend/scripts/smoke_rag.py`:

```python
"""
手动冒烟(不进 pytest):真起 Qdrant + 真 embedding key,验证端到端灌库→检索。
前置:docker start qdrant;设置页/config.json 配好 embedding 的 api_key;
     security.kb_dir 指向一个放了文档的目录。
运行:cd backend && uv run python scripts/smoke_rag.py
"""
from pathlib import Path

from app.services import rag_store

if __name__ == "__main__":
    print("stats(初始):", rag_store.stats())
    sample = Path("scripts/_sample.md")
    sample.write_text("ReAct 是推理与行动结合的 Agent 范式。CoT 是思维链。", encoding="utf-8")
    print("灌库:", rag_store.index_document(sample, source="_sample.md"))
    print("列表:", rag_store.list_documents())
    print("检索『什么是ReAct』:")
    for text, source, score in rag_store.search("什么是ReAct"):
        print(f"  [{score:.3f}] {text}  [来源: {source}]")
    print("检索『库里没有的东西·股票代码』:", rag_store.search("腾讯股票代码是多少"))
    print("删除:", rag_store.delete_document("_sample.md"))
    sample.unlink(missing_ok=True)
```

- [ ] **Step 4: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add backend/pyproject.toml backend/uv.lock backend/scripts/smoke_rag.py
git commit -m "feat(p3): 加 qdrant-client/pypdf 依赖 + RAG 冒烟脚本"
```

---

### Task 10: 前端渐进引入 shadcn(Tailwind + 初始化)

**Files:**
- Modify: `frontend/package.json`、`frontend/vite.config.ts`、`frontend/tsconfig.json`、`frontend/tsconfig.app.json`
- Create: `frontend/tailwind.config.js`、`frontend/postcss.config.js`、`frontend/components.json`、`frontend/src/lib/utils.ts`
- Modify: `frontend/src/index.css`(加 Tailwind 指令 + shadcn CSS 变量)

**Interfaces:**
- Produces: 可用的 Tailwind + shadcn 环境;`@/` 别名指向 `src/`;`cn()` 工具函数

- [ ] **Step 1: 装 Tailwind + shadcn 依赖**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/frontend
npm install -D tailwindcss@3 postcss autoprefixer
npm install class-variance-authority clsx tailwind-merge lucide-react
npx tailwindcss init -p
```
Expected: 生成 `tailwind.config.js` + `postcss.config.js`,package.json 出现上述依赖。

- [ ] **Step 2: 配 Tailwind content + 路径别名**

改 `frontend/tailwind.config.js`:

```js
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
}
```

在 `frontend/src/index.css` **顶部**加(保留原有内容在下方):

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

配 `@/` 别名——`frontend/vite.config.ts` 加 resolve.alias:

```ts
import path from "path"
// defineConfig 内加:
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
```

`frontend/tsconfig.app.json` 的 compilerOptions 加:

```json
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
```

创建 `frontend/src/lib/utils.ts`:

```ts
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

创建 `frontend/components.json`(shadcn 配置):

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "default",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "tailwind.config.js",
    "css": "src/index.css",
    "baseColor": "slate",
    "cssVariables": true
  },
  "aliases": { "components": "@/components", "utils": "@/lib/utils" }
}
```

- [ ] **Step 3: 装几个要用的 shadcn 组件**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar/frontend
npx shadcn@latest add button card progress
```
Expected: `src/components/ui/{button,card,progress}.tsx` 生成。若交互式询问,选默认。

- [ ] **Step 4: 构建验证(类型 + 编译不破)**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/frontend && npm run build`
Expected: 构建成功(tsc -b + vite build 均过);老页面样式不受影响(Tailwind base 可能微调默认样式,但 App.css 显式规则仍生效)。

- [ ] **Step 5: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add frontend/package.json frontend/package-lock.json frontend/tailwind.config.js frontend/postcss.config.js frontend/components.json frontend/vite.config.ts frontend/tsconfig.app.json frontend/src/index.css frontend/src/lib/utils.ts frontend/src/components/ui
git commit -m "feat(p3): 前端渐进引入 shadcn(Tailwind+别名+button/card/progress)"
```

---

### Task 11: 前端 kb API 封装

**Files:**
- Modify: `frontend/src/lib/api.ts`(追加)

**Interfaces:**
- Produces: `KbDoc`(`{source, chunks}`)、`KbStats`(`{documents, chunks, dimension}`);`uploadKb(file, onProgress?)`、`listKb()`、`deleteKb(source)`、`rebuildKb()`、`kbStats()`

- [ ] **Step 1: 追加 kb API**

在 `frontend/src/lib/api.ts` 末尾追加:

```ts
// ---- 知识库(P3) ----
export type KbDoc = { source: string; chunks: number }
export type KbStats = { documents: number; chunks: number; dimension: number }

export async function uploadKb(file: File): Promise<KbDoc> {
  const form = new FormData()
  form.append('file', file)
  const r = await fetch('/api/kb/upload', { method: 'POST', body: form })
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}))
    throw new Error(detail.detail || '上传失败')
  }
  return r.json()
}

export async function listKb(): Promise<KbDoc[]> {
  const r = await fetch('/api/kb/list')
  if (!r.ok) throw new Error('拉取知识库列表失败')
  return r.json()
}

export async function deleteKb(source: string): Promise<void> {
  const r = await fetch(`/api/kb/${encodeURIComponent(source)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error('删除失败')
}

export async function rebuildKb(): Promise<{ documents: number; chunks: number }> {
  const r = await fetch('/api/kb/rebuild', { method: 'POST' })
  if (!r.ok) throw new Error('重建失败')
  return r.json()
}

export async function kbStats(): Promise<KbStats> {
  const r = await fetch('/api/kb/stats')
  if (!r.ok) throw new Error('拉取状态失败')
  return r.json()
}
```

- [ ] **Step 2: 构建验证**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/frontend && npm run build`
Expected: 构建成功(纯新增导出,不影响现有)

- [ ] **Step 3: Commit**

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add frontend/src/lib/api.ts
git commit -m "feat(p3): 前端 kb API 封装(上传/列表/删除/重建/状态)"
```

---

### Task 12: KbManager 组件 + 入口挂载(含假进度条)

**Files:**
- Create: `frontend/src/components/KbManager.tsx`
- Modify: `frontend/src/App.tsx`(加视图切换 + 知识库入口)
- Modify: `frontend/src/components/SessionList.tsx`(底部加「📚 知识库」按钮)

**Interfaces:**
- Consumes: `uploadKb/listKb/deleteKb/rebuildKb/kbStats`、shadcn `Button/Card/Progress`
- Produces: `KbManager` 组件(default export)

- [ ] **Step 1: 写 KbManager 组件**

创建 `frontend/src/components/KbManager.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'

import { deleteKb, kbStats, listKb, rebuildKb, uploadKb, type KbDoc, type KbStats } from '../lib/api'

export default function KbManager() {
  const [docs, setDocs] = useState<KbDoc[]>([])
  const [stats, setStats] = useState<KbStats | null>(null)
  const [progress, setProgress] = useState(0) // 假进度条:0=空闲
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = async () => {
    try {
      setDocs(await listKb())
      setStats(await kbStats())
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  useEffect(() => { void refresh() }, [])

  // 假进度条:匀速爬到 90%,请求回来跳 100% 再归零
  const startFakeProgress = () => {
    setProgress(8)
    timer.current = setInterval(() => {
      setProgress((p) => (p < 90 ? p + Math.max(1, (90 - p) * 0.15) : p))
    }, 200)
  }
  const stopFakeProgress = () => {
    if (timer.current) clearInterval(timer.current)
    timer.current = null
    setProgress(100)
    setTimeout(() => setProgress(0), 400)
  }

  const onUpload = async (file: File) => {
    setError('')
    startFakeProgress()
    try {
      await uploadKb(file)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      stopFakeProgress()
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const onRebuild = async () => {
    setError('')
    startFakeProgress()
    try {
      await rebuildKb()
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      stopFakeProgress()
    }
  }

  const busy = progress > 0

  return (
    <div className="kb-manager">
      <h2>📚 知识库</h2>

      <div className="kb-upload">
        <input
          ref={fileRef}
          type="file"
          accept=".md,.txt,.pdf,.py,.js,.ts,.tsx,.json,.yaml,.yml"
          disabled={busy}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void onUpload(f) }}
        />
        {busy && (
          <div className="kb-progress"><div className="kb-progress-bar" style={{ width: `${progress}%` }} /></div>
        )}
      </div>

      {error && <div className="kb-error">⚠️ {error}</div>}

      <div className="kb-list">
        {docs.length === 0 && <div className="kb-empty">还没有文档,上传一篇试试。</div>}
        {docs.map((d) => (
          <div key={d.source} className="kb-item">
            <span className="kb-source">{d.source}</span>
            <span className="kb-chunks">{d.chunks} 块</span>
            <button disabled={busy} onClick={async () => { await deleteKb(d.source); void refresh() }}>删除</button>
          </div>
        ))}
      </div>

      <div className="kb-footer">
        <button disabled={busy} onClick={onRebuild}>重建索引</button>
        {stats && <span className="kb-stats">{stats.documents} 篇 / {stats.chunks} 块 / {stats.dimension} 维</span>}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: App.tsx 加视图切换**

改 `frontend/src/App.tsx`——顶部加 import + view 状态,SessionList 传 onOpenKb,右侧按 view 渲染 KbManager 或聊天区:

```tsx
import { useState } from 'react'

import KbManager from './components/KbManager'
import SessionList from './components/SessionList'
import ToolCallCard from './components/ToolCallCard'
import { useChatStream } from './hooks/useChatStream'
import './App.css'

export default function App() {
  const {
    messages, sessions, currentId, streaming, hasPending,
    send, approve, newSession, switchSession, removeSession, rename,
  } = useChatStream()
  const [input, setInput] = useState('')
  const [view, setView] = useState<'chat' | 'kb'>('chat')
  const locked = streaming || hasPending

  const onSend = () => {
    const text = input.trim()
    if (!text || locked) return
    setInput('')
    void send(text)
  }

  return (
    <div className="layout">
      <SessionList
        sessions={sessions}
        currentId={currentId}
        onNew={() => { setView('chat'); newSession() }}
        onSwitch={(id) => { setView('chat'); switchSession(id) }}
        onDelete={removeSession}
        onRename={rename}
        onOpenKb={() => setView('kb')}
      />
      <div className="app">
        {view === 'kb' ? (
          <KbManager />
        ) : (
          <>
            <h1>Superstar</h1>
            <div className="messages">
              {messages.map((it, i) =>
                it.kind === 'tool' ? (
                  <ToolCallCard key={i} name={it.name} args={it.args} result={it.result}
                    approval={it.approval} onDecision={(d) => approve(it.id, d)} />
                ) : (
                  <div key={i} className={`msg ${it.role}`}>
                    <b>{it.role === 'user' ? '你' : 'AI'}:</b> {it.content}
                    {streaming && i === messages.length - 1 && it.role === 'assistant' ? ' ▋' : ''}
                  </div>
                ),
              )}
            </div>
            <div className="composer">
              <input value={input} onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && onSend()}
                disabled={locked}
                placeholder={hasPending ? '请先处理待批准的操作…' : '说点什么…'} />
              <button onClick={onSend} disabled={locked}>发送</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: SessionList 加知识库入口**

改 `frontend/src/components/SessionList.tsx`:props 类型加 `onOpenKb: () => void`;在新建按钮附近(或列表底部)加一个按钮:

```tsx
      <button className="new-btn" onClick={onOpenKb}>📚 知识库</button>
```
(在组件 props 解构里加 `onOpenKb`,类型定义里加 `onOpenKb: () => void`)

- [ ] **Step 4: 加 KbManager 样式**

在 `frontend/src/App.css` 末尾追加:

```css
/* P3 知识库管理页 */
.kb-manager { padding: 8px; }
.kb-upload { margin: 12px 0; }
.kb-progress { height: 6px; background: #eee; border-radius: 3px; margin-top: 8px; overflow: hidden; }
.kb-progress-bar { height: 100%; background: #0b6; transition: width .2s; }
.kb-error { color: #cf222e; margin: 8px 0; }
.kb-list { display: flex; flex-direction: column; gap: 6px; margin: 12px 0; }
.kb-empty { color: #888; font-size: 14px; }
.kb-item { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border: 1px solid #eee; border-radius: 6px; }
.kb-source { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-chunks { color: #57606a; font-size: 13px; }
.kb-footer { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
.kb-stats { color: #57606a; font-size: 13px; }
```

- [ ] **Step 5: 构建验证 + Commit**

Run: `cd /Users/shuangxingyang/Desktop/myspace/superstar/frontend && npm run build`
Expected: 构建成功

```bash
cd /Users/shuangxingyang/Desktop/myspace/superstar
git add frontend/src/components/KbManager.tsx frontend/src/App.tsx frontend/src/components/SessionList.tsx frontend/src/App.css
git commit -m "feat(p3): 知识库管理页(上传/列表/删除/重建+假进度条)+ 侧边栏入口"
```

---

## 完成后

全部 12 task 完成后:
- 后端 `cd backend && uv run pytest -q` 全绿
- 前端 `cd frontend && npm run build` 全绿
- 手动冒烟:`docker start qdrant` → 配 embedding key → `uv run python scripts/smoke_rag.py` 看灌库+检索+反幻觉
- 浏览器端到端:知识库页拖文档 → 对话问库内(带来源)/库外(说没有)

**验证使用故事**(spec 第七节)逐条过一遍。
