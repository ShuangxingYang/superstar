from app.services import chunker


def test_short_text_single_chunk():
    # 短于 chunk_size → 整段一块
    assert chunker.split("hello world", 100, 10) == ["hello world"]


def test_empty_text_returns_empty():
    assert chunker.split("", 100, 10) == []
    assert chunker.split("   ", 100, 10) == []


def test_splits_on_paragraph_boundary():
    # 两段各 30 字符,chunk_size=40 → 应在段落边界(\n\n)切开,不硬切
    p1 = "第一段" * 10   # 30 字符
    p2 = "第二段" * 10
    text = f"{p1}\n\n{p2}"
    chunks = chunker.split(text, 40, 0)  # overlap=0 便于验证纯边界切结果
    assert len(chunks) == 2
    # 核心:块必须落在段落边界上,而不是被定长从中间斩断。
    # 若退化成纯定长(每 40 字符一刀),第一块会含第二段开头,这里就会失败。
    assert chunks[0].strip() == p1
    assert chunks[1].strip() == p2


def test_overlap_between_chunks():
    # 无自然边界的长文本 → 硬切 + 重叠。相邻块块首含前块尾部 overlap 字符
    text = "A" * 100
    chunks = chunker.split(text, 30, 10)
    assert len(chunks) > 1
    # 重叠语义:每块前面补了前一块尾部 overlap 字符,故块长可比 chunk_size 长 overlap
    assert all(len(c) <= 30 + 10 for c in chunks)
    # 还原:第一块原样 + 后续块去掉 overlap 前缀 == 原文(证明只补尾巴、没丢字没重切)
    assert "".join([chunks[0]] + [c[10:] for c in chunks[1:]]) == text


def test_overlap_preserves_boundary_chunks():
    # 关键回归:开了 overlap 也不能把边界切的成果拼回去重切成定长。
    # 三段,每段 30 字符,chunk_size=40 → 三块各含一段;加 overlap 后每块块首带前块尾巴,
    # 但每块的「主体」仍是完整的一段,不会跨段错位。
    segs = ["甲" * 30, "乙" * 30, "丙" * 30]
    text = "\n\n".join(segs)
    chunks = chunker.split(text, 40, 5)
    assert len(chunks) == 3
    # 首块无前缀,应正好是第一段
    assert chunks[0].strip() == segs[0]
    # 后续块去掉 overlap 前缀后,主体是对应的整段(证明边界没被 overlap 破坏)
    assert chunks[1][5:].strip() == segs[1]
    assert chunks[2][5:].strip() == segs[2]


def test_long_no_boundary_hard_split():
    # 一个超长无分隔符 token 也能被硬切,不死循环
    chunks = chunker.split("X" * 250, 100, 0)
    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [100, 100, 50]
