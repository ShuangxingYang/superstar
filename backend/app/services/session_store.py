"""
session_store.py —— 会话持久化(每会话一个 JSONL + index.json 元数据缓存)

设计(详见 docs/specs/2026-07-03-p1-sessions-design.md):
  - data/sessions/<id>.jsonl:只存 message 行,追加写。行 = {"ts","message":{...OpenAI 消息...}}
  - data/sessions/index.json:[{id,title,created_at,updated_at}],可重建的元数据缓存
  - 一致性心法:.jsonl 是真相,index 是缓存;写序防幽灵(建时最后写 index、删时最先去 index)
  - index 读改写走锁 + 原子写(复用 atomic_json)
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.services import atomic_json

logger = logging.getLogger(__name__)

_index_lock = threading.Lock()   # 串行化 index.json 的读-改-写,防并发丢更新
TITLE_MAX = 20


class SessionNotFound(Exception):
    """会话不存在 —— 路由层映射成 404。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sessions_dir() -> Path:
    # 从 settings 现取,便于测试 monkeypatch data_dir
    return Path(settings.data_dir) / "sessions"


def _session_path(sid: str) -> Path:
    return _sessions_dir() / f"{sid}.jsonl"


def _index_path() -> Path:
    return _sessions_dir() / "index.json"


def _truncate(text: str) -> str:
    text = text.strip().replace("\n", " ")
    return text[:TITLE_MAX] + ("…" if len(text) > TITLE_MAX else "")


def create() -> str:
    """新建会话:先建空 .jsonl,再把 index 条目当「提交点」最后写。"""
    sid = uuid.uuid4().hex
    now = _now()
    path = _session_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()  # 先落文件,崩在这里只剩无害孤儿
    with _index_lock:
        index = atomic_json.read_json(_index_path(), [])
        index.append({"id": sid, "title": "", "created_at": now, "updated_at": now})
        atomic_json.write_json_atomic(_index_path(), index)
    logger.info("新建会话: sid=%s", sid)
    return sid


def read_messages(sid: str) -> list[dict]:
    """读该会话所有 message(去信封),跳过解析失败的行。喂模型用。"""
    path = _session_path(sid)
    if not path.exists():
        raise SessionNotFound(sid)
    messages: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("跳过损坏行: sid=%s", sid)  # 最坏情况:最后一行写一半
            continue
        if "message" in rec:
            messages.append(rec["message"])
    return messages


def list_sessions() -> list[dict]:
    """读 index,按最近活跃倒序。"""
    index = atomic_json.read_json(_index_path(), [])
    return sorted(index, key=lambda s: s["updated_at"], reverse=True)


def append_message(sid: str, message: dict) -> None:
    """追加一条消息行;首条 user 消息落 title,每次 bump updated_at。"""
    path = _session_path(sid)
    if not path.exists():
        raise SessionNotFound(sid)
    now = _now()
    # .jsonl 追加写:逐行 append,最坏只写坏最后一行(读时跳过),无需原子写
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now, "message": message}, ensure_ascii=False) + "\n")
    # index 是缓存,读-改-写走锁 + 原子写
    with _index_lock:
        index = atomic_json.read_json(_index_path(), [])
        for entry in index:
            if entry["id"] == sid:
                entry["updated_at"] = now
                if not entry["title"] and message.get("role") == "user":
                    entry["title"] = _truncate(message.get("content", ""))
                break
        atomic_json.write_json_atomic(_index_path(), index)


def rename(sid: str, title: str) -> None:
    """重命名:改 index 条目。"""
    with _index_lock:
        index = atomic_json.read_json(_index_path(), [])
        for entry in index:
            if entry["id"] == sid:
                entry["title"] = title
                atomic_json.write_json_atomic(_index_path(), index)
                logger.info("重命名会话: sid=%s", sid)
                return
    raise SessionNotFound(sid)


def delete(sid: str) -> None:
    """删除:先去 index 条目(提交点),再删文件 → 崩溃只留无害孤儿,绝不留幽灵。"""
    with _index_lock:
        index = atomic_json.read_json(_index_path(), [])
        kept = [e for e in index if e["id"] != sid]
        if len(kept) == len(index):
            raise SessionNotFound(sid)
        atomic_json.write_json_atomic(_index_path(), kept)
    _session_path(sid).unlink(missing_ok=True)
    logger.info("删除会话: sid=%s", sid)


def rebuild_index() -> None:
    """兜底:扫目录重建 index(index 丢失/损坏时)。title 回退首条 user 消息,自定义重命名会丢。"""
    entries = []
    for path in sorted(_sessions_dir().glob("*.jsonl")):
        sid = path.stem
        title = ""
        for msg in read_messages(sid):
            if msg.get("role") == "user":
                title = _truncate(msg.get("content", ""))
                break
        ts = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        entries.append({"id": sid, "title": title, "created_at": ts, "updated_at": ts})
    with _index_lock:
        atomic_json.write_json_atomic(_index_path(), entries)
    logger.info("重建 index: 恢复 %d 个会话", len(entries))


def _fit_context(messages: list[dict]) -> list[dict]:
    """裁剪钩子:P1 全量喂,原样返回;真超长了再接 M12。"""
    return messages
