"""验证 config.json 的原子写:写入过程中崩溃,不能损坏已存在的好文件。"""

import json

import pytest

from app.config import settings
from app.services import config_store


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    yield tmp_path
    config_store._reset_cache()


def test_write_crash_keeps_old_file_intact(tmp_config, monkeypatch):
    # 先成功写一份好配置
    config_store.update({"llm": {"api_key": "sk-good", "model": "m-good"}})
    good = json.loads((tmp_config / "config.json").read_text(encoding="utf-8"))
    assert good["llm"]["api_key"] == "sk-good"

    # 模拟"写盘写到一半崩溃":真实地往目标/临时文件写入半截内容后抛错。
    # 非原子实现会直接写坏 config.json;原子实现只会写坏 .tmp,目标文件不受影响。
    real_write_text = config_store.Path.write_text

    def half_write_then_boom(self, data, *args, **kwargs):
        real_write_text(self, data[: len(data) // 2], *args, **kwargs)  # 只写一半
        raise RuntimeError("模拟写入中途崩溃")

    monkeypatch.setattr(config_store.Path, "write_text", half_write_then_boom)
    with pytest.raises(RuntimeError):
        config_store.update({"llm": {"api_key": "sk-bad"}})

    # 关键断言:原来的好文件必须完好无损、仍可正常解析,不是半截
    still = json.loads((tmp_config / "config.json").read_text(encoding="utf-8"))
    assert still["llm"]["api_key"] == "sk-good"

    # 且不该残留脏的临时文件
    assert not (tmp_config / "config.json.tmp").exists()
