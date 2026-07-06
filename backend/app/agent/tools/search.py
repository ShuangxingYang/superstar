"""
search.py —— 代码搜索(只读,纯 Python,不走 shell)。

grep:os.walk 遍历工作区 + re 逐行匹配,返回「相对路径:行号:内容」。
glob:pathlib 按通配模式列文件名。
纯 Python 的理由:零外部依赖、跨平台、根本没有 shell 注入面(对比 Claude Code 打包 ripgrep
是为伺候巨型仓库;个人项目慢一点无感)。命中/匹配过多则截断,提示缩小范围。
"""
import os
import re

from pydantic import BaseModel, Field

from app.services.security import get_workspace, safe_path

MAX_HITS = 100          # grep 最多回这么多条命中
MAX_MATCHES = 200       # glob 最多回这么多个文件
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


class GrepArgs(BaseModel):
    pattern: str = Field(description="正则表达式,按行匹配")
    path: str = Field(default=".", description="搜索起点,相对工作区根,默认整个工作区")


def grep(args: GrepArgs) -> str:
    start = safe_path(args.path)                     # 起点也过沙箱
    try:
        regex = re.compile(args.pattern)
    except re.error as e:
        return f"错误:正则表达式非法: {e}"
    root = get_workspace()
    base = start if start.is_dir() else start.parent
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]   # 原地裁剪:不进这些目录
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            rel = os.path.relpath(fp, root)
                            hits.append(f"{rel}:{lineno}:{line.rstrip()}")
                            if len(hits) >= MAX_HITS:
                                hits.append(f"…命中过多(≥{MAX_HITS}),请缩小 pattern 或 path")
                                return "\n".join(hits)
            except OSError:
                continue     # 读不了的文件(权限/特殊文件)跳过,不影响整体
    return "\n".join(hits) if hits else "(无匹配)"


class GlobArgs(BaseModel):
    pattern: str = Field(description="通配模式,相对工作区根,如 **/*.py")


def glob(args: GlobArgs) -> str:
    root = get_workspace()
    try:
        found = list(root.glob(args.pattern))
    except ValueError as e:
        return f"错误:glob 模式非法: {e}"      # 绝对路径/含 .. 的模式 pathlib 会拒
    matches: list[str] = []
    for p in found:
        rel = p.relative_to(root)               # glob 结果天然在 root 下
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        matches.append(str(rel))
        if len(matches) >= MAX_MATCHES:
            matches.append(f"…匹配过多(≥{MAX_MATCHES}),请缩小 pattern")
            break
    return "\n".join(matches) if matches else "(无匹配)"
