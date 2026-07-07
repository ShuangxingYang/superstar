"""
chunker.py —— 递归字符切块(RAG 头号变量)

思路(同 LangChain RecursiveCharacterTextSplitter 内核):按分隔符优先级
["\\n\\n", "\\n", "。", " ", ""] 逐级找切点——尽量在段落边界切,切不动才退到
换行/句号/空格,最后("")硬切。相邻块保留 overlap 重叠,防答案落在接缝被割裂。

为什么不纯定长:M3 实测纯定长会把术语从中间斩断(召回仅 40%);按语义边界切召回明显更好。
"""

SEPARATORS = ["\n\n", "\n", "。", " ", ""]


def split(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks = _recursive_split(text, chunk_size, overlap, SEPARATORS)
    return _apply_overlap(chunks, overlap)


def _recursive_split(text: str, chunk_size: int, overlap: int, seps: list[str]) -> list[str]:
    """用当前分隔符把 text 切成"段",段太大就用下一级分隔符继续切,再把段贪心合并成块。"""
    sep = seps[0]
    rest = seps[1:]
    # 按当前分隔符切;sep=="" 表示无分隔符,退化为逐字符
    if sep == "":
        pieces = list(text)
    else:
        parts = text.split(sep)
        # 切完把分隔符补回(除最后一段),保留原文可还原性
        pieces = [p + sep for p in parts[:-1]] + [parts[-1]]

    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        # 单个 piece 就超长:先冲掉 buf,再用下一级分隔符拆这个 piece
        if len(piece) > chunk_size:
            if buf:
                chunks.append(buf)
                buf = ""
            if rest:
                chunks.extend(_recursive_split(piece, chunk_size, overlap, rest))
            else:
                chunks.extend(_hard_split(piece, chunk_size))
            continue
        # 贪心累加:加上这个 piece 不超 chunk_size 就并入,否则先冲 buf
        if len(buf) + len(piece) <= chunk_size:
            buf += piece
        else:
            if buf:
                chunks.append(buf)
            buf = piece
    # 收尾冲最后一块。已知局限:贪心切块可能在此落下一个很短的孤儿尾块
    # (buf 近满时来个小 piece 就独立成块)。同 LangChain 内核的固有性质,不是 bug;
    # _apply_overlap 会给它补前块尾部 overlap 兜住语义。尾块合并(min_chunk_size)留二版,
    # 见 spec 4.2「已知局限」。
    if buf:
        chunks.append(buf)
    return chunks


def _hard_split(text: str, chunk_size: int) -> list[str]:
    """无任何分隔符的长串:按 chunk_size 步长硬切。"""
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def _apply_overlap(chunks: list[str], overlap: int) -> list[str]:
    """给相邻块加重叠:每块前面补上一块的尾部 overlap 个字符。

    关键:块本身保持边界对齐(_recursive_split 的成果),只在块首拼接
    前一块的尾巴,绝不把整段拼回去重切——那会退化成纯定长斩断,前功尽弃。
    每块因此可能比 chunk_size 略长(多出 overlap),这是重叠的正常代价。
    还原公式:chunks[0] + 后续块去掉 overlap 前缀 == 原文。
    """
    if overlap <= 0 or len(chunks) <= 1:
        return chunks
    result = [chunks[0]]
    for prev, cur in zip(chunks, chunks[1:]):
        result.append(prev[-overlap:] + cur)
    return result
