"""
pending.py —— 审批暂停态 sidecar(data/sessions/<sid>.pending.json)。

为何独立于 JSONL:JSONL 是「消息日志」,应纯净且只追加;pending 是「可清除的临时暂停态」,
读/写/删都独立。二者分离,_prune_dangling_tool_calls 也不必理解 pending。

结构:{ "tool_calls": [完整 tool_call...], "previews": {tool_call_id: 预览} }
"""
import logging
from pathlib import Path

from app.config import settings
from app.services import atomic_json

logger = logging.getLogger(__name__)


def _path(sid: str) -> Path:
    return Path(settings.data_dir) / "sessions" / f"{sid}.pending.json"


def read(sid: str) -> dict | None:
    """无文件 → None。"""
    return atomic_json.read_json(_path(sid), None)


def write(sid: str, tool_calls: list[dict], previews: dict) -> None:
    atomic_json.write_json_atomic(_path(sid), {"tool_calls": tool_calls, "previews": previews})
    logger.info("写 pending: sid=%s, 待审批=%d", sid, len(tool_calls))


def clear(sid: str) -> None:
    _path(sid).unlink(missing_ok=True)
    logger.info("清 pending: sid=%s", sid)
