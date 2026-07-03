"""session_store:会话 JSONL + index.json 元数据缓存。"""
import pytest

from app.config import settings
from app.services import session_store


@pytest.fixture
def tmp_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    yield tmp_path


def test_create_then_list_and_read_empty(tmp_sessions):
    sid = session_store.create()
    assert (tmp_sessions / "sessions" / f"{sid}.jsonl").exists()  # 先建了空 .jsonl
    listed = session_store.list_sessions()
    assert [s["id"] for s in listed] == [sid]
    assert listed[0]["title"] == ""                              # 未发消息,标题空
    assert session_store.read_messages(sid) == []                # 空会话无消息


def test_read_missing_raises(tmp_sessions):
    with pytest.raises(session_store.SessionNotFound):
        session_store.read_messages("nope")


def test_append_sets_title_and_bumps(tmp_sessions):
    sid = session_store.create()
    session_store.append_message(sid, {"role": "user", "content": "我叫小明,帮我看下 utils.py 有没有问题"})
    session_store.append_message(sid, {"role": "assistant", "content": "好的"})
    msgs = session_store.read_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]        # 多轮按序落盘
    meta = session_store.list_sessions()[0]
    assert meta["title"].startswith("我叫小明")                       # 首条 user 消息落标题
    assert meta["title"].endswith("…")                              # 超 20 字截断
    assert meta["updated_at"] >= meta["created_at"]                 # 活跃时间被 bump


def test_append_missing_raises(tmp_sessions):
    with pytest.raises(session_store.SessionNotFound):
        session_store.append_message("nope", {"role": "user", "content": "x"})


def test_rename_and_delete(tmp_sessions):
    sid = session_store.create()
    session_store.rename(sid, "自我介绍")
    assert session_store.list_sessions()[0]["title"] == "自我介绍"
    session_store.delete(sid)
    assert session_store.list_sessions() == []                       # 从列表消失
    assert not (tmp_sessions / "sessions" / f"{sid}.jsonl").exists()  # 文件也删了
    with pytest.raises(session_store.SessionNotFound):
        session_store.rename("nope", "x")
    with pytest.raises(session_store.SessionNotFound):
        session_store.delete("nope")


def test_rebuild_index_recovers_orphan(tmp_sessions):
    sid = session_store.create()
    session_store.append_message(sid, {"role": "user", "content": "孤儿会话内容"})
    # 模拟 index 丢失:删掉 index.json,只剩 .jsonl(孤儿)
    (tmp_sessions / "sessions" / "index.json").unlink()
    assert session_store.list_sessions() == []
    session_store.rebuild_index()
    recovered = session_store.list_sessions()
    assert [s["id"] for s in recovered] == [sid]
    assert recovered[0]["title"].startswith("孤儿会话内容")           # 标题回退首条 user 消息


def test_fit_context_passthrough(tmp_sessions):
    msgs = [{"role": "user", "content": "a"}]
    assert session_store._fit_context(msgs) == msgs                  # P1 原样返回
