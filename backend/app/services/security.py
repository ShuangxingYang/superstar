"""
security.py —— 沙箱守卫(P2a 只读工具的最底层防线)

心法:光看路径字符串里有没有 `..` 防不住(软链接、绝对路径、深层 ../ 都能绕过)。
正确姿势:先 (root / rel) 再 resolve() 算出真实绝对路径,再判断它是不是 workspace 根的后代。
碰文件的工具(read_file/grep/glob)都必须先过 safe_path。
"""
import logging
import re
from pathlib import Path

from app.services import config_store

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """安全拦截(越界 / 未配置工作区)。被工具执行层捕获成 tool 结果喂回模型,不崩流。"""


def get_default_cwd() -> Path:
    """默认工作目录 = run_command 默认 cwd + 相对路径基准。不存在则自动创建(~/.superstar 首次)。

    default_cwd 为空时退一步取 allowed_dirs[0];仍为空则报错引导去设置页(绝不默认乱翻)。
    """
    sec = config_store.get()["security"]
    raw = sec.get("default_cwd") or ""
    if not raw:
        roots = sec.get("allowed_dirs") or []
        raw = roots[0] if roots else ""
    if not raw:
        raise SecurityError("未配置工作目录,请先在设置页指定默认工作目录")
    p = Path(raw).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_allowed_roots() -> list[Path]:
    """所有可访问根 = default_cwd + allowed_dirs(各 expanduser+resolve,去空去重)。全空则报错。"""
    sec = config_store.get()["security"]
    raw = [sec.get("default_cwd") or "", *(sec.get("allowed_dirs") or [])]
    roots: list[Path] = []
    for r in raw:
        if not r:
            continue
        p = Path(r).expanduser().resolve()
        if p not in roots:
            roots.append(p)
    if not roots:
        raise SecurityError("未配置任何可访问目录,请先在设置页指定")
    return roots


def safe_path(path: str) -> Path:
    """把工具传来的路径钉进任一允许根内;都不命中抛 SecurityError。

    对每个 root 计算 (root / path).resolve():
      - path 相对路径 → 拼在 root 下
      - path 绝对路径(如 /etc/passwd)→ Path 语义下 root / "/etc/passwd" == "/etc/passwd",
        resolve 后不在任何 root 内 → 拒
      - ../ 和软链接都被 resolve 解开成真实路径再判断
    用 `root in target.parents` 判祖先(比 startswith 稳,避开 /home/user-evil 冒充 /home/user)。
    命中任一根即返回;遍历完都不命中才拒。
    """
    for root in get_allowed_roots():
        target = (root / path).resolve()
        if target == root or root in target.parents:
            return target
    logger.warning("路径越界拦截: path=%s", path)
    raise SecurityError(f"路径越界,超出允许目录: {path}")


# ---- P2b: 命令分级(白/黑/灰),拆段判级防拼接绕过 ----
SHELL_SEP = re.compile(r"&&|\|\||;|\|")   # && || ; |


def _segments(command: str) -> list[str]:
    """按 shell 操作符拆成多段,去空白空段。"""
    return [s.strip() for s in SHELL_SEP.split(command) if s.strip()]


def classify_command(command: str) -> str:
    """返回 'white' | 'black' | 'gray'。

    规则(顺序敏感):
      1. 黑优先:任一段含黑名单词(子串)→ black。防 `grep x && rm -rf /` 绕过。
      2. 全白才白:每段都以某白名单项开头(token 边界)→ white。
      3. 其余 → gray(审批)。
    """
    cfg = config_store.get()["security"]
    whitelist, blacklist = cfg["cmd_whitelist"], cfg["cmd_blacklist"]
    segs = _segments(command)
    if not segs:
        return "black"                                   # 空命令直接拒
    for seg in segs:
        if any(b in seg for b in blacklist):
            logger.info("命令分级=black: seg=%s", seg)
            return "black"

    def seg_white(seg: str) -> bool:                     # 'grep' 配 'grep foo',不配 'grepx'
        return any(seg == w or seg.startswith(w + " ") for w in whitelist)

    if all(seg_white(seg) for seg in segs):
        return "white"
    return "gray"
