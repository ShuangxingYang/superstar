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
    def __init__(self, content=None, tool_calls=None, reasoning_content=None):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content   # 推理模型的思考字段(网关/DeepSeek 系)


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


# ============ P5: 推理模型思考过程(reasoning_content)============
def _reasoning_then_answer_stream():
    # 推理模型的典型形状:思考先到(content=null),正文随后
    yield _Chunk(_Delta(reasoning_content="想想:"))
    yield _Chunk(_Delta(reasoning_content="连续奇数"))
    yield _Chunk(_Delta(content="答案是"))
    yield _Chunk(_Delta(content="31,33,35"))


class _EffortCompletions:
    """记录 create 收到的 kwargs,好断言 reasoning_effort 有没有透传。"""
    def __init__(self):
        self.kwargs = None

    def create(self, model, messages, tools, stream, **kwargs):
        self.kwargs = kwargs
        return _reasoning_then_answer_stream()


class _EffortClient:
    def __init__(self):
        self.comp = _EffortCompletions()
        self.chat = type("Chat", (), {"completions": self.comp})()


def test_reasoning_content_yields_events_and_persisted(ready, monkeypatch):
    client = _EffortClient()
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    config_store.update({"llm": {"reasoning_effort": "high"}})

    events = list(loop.run_agent_streaming(ready))
    assert [e["type"] for e in events] == ["reasoning", "reasoning", "text", "text", "done"]
    reasoning = "".join(e["content"] for e in events if e["type"] == "reasoning")
    assert reasoning == "想想:连续奇数"

    # 配了 high → 透传给 create
    assert client.comp.kwargs.get("reasoning_effort") == "high"

    # 思考落盘:终答的 assistant 消息里存了 reasoning(供刷新回放),content 是正文
    msgs = session_store.read_messages(ready)
    assert msgs[-1]["content"] == "答案是31,33,35"
    assert msgs[-1]["reasoning"] == "想想:连续奇数"


def test_reasoning_stripped_before_sending_to_model(ready, monkeypatch):
    # 落盘的 reasoning 不能喂回模型(省 token、不污染上下文):
    # 造一条带 reasoning 的历史 assistant,再跑一轮,断言发给 create 的 messages 里没有 reasoning。
    session_store.append_message(ready, {
        "role": "assistant", "content": "上一轮答案", "reasoning": "上一轮的思考,不该喂回"})
    session_store.append_message(ready, {"role": "user", "content": "再来一个"})

    client = _EffortClient()
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    list(loop.run_agent_streaming(ready))

    # 直接查过滤函数:读回历史(含刚落盘的带 reasoning 消息)→ 过滤 → 应无 reasoning
    stripped = loop._strip_reasoning(session_store.read_messages(ready))
    assert all("reasoning" not in m for m in stripped)
    # 且 content 完整保留(过滤只摘 reasoning,不动别的)
    assert any(m.get("content") == "上一轮答案" for m in stripped)


def test_reasoning_effort_not_sent_when_unset(ready, monkeypatch):
    # 默认配置 reasoning_effort 为空 → 不传该参数(非推理模型不认,传了会 400)
    client = _EffortClient()
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    list(loop.run_agent_streaming(ready))
    assert "reasoning_effort" not in client.comp.kwargs


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


def _cmd_tool_stream():
    # 灰名单命令(python demo.py)→ gate 判 approve,触发审批暂停
    yield _Chunk(_Delta(tool_calls=[_TC(0, id="w1", name="run_command",
        arguments='{"command": "python demo.py"}')]))


class _CmdThenAnswer:
    """第 1 次 create 要跑灰名单命令(触发审批),之后(resume 续跑)给终答。"""
    def __init__(self):
        self.calls = 0

    def create(self, model, messages, tools, stream):
        self.calls += 1
        return _cmd_tool_stream() if self.calls == 1 else _answer_stream()


class _CmdClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _CmdThenAnswer()})()


@pytest.fixture
def p2b_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    config_store.update({"security": {"default_cwd": str(proj), "allowed_dirs": []}})
    client = _CmdClient()                                   # 共享实例:calls 跨 run+resume 累计
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    sid = session_store.create()
    session_store.append_message(sid, {"role": "user", "content": "跑个命令"})
    return sid, proj


