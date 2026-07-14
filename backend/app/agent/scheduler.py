"""
scheduler.py —— 后台定时调度(目前只挂记忆蒸馏)。

用 APScheduler BackgroundScheduler(守护线程,不阻塞主进程)。
按 config.distill.enabled 决定要不要注册 job:默认关 → 不启动,零开销。
由 api/main.py 的 lifespan 在启动/关闭时调用 start/stop。
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.services import config_store, distill

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    """按 config 决定是否注册蒸馏 job。enabled=false → 不启动。"""
    global _scheduler
    cfg = config_store.get()["distill"]
    if not cfg["enabled"]:
        logger.info("蒸馏调度未启用(distill.enabled=false),跳过")
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(distill.distill_memory, "interval",
                       hours=cfg["interval_hours"], id="distill")
    _scheduler.start()
    logger.info("蒸馏调度已启动:每 %d 小时一次", cfg["interval_hours"])


def stop_scheduler() -> None:
    """关闭时优雅停(如果起过)。"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("蒸馏调度已停止")
