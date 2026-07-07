"""
rag_store.py —— 检索设施(收敛 M3 散落 7 份的 embed + Qdrant + rerank)

模块级函数(非 class:无跨调用共享内存状态)。客户端按 llm.py 那套模块级缓存。
集合管理三坑:建/判复用(绝不自动删)、维度漂移报错(不偷删)、连不上包装成 RagStoreError。
"""
import hashlib
import logging

from openai import OpenAI

from app.config import settings
from app.services import chunker, config_store, loaders

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


def _point_id(source: str, idx: int) -> int:
    """source+块序号 → 稳定正整数 id。重灌同文档同块=同 id(upsert 覆盖,不堆积)。"""
    h = hashlib.md5(f"{source}#{idx}".encode()).hexdigest()
    return int(h[:15], 16)   # 取 60 bit,稳妥落在 Qdrant 支持的无符号整数范围


def index_document(path, source: str) -> dict:
    """loaders 取文本 → chunker 切块 → embed 每块 → upsert。返回 {source, chunks}。"""
    from qdrant_client.models import PointStruct

    _ensure_collection()
    doc = loaders.load_document(path, source)
    if not doc.text.strip():
        logger.warning("文档没抽到文本: source=%s", source)
        return {"source": source, "chunks": 0}
    rag = config_store.get()["rag"]
    pieces = chunker.split(doc.text, rag["chunk_size"], rag["overlap"])
    points = [
        PointStruct(id=_point_id(source, i), vector=_embed(piece),
                    payload={"text": piece, "source": source})
        for i, piece in enumerate(pieces)
    ]
    _get_qdrant().upsert(collection_name=COLLECTION, points=points)
    logger.info("灌库完成: source=%s, chunks=%d", source, len(points))
    return {"source": source, "chunks": len(points)}


def _scroll_all() -> list:
    """拉集合里全部点(payload,不要向量)。文档量小,一次 scroll 够。"""
    client = _get_qdrant()
    if not client.collection_exists(COLLECTION):
        return []
    points, _ = client.scroll(collection_name=COLLECTION, limit=10000, with_payload=True, with_vectors=False)
    return points


def list_documents() -> list[dict]:
    """按 source 聚合已灌文档 → [{source, chunks}]。"""
    counts: dict[str, int] = {}
    for pt in _scroll_all():
        src = pt.payload.get("source", "?")
        counts[src] = counts.get(src, 0) + 1
    return [{"source": s, "chunks": n} for s, n in sorted(counts.items())]


def delete_document(source: str) -> int:
    """删掉某 source 的所有块,返回删除数。"""
    ids = [pt.id for pt in _scroll_all() if pt.payload.get("source") == source]
    if ids:
        _get_qdrant().delete(collection_name=COLLECTION, points_selector=ids)
    logger.info("删除文档: source=%s, chunks=%d", source, len(ids))
    return len(ids)


def rebuild() -> dict:
    """显式清空重建:删集合 → 重扫 kb_dir 全部文件重灌。返回汇总。"""
    from pathlib import Path

    client = _get_qdrant()
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    _ensure_collection()
    kb_dir = config_store.get()["security"].get("kb_dir") or ""
    docs = chunks = 0
    if kb_dir:
        root = Path(kb_dir)
        for fp in sorted(root.rglob("*")):
            if fp.is_file():
                r = index_document(fp, source=str(fp.relative_to(root)))
                docs += 1
                chunks += r["chunks"]
    logger.info("重建完成: documents=%d, chunks=%d", docs, chunks)
    return {"documents": docs, "chunks": chunks}


def stats() -> dict:
    docs = list_documents()
    return {"documents": len(docs), "chunks": sum(d["chunks"] for d in docs), "dimension": _dimension()}


def _reset() -> None:
    """仅测试用:清 embedding 客户端缓存。"""
    global _embed_client, _embed_key
    _embed_client = None
    _embed_key = None
