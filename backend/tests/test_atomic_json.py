"""atomic_json:原子 JSON 读写。写中途崩溃不能损坏已存在的好文件。"""
import json

import pytest

from app.services import atomic_json
from app.services.atomic_json import Path  # 与实现同一个 Path 类,便于 monkeypatch


def test_roundtrip_and_defaults(tmp_path):
    p = tmp_path / "x.json"
    assert atomic_json.read_json(p, {"a": 1}) == {"a": 1}   # 不存在 → default
    atomic_json.write_json_atomic(p, {"a": 2})
    assert atomic_json.read_json(p, None) == {"a": 2}       # 写后读回
    p.write_text("{坏 json", encoding="utf-8")
    assert atomic_json.read_json(p, []) == []               # 解析失败 → default


def test_write_crash_keeps_old_file(tmp_path, monkeypatch):
    p = tmp_path / "x.json"
    atomic_json.write_json_atomic(p, {"v": "good"})
    real = Path.write_text

    def half_then_boom(self, data, *a, **k):
        real(self, data[: len(data) // 2], *a, **k)
        raise RuntimeError("boom")

    monkeypatch.setattr(Path, "write_text", half_then_boom)
    with pytest.raises(RuntimeError):
        atomic_json.write_json_atomic(p, {"v": "bad"})
    assert json.loads(p.read_text(encoding="utf-8"))["v"] == "good"  # 旧文件完好
    assert not (tmp_path / "x.json.tmp").exists()                    # 无脏 tmp
