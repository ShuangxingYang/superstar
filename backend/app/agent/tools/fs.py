"""
fs.py —— 文件读取(只读工具)。写文件留给 P2b。

read_file:过 safe_path 沙箱 → 读文本 → 超大截断(限行数,防爆上下文/省 token)。
write_file(P2b):同样过 safe_path → 建父目录 → 整体覆盖写。是否需审批由上层 gate 决定。
"""
import logging

from pydantic import BaseModel, Field

from app.services.security import safe_path

logger = logging.getLogger(__name__)

MAX_LINES = 400   # 单次最多回这么多行,超了截断并提示模型缩小范围


class ReadFileArgs(BaseModel):
    path: str = Field(description="文件路径,优先用绝对路径;相对路径以默认工作目录为基准。须在允许目录内")


def read_file(args: ReadFileArgs) -> str:
    target = safe_path(args.path)          # 越界在这里抛 SecurityError,由 registry 兜
    if not target.is_file():
        return f"错误:文件不存在或不是文件: {args.path}"
    # errors="replace":遇到非 UTF-8 字节不炸,替换成占位符
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > MAX_LINES:
        head = "\n".join(lines[:MAX_LINES])
        return f"{head}\n…(共 {len(lines)} 行,只显示前 {MAX_LINES} 行,请缩小范围或指定区间)"
    return "\n".join(lines)


class WriteFileArgs(BaseModel):
    path: str = Field(description="文件路径,优先用绝对路径;相对路径以默认工作目录为基准。须在允许目录内")
    content: str = Field(description="要写入的完整文本内容(整体覆盖原文件)")


def write_file(args: WriteFileArgs) -> str:
    target = safe_path(args.path)          # 越界抛 SecurityError,由 registry 兜
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(args.content, encoding="utf-8")
    logger.info("写文件完成: path=%s, len=%d", args.path, len(args.content))
    return f"已写入 {args.path}({len(args.content)} 字符)"
