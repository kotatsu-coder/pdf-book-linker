"""Goodnotes 用の小さな抜粋と手動仕様の再対応。"""
import pymupdf as fitz
from .linking import entries, safe_output, save_pdf


def extract_pdf(source, output, indices, spec, pagemap):
    output = safe_output(source, output)
    if len(set(indices)) != len(indices):
        raise ValueError("抜粋ページが重複しています")
    mapping = {old: new for new, old in enumerate(indices)}
    selected = []
    for item in entries(spec):
        if item.get("status", "approved") != "approved":
            continue
        target = pagemap.print_to_pdf(item["print_page"])
        if item["source"]["pdf_index0"] in mapping and target in mapping:
            selected.append({**item, "source": {**item["source"],
                            "pdf_index0": mapping[item["source"]["pdf_index0"]]}})
    # 印刷ラベルをそのまま保持するため、対応ラベルごとに 1 ページ区間を作る。
    segments = []
    for item in selected:
        label = str(item["print_page"])
        new = mapping[pagemap.print_to_pdf(label)]
        if any(s["pdf_index0_start"] == new for s in segments):
            continue
        if not label.isdigit():
            raise ValueError("抜粋 CLI は現段階ではアラビア数字の印刷ページを対象とします")
        segments.append({"pdf_index0_start": new, "pdf_index0_end": new,
                         "print_start": int(label), "style": "arabic"})
    with fitz.open(source) as original, fitz.open() as result:
        for index in indices:
            if not 0 <= index < len(original):
                raise ValueError("抜粋ページが範囲外です")
            result.insert_pdf(original, from_page=index, to_page=index, links=False, annots=True)
        save_pdf(result, output)
    return {"entries": selected}, {"segments": segments}, {"pages": [{"source_pdf_index0": old, "extract_pdf_index0": new} for old, new in mapping.items()]}
