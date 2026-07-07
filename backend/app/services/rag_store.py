"""
rag_store.py —— 检索设施(收敛 M3 散落 7 份的 embed + Qdrant + rerank)

模块级函数(非 class:无跨调用共享内存状态)。客户端按 llm.py 那套模块级缓存。
集合管理三坑:建/判复用(绝不自动删)、维度漂移报错(不偷删)、连不上包装成 RagStoreError。
"""
import logging

from openai import OpenAI

from app.config import settings
from app.services import config_store

logger = logging.getLogger(__name__)

COLLECTION = "superstar_kb"


class RagStoreError(Exception):
    """RAG 相关的可预期错误(连不上/维度不一致等),给用户友好提示用。"""


# ---- embedding 客户端(照 llm.py:按 (base_url, api_key) 缓存)----
_embed_client: OpenAI | None = None
_embed_key: tuple[str, str] | None = None


def _get_embed_client() -> tuple[OpenAI, str]:
    emb = config_store.get()["embedding"]
    base_url, api_key, model = emb.get("base_url") or "", emb.get("api_key") or "", emb.get("model") or ""
    if not api_key or not model:
        raise RagStoreError("embedding 未配置:请在设置页填写 embedding 的 api_key 与 model")
    global _embed_client, _embed_key
    key = (base_url, api_key)
    if _embed_client is None or _embed_key != key:
        _embed_client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=60)
        _embed_key = key
        logger.info("重建 embedding 客户端: base_url=%s", base_url or "(默认)")  # 不打印 key
    return _embed_client, model


def _embed(text: str) -> list[float]:
    client, model = _get_embed_client()
    resp = client.embeddings.create(model=model, input=[text], encoding_format="float")
    return resp.data[0].embedding


# ---- Qdrant 客户端 ----
def _get_qdrant():
    from qdrant_client import QdrantClient
    return QdrantClient(url=settings.qdrant_url, timeout=10)


def _dimension() -> int:
    return int(config_store.get()["embedding"]["dimension"])


def _ensure_collection() -> None:
    """集合不存在则按 (dimension, COSINE) 建;存在则校验维度一致;连不上包装报错。"""
    try:
        client = _get_qdrant()
        exists = client.collection_exists(COLLECTION)
    except (ConnectionError, OSError) as e:
        raise RagStoreError("知识库服务未启动,请先 docker start qdrant") from e
    except Exception as e:  # noqa: BLE001  qdrant 连接类异常五花八门,统一兜
        raise RagStoreError(f"连接知识库服务失败:{e}") from e

    want = _dimension()
    if not exists:
        # Distance/VectorParams 只有真建集合才用到,放这里懒加载:
        # 未装 qdrant_client 时也能 import 本模块、跑 mock 掉 _get_qdrant 的测试。
        from qdrant_client.models import Distance, VectorParams
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=want, distance=Distance.COSINE),
        )
        logger.info("建集合: %s (dim=%d, COSINE)", COLLECTION, want)
        return
    have = client.get_collection(COLLECTION).config.params.vectors.size
    if have != want:
        raise RagStoreError(
            f"知识库是用 {have} 维建的,当前 embedding 配置 {want} 维,不匹配。"
            f"请在设置页确认 embedding,或到知识库页「重建索引」。"
        )


def _reset() -> None:
    """仅测试用:清 embedding 客户端缓存。"""
    global _embed_client, _embed_key
    _embed_client = None
    _embed_key = None
