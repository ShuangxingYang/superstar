"""distill_memory:非流式假 LLM,断言 正常蒸馏/空日志短路不调LLM/返空不覆盖/异常兜底。"""
import pytest

from app.config import settings
from app.services import config_store, distill, llm, memory


# --- 非流式假对象(参照 test_subagent)---
class _Msg:
    def __init__(self, content):
        self.content = content


class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": _Msg(content)})()]


class _Completions:
    """记录被调用次数与收到的 messages;返回预设 content。"""
    def __init__(self, content):
        self.content = content
        self.calls = 0
        self.seen = None

    def create(self, model, messages, **kwargs):
        self.calls += 1
        self.seen = messages
        return _Resp(self.content)


class _Client:
    def __init__(self, content):
        self.comp = _Completions(content)
        self.chat = type("Chat", (), {"completions": self.comp})()


class _RaisingClient:
    class _C:
        def create(self, *a, **k):
            raise RuntimeError("boom")
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _RaisingClient._C()})()


@pytest.fixture
def mem_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    return tmp_path


def _make_log(tmp_path, memory_mod):
    # 造一天今天的日志
    d = memory_mod._today()
    log_dir = tmp_path / "memory"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{d.isoformat()}.md").write_text("- 10:00 项目测试用 uv run pytest\n", encoding="utf-8")


def test_distill_normal(mem_ready, monkeypatch):
    _make_log(mem_ready, memory)
    client = _Client("# 长期记忆\n- 测试用 uv run pytest")
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    out = distill.distill_memory()
    assert "蒸馏完成" in out
    assert memory.read_memory() == "# 长期记忆\n- 测试用 uv run pytest"   # 写回模型输出
    assert client.comp.calls == 1


def test_distill_no_logs_short_circuits(mem_ready, monkeypatch):
    # 没有任何日志 → 短路,不调 LLM
    client = _Client("不该被用到")
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    out = distill.distill_memory()
    assert "无日志" in out
    assert client.comp.calls == 0                       # 关键:没调模型


def test_distill_empty_response_keeps_old(mem_ready, monkeypatch):
    _make_log(mem_ready, memory)
    memory.write_memory("旧的长期记忆")                  # 先有旧记忆
    client = _Client("   ")                              # 模型返回空白
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    out = distill.distill_memory()
    assert "失败" in out
    assert memory.read_memory() == "旧的长期记忆"         # 未被覆盖


def test_distill_llm_exception_caught(mem_ready, monkeypatch):
    _make_log(mem_ready, memory)
    memory.write_memory("旧的长期记忆")
    monkeypatch.setattr(llm, "get_llm_client", lambda: (_RaisingClient(), "fake"))
    out = distill.distill_memory()
    assert out.startswith("蒸馏失败")                     # 收敛成串,没抛
    assert memory.read_memory() == "旧的长期记忆"          # 原记忆不动


def test_distill_prompt_includes_existing_and_logs(mem_ready, monkeypatch):
    _make_log(mem_ready, memory)
    memory.write_memory("现有记忆内容XYZ")
    client = _Client("新记忆")
    monkeypatch.setattr(llm, "get_llm_client", lambda: (client, "fake"))
    distill.distill_memory()
    user_msg = client.comp.seen[-1]["content"]
    assert "现有记忆内容XYZ" in user_msg                  # 现有 MEMORY 进了 prompt
    assert "uv run pytest" in user_msg                    # 日志进了 prompt
