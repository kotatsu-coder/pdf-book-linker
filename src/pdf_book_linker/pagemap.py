"""印刷ページラベルと 0 始まり PDF ページの対応。"""
import unicodedata


def roman(number):
    if not 1 <= number <= 3999:
        raise ValueError("ローマ数字は 1〜3999 にしてください")
    result = ""
    for value, token in [(1000,"m"),(900,"cm"),(500,"d"),(400,"cd"),(100,"c"),(90,"xc"),(50,"l"),(40,"xl"),(10,"x"),(9,"ix"),(5,"v"),(4,"iv"),(1,"i")]:
        while number >= value:
            result += token
            number -= value
    return result


def normalize(label):
    return unicodedata.normalize("NFKC", str(label)).strip()


class PageMap:
    def __init__(self, data):
        self.by_label = {}
        self.by_index = {}
        for segment in data["segments"]:
            start, end = segment["pdf_index0_start"], segment["pdf_index0_end"]
            if type(start) is not int or type(end) is not int or start < 0 or end < start:
                raise ValueError("ページ区間が不正です")
            style = segment.get("style", "arabic")
            if type(segment["print_start"]) is not int or segment["print_start"] < 1:
                raise ValueError("印刷ページの開始番号は正の整数にしてください")
            if style not in ("arabic", "roman", "ROMAN"):
                raise ValueError("ページ番号形式が不正です")
            for index in range(start, end + 1):
                number = segment["print_start"] + index - start
                label = str(number) if style == "arabic" else roman(number)
                if style == "ROMAN":
                    label = label.upper()
                label = normalize(segment.get("prefix", "") + label)
                if label in self.by_label or index in self.by_index:
                    raise ValueError("ページ対応が重複しています（章のリセットには prefix を使用）")
                self.by_label[label] = index
                self.by_index[index] = label

    def print_to_pdf(self, label):
        try:
            return self.by_label[normalize(label)]
        except KeyError as exc:
            raise ValueError(f"印刷ページが未定義です: {label}") from exc

    def pdf_to_print(self, index):
        return self.by_index.get(index)
