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


async def _text_stream():
    for c in ["你", "好"]:
        yield _Chunk(_Delta(content=c))


class _TextCompletions:
    async def create(self, model, messages, tools=None, stream=True, **kwargs):
        return _text_stream()


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


async def _tool_stream():
    yield _Chunk(_Delta(tool_calls=[_TC(0, id="c1", name="glob", arguments='{"pattern": "*.py"}')]))


async def _answer_stream():
    yield _Chunk(_Delta(content="有一个文件"))


class _ToolCompletions:
    def __init__(self):
        self.calls = 0

    async def create(self, model, messages, tools=None, stream=True, **kwargs):
        self.calls += 1
        return _tool_stream() if self.calls == 1 else _answer_stream()


class _ToolClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _ToolCompletions()})()


def test_chat_with_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "x.py").write_text("", encoding="utf-8")
    config_store.update({"security": {"default_cwd": str(proj), "allowed_dirs": []}})
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


# ============ P2b: /resume + 残留 pending 防护 ============
from app.agent import pending


async def _cmd_stream():
    yield _Chunk(_Delta(tool_calls=[_TC(0, id="w1", name="run_command",
        arguments='{"command": "python demo.py"}')]))


async def _done_stream():
    yield _Chunk(_Delta(content="跑好了"))


class _CmdThenAnswerC:
    """第 1 次要跑灰名单命令(触发审批),之后给终答。"""
    def __init__(self):
        self.calls = 0

    async def create(self, model, messages, tools=None, stream=True, **kwargs):
        self.calls += 1
        return _cmd_stream() if self.calls == 1 else _done_stream()


class _CmdClientC:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _CmdThenAnswerC()})()


@pytest.fixture
def client_ws(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    config_store.update({"security": {"default_cwd": str(proj), "allowed_dirs": []}})
    client_obj = _CmdClientC()                                  # 共享实例:calls 跨 stream+resume 累计
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client_obj, "fake"))
    from app.api.main import app
    return TestClient(app), proj


def test_resume_endpoint_executes(client_ws):
    c, proj = client_ws
    r1 = c.post("/api/chat/stream", json={"message": "跑个命令"})
    ev1 = _events(r1.text)
    ar = next(e for e in ev1 if e["type"] == "approval_required")
    sid = next(e for e in ev1 if e["type"] == "session")["session_id"]
    assert pending.read(sid) is not None
    # 批准 → /resume 续跑到 done,sidecar 清空
    r2 = c.post("/api/chat/resume",
                json={"session_id": sid, "tool_call_id": ar["id"], "decision": "approve"})
    ev2 = _events(r2.text)
    assert any(e["type"] == "done" for e in ev2)
    assert pending.read(sid) is None


def test_stream_with_residual_pending_auto_rejects(client_ws):
    c, _ = client_ws
    r1 = c.post("/api/chat/stream", json={"message": "写文件"})
    sid = next(e for e in _events(r1.text) if e["type"] == "session")["session_id"]
    assert pending.read(sid) is not None
    # 不点审批,直接发新消息 → 残留 pending 被自动拒绝,新消息照常处理
    r2 = c.post("/api/chat/stream", json={"session_id": sid, "message": "算了"})
    assert _events(r2.text)[-1]["type"] == "done"
    assert pending.read(sid) is None
    msgs = session_store.read_messages(sid)
    assert any(m["role"] == "tool" and "拒绝" in (m.get("content") or "") for m in msgs)
