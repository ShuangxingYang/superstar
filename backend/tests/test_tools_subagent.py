"""dispatch_subagent 工具外壳:透传 task 给 run_subagent、已注册、经 gate 自动放行。"""
from app.agent import subagent
from app.agent.gate import gate_tool_call
from app.agent.tools import registry


def test_dispatch_subagent_registered():
    names = {s["function"]["name"] for s in registry.to_openai_schema()}
    assert "dispatch_subagent" in names


def test_dispatch_subagent_not_in_subagent_tools():
    # 递归防护:子 Agent 白名单不含 dispatch_subagent,其子集 schema 里也没有
    assert "dispatch_subagent" not in subagent.SUBAGENT_TOOLS
    sub_names = {s["function"]["name"] for s in registry.to_openai_schema(subagent.SUBAGENT_TOOLS)}
    assert "dispatch_subagent" not in sub_names


def test_dispatch_subagent_passes_task(monkeypatch):
    # 外壳把 task 透传给 run_subagent,并把返回值原样带回
    seen = {}
    def fake_run(task):
        seen["task"] = task
        return "子 Agent 的结论"
    monkeypatch.setattr(subagent, "run_subagent", fake_run)   # 外壳内延迟 import 会取到这个替身
    out = registry.run("dispatch_subagent", {"task": "查一下 X 的用法"})
    assert seen["task"] == "查一下 X 的用法"
    assert out == "子 Agent 的结论"


def test_dispatch_subagent_missing_task_self_heals():
    out = registry.run("dispatch_subagent", {})
    assert "参数错误" in out


def test_dispatch_subagent_gate_auto():
    # dispatch_subagent 无特判 → 落 gate 默认 auto
    assert gate_tool_call("dispatch_subagent", {"task": "x"}) == ("auto", None)
