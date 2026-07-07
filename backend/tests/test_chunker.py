from app.services import chunker


def test_short_text_single_chunk():
    # 短于 chunk_size → 整段一块
    assert chunker.split("hello world", 100, 10) == ["hello world"]


def test_empty_text_returns_empty():
    assert chunker.split("", 100, 10) == []
    assert chunker.split("   ", 100, 10) == []


def test_splits_on_paragraph_boundary():
    # 两段各 ~30 字符,chunk_size=40 → 应在段落边界(\n\n)切开,不硬切
    p1 = "第一段" * 10   # 30 字符
    p2 = "第二段" * 10
    text = f"{p1}\n\n{p2}"
    chunks = chunker.split(text, 40, 5)
    assert len(chunks) >= 2
    # 每块不超过 chunk_size 太多(边界切,允许略超但不离谱)
    assert all(len(c) <= 40 + 5 for c in chunks)


def test_overlap_between_chunks():
    # 无自然边界的长文本 → 硬切 + 重叠。相邻块尾首应有重叠
    text = "A" * 100
    chunks = chunker.split(text, 30, 10)
    assert len(chunks) > 1
    # 每块长度约 30
    assert all(len(c) <= 30 for c in chunks)
    # 重叠:第 2 块起点应回退 overlap,总覆盖 = 完整还原
    assert "".join([chunks[0]] + [c[10:] for c in chunks[1:]]) == text


def test_long_no_boundary_hard_split():
    # 一个超长无分隔符 token 也能被硬切,不死循环
    chunks = chunker.split("X" * 250, 100, 0)
    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [100, 100, 50]
