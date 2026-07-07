"""
loaders.py —— 文档加载适配器层(业界通行的三层骨架之第一层)

每种来源一个 loader,统一吐 Document(text + source),下游(切块/embed/检索)
与来源彻底解耦。只做当下需要的格式:.pdf 走 pypdf 抽文字层,其余 read_text。
以后加飞书 API loader / OCR,只在 _LOADERS 映射表注册一项,下游不动。
"""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Document:
    text: str
    source: str


def _load_pdf(path: Path) -> str:
    """纯代码抽 PDF 文字层(不依赖 LLM)。扫描件/图片型 PDF 抽不出字,返回空。

    pypdf 是懒加载(import 放函数内):没装 pypdf 也能 import 本模块、跑其余 loader,
    只有真去抽 PDF 时才需要它(Task 9 才装)。
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = "\n".join(pages)
    # 简单清洗:压掉连续 3+ 空行为 2 行,去首尾空白
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# 扩展名 → 抽取器映射;未命中默认 read_text
_LOADERS = {".pdf": _load_pdf}


def load_document(path: Path, source: str) -> Document:
    loader = _LOADERS.get(path.suffix.lower())
    if loader is not None:
        text = loader(path)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    # 只记 source 和字符数,绝不打印文档正文
    logger.info("加载文档: source=%s, chars=%d", source, len(text))
    return Document(text=text, source=source)
