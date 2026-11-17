import hashlib
import pymupdf as fitz
import pytest
from pdf_book_linker.linking import apply_pdf
from pdf_book_linker.pagemap import PageMap
from pdf_book_linker.verification import verify_pdf


@pytest.fixture
def sample(tmp_path):
    source = tmp_path / "source.pdf"
    with fitz.open() as doc:
        for _ in range(3):
            page = doc.new_page(width=400, height=500)
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 5, 5), False)
            pixmap.clear_with(150)
            page.insert_image(fitz.Rect(80, 100, 200, 220), pixmap=pixmap)
            page.insert_text((70, 80), "Chapter One")
        doc.save(source)
    spec = {"entries": [{"title": "第1章", "level": 1, "print_page": "1",
                         "source": {"pdf_index0": 0, "search": "Chapter One"}, "dest": {"y": 45}},
                        {"title": "第1節", "level": 2, "print_page": "2",
                         "source": {"pdf_index0": 1, "search": "Chapter One"}, "dest": "top"}]}
    mapping = PageMap({"segments": [{"pdf_index0_start": 1, "pdf_index0_end": 2, "print_start": 1}]})
    return source, spec, mapping


def test_links_and_images(sample, tmp_path):
    source, spec, mapping = sample
    before = hashlib.sha256(source.read_bytes()).digest()
    output = tmp_path / "linked.pdf"
    apply_pdf(source, output, spec, mapping)
    report = verify_pdf(output, source, spec, mapping)
    assert report["passed"], report["errors"]
    assert report["link_count"] == report["outline_count"] == 2
    assert report["images_unchanged"]
    assert all(check["image_count"] == 1 for check in report["image_checks"])
    assert hashlib.sha256(source.read_bytes()).digest() == before


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotation_and_cropbox(sample, tmp_path, rotation):
    source, spec, mapping = sample
    cropped = tmp_path / "cropped.pdf"
    with fitz.open(source) as doc:
        for page in doc:
            page.set_cropbox(fitz.Rect(30, 25, 360, 460))
            page.set_rotation(rotation)
        doc.save(cropped)
    output = tmp_path / "rotated.pdf"
    apply_pdf(cropped, output, spec, mapping)
    report = verify_pdf(output, cropped, spec, mapping)
    assert report["passed"], report["errors"]
    # 独立に描画後の矩形とリンクの表示座標の重なりを確認。
    with fitz.open(output) as doc:
        page = doc[0]
        expected = page.search_for("Chapter One")[0] * page.rotation_matrix
        assert (page.get_links()[0]["from"] & expected).get_area() > 0


def test_pagemap():
    mapping = PageMap({"segments": [
        {"pdf_index0_start": 0, "pdf_index0_end": 3, "print_start": 3, "style": "roman"},
        {"pdf_index0_start": 5, "pdf_index0_end": 10, "print_start": 1, "style": "arabic"}]})
    assert mapping.print_to_pdf("ⅳ") == 1
    assert mapping.print_to_pdf("３") == 7
    assert mapping.pdf_to_print(0) == "iii"
    assert mapping.pdf_to_print(4) is None
    with pytest.raises(ValueError):
        mapping.print_to_pdf("i")
    with pytest.raises(ValueError):
        PageMap({"segments": [{"pdf_index0_start": 1, "pdf_index0_end": 0, "print_start": 1}]})


def test_broken_link(sample, tmp_path):
    source, spec, mapping = sample
    linked, broken = tmp_path / "linked.pdf", tmp_path / "broken.pdf"
    apply_pdf(source, linked, spec, mapping)
    with fitz.open(linked) as doc:
        page = doc[0]
        link = page.get_links()[0]
        link["page"] = 0  # 存在するが仕様と異なる誤リンク。
        page.update_link(link)
        doc.save(broken)
    report = verify_pdf(broken, source, spec, mapping)
    assert not report["passed"]
    assert any("リンク先" in error for error in report["errors"])


def test_protection_and_review(sample, tmp_path):
    source, spec, mapping = sample
    with pytest.raises(ValueError):
        apply_pdf(source, source, spec, mapping)
    spec["entries"][1]["status"] = "review"
    output = tmp_path / "review.pdf"
    report = apply_pdf(source, output, spec, mapping)
    assert report["links_added"] == 1 and report["review_count"] == 1
    with pytest.raises(ValueError):
        apply_pdf(source, output, spec, mapping)


def test_duplicate_and_missing_text(sample, tmp_path):
    source, spec, mapping = sample
    spec["entries"].append(spec["entries"][1])
    with pytest.raises(ValueError, match="重複"):
        apply_pdf(source, tmp_path / "duplicate.pdf", spec, mapping)
    spec["entries"].pop()
    spec["entries"][0]["source"] = {"pdf_index0": 0, "rect": [0, 0, 10, 10]}
    with pytest.raises(ValueError, match="テキスト"):
        apply_pdf(source, tmp_path / "empty.pdf", spec, mapping)
