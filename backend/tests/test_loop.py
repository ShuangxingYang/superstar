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
    config_store.update({"security": {"default_cwd": str(proj), "allowed_dirs": []}})
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


# ============ P2b: 审批回合边界 + resume ============
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
    config_store.update({"security": {"default_cwd": str(proj), "allowed_dirs": []}})
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
