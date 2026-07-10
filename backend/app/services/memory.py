"""
memory.py —— 跨会话长期记忆(profile 用户画像 + soul Agent 准则)

设计(详见 docs/specs/2026-07-09-p5-memory-design.md):
  - data/profile.md:用户画像,初始不存在,由 Agent 通过 update_profile 沉淀
  - data/soul.md:Agent 准则,首次读取自举一份默认模板,可被 Agent/用户改
  - 全量覆盖写、原子落盘;不加内存缓存(每轮读盘,避免"改盘不重启读旧缓存"的坑)
  - build_memory_block() 把两者拼成注入 system prompt 的稳定文本(保 prompt cache)
"""
import logging
from datetime import date, datetime, timedelta
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


def _memory_path() -> Path:
    return Path(settings.data_dir) / "MEMORY.md"


def read_memory() -> str:
    """读 MEMORY.md(长期客观记忆)。不存在 → 空串(不自举,同 profile)。errors=replace 防乱码。"""
    p = _memory_path()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def write_memory(content: str) -> None:
    """整份覆盖写 MEMORY.md(原子写)。"""
    atomic_json.write_text_atomic(_memory_path(), content)
    logger.info("已更新长期记忆(memory), len=%d", len(content))


def _today() -> date:
    """当前日期。独立成函数 → 测试可 monkeypatch 造'今天/昨天/跨天'场景。"""
    return date.today()


def _log_dir() -> Path:
    return Path(settings.data_dir) / "memory"


def _log_path(d: date) -> Path:
    return _log_dir() / f"{d.isoformat()}.md"          # 如 memory/2026-07-10.md


def append_log(entry: str) -> None:
    """把一条带时间戳的条目追加到今天的日志。目录/文件不存在则建。
    用 open(mode='a') 追加(最坏只坏最后一行,不必原子)。"""
    d = _today()
    path = _log_path(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H:%M")           # 条目内时间,仅进文件、不进 system 注入
    # 把内部换行/多余空白折成单空格:保住「一条一行」不变量(工具描述允许传"一小段")
    one_line = " ".join(entry.split())
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {stamp} {one_line}\n")
    logger.info("已追加日志: date=%s, len=%d", d.isoformat(), len(entry))


def read_log(d: date) -> str:
    """读某天日志。不存在 → 空串。errors=replace 防乱码。"""
    p = _log_path(d)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def recent_logs() -> list[tuple[date, str]]:
    """返回'今天+昨天'里非空的 (日期, 内容),今天在前。供 build_memory_block 用。"""
    today = _today()
    out: list[tuple[date, str]] = []
    for d in (today, today - timedelta(days=1)):
        content = read_log(d).strip()
        if content:
            out.append((d, content))
    return out


def build_memory_block() -> str:
    """拼成注入 system prompt 的一段文本;都空 → 空串。
    整体兜错:读记忆失败绝不让 agent 循环挂掉,退化成本轮不注入 + 记 warning。
    格式固定(日志小标题只用文件名日期,无 HH:MM/随机项),保 prompt cache 前缀稳定。"""
    try:
        profile = read_profile().strip()
        memory_ = read_memory().strip()
        soul = read_soul().strip()
        logs = recent_logs()
    except Exception as e:  # noqa: BLE001 - 关键路径,任何异常都退化成"不注入"
        logger.warning("读取记忆失败,本轮不注入: %s", type(e).__name__)
        return ""
    parts: list[str] = []
    if profile:
        parts.append(f"## 关于用户\n{profile}")
    if memory_:
        parts.append(f"## 长期记忆\n{memory_}")
    if soul:
        parts.append(f"## 你的准则\n{soul}")
    for d, content in logs:
        label = "今天" if d == _today() else "昨天"
        parts.append(f"## {label}的日志({d.isoformat()})\n{content}")
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)
