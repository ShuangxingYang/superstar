"""
gate.py —— 处置判定:给一个 tool_call,决定「直接跑 / 直接拒 / 停下等审批」。

放在 loop 调工具之前:因为要先知道该不该停下等审批,不能等执行了才判。
  - write_file:越界 → deny;否则 auto(允许目录内免审批,2026-07-13 起)
  - run_command:白 → auto,黑 → deny,灰 → approve(带命令预览)
  - 只读工具(read_file/grep/glob)→ auto
"""
import logging
from pathlib import Path

from app.services import security
from app.services.security import SecurityError

logger = logging.getLogger(__name__)


def gate_tool_call(name: str, args: dict) -> tuple[str, dict | None]:
    """返回 (action, preview)。action ∈ {'auto','deny','approve'}。"""
    if name == "write_file":
        # 写文件已改为「允许目录内自动放行」(2026-07-13):越界仍拒,允许目录内直接跑,不再审批。
        try:
            security.safe_path(args["path"])          # 越界 → deny(沙箱最后防线在工具内,这里提前拦)
        except (SecurityError, KeyError):
            logger.info("gate: write_file 越界/缺参 → deny")
            return "deny", None
        return "auto", None

    if name == "run_command":
        level = security.classify_command(args.get("command", ""))
        if level == "white":
            return "auto", None
        if level == "black":
            logger.info("gate: run_command 黑名单 → deny")
            return "deny", None
        return "approve", {"kind": "command", "command": args.get("command", ""), "level": "gray"}

    if name == "add_workspace":
        # 扩权操作,需审批;预览用 resolve() 后的绝对路径,让用户看清真实目标(防 ~/.. 障眼)
        abs_path = str(Path(args.get("path", "")).expanduser().resolve())
        return "approve", {"kind": "add_workspace", "path": abs_path}

    return "auto", None                                        # 只读工具 / remove_workspace(收权无害)直接跑
