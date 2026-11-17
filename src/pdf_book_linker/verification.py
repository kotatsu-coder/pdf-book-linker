"""出力を再オープンし、リンク・ページ・画像ストリームを検証する。"""
import hashlib
from pathlib import Path
import pymupdf as fitz
from .linking import resolve


def image_hashes(doc, page):
    result = []
    for image in page.get_images(full=True):
        # 主画像と soft mask の圧縮済みストリームを比較する。
        result.append([hashlib.sha256(doc.xref_stream_raw(xref)).hexdigest()
                       if xref else None for xref in image[:2]])
    return result


def close_rect(first, second):
    return all(abs(a - b) < .05 for a, b in zip(first, second))


def verify_pdf(output, source, spec, pagemap):
    errors, image_checks = [], []
    with fitz.open(source) as original, fitz.open(output) as result:
        expected, review = resolve(original, spec, pagemap)
        if len(original) != len(result):
            errors.append("ページ数が元 PDF と一致しません")
        for index in range(min(len(original), len(result))):
            before, after = original[index], result[index]
            if (before.rotation != after.rotation or before.cropbox != after.cropbox
                    or before.mediabox != after.mediabox):
                errors.append(f"ページ形状が変わっています: {index}")
            matched = image_hashes(original, before) == image_hashes(result, after)
            image_checks.append({"pdf_index0": index, "image_count": len(before.get_images()), "matched": matched})
            if not matched:
                errors.append(f"画像ストリームが変わっています: {index}")
        # get_links / get_toc の座標を編集時と同じ回転前の座標へ統一する。
        for doc in (original, result):
            for page in doc:
                page.set_rotation(0)
        links_by_page = [p.get_links() for p in result]
        source_count = sum(len(p.get_links()) for p in original)
        actual_count = sum(map(len, links_by_page))
        if actual_count != source_count + len(expected):
            errors.append("リンク数が仕様と一致しません")
        for index, links in enumerate(links_by_page):
            seen = set()
            for link in links:
                if link["kind"] != fitz.LINK_GOTO or not 0 <= link.get("page", -1) < len(result):
                    errors.append(f"無効な GoTo リンク: {index}")
                key = (tuple(round(v, 2) for v in link["from"]), link.get("page"), tuple(link.get("to", ())))
                if key in seen:
                    errors.append(f"重複リンク: {index}")
                seen.add(key)
        for item in expected:
            index = item["source_pdf_index0"]
            candidates = links_by_page[index] if index < len(result) else []
            matches = [link for link in candidates if close_rect(link["from"], item["rect"])]
            if len(matches) != 1:
                errors.append(f"リンク領域が一致しません: {item['title']}")
                continue
            link = matches[0]
            point = link.get("to", fitz.Point(-999, -999))
            if link.get("page") != item["target_pdf_index0"] or abs(point.y - item["y"]) > .05 or abs(point.x) > .05:
                errors.append(f"リンク先が仕様と一致しません: {item['title']}")
            if not any((link["from"] & fitz.Rect(w[:4])).get_area() > 0 for w in original[index].get_text("words")):
                errors.append(f"リンクが元テキストと重なりません: {item['title']}")
            # PDF 注釈の境界線幅が 0 であることも確認する。
            annotation = result.xref_object(link["xref"])
            if "/W 0" not in annotation and "/Border [ 0 0 0 ]" not in annotation:
                errors.append(f"不可視の境界線を確認できません: {item['title']}")
        expected_toc = [[r["level"], r["title"], r["target_pdf_index0"] + 1] for r in expected]
        if result.get_toc() != expected_toc:
            errors.append("アウトラインの件数・階層・タイトル・リンク先が仕様と一致しません")
        for row, item in zip(result.get_toc(simple=False), expected):
            point = row[3].get("to", fitz.Point(-999, -999))
            if abs(point.y - item["y"]) > .05 or abs(point.x) > .05:
                errors.append(f"アウトラインの移動先座標が一致しません: {item['title']}")
        report = {"passed": not errors, "errors": errors, "page_count": len(result),
                  "link_count": actual_count, "outline_count": len(result.get_toc()),
                  "review_count": len(review), "images_unchanged": all(p["matched"] for p in image_checks),
                  "image_checks": image_checks, "source_size_bytes": Path(source).stat().st_size,
                  "output_size_bytes": Path(output).stat().st_size}
        report["size_change_bytes"] = report["output_size_bytes"] - report["source_size_bytes"]
        return report