def test_command_pauses_for_approval(p2b_ready):
    """灰名单命令触发审批暂停(write_file 改 auto 后改用 run_command 作为触发样例)"""
    sid, _ = p2b_ready
    events = list(loop.run_agent_streaming(sid))
    ar = next(e for e in events if e["type"] == "approval_required")
    assert ar["name"] == "run_command" and ar["preview"]["kind"] == "command"
    # 落盘:assistant(tool_calls) 有,但还没有任何 tool 结果(有意悬空)
    msgs = session_store.read_messages(sid)
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["tool_calls"]
    assert not any(m["role"] == "tool" for m in msgs)
    # pending sidecar 已写
    assert pending.read(sid) is not None


def test_resume_approve_executes_and_continues(p2b_ready):
    sid, proj = p2b_ready
    events = list(loop.run_agent_streaming(sid))            # 先跑到审批暂停
    ar = next(e for e in events if e["type"] == "approval_required")

    ev2 = list(loop.resume_streaming(sid, ar["id"], "approve"))
    assert any(e["type"] == "done" for e in ev2)            # 续跑拿到终答
    msgs = session_store.read_messages(sid)
    # approve 后:有对应 role:tool 结果(tool_call_id 匹配)
    assert any(m["role"] == "tool" and m["tool_call_id"] == ar["id"] for m in msgs)
    assert pending.read(sid) is None                        # sidecar 已清


def test_resume_reject_records_and_continues(p2b_ready):
    sid, proj = p2b_ready
    events = list(loop.run_agent_streaming(sid))
    ar = next(e for e in events if e["type"] == "approval_required")

    ev2 = list(loop.resume_streaming(sid, ar["id"], "reject"))
    assert any(e["type"] == "done" for e in ev2)
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


# ============ P5: 记忆注入 system prompt ============
class _CaptureCompletions:
    """记录 create 收到的 messages,好断言记忆有没有拼进 system。"""
    def __init__(self):
        self.messages = None

    def create(self, model, messages, tools, stream, **kwargs):
        self.messages = messages
        return _answer_stream()          # 直接给终答,不调工具


class _CaptureClient:
    def __init__(self):
        self.comp = _CaptureCompletions()
        self.chat = type("Chat", (), {"completions": self.comp})()


def test_memory_injected_into_system(ready, monkeypatch):
    from app.services import memory
    memory.write_profile("用户叫小明,常用 superstar")
    memory.write_soul("回答要简短")
    client = _CaptureClient()
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))

    list(loop.run_agent_streaming(ready))
    system = client.comp.messages[0]
    assert system["role"] == "system"
    assert "用户叫小明,常用 superstar" in system["content"]
    assert "回答要简短" in system["content"]
    assert loop.SYSTEM_PROMPT in system["content"]        # 基础 prompt 仍在


def test_system_unchanged_when_memory_empty(ready, monkeypatch):
    # profile 不存在、soul 置空 → system 内容等于基础 SYSTEM_PROMPT(前缀稳定性回归)
    from app.services import memory
    memory.write_soul("")
    client = _CaptureClient()
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))

    list(loop.run_agent_streaming(ready))
    assert client.comp.messages[0]["content"] == loop.SYSTEM_PROMPT


# ============ P5+: SYSTEM_PROMPT 瘦身回归 ============
def test_system_prompt_no_tool_list_recital():
    # 瘦身后不应再逐个复述工具是什么(tool description 已负责 what)
    assert "grep(按正则搜索)" not in loop.SYSTEM_PROMPT
    assert "glob(按通配列文件)" not in loop.SYSTEM_PROMPT


def test_system_prompt_keeps_policy():
    # 策略/边界必须保留(tool schema 传达不了的)
    assert "允许目录" in loop.SYSTEM_PROMPT           # 沙箱边界
    assert "来源" in loop.SYSTEM_PROMPT                # search_kb 反幻觉


def test_system_prompt_keeps_memory_routing():
    # 三种记忆的路由要在(哪种事记哪),但不逐个复述工具机制
    assert "update_profile" in loop.SYSTEM_PROMPT
    assert "append_log" in loop.SYSTEM_PROMPT


# ============ P5++: 四工具记忆路由 + profile 收紧 ============
def test_system_prompt_has_update_memory():
    assert "update_memory" in loop.SYSTEM_PROMPT


def test_system_prompt_profile_tightened():
    # 收紧后 SYSTEM_PROMPT 应体现"个人信息"这一 profile 边界措辞
    assert "个人信息" in loop.SYSTEM_PROMPT
