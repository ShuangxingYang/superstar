"""
fs.py —— 文件读取(只读工具)。写文件留给 P2b。

read_file:过 safe_path 沙箱 → 读文本 → 超大截断(限行数,防爆上下文/省 token)。
"""
from pydantic import BaseModel, Field

from app.services.security import safe_path

MAX_LINES = 400   # 单次最多回这么多行,超了截断并提示模型缩小范围


class ReadFileArgs(BaseModel):
    path: str = Field(description="相对工作区根目录的文件路径,如 src/main.py")


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
