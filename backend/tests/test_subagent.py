"""run_subagent:非流式假 LLM,断言隔离循环的正常闭环 / 能写 / 越权拦截 / 递归防护 / 超限 / 异常 / 沙箱。"""
import pytest

from app.agent import subagent
from app.config import settings
from app.services import config_store, llm


# --- 非流式假对象(模仿 OpenAI SDK 的 resp.choices[0].message)---
class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _TC:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none=False):
        d = {"role": "assistant", "content": self.content, "tool_calls": self.tool_calls}
        return {k: v for k, v in d.items() if v is not None} if exclude_none else d


class _Resp:
    def __init__(self, msg):
        self.choices = [type("C", (), {"message": msg})()]


class _ScriptCompletions:
    """按脚本逐轮返回;记录每轮收到的 messages,供断言喂回内容。"""
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen = []

    def create(self, model, messages, tools, **kwargs):
        self.seen.append(list(messages))
        resp = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return resp


class _ScriptClient:
    def __init__(self, script):
        self.comp = _ScriptCompletions(script)
        self.chat = type("Chat", (), {"completions": self.comp})()


class _RaisingClient:
    class _C:
        def create(self, *a, **k):
            raise RuntimeError("boom")
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _RaisingClient._C()})()


@pytest.fixture
def sub_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
    config_store.update({"security": {"default_cwd": str(proj), "allowed_dirs": []}})
    return proj


def _install(monkeypatch, script):
    client = _ScriptClient(script)
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    return client


def test_subagent_normal_loop(sub_ready, monkeypatch):
    # 第一轮 grep,第二轮给结论
    _install(monkeypatch, [
        _Resp(_Msg(tool_calls=[_TC("c1", "grep", '{"pattern": "def"}')])),
        _Resp(_Msg(content="找到了 a.py:1 的 def foo")),
    ])
    out = subagent.run_subagent("在项目里找 def")
    assert out == "找到了 a.py:1 的 def foo"


def test_subagent_can_write(sub_ready, monkeypatch):
    _install(monkeypatch, [
        _Resp(_Msg(tool_calls=[_TC("w1", "write_file", '{"path": "out.txt", "content": "hi"}')])),
        _Resp(_Msg(content="已写好 out.txt")),
    ])
    out = subagent.run_subagent("写个 out.txt")
    assert "已写好" in out
    assert (sub_ready / "out.txt").read_text(encoding="utf-8") == "hi"   # 真写了(auto,无暂停)


def test_subagent_rejects_forbidden_tool(sub_ready, monkeypatch):
    # 幻觉调 run_command(不在白名单)→ 拒绝串喂回 → 第二轮给结论
    client = _install(monkeypatch, [
        _Resp(_Msg(tool_calls=[_TC("c1", "run_command", '{"command": "ls"}')])),
        _Resp(_Msg(content="好的,不跑命令")),
    ])
    out = subagent.run_subagent("试图跑命令")
    assert "好的" in out
    # 第二轮 create 收到的 messages 里应有 role:tool 的拒绝串
    fed_back = [m for m in client.comp.seen[1] if m.get("role") == "tool"]
    assert any("只能用工具" in m["content"] for m in fed_back)


def test_subagent_no_recursion_in_whitelist():
    # 递归防护:dispatch_subagent 不在白名单,子集 schema 里也没有它
    assert "dispatch_subagent" not in subagent.SUBAGENT_TOOLS


def test_subagent_max_iters(sub_ready, monkeypatch):
    config_store.update({"agent": {"max_iters": 2}})
    _install(monkeypatch, [
        _Resp(_Msg(tool_calls=[_TC("c", "grep", '{"pattern": "x"}')])),   # 永远只调工具
    ])
    out = subagent.run_subagent("死循环任务")
    assert "最大步数" in out


def test_subagent_llm_exception_caught(sub_ready, monkeypatch):
    monkeypatch.setattr(llm, "get_llm_client", lambda: (_RaisingClient(), "fake"))
    out = subagent.run_subagent("会炸的任务")
    assert out.startswith("(子 Agent 执行失败")   # 收敛成字符串,没抛出


def test_subagent_sandbox_blocks_escape(sub_ready, monkeypatch):
    # 子 Agent 不过 gate,但越界写被 write_file 内部 safe_path 拦成"安全拦截"串
    client = _install(monkeypatch, [
        _Resp(_Msg(tool_calls=[_TC("w1", "write_file",
            '{"path": "../../etc/evil", "content": "x"}')])),
        _Resp(_Msg(content="写不了,越界了")),
    ])
    out = subagent.run_subagent("试图越界写")
    assert "写不了" in out
    fed_back = [m for m in client.comp.seen[1] if m.get("role") == "tool"]
    assert any("安全拦截" in m["content"] for m in fed_back)
