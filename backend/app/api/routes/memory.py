"""routes/memory.py —— 记忆相关 HTTP 接口。目前只有手动触发蒸馏。"""
import logging

from fastapi import APIRouter

from app.services import distill

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.post("/distill")
def trigger_distill() -> dict:
    """手动立即蒸馏一次,返回结果摘要。与定时器共用 distill_memory()。"""
    logger.info("手动触发蒸馏")
    result = distill.distill_memory()
    return {"result": result}
