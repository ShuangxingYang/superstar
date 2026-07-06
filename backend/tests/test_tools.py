"""ToolRegistry:schema 生成 + 三处自愈(未知工具/参数错/执行异常都返回字符串,不抛)。"""
from pydantic import BaseModel

from app.agent.tools import ToolRegistry
from app.services.security import SecurityError


class _EchoArgs(BaseModel):
    text: str


def _echo(args: _EchoArgs) -> str:
    return f"echo:{args.text}"


def _boom(args: _EchoArgs) -> str:
    raise RuntimeError("炸了")


def _escape(args: _EchoArgs) -> str:
    raise SecurityError("越界")


def test_schema_shape():
    r = ToolRegistry()
    r.register("echo", _echo, _EchoArgs, "回声")
    fn = r.to_openai_schema()[0]
    assert fn["type"] == "function"
    assert fn["function"]["name"] == "echo"
    assert fn["function"]["description"] == "回声"
    assert "text" in fn["function"]["parameters"]["properties"]


def test_run_ok():
    r = ToolRegistry()
    r.register("echo", _echo, _EchoArgs, "回声")
    assert r.run("echo", {"text": "hi"}) == "echo:hi"


def test_selfheal_unknown_tool():
    assert ToolRegistry().run("nope", {}).startswith("错误:未知工具")


def test_selfheal_bad_args():
    r = ToolRegistry()
    r.register("echo", _echo, _EchoArgs, "回声")
    assert r.run("echo", {}).startswith("参数错误")           # 缺 text


def test_selfheal_security_error():
    r = ToolRegistry()
    r.register("bad", _escape, _EchoArgs, "x")
    assert r.run("bad", {"text": "a"}).startswith("安全拦截")


def test_selfheal_runtime_error():
    r = ToolRegistry()
    r.register("bad", _boom, _EchoArgs, "x")
    assert r.run("bad", {"text": "a"}).startswith("工具执行失败")


# ============ read_file / grep / glob 需要真实 workspace ============
import pytest

from app.config import settings
from app.services import config_store
from app.agent.tools.fs import ReadFileArgs, read_file


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    config_store.update({"security": {"workspace_dir": str(proj)}})
    return proj


def test_read_file_ok(ws):
    (ws / "a.txt").write_text("hello\nworld", encoding="utf-8")
    assert read_file(ReadFileArgs(path="a.txt")) == "hello\nworld"


def test_read_file_missing(ws):
    assert read_file(ReadFileArgs(path="nope.txt")).startswith("错误:文件不存在")


def test_read_file_truncated(ws):
    (ws / "big.txt").write_text("\n".join(str(i) for i in range(1000)), encoding="utf-8")
    out = read_file(ReadFileArgs(path="big.txt"))
    assert "只显示前" in out


def test_read_file_escape_via_registry(ws):
    from app.agent.tools import registry

    # 经全局 registry.run 走自愈:越界返回「安全拦截」而不是抛(验证 read_file 已登记)
    assert registry.run("read_file", {"path": "../../etc/passwd"}).startswith("安全拦截")
