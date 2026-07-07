"""security.safe_path:挡越界(../、绝对路径、软链接),放行合法相对路径;未配置报错。"""
import pytest

from app.config import settings
from app.services import config_store, security
from app.services.security import SecurityError


@pytest.fixture
def ws(tmp_path, monkeypatch):
    # 用真实临时目录当 workspace;data_dir 指向 tmp_path(其下没有 config.json → 从 DEFAULTS 起)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    config_store.update({"security": {"workspace_dir": str(proj)}})
    return proj


def test_legal_relative_path_ok(ws):
    (ws / "a.py").write_text("x", encoding="utf-8")
    assert security.safe_path("a.py") == (ws / "a.py").resolve()


def test_reject_parent_escape(ws):
    with pytest.raises(SecurityError):
        security.safe_path("../../etc/passwd")


def test_reject_absolute_path(ws):
    with pytest.raises(SecurityError):
        security.safe_path("/etc/passwd")


def test_reject_symlink_escape(ws, tmp_path):
    # 工作区内造一个指向外部的软链接,resolve 后越界应被拒
    secret = tmp_path / "secret.txt"
    secret.write_text("top", encoding="utf-8")
    (ws / "link").symlink_to(secret)
    with pytest.raises(SecurityError):
        security.safe_path("link")


def test_unconfigured_workspace_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))  # 无 config.json → workspace_dir 为空
    config_store._reset_cache()
    with pytest.raises(SecurityError):
        security.get_workspace()


# ============ P2b: classify_command 命令分级 ============
from app.services.security import classify_command


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    # 只需 config_store 默认名单(不依赖 workspace);隔离到 tmp data_dir
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    return None


def test_classify_white(cfg):
    assert classify_command("grep foo") == "white"
    assert classify_command("git status") == "white"      # 短语白名单项


def test_classify_black_direct(cfg):
    assert classify_command("sudo reboot") == "black"      # sudo 命中


def test_classify_black_chained_bypass(cfg):
    # 白名单开头 + 危险尾巴:拆段后第二段命中 rm -rf → 整条 black(防绕过)
    assert classify_command("grep x && rm -rf /") == "black"


def test_classify_gray(cfg):
    assert classify_command("python demo.py") == "gray"


def test_classify_empty_is_black(cfg):
    assert classify_command("   ") == "black"


def test_classify_token_boundary(cfg):
    # grepx 不是 grep,不应算白名单
    assert classify_command("grepx foo") == "gray"
