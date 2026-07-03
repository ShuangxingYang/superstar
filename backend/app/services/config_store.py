"""
config_store.py —— 业务配置读写(存 data/config.json)

与 config.py 分工:
  - config.py = 启动必需、很少变(端口/data_dir/qdrant_url),从 .env 读
  - 本文件   = 业务配置、随时可改热生效(LLM/embedding 的 key、安全白黑名单、Agent 参数)

设计:
  - 内存缓存 _cache:首次 get() 从磁盘加载,之后读缓存
  - update(partial):深合并(只改传进来的字段)→ 写回磁盘 → 刷新缓存
  - 缺文件/缺字段用 DEFAULTS 兜底(向后兼容:以后加新字段,老 config.json 不会因缺字段报错)
"""

import logging
import threading
from copy import deepcopy
from pathlib import Path

from app.config import settings
from app.services import atomic_json

logger = logging.getLogger(__name__)

# 默认配置:api_key/model 留空 → is_llm_configured() 为 False,前端引导先进设置页
DEFAULTS: dict = {
    "llm": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": "",
        "model": "",
    },
    "embedding": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
        "model": "text-embedding-v3",
    },
    "security": {
        "workspace_dir": "",
        "kb_dir": "",
        "cmd_whitelist": ["grep", "ls", "cat", "git status", "find", "wc"],
        "cmd_blacklist": ["rm -rf", "sudo", "curl", "wget", "mkfs", "dd"],
    },
    "agent": {"max_iters": 10, "temperature": 0.7},
}

_cache: dict | None = None
_lock = threading.Lock()


def _config_path() -> Path:
    # 每次从 settings 现取,便于测试用 monkeypatch 换 data_dir
    return Path(settings.data_dir) / "config.json"


def _deep_merge(base: dict, patch: dict) -> dict:
    """深合并:嵌套 dict 递归合并,而非整段替换。
    类比 JS:要的是 lodash.merge,而不是 Object.assign / {...a,...b} 那种浅合并。"""
    result = deepcopy(base)
    for key, value in patch.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load() -> dict:
    """从磁盘读;不存在/坏了用 DEFAULTS。读到的再与 DEFAULTS 深合并,补齐缺失字段(向后兼容)。"""
    raw = atomic_json.read_json(_config_path(), None)
    if raw is None:
        return deepcopy(DEFAULTS)
    return _deep_merge(DEFAULTS, raw)


def get() -> dict:
    """返回当前配置(带缓存)。返回副本,防外部误改缓存。"""
    global _cache
    if _cache is None:
        _cache = load()
    return deepcopy(_cache)


def update(partial: dict) -> dict:
    """深合并 partial → 写回磁盘 → 刷新缓存。返回更新后的完整配置。
    整段加锁保证读-改-写原子(单用户也可能并发几个请求)。"""
    global _cache
    with _lock:
        current = _cache if _cache is not None else load()
        merged = _deep_merge(current, partial)
        # 原子写抽到 atomic_json 复用:写 .tmp 途中崩溃只会写坏 .tmp,目标 config.json
        # 要么旧内容、要么新内容,绝不会被读到"写了一半"的残缺 JSON。
        atomic_json.write_json_atomic(_config_path(), merged)
        _cache = merged
        # 只记录改了哪些分组,绝不打印字段值(避免泄露 api_key)
        logger.info("配置更新: sections=%s", list(partial.keys()))
        return deepcopy(merged)


def is_llm_configured() -> bool:
    """LLM 三要素是否齐全 —— 前端首启引导用它判断要不要强制进设置页。"""
    llm = get()["llm"]
    return bool(llm.get("base_url") and llm.get("api_key") and llm.get("model"))


def _reset_cache() -> None:
    """仅测试用:清空缓存,让下次 get() 重新从磁盘加载。"""
    global _cache
    _cache = None
