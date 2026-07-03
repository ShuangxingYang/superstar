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
