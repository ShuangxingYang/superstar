"""chat 接 session:懒创建、session 事件、多轮落盘。mock 掉 LLM 流。"""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import llm, session_store


class _Delta:
    def __init__(self, c): self.content = c
class _Choice:
    def __init__(self, c): self.delta = _Delta(c)
class _Chunk:
    def __init__(self, c): self.choices = [_Choice(c)]
class _Completions:
    def create(self, model, messages, stream):
        for c in ["你", "好"]:
            yield _Chunk(c)
class _Chat:
    completions = _Completions()
class _Client:
    chat = _Chat()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(llm, "get_llm_client", lambda: (_Client(), "fake-model"))
    from app.api.main import app
    return TestClient(app)


def _events(resp_text):
    out = []
    for part in resp_text.split("\n\n"):
        part = part.strip()
        if part.startswith("data:"):
            out.append(json.loads(part[len("data:"):].strip()))
    return out


def test_lazy_create_and_persist(client):
    # 首句不带 session_id → 懒创建
    r = client.post("/api/chat/stream", json={"message": "我叫小明"})
    evs = _events(r.text)
    assert evs[0]["type"] == "session"                       # 首个事件回传 session
    sid = evs[0]["session_id"]
    assert evs[0]["title"].startswith("我叫小明")            # 标题即时生成
    assert any(e["type"] == "text" for e in evs)
    assert evs[-1]["type"] == "done"

    # user + assistant 都落盘了
    msgs = session_store.read_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "你好"                       # 累积的全文

    # 第二句带 sid → 续写同一会话(历史含第一轮)
    r2 = client.post("/api/chat/stream", json={"session_id": sid, "message": "我叫啥"})
    assert _events(r2.text)[0]["session_id"] == sid
    assert [m["role"] for m in session_store.read_messages(sid)] == [
        "user", "assistant", "user", "assistant",
    ]
