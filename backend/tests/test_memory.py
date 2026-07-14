"""memory service:profile/soul 的读写与注入拼接。
- profile 不存在返回空串;soul 不存在自举默认模板。
- 全量覆盖写、读回一致。
- build_memory_block 四种拼接:双空/只profile/只soul/都有。
"""
import pytest

from app.config import settings
from app.services import memory


@pytest.fixture
def tmp_mem(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path


def test_read_profile_missing_returns_empty(tmp_mem):
    assert memory.read_profile() == ""                       # 不存在 → 空串,不造模板


def test_read_soul_bootstraps_default(tmp_mem):
    # 首次读 soul:文件不存在 → 写入默认模板并返回它
    out = memory.read_soul()
    assert out == memory.DEFAULT_SOUL
    assert (tmp_mem / "soul.md").read_text(encoding="utf-8") == memory.DEFAULT_SOUL
    # 二次读:返回已存在内容(不再覆盖成模板)
    memory.write_soul("我的自定义准则")
    assert memory.read_soul() == "我的自定义准则"


def test_write_then_read_profile_overwrites(tmp_mem):
    memory.write_profile("用户叫小明")
    assert memory.read_profile() == "用户叫小明"
    memory.write_profile("用户叫小红")                        # 整份覆盖
    assert memory.read_profile() == "用户叫小红"


def test_build_block_empty_when_both_empty(tmp_mem):
    # profile 不存在、soul 也置空 → 整块为空串(system prompt 不变)
    memory.write_soul("")
    assert memory.build_memory_block() == ""


def test_build_block_only_profile(tmp_mem):
    memory.write_profile("用户叫小明")
    memory.write_soul("")
    block = memory.build_memory_block()
    assert "## 关于用户" in block and "用户叫小明" in block
    assert "## 你的准则" not in block


def test_build_block_only_soul(tmp_mem):
    memory.write_soul("要简洁")
    block = memory.build_memory_block()
    assert "## 你的准则" in block and "要简洁" in block
    assert "## 关于用户" not in block


def test_build_block_both(tmp_mem):
    memory.write_profile("用户叫小明")
    memory.write_soul("要简洁")
    block = memory.build_memory_block()
    assert "## 关于用户" in block and "用户叫小明" in block
    assert "## 你的准则" in block and "要简洁" in block


def test_build_block_survives_read_error(tmp_mem, monkeypatch):
    # 读记忆抛异常时,build_memory_block 必须兜住返回空串(不让 agent 循环挂掉)
    monkeypatch.setattr(memory, "read_profile", lambda: (_ for _ in ()).throw(OSError("boom")))
    assert memory.build_memory_block() == ""


# ============ P5+: 每日日志层 ============
from datetime import date


def test_append_log_writes_today(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.append_log("帮用户加了日志层")
    content = memory.read_log(date(2026, 7, 10))
    assert "帮用户加了日志层" in content
    assert content.startswith("- ")                 # 带 "- HH:MM " 前缀的条目


def test_append_log_appends_not_overwrites(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.append_log("第一条")
    memory.append_log("第二条")
    content = memory.read_log(date(2026, 7, 10))
    assert "第一条" in content and "第二条" in content   # 追加,不覆盖
    assert content.count("\n") == 2                       # 两行条目


def test_append_log_multiline_entry_stays_one_line(tmp_mem, monkeypatch):
    # 多行 entry(工具描述允许传"一小段")要折成单行,保住"一条一行"不变量
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.append_log("第一行\n第二行\n\n第三行")
    content = memory.read_log(date(2026, 7, 10))
    assert content.count("\n") == 1                        # 只有一条(结尾一个换行)
    assert "第一行 第二行 第三行" in content                # 内部换行折成单空格


def test_read_log_missing_returns_empty(tmp_mem):
    assert memory.read_log(date(2026, 1, 1)) == ""


def test_recent_logs_today_and_yesterday_only(tmp_mem, monkeypatch):
    # 造:今天(07-10)、昨天(07-09)、前天(07-08)各写一条,前天不该出现
    for d, text in [(date(2026, 7, 10), "今天事"),
                    (date(2026, 7, 9), "昨天事"),
                    (date(2026, 7, 8), "前天事")]:
        monkeypatch.setattr(memory, "_today", lambda d=d: d)
        memory.append_log(text)
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    logs = memory.recent_logs()
    assert [d for d, _ in logs] == [date(2026, 7, 10), date(2026, 7, 9)]  # 今天在前,只两天
    joined = " ".join(c for _, c in logs)
    assert "今天事" in joined and "昨天事" in joined and "前天事" not in joined


def test_recent_logs_skips_empty_days(tmp_mem, monkeypatch):
    # 只有今天有日志,昨天没有 → recent_logs 只返回今天
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.append_log("只有今天")
    logs = memory.recent_logs()
    assert [d for d, _ in logs] == [date(2026, 7, 10)]


def test_build_block_includes_today_log(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_soul("")                            # 排除 soul 干扰
    memory.append_log("加了日志层")
    block = memory.build_memory_block()
    assert "## 今天的日志(2026-07-10)" in block
    assert "加了日志层" in block


def test_build_block_no_log_section_when_empty(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_soul("")
    block = memory.build_memory_block()
    assert "的日志(" not in block                     # 无日志 → 无日志段


def test_build_block_prefix_stable_same_day(tmp_mem, monkeypatch):
    # 前缀稳定性:同一天、内容不变,两次调用逐字节相同(保 prompt cache)
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_profile("用户叫小明")
    memory.append_log("干了活")
    assert memory.build_memory_block() == memory.build_memory_block()


# ============ P5++: 长期客观记忆 MEMORY.md ============
def test_read_memory_missing_returns_empty(tmp_mem):
    assert memory.read_memory() == ""                  # 不存在 → 空串,不自举


def test_write_then_read_memory_overwrites(tmp_mem):
    memory.write_memory("项目用 uv 管依赖")
    assert memory.read_memory() == "项目用 uv 管依赖"
    memory.write_memory("测试跑 uv run pytest")         # 整份覆盖
    assert memory.read_memory() == "测试跑 uv run pytest"


def test_build_block_includes_memory(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_soul("")                              # 排除 soul 干扰
    memory.write_memory("项目用 uv")
    block = memory.build_memory_block()
    assert "## 长期记忆" in block and "项目用 uv" in block


def test_build_block_no_memory_section_when_empty(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_soul("")
    block = memory.build_memory_block()
    assert "## 长期记忆" not in block


def test_build_block_injection_order(tmp_mem, monkeypatch):
    # 注入顺序:profile → memory → soul(都稳定,排一起;日志垫底)
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_profile("用户叫小明")
    memory.write_memory("项目用 uv")
    memory.write_soul("用中文")
    block = memory.build_memory_block()
    assert block.index("## 关于用户") < block.index("## 长期记忆") < block.index("## 你的准则")


def test_build_block_memory_prefix_stable(tmp_mem, monkeypatch):
    monkeypatch.setattr(memory, "_today", lambda: date(2026, 7, 10))
    memory.write_memory("项目用 uv")
    assert memory.build_memory_block() == memory.build_memory_block()


def test_recent_log_days(tmp_path, monkeypatch):
    from datetime import date
    from app.config import settings
    from app.services import memory
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    # 固定"今天"为可控日期,造 今天 / 前2天 / 前5天 三份日志
    fixed_today = date(2026, 7, 14)
    monkeypatch.setattr(memory, "_today", lambda: fixed_today)
    log_dir = tmp_path / "memory"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "2026-07-14.md").write_text("- 10:00 今天的事\n", encoding="utf-8")
    (log_dir / "2026-07-12.md").write_text("- 09:00 前两天的事\n", encoding="utf-8")
    (log_dir / "2026-07-09.md").write_text("- 08:00 前五天的事\n", encoding="utf-8")

    # n=3:只覆盖 07-14/13/12 → 命中 14 和 12,今天在前;09 在窗口外
    got = memory.recent_log_days(3)
    assert [d.isoformat() for d, _ in got] == ["2026-07-14", "2026-07-12"]
    assert "今天的事" in got[0][1]

    # n=0 → 空
    assert memory.recent_log_days(0) == []
