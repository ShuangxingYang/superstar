"""scheduler:enabled=false 不启动;enabled=true 注册 id='distill' 的 job;stop 后清空。"""
import pytest

from app.config import settings
from app.services import config_store


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    yield
    # 每个用例后确保调度器停掉,别把线程漏到别的测试
    from app.agent import scheduler
    scheduler.stop_scheduler()


def test_scheduler_disabled_does_not_start():
    from app.agent import scheduler
    config_store.update({"distill": {"enabled": False}})
    scheduler.start_scheduler()
    assert scheduler._scheduler is None


def test_scheduler_enabled_registers_job():
    from app.agent import scheduler
    config_store.update({"distill": {"enabled": True, "interval_hours": 72, "scan_days": 3}})
    scheduler.start_scheduler()
    assert scheduler._scheduler is not None
    assert scheduler._scheduler.get_job("distill") is not None
    scheduler.stop_scheduler()
    assert scheduler._scheduler is None
