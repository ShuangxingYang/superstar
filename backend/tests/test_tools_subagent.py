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


# ============ 复数版:dispatch_subagents 并行派发 ============
def test_dispatch_subagents_registered():
    names = {s["function"]["name"] for s in registry.to_openai_schema()}
    assert "dispatch_subagents" in names


def test_dispatch_subagents_not_in_subagent_tools():
    # 复数版也不在子 Agent 白名单(防子 Agent 并行派孙 Agent)
    assert "dispatch_subagents" not in subagent.SUBAGENT_TOOLS


def test_dispatch_subagents_runs_all_and_preserves_order(monkeypatch):
    # monkeypatch run_subagent:按 task 回不同结果,验证并发跑全 + 保序
    def fake_run(task):
        return f"结论-{task}"
    monkeypatch.setattr(subagent, "run_subagent", fake_run)
    out = registry.run("dispatch_subagents", {"tasks": ["A", "B", "C"]})
    # 保序:A 在 B 前、B 在 C 前
    assert out.index("结论-A") < out.index("结论-B") < out.index("结论-C")
    assert "A" in out and "B" in out and "C" in out


def test_dispatch_subagents_empty_tasks():
    out = registry.run("dispatch_subagents", {"tasks": []})
    assert "没有要派发的子任务" in out


def test_dispatch_subagents_one_failure_does_not_break_others(monkeypatch):
    # 某个子 Agent 返回失败串(run_subagent 本就兜底不抛),其余照常
    def fake_run(task):
        if task == "bad":
            return "(子 Agent 执行失败:boom)"
        return f"结论-{task}"
    monkeypatch.setattr(subagent, "run_subagent", fake_run)
    out = registry.run("dispatch_subagents", {"tasks": ["ok1", "bad", "ok2"]})
    assert "结论-ok1" in out and "结论-ok2" in out
    assert "子 Agent 执行失败" in out          # 失败的也如实带回,不吞


def test_dispatch_subagents_missing_tasks_self_heals():
    out = registry.run("dispatch_subagents", {})
    assert "参数错误" in out                    # Pydantic 缺 tasks → registry 自愈
