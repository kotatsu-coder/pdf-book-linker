"""手動仕様の解決と GoTo / PDF アウトラインの追加。"""
import math
import os
from pathlib import Path
import tempfile
import pymupdf as fitz


def safe_output(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or (output.exists() and os.path.samefile(source, output)):
        raise ValueError("入力 PDF と同じファイルには保存できません")
    if output.exists():
        raise ValueError("出力ファイルが既に存在します。別の出力名を指定してください")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def save_pdf(doc, output):
    # 画像ストリームの再圧縮・最適化・再描画を行わない。
    handle, temporary = tempfile.mkstemp(suffix=".pdf", dir=Path(output).parent)
    os.close(handle)
    try:
        doc.save(temporary, garbage=0, deflate=False)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


def entries(spec):
    return spec if isinstance(spec, list) else spec["entries"]


def resolve(doc, spec, pagemap):
    resolved, review = [], []
    previous = 0
    for position, item in enumerate(entries(spec)):
        if item.get("status", "approved") != "approved":
            review.append(item)
            continue
        level = item["level"]
        if type(level) is not int or level < 1 or level > previous + 1:
            raise ValueError(f"目次階層が不正です: 項目 {position}")
        previous = level
        if not item["title"].strip():
            raise ValueError("目次タイトルが空です")
        source = item["source"]
        source_index = source["pdf_index0"]
        target_index = pagemap.print_to_pdf(item["print_page"])
        if not (0 <= source_index < len(doc) and 0 <= target_index < len(doc)):
            raise ValueError("リンク元またはリンク先のページが範囲外です")
        page = doc[source_index]
        if ("rect" in source) == ("search" in source):
            raise ValueError("source には rect / search の一方だけを指定してください")
        if "search" in source:
            hits = page.search_for(source["search"])
            if len(hits) != 1:
                raise ValueError(f"検索文字列は一意である必要があります: {source['search']} ({len(hits)} 件)")
            rect = hits[0]
        else:
            rect = fitz.Rect(source["rect"])
        bounds = fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        if not all(math.isfinite(v) for v in rect) or rect.is_empty or not bounds.contains(rect):
            raise ValueError("リンク領域が CropBox の範囲外です")
        if not any((rect & fitz.Rect(w[:4])).get_area() > 0 for w in page.get_text("words")):
            raise ValueError(f"リンク領域に既存テキストがありません: {item['title']}")
        dest = item.get("dest", "top")
        y = 0.0 if dest == "top" else float(dest["y"])
        if not math.isfinite(y) or not 0 <= y < doc[target_index].cropbox.height:
            raise ValueError("リンク先 y が範囲外です")
        resolved.append({"title": item["title"], "level": level,
                         "source_pdf_index0": source_index, "rect": list(rect),
                         "target_pdf_index0": target_index, "y": y})
    keys = [(r["source_pdf_index0"], tuple(r["rect"])) for r in resolved]
    if len(set(keys)) != len(keys):
        raise ValueError("同じ領域のリンク候補が重複しています")
    return resolved, review


def apply_pdf(source, output, spec, pagemap):
    output = safe_output(source, output)
    with fitz.open(source) as doc:
        if doc.needs_pass:
            raise ValueError("暗号化 PDF は未対応です")
        resolved, review = resolve(doc, spec, pagemap)
        # API の版による回転座標の違いを避け、編集時だけ全ページを回転 0 にする。
        # CropBox は変更しない。保存前に元の回転へ戻す。
        rotations = [p.rotation for p in doc]
        for page in doc:
            page.set_rotation(0)
        for item in resolved:
            page = doc[item["source_pdf_index0"]]
            rectangle = fitz.Rect(item["rect"])
            for link in page.get_links():
                if (rectangle & link["from"]).get_area() > 0:
                    raise ValueError("既存リンクと新しいリンクが重なります")
            page.insert_link({"kind": fitz.LINK_GOTO, "from": rectangle,
                              "page": item["target_pdf_index0"],
                              "to": fitz.Point(0, item["y"]), "zoom": 0})
        doc.set_toc([[r["level"], r["title"], r["target_pdf_index0"] + 1,
                      {"kind": fitz.LINK_GOTO, "page": r["target_pdf_index0"],
                       "to": fitz.Point(0, r["y"]), "zoom": 0}] for r in resolved])
        # set_toc の CropBox オフセット処理は版によって異なる。
        # 階層作成後、正式な PDF 座標変換で /XYZ を明示し原点ずれを防ぐ。
        for row, item in zip(doc.get_toc(simple=False), resolved):
            target = doc[item["target_pdf_index0"]]
            point = fitz.Point(0, item["y"]) * ~target.transformation_matrix
            doc.xref_set_key(row[3]["xref"], "A",
                             f"<</S/GoTo/D[{target.xref} 0 R /XYZ {point.x:.8f} {point.y:.8f} 0]>>")
        for index, rotation in enumerate(rotations):
            doc[index].set_rotation(rotation)
        save_pdf(doc, output)
    return {"links_added": len(resolved), "outline_count": len(resolved),
            "review_count": len(review), "resolved": resolved, "review": review}
