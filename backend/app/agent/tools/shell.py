"""
shell.py —— 执行 shell 命令(P2b 写操作之一)。

工具本身只负责执行:在工作区目录下跑命令、限时、截断输出。
命令是否放行(白/黑/灰)由 loop 调工具之前的 gate 判定,不在这里。
cwd=工作区 是弱边界(shell 命令本质能读写工作区外),主控制是三级名单。
"""
import logging
import subprocess

from pydantic import BaseModel, Field

from app.services import security

logger = logging.getLogger(__name__)

CMD_TIMEOUT = 30       # 秒:防命令挂死
MAX_OUTPUT = 4000      # 字符:防爆上下文


class RunCommandArgs(BaseModel):
    command: str = Field(description="要执行的 shell 命令")
    cwd: str | None = Field(
        default=None,
        description="命令工作目录(绝对路径,须在允许目录内);留空则用默认工作目录",
    )


def run_command(args: RunCommandArgs) -> str:
    # 传了 cwd 就过沙箱校验(越界抛 SecurityError,由 registry 兜成「安全拦截」);没传用默认工作目录
    cwd = security.safe_path(args.cwd) if args.cwd else security.get_default_cwd()
    logger.info("执行命令: %s (cwd=%s)", args.command, cwd)
    try:
        proc = subprocess.run(
            args.command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=CMD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.warning("命令超时: %s", args.command)
        return f"命令超时(>{CMD_TIMEOUT}s),已终止"
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + f"\n…(输出过长已截断,共 {len(out)} 字符)"
    logger.info("命令完成: exit=%d, out_len=%d", proc.returncode, len(out))
    body = f"[exit {proc.returncode}]\n{out}".rstrip()
    return body or f"[exit {proc.returncode}](无输出)"
