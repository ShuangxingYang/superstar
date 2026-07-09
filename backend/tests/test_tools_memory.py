"""update_profile / update_soul:经 registry.run 调用写盘生效,参数缺失走 Pydantic 自愈。"""
import pytest

from app.config import settings
from app.services import memory
from app.agent.tools import registry


@pytest.fixture
def tmp_mem(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path


def test_update_profile_writes(tmp_mem):
    out = registry.run("update_profile", {"content": "用户叫小明,常用 superstar 项目"})
    assert "profile" in out
    assert memory.read_profile() == "用户叫小明,常用 superstar 项目"


def test_update_soul_writes(tmp_mem):
    out = registry.run("update_soul", {"content": "回答尽量简短"})
    assert "soul" in out
    assert memory.read_soul() == "回答尽量简短"


def test_update_profile_missing_content_self_heals(tmp_mem):
    # 缺 content → registry 的 Pydantic 校验兜住,返回"参数错误"而非抛异常
    out = registry.run("update_profile", {})
    assert "参数错误" in out


def test_memory_tools_registered():
    names = {s["function"]["name"] for s in registry.to_openai_schema()}
    assert "update_profile" in names and "update_soul" in names


def test_tool_write_then_reinject_roundtrip(tmp_mem):
    # 验收核心闭环:Agent 调 update_profile 沉淀 → 之后 build_memory_block 能反映出来
    # (工具写盘与注入读盘走同一份 profile.md,这里把两半接起来断言端到端一致)
    registry.run("update_profile", {"content": "用户叫小明,常用 superstar 项目"})
    block = memory.build_memory_block()
    assert "## 关于用户" in block
    assert "用户叫小明,常用 superstar 项目" in block
