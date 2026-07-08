"""
gate.py —— 处置判定:给一个 tool_call,决定「直接跑 / 直接拒 / 停下等审批」。

放在 loop 调工具之前:因为要先知道该不该停下等审批,不能等执行了才判。
  - write_file:越界 → deny;否则 approve(顺带造 diff 预览)
  - run_command:白 → auto,黑 → deny,灰 → approve(带命令预览)
  - 只读工具(read_file/grep/glob)→ auto
"""
import difflib
import logging
from pathlib import Path

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
        new = args.get("content") or ""
        # 按行切并保证每行以 \n 结尾:否则无结尾换行的内容(如 "hello")会让 difflib 把
        # -hello 和 +world 拼成一行 "-hello+world",前端按 \n 着色就切不开。
        old_lines = [ln if ln.endswith("\n") else ln + "\n" for ln in old.splitlines()]
        new_lines = [ln if ln.endswith("\n") else ln + "\n" for ln in new.splitlines()]
        diff = "".join(difflib.unified_diff(
            old_lines, new_lines,
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

    if name == "add_workspace":
        # 扩权操作,需审批;预览用 resolve() 后的绝对路径,让用户看清真实目标(防 ~/.. 障眼)
        abs_path = str(Path(args.get("path", "")).expanduser().resolve())
        return "approve", {"kind": "add_workspace", "path": abs_path}

    return "auto", None                                        # 只读工具 / remove_workspace(收权无害)直接跑
