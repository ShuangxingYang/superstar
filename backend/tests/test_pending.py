"""pending sidecar:审批暂停态落盘(读/写/清)。"""
import pytest

from app.agent import pending
from app.config import settings


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    return tmp_path


def test_read_missing_is_none(data):
    assert pending.read("nope") is None


def test_write_then_read_roundtrip(data):
    tcs = [{"id": "w1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]
    previews = {"w1": {"kind": "write", "path": "a.txt", "diff": "x"}}
    pending.write("s1", tcs, previews)
    got = pending.read("s1")
    assert got["tool_calls"] == tcs
    assert got["previews"]["w1"]["path"] == "a.txt"


def test_clear(data):
    pending.write("s1", [], {})
    pending.clear("s1")
    assert pending.read("s1") is None
