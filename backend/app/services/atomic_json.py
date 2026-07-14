"""
atomic_json.py —— 共用的原子 JSON 读写。

从 config_store 抽出,给 config.json 和 sessions/index.json 复用(DRY)。
- read_json:不存在或解析失败都返回 default(调用方不用到处 try)。
- write_json_atomic:tmp 写 + os.replace,读者永远看不到写一半的残缺 JSON。
  (rename 只有同一文件系统内才原子,所以 tmp 必须和目标同目录。)
- tmp 名带唯一后缀(pid+uuid):两个并发写者(如蒸馏定时器 + 手动接口同时写 MEMORY.md)
  各用各的 tmp,互不覆盖,os.replace 依次落地 = 最后写的赢,绝不会字节级交错写坏目标。
"""
import json
import os
import uuid
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _tmp_path(path: Path) -> Path:
    """同目录、唯一名的临时文件(pid+uuid):保证并发写者互不争用同一 tmp。"""
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def write_text_atomic(path: Path, text: str) -> None:
    """原子写纯文本(markdown 等):tmp 写 + os.replace,读者永不见写一半的文件。
    与 write_json_atomic 同构,只是不做 json.dumps。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
