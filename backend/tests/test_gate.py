"""gate_tool_call:每个 tool_call 的处置判定(auto/deny/approve + 预览)。"""
import pytest

from app.agent.gate import gate_tool_call
from app.config import settings
from app.services import config_store


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    config_store.update({"security": {"default_cwd": str(proj), "allowed_dirs": []}})
    return proj


def test_gate_readonly_auto(ws):
    assert gate_tool_call("grep", {"pattern": "x"}) == ("auto", None)
    assert gate_tool_call("read_file", {"path": "a"})[0] == "auto"


def test_gate_write_approve_with_diff(ws):
    (ws / "a.txt").write_text("old\n", encoding="utf-8")
    action, preview = gate_tool_call("write_file", {"path": "a.txt", "content": "new\n"})
    assert action == "approve"
    assert preview["kind"] == "write" and preview["path"] == "a.txt"
    assert "old" in preview["diff"] and "new" in preview["diff"]


def test_gate_diff_splits_lines_without_trailing_newline(ws):
    # 旧内容无结尾换行(如 "hello"),diff 不能把 -hello 和 +world 挤成一行 -hello+world;
    # 前端按 \n 切行着色,必须保证每个 +/- 行独占一行。
    (ws / "b.txt").write_text("hello", encoding="utf-8")   # 注意:无结尾 \n
    _, preview = gate_tool_call("write_file", {"path": "b.txt", "content": "world"})
    lines = preview["diff"].split("\n")
    assert any(ln.startswith("-hello") and "+world" not in ln for ln in lines)  # -hello 独占一行
    assert any(ln.startswith("+world") for ln in lines)                          # +world 独占一行


def test_gate_write_escape_deny(ws):
    assert gate_tool_call("write_file", {"path": "../../tmp/x", "content": "z"}) == ("deny", None)


def test_gate_command_white_auto(ws):
    assert gate_tool_call("run_command", {"command": "ls"}) == ("auto", None)


def test_gate_command_black_deny(ws):
    assert gate_tool_call("run_command", {"command": "rm -rf /"}) == ("deny", None)


def test_gate_command_gray_approve(ws):
    action, preview = gate_tool_call("run_command", {"command": "python demo.py"})
    assert action == "approve"
    assert preview["kind"] == "command" and preview["command"] == "python demo.py"


def test_gate_add_workspace_needs_approval():
    # add_workspace 需审批;预览展示 expanduser().resolve() 后的绝对路径(防 ~/.. 障眼)
    action, preview = gate_tool_call("add_workspace", {"path": "~/proj"})
    assert action == "approve"
    assert preview["kind"] == "add_workspace"
    assert preview["path"].startswith("/")          # 绝对路径
    assert "~" not in preview["path"]


def test_gate_remove_workspace_auto():
    # remove_workspace 收权无害 → 自动放行
    assert gate_tool_call("remove_workspace", {"path": "/x"}) == ("auto", None)
