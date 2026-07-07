from app.services import loaders


def test_load_txt(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("纯文本内容", encoding="utf-8")
    doc = loaders.load_document(p, source="a.txt")
    assert doc.text == "纯文本内容"
    assert doc.source == "a.txt"


def test_load_md(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("# 标题\n正文", encoding="utf-8")
    doc = loaders.load_document(p, source="b.md")
    assert "正文" in doc.text


def test_load_unknown_ext_as_text(tmp_path):
    # 代码文件等未登记扩展名 → 默认 read_text
    p = tmp_path / "c.py"
    p.write_text("print('hi')", encoding="utf-8")
    doc = loaders.load_document(p, source="c.py")
    assert "print" in doc.text


def test_pdf_dispatches_to_pdf_loader(tmp_path, monkeypatch):
    # 不造真 PDF:mock 掉映射表里的 .pdf 抽取器,只验证 .pdf 走到它
    # (也让测试不依赖 pypdf 装没装)。
    # 注意:要 patch _LOADERS[".pdf"] 而非 loaders._load_pdf ——
    # _LOADERS 在模块导入时就把原函数对象存进字典了,改模块属性名不影响字典里的旧引用。
    p = tmp_path / "d.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setitem(loaders._LOADERS, ".pdf", lambda path: "PDF抽出的文字")
    doc = loaders.load_document(p, source="d.pdf")
    assert doc.text == "PDF抽出的文字"
    assert doc.source == "d.pdf"
