"""
services/llm.py —— 动态 LLM 客户端工厂(替代 agent-study 写死读 env 的 build_client)

关键:base_url/api_key/model 来自 config_store 的当前配置,配置页改完即时生效。
客户端按 (base_url, api_key) 缓存:只有它俩变了才重建;换 model 无需重建(model 每次现取)。

两个工厂,各自独立缓存:
- get_llm_client()      → AsyncOpenAI:主对话链路 + 子 Agent 用(可中断、可并发)
- get_sync_llm_client() → OpenAI:蒸馏 + 测试连接用(后台/非交互,同步最简)
"""

import logging

from openai import AsyncOpenAI, OpenAI

from app.services import config_store

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None
_client_key: tuple[str, str] | None = None

_sync_client: OpenAI | None = None
_sync_client_key: tuple[str, str] | None = None


def _read_llm_conf() -> tuple[str, str, str]:
    """读当前 LLM 配置,返回 (base_url, api_key, model);缺 api_key/model 直接报错。"""
    llm = config_store.get()["llm"]
    base_url = llm.get("base_url") or ""
    api_key = llm.get("api_key") or ""
    model = llm.get("model") or ""
    if not api_key or not model:
        raise RuntimeError("LLM 未配置:请在设置页填写 api_key 与 model")
    return base_url, api_key, model


def get_llm_client() -> tuple[AsyncOpenAI, str]:
    """读当前配置返回 (async client, model)。主对话链路 + 子 Agent 用。未配置直接报错。"""
    base_url, api_key, model = _read_llm_conf()

    global _client, _client_key
    key = (base_url, api_key)
    if _client is None or _client_key != key:
        _client = AsyncOpenAI(api_key=api_key, base_url=base_url or None, timeout=60)
        _client_key = key
        logger.info("重建 async LLM 客户端: base_url=%s", base_url or "(默认)")  # 不打印 key
    return _client, model


def get_sync_llm_client() -> tuple[OpenAI, str]:
    """读当前配置返回 (sync client, model)。蒸馏 + 测试连接用。未配置直接报错。"""
    base_url, api_key, model = _read_llm_conf()

    global _sync_client, _sync_client_key
    key = (base_url, api_key)
    if _sync_client is None or _sync_client_key != key:
        _sync_client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=60)
        _sync_client_key = key
        logger.info("重建 sync LLM 客户端: base_url=%s", base_url or "(默认)")  # 不打印 key
    return _sync_client, model


def _reset_client() -> None:
    """仅测试用:清空两套客户端缓存。"""
    global _client, _client_key, _sync_client, _sync_client_key
    _client = None
    _client_key = None
    _sync_client = None
    _sync_client_key = None
