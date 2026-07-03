"""
services/llm.py —— 动态 LLM 客户端工厂(替代 agent-study 写死读 env 的 build_client)

关键:base_url/api_key/model 来自 config_store 的当前配置,配置页改完即时生效。
客户端按 (base_url, api_key) 缓存:只有它俩变了才重建;换 model 无需重建(model 每次现取)。
"""

import logging

from openai import OpenAI

from app.services import config_store

logger = logging.getLogger(__name__)

_client: OpenAI | None = None
_client_key: tuple[str, str] | None = None


def get_llm_client() -> tuple[OpenAI, str]:
    """读当前配置返回 (client, model)。未配置直接报错,别让后面莫名失败。"""
    llm = config_store.get()["llm"]
    base_url = llm.get("base_url") or ""
    api_key = llm.get("api_key") or ""
    model = llm.get("model") or ""
    if not api_key or not model:
        raise RuntimeError("LLM 未配置:请在设置页填写 api_key 与 model")

    global _client, _client_key
    key = (base_url, api_key)
    if _client is None or _client_key != key:
        _client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=60)
        _client_key = key
        logger.info("重建 LLM 客户端: base_url=%s", base_url or "(默认)")  # 不打印 key
    return _client, model


def _reset_client() -> None:
    """仅测试用:清空客户端缓存。"""
    global _client, _client_key
    _client = None
    _client_key = None
