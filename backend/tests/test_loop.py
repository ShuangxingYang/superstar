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


# ============ 悬空 tool_call 清理(修复「会话被毒死」的 400)============
def _tc_msg(id_, name="grep", args="{}"):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": id_, "type": "function",
                            "function": {"name": name, "arguments": args}}]}


def test_prune_drops_dangling_tool_call():
    # assistant 发起了 tool_call 但后面没有 tool 结果 → 整条丢弃(content 也为空)
    msgs = [{"role": "user", "content": "hi"}, _tc_msg("a"), {"role": "user", "content": "再来"}]
    out = loop._prune_dangling_tool_calls(msgs)
    assert out == [{"role": "user", "content": "hi"}, {"role": "user", "content": "再来"}]


def test_prune_keeps_valid_pair_and_drops_orphan():
    msgs = [
        {"role": "user", "content": "hi"},
        _tc_msg("a"),
        {"role": "tool", "tool_call_id": "a", "content": "结果"},        # 有效配对 → 留
        {"role": "tool", "tool_call_id": "zzz", "content": "孤儿"},       # 无归属 → 丢
    ]
    out = loop._prune_dangling_tool_calls(msgs)
    assert out == [
        {"role": "user", "content": "hi"},
        _tc_msg("a"),
        {"role": "tool", "tool_call_id": "a", "content": "结果"},
    ]


def test_prune_keeps_content_when_tool_calls_dangling():
    # assistant 既有正文又有悬空 tool_calls → 保留正文,剥掉悬空调用
    msgs = [{"role": "assistant", "content": "我想想", "tool_calls":
             [{"id": "a", "type": "function", "function": {"name": "grep", "arguments": "{}"}}]}]
    out = loop._prune_dangling_tool_calls(msgs)
    assert out == [{"role": "assistant", "content": "我想想"}]
