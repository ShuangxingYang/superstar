"""security.safe_path:挡越界(../、绝对路径、软链接),放行合法相对路径;未配置报错。"""
import pytest

from app.config import settings
from app.services import config_store, security
from app.services.security import SecurityError


@pytest.fixture
def ws(tmp_path, monkeypatch):
    # 用真实临时目录当默认工作目录;data_dir 指向 tmp_path(其下没有 config.json → 从 DEFAULTS 起)
    # allowed_dirs 显式清空,隔离掉 DEFAULTS 里的真实 Desktop,避免测试搜到工作区外
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    proj = tmp_path / "proj"
    proj.mkdir()
    config_store.update({"security": {"default_cwd": str(proj), "allowed_dirs": []}})
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


def test_unconfigured_raises(monkeypatch):
    # default_cwd 与 allowed_dirs 都空 → 无任何可访问根,报错引导去设置页
    monkeypatch.setattr(config_store, "get", lambda: {
        "security": {"default_cwd": "", "allowed_dirs": []}
    })
    with pytest.raises(SecurityError):
        security.get_default_cwd()
    with pytest.raises(SecurityError):
        security.get_allowed_roots()


def test_safe_path_multi_root(tmp_path, monkeypatch):
    # 两个允许根:default_cwd=a、allowed_dirs=[b];命中任一即放行,都不命中则拒
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "x.txt").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(config_store, "get", lambda: {
        "security": {"default_cwd": str(a), "allowed_dirs": [str(b)]}
    })
    assert security.safe_path(str(a / "x.txt")) == (a / "x.txt").resolve()   # 命中 default_cwd
    assert security.safe_path(str(b / "y.txt")) == (b / "y.txt").resolve()   # 命中 allowed_dirs
    with pytest.raises(SecurityError):
        security.safe_path(str(tmp_path / "outside.txt"))                    # 两根都不命中 → 越界


def test_safe_path_blocks_traversal(tmp_path, monkeypatch):
    a = tmp_path / "a"
    a.mkdir()
    monkeypatch.setattr(config_store, "get", lambda: {
        "security": {"default_cwd": str(a), "allowed_dirs": []}
    })
    with pytest.raises(SecurityError):
        security.safe_path("../../etc/passwd")                              # resolve 后越界仍被拒


def test_default_cwd_tilde_expand_and_mkdir(tmp_path, monkeypatch):
    # ~ 展开 + 不存在则自动创建
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config_store, "get", lambda: {
        "security": {"default_cwd": "~/.superstar", "allowed_dirs": []}
    })
    cwd = security.get_default_cwd()
    assert cwd == (home / ".superstar").resolve()
    assert cwd.is_dir()                                                     # 首次访问自动建目录


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
