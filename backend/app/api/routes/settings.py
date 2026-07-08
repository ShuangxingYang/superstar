"""
routes/settings.py —— 配置读写 + 测试连接

GET  /api/settings        读当前配置(key 脱敏)
PUT  /api/settings        局部更新(只传要改的字段),返回更新后配置(脱敏)
POST /api/settings/test   用传入的 LLM 配置发一次最小请求,验证连通(存之前先验)
"""

import logging

from fastapi import APIRouter
from openai import OpenAI

from app.models import schemas
from app.services import config_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


def _drop_masked_keys(partial: dict) -> dict:
    """防御性保护:若某处传回含 *** 的脱敏 key,视为未改,丢弃,避免用掩码覆盖真 key。
    (现在 GET 回明文、前端正常回传真 key;此保护仍保留,以防前端某路径传了脱敏值。)"""
    for section in ("llm", "embedding"):
        sec = partial.get(section)
        if isinstance(sec, dict) and isinstance(sec.get("api_key"), str) and "***" in sec["api_key"]:
            sec.pop("api_key")
    return partial


@router.get("", response_model=schemas.AppConfig)
def get_settings() -> schemas.AppConfig:
    return schemas.to_config_response(config_store.get())


@router.put("", response_model=schemas.AppConfig)
def update_settings(update: schemas.ConfigUpdate) -> schemas.AppConfig:
    partial = update.model_dump(exclude_none=True)   # 丢掉没传的字段 → 天然是局部更新
    partial = _drop_masked_keys(partial)
    merged = config_store.update(partial)
    logger.info("配置已更新: sections=%s", list(partial.keys()))  # 只记分组名
    return schemas.to_config_response(merged)


@router.post("/test", response_model=schemas.TestConnectionResult)
def test_connection(req: schemas.TestConnectionRequest) -> schemas.TestConnectionResult:
    """临时建客户端发一次最小请求,验证 base_url/key/model 是否可用。按 kind 分流:
    llm 走 chat.completions;embedding 走 embeddings(两种服务的探活接口不同)。"""
    try:
        client = OpenAI(api_key=req.api_key, base_url=req.base_url or None, timeout=20)
        if req.kind == "embedding":
            client.embeddings.create(model=req.model, input="ping")
        else:
            # 用流式探活,与对话循环(loop.py)一致:部分网关(如 tokenhub codex/v1)
            # 只接受 stream=True,非流式会被 400「Stream must be set to true」拒。
            stream = client.chat.completions.create(
                model=req.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                stream=True,
            )
            try:
                for _ in stream:      # 消费一个分片即证明连通
                    break
            finally:
                getattr(stream, "close", lambda: None)()   # 尽早关闭流
        return schemas.TestConnectionResult(ok=True)
    except Exception as e:  # noqa: BLE001 - 错误信息透传给前端展示
        logger.warning("测试连接失败(%s): %s", req.kind, type(e).__name__)  # 不打印 key
        return schemas.TestConnectionResult(ok=False, error=str(e))
