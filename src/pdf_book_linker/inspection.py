"""ページ単位の PDF 構造検査。"""
import pymupdf as fitz


def inspect_pdf(path):
    with fitz.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("暗号化 PDF は未対応です")
        pages = []
        for page in doc:
            pages.append({"pdf_index0": page.number, "size_pt": [page.rect.width, page.rect.height],
                          "page_rect_pt": list(page.rect),
                          "rotation": page.rotation, "cropbox": list(page.cropbox),
                          "mediabox": list(page.mediabox),
                          "image_count": len(page.get_images(full=True)),
                          "text_char_count": len(page.get_text().strip()),
                          "has_text_layer": bool(page.get_text().strip()),
                          "link_count": len(page.get_links())})
        return {"schema_version": 1, "page_count": len(doc), "pages": pages,
                "outline": doc.get_toc(), "link_count": sum(p["link_count"] for p in pages)}
