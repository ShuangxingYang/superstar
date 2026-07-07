"""
gate.py —— 处置判定:给一个 tool_call,决定「直接跑 / 直接拒 / 停下等审批」。

放在 loop 调工具之前:因为要先知道该不该停下等审批,不能等执行了才判。
  - write_file:越界 → deny;否则 approve(顺带造 diff 预览)
  - run_command:白 → auto,黑 → deny,灰 → approve(带命令预览)
  - 只读工具(read_file/grep/glob)→ auto
"""
import difflib
import logging

from app.services import security
from app.services.security import SecurityError

logger = logging.getLogger(__name__)


def gate_tool_call(name: str, args: dict) -> tuple[str, dict | None]:
    """返回 (action, preview)。action ∈ {'auto','deny','approve'}。"""
    if name == "write_file":
        try:
            target = security.safe_path(args["path"])          # 越界 → 连审批都不给
        except (SecurityError, KeyError):
            logger.info("gate: write_file 越界/缺参 → deny")
            return "deny", None
        old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        diff = "".join(difflib.unified_diff(
            old.splitlines(keepends=True),
            (args.get("content") or "").splitlines(keepends=True),
            fromfile=f"{args['path']} (原)", tofile=f"{args['path']} (新)"))
        return "approve", {"kind": "write", "path": args["path"], "diff": diff or "(无变化)"}

    if name == "run_command":
        level = security.classify_command(args.get("command", ""))
        if level == "white":
            return "auto", None
        if level == "black":
            logger.info("gate: run_command 黑名单 → deny")
            return "deny", None
        return "approve", {"kind": "command", "command": args.get("command", ""), "level": "gray"}

    return "auto", None                                        # 只读工具直接跑
