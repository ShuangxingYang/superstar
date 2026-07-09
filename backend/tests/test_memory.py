"""memory service:profile/soul 的读写与注入拼接。
- profile 不存在返回空串;soul 不存在自举默认模板。
- 全量覆盖写、读回一致。
- build_memory_block 四种拼接:双空/只profile/只soul/都有。
"""
import pytest

from app.config import settings
from app.services import memory


@pytest.fixture
def tmp_mem(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path


def test_read_profile_missing_returns_empty(tmp_mem):
    assert memory.read_profile() == ""                       # 不存在 → 空串,不造模板


def test_read_soul_bootstraps_default(tmp_mem):
    # 首次读 soul:文件不存在 → 写入默认模板并返回它
    out = memory.read_soul()
    assert out == memory.DEFAULT_SOUL
    assert (tmp_mem / "soul.md").read_text(encoding="utf-8") == memory.DEFAULT_SOUL
    # 二次读:返回已存在内容(不再覆盖成模板)
    memory.write_soul("我的自定义准则")
    assert memory.read_soul() == "我的自定义准则"


def test_write_then_read_profile_overwrites(tmp_mem):
    memory.write_profile("用户叫小明")
    assert memory.read_profile() == "用户叫小明"
    memory.write_profile("用户叫小红")                        # 整份覆盖
    assert memory.read_profile() == "用户叫小红"


def test_build_block_empty_when_both_empty(tmp_mem):
    # profile 不存在、soul 也置空 → 整块为空串(system prompt 不变)
    memory.write_soul("")
    assert memory.build_memory_block() == ""


def test_build_block_only_profile(tmp_mem):
    memory.write_profile("用户叫小明")
    memory.write_soul("")
    block = memory.build_memory_block()
    assert "## 关于用户" in block and "用户叫小明" in block
    assert "## 你的准则" not in block


def test_build_block_only_soul(tmp_mem):
    memory.write_soul("要简洁")
    block = memory.build_memory_block()
    assert "## 你的准则" in block and "要简洁" in block
    assert "## 关于用户" not in block


def test_build_block_both(tmp_mem):
    memory.write_profile("用户叫小明")
    memory.write_soul("要简洁")
    block = memory.build_memory_block()
    assert "## 关于用户" in block and "用户叫小明" in block
    assert "## 你的准则" in block and "要简洁" in block


def test_build_block_survives_read_error(tmp_mem, monkeypatch):
    # 读记忆抛异常时,build_memory_block 必须兜住返回空串(不让 agent 循环挂掉)
    monkeypatch.setattr(memory, "read_profile", lambda: (_ for _ in ()).throw(OSError("boom")))
    assert memory.build_memory_block() == ""
