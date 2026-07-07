import sys
import types

import pytest

from app.config import settings
from app.services import config_store, rag_store


@pytest.fixture(autouse=True)
def fake_qdrant_models(monkeypatch):
    """注入假的 qdrant_client.models(Distance/VectorParams),让测试不依赖真库。

    qdrant_client 到 Task 9 才装;这里只需要 VectorParams 这个「size+distance 容器」
    和 Distance.COSINE 这个枚举值,用轻量替身顶上,建集合分支就能跑通。
    """
    mod = types.ModuleType("qdrant_client.models")

    class VectorParams:
        def __init__(self, size, distance):
            self.size = size
            self.distance = distance

    class Distance:
        COSINE = "Cosine"

    class PointStruct:
        def __init__(self, id, vector, payload):
            self.id = id
            self.vector = vector
            self.payload = payload

    mod.VectorParams = VectorParams
    mod.Distance = Distance
    mod.PointStruct = PointStruct
    monkeypatch.setitem(sys.modules, "qdrant_client.models", mod)
    yield


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    rag_store._reset()
    config_store.update({"embedding": {"api_key": "sk-x", "model": "text-embedding-v3", "dimension": 1024}})
    yield
    config_store._reset_cache()
    rag_store._reset()


class _FakeQdrant:
    """假 Qdrant:记录调用,内存存点。"""

    def __init__(self):
        self.collections = {}   # name -> dim
        self.points = {}        # name -> list[point-like]
        self.upserted = []

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, collection_name, vectors_config):
        self.collections[collection_name] = vectors_config.size
        self.points[collection_name] = []

    def get_collection(self, name):
        dim = self.collections[name]

        class C:  # 仿 qdrant 返回结构 config.params.vectors.size
            class config:
                class params:
                    class vectors:
                        size = dim

        C.config.params.vectors.size = dim
        return C


def test_ensure_collection_creates_when_absent(cfg, monkeypatch):
    fake = _FakeQdrant()
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    rag_store._ensure_collection()
    assert rag_store.COLLECTION in fake.collections
    assert fake.collections[rag_store.COLLECTION] == 1024


def test_ensure_collection_dimension_mismatch_raises(cfg, monkeypatch):
    fake = _FakeQdrant()
    fake.collections[rag_store.COLLECTION] = 768   # 已存在且 768 维
    fake.points[rag_store.COLLECTION] = []
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    config_store.update({"embedding": {"dimension": 1024}})  # 当前配置 1024
    with pytest.raises(rag_store.RagStoreError, match="维"):
        rag_store._ensure_collection()


def test_qdrant_connection_error_wrapped(cfg, monkeypatch):
    def boom():
        raise ConnectionError("refused")
    monkeypatch.setattr(rag_store, "_get_qdrant", boom)
    with pytest.raises(rag_store.RagStoreError, match="未启动"):
        rag_store._ensure_collection()


def test_point_id_stable_and_distinct():
    a = rag_store._point_id("a.md", 0)
    assert a == rag_store._point_id("a.md", 0)          # 稳定:重灌同文档同块 → 同 id(覆盖非堆积)
    assert a != rag_store._point_id("a.md", 1)          # 不同块不同 id
    assert a != rag_store._point_id("b.md", 0)          # 不同文档不同 id


def test_index_document_flow(cfg, tmp_path, monkeypatch):
    fake = _FakeQdrant()
    fake.collections[rag_store.COLLECTION] = 1024   # 集合已存在(维度匹配),index 时不再新建
    fake.points[rag_store.COLLECTION] = []
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    monkeypatch.setattr(rag_store, "_embed", lambda t: [0.1] * 1024)

    # 捕获 upsert
    def fake_upsert(collection_name, points):
        fake.points.setdefault(collection_name, []).extend(points)
    fake.upsert = fake_upsert

    # 每段 750 字符,两段合计 ~1502 字符,chunk_size 默认 500 → 必切多块
    p = tmp_path / "doc.md"
    p.write_text("第一段" * 250 + "\n\n" + "第二段" * 250, encoding="utf-8")
    result = rag_store.index_document(p, source="doc.md")
    assert result["source"] == "doc.md"
    assert result["chunks"] >= 2
    assert len(fake.points[rag_store.COLLECTION]) == result["chunks"]
    # 每个 point 的 payload 带 text + source
    pt = fake.points[rag_store.COLLECTION][0]
    assert pt.payload["source"] == "doc.md"
    assert "text" in pt.payload
