"""
memory.py —— 跨会话长期记忆(profile 用户画像 + soul Agent 准则)

设计(详见 docs/specs/2026-07-09-p5-memory-design.md):
  - data/profile.md:用户画像,初始不存在,由 Agent 通过 update_profile 沉淀
  - data/soul.md:Agent 准则,首次读取自举一份默认模板,可被 Agent/用户改
  - 全量覆盖写、原子落盘;不加内存缓存(每轮读盘,避免"改盘不重启读旧缓存"的坑)
  - build_memory_block() 把两者拼成注入 system prompt 的稳定文本(保 prompt cache)
"""
import logging
from pathlib import Path

from app.config import settings
from app.services import atomic_json

logger = logging.getLogger(__name__)

# soul.md 首次不存在时写入的基线模板;profile 无默认(初始为空,靠 Agent 沉淀)
DEFAULT_SOUL = """\
# Agent 准则

- 用中文回答,说人话,不用翻译腔黑话。
- 动手改文件 / 跑命令前想清楚意图,危险操作先确认。
- 不确定就说不确定,别编造。
"""


def _profile_path() -> Path:
    return Path(settings.data_dir) / "profile.md"


def _soul_path() -> Path:
    return Path(settings.data_dir) / "soul.md"


def read_profile() -> str:
    """读 profile.md。不存在 → 空串(不造模板)。errors=replace 防乱码崩。"""
    p = _profile_path()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def read_soul() -> str:
    """读 soul.md。不存在 → 写入默认模板并返回它(首次自举)。"""
    p = _soul_path()
    if not p.exists():
        write_soul(DEFAULT_SOUL)
        return DEFAULT_SOUL
    return p.read_text(encoding="utf-8", errors="replace")


def write_profile(content: str) -> None:
    """整份覆盖写 profile.md(原子写)。"""
    atomic_json.write_text_atomic(_profile_path(), content)
    logger.info("已更新用户画像(profile), len=%d", len(content))


def write_soul(content: str) -> None:
    """整份覆盖写 soul.md(原子写)。"""
    atomic_json.write_text_atomic(_soul_path(), content)
    logger.info("已更新 Agent 准则(soul), len=%d", len(content))


def build_memory_block() -> str:
    """拼成注入 system prompt 的一段文本;两者都空 → 空串。
    整体兜错:读记忆失败绝不让 agent 循环挂掉,退化成本轮不注入 + 记 warning。
    格式固定(无时间戳/随机项),保 prompt cache 前缀稳定。"""
    try:
        profile = read_profile().strip()
        soul = read_soul().strip()
    except Exception as e:  # noqa: BLE001 - 关键路径,任何异常都退化成"不注入"
        logger.warning("读取记忆失败,本轮不注入: %s", type(e).__name__)
        return ""
    parts: list[str] = []
    if profile:
        parts.append(f"## 关于用户\n{profile}")
    if soul:
        parts.append(f"## 你的准则\n{soul}")
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)
