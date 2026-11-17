# pdf-book-linker

[English](README.md) | 日本語

自分でスキャンした本の PDF に、**目次・索引・本文中の「○ページ参照」から該当ページへ飛べる内部リンク**と、**階層つきのしおり（PDF アウトライン）**、**章を行き来する薄いナビボタン**を追加するローカルツールです。

iPad の Goodnotes などで、電子書籍のように目次と本文と索引を行き来しながら読むことを想定しています。教科書・参考書・技術書・資格試験の本・攻略本など、目次やページ番号がある本ならジャンルを問いません。

- 処理はすべて手元の PC で行います。本のデータを外部に送信しません。
- 元の PDF は変更しません。結果は別ファイルに書き出します。
- ページ画像を再圧縮・再描画しません（リンク・しおり・ボタンの線を足すだけです）。

> [!IMPORTANT]
> **自分が正当に所有する本の、個人的な利用のためのツールです。** DRM やコピー防止を解除する機能はなく、そうした用途を想定していません。作成した PDF を他人に配布しないでください。

## できること

| 機能 | 内容 |
|---|---|
| 目次リンク | 目次の各行（タイトル部分と数字部分）を押すと、その章へ移動 |
| 章扉の小目次 | 各章（PART）の扉にある小目次にもリンク |
| 索引リンク | 「関数 125, 128, 132」の各数字をそれぞれ別のリンクに |
| 本文のページ参照 | 「○ページ参照」「45頁」「p.12」「pp.12-14」、英語の「page 12」「pages 12-14」などを該当ページへ |
| しおり | 前付け・章・索引見出しを階層化した PDF アウトライン |
| ナビボタン | ←← 目次 / ← 章の先頭 / → 次の章 / →→ 索引。本ごとに余白を測って配置し、明るいページには濃い線、暗いページには白い線で描く |
| ページ対応 | 印刷されたページ番号と PDF のページ番号の対応を自動で推定 |
| 検証 | 画像が変わっていないこと、リンク先が存在することなどを自動点検 |

ナビボタンの ← は、章の途中では今の章の先頭へ、章扉では一つ前の章へ移動します。

## 必要なもの

- Windows 11 で開発・確認しています（macOS / Linux は未確認ですが、OS 依存の部分は分岐してあります）
- Python 3.12 以上と [uv](https://docs.astral.sh/uv/)
- **文字層（OCR 済みのテキスト）がある PDF**。スキャンしただけで文字が選択できない PDF は、先に OCR をかけてください（このツールは全文 OCR をしません）。
- 任意：[Tesseract](https://github.com/tesseract-ocr/tesseract)（日本語データ `jpn` 付き）。目次の見出しや数字だけ OCR が抜けている本の補助に使います。なくても動きます。

## インストール

```bash
git clone https://github.com/kotatsu-coder/pdf-book-linker.git
cd pdf-book-linker
uv sync
uv run pdf-book-linker --help
```

Windows の PowerShell で日本語が文字化けする場合は、先に `$env:PYTHONIOENCODING = 'utf-8'` を実行してください。

## まず試す（デモ本）

著作権の問題がない架空の攻略本 PDF を作って、一通り試せます。`--lang en` を付けると英語版になります。

```bash
uv run python tools/make_demo_book.py -o work/demo.pdf
uv run pdf-book-linker build work/demo.pdf -o output/demo_linked.pdf --report-root output/report
```

`output/demo_linked.pdf` を PDF ビューアや Goodnotes で開き、目次・索引・ボタンを押してみてください。`output/report/` に判定の報告とプレビュー画像が出ます。

## 自分の本で使う

### 1冊を処理する

```bash
uv run pdf-book-linker build "本.pdf" -o "output/本_linked.pdf" --report-root output/report
```

目次・索引・章扉・ページ対応・ナビの位置を自動で判定し、リンク付き PDF を作り、検証まで行います。結果は `output/report/<本の名前>/` の `autoconfig_report.md` とプレビュー PNG で確認できます。

### フォルダ内の本をまとめて判定する

```bash
uv run pdf-book-linker batch "本のフォルダ" --report-root output/report --work-root work
```

サブフォルダを含む全 PDF を1冊ずつ判定し、`output/report/summary.md`（と Excel で開ける `summary.csv`）に、出来の悪い順の一覧を出します。**この段階では PDF は作りません。** 一覧で `ok` になった本、または補正した本から `build` で作ってください。

- 途中で止めても、同じコマンドで続きから再開できます（全部やり直すときは `--force`）。
- メモリに余裕があれば `--jobs 2`〜`4` で並列にできます。

一覧の状態の意味は次のとおりです。

| 状態 | 意味 |
|---|---|
| `ok` | 自動判定の指標が基準を満たした（それでも中身の確認はおすすめします） |
| `needs review` | 一部の指標が基準未満。報告とプレビューを見て、必要なら補正 |
| `OCR required` | 文字層がない、または足りない。先に OCR が必要 |
| `failed` | 処理中のエラー |

一致率などの数字は、ツールが自分で確かめた目安で、正解率ではありません。

### うまくいかない本を補正する

自動判定が外れた項目だけを、小さな JSON で上書きできます。ページは **0 始まりの PDF のページ番号**です（PDF ビューアで「1 ページ目」と表示されるページが 0）。

```json
{
  "toc_pages": [4, 5],
  "index_pages": [233, 234, 235],
  "index": {"columns": 2},
  "nav": {"position": "bottom-outer", "size": 0.055, "contrast": 4.5}
}
```

```bash
uv run pdf-book-linker build "本.pdf" -o "output/本_linked.pdf" --config "本.override.json"
```

`batch` で使う場合は、`--override-root <フォルダ>` を指定し、`<本のslug>.json` という名前で置きます（slug は報告フォルダの名前と同じです）。

よく使う補正：

| やりたいこと | 書き方の例 |
|---|---|
| 目次・索引のページを指定 | `"toc_pages": [4,5]`、`"index_page_range": [400, 420]` |
| 索引の段数を指定 | `"index": {"columns": 3}` |
| 印刷ページ番号との対応を指定 | `"pagemap": {"segments": [{"pdf_index0_start": 8, "pdf_index0_end": 218, "print_start": 1, "style": "arabic"}]}` |
| ナビの位置 | `"nav": {"position": {"left": "bottom-outer", "right": "outer-edge"}}`（`bottom-centre` / `bottom-outer` / `top-outer` / `outer-edge`） |
| ナビの大きさ | `"nav": {"size": 0.05}`（表示時のページ幅に対する比率。既定 0.055 で、11インチ iPad なら約9mm） |
| 余白が狭いページでの最小サイズ | `"nav": {"min_size": 0.04}`（既定 0.045、約7mm） |
| 見た目 | `"nav": {"style": "pill"}`（電子書籍アプリの操作ボタン風の半透明の角丸。既定 `"line"` は薄い矢印だけ） |
| 余白がまったくないページ | `"nav": {"crowded": "overlay"}`（不透明な下地を敷いて印字の上に描く。既定 `"skip"` は省略） |
| ナビの見やすさ | `"nav": {"contrast": 4.5}`（大きいほどはっきり、小さいほど控えめ。既定 3.0） |
| ナビの色・濃さを固定 | `"nav": {"color": "#1a3c8c", "opacity": 0.4}`（どちらも既定は `"auto"`） |

すべての項目と判定の仕組みは [docs/reference.md](docs/reference.md) にあります。

## 限界

- **Goodnotes での動作は保証しません。** 作者の環境では目次などの基本的なリンクが Goodnotes で動くことを確認していますが、端末やアプリの版による違い、手書き後や再エクスポート後の挙動は確認しきれていません。確認方法は [docs/goodnotes_checklist.md](docs/goodnotes_checklist.md) にあります。
- Goodnotes には「戻る」機能がないため、リンクで飛んだ後に元の場所へ戻るボタンは作れません。
- 判定は既存の文字層に頼ります。OCR の誤認識、縦書き、見開きスキャン、特殊な段組の本では補正が必要になることがあります。
- 図・表番号（「図3-2」など）へのリンクはまだありません。
- 上下巻で共通の索引のように、別の PDF を指す項目にはリンクできません。
- 低信頼度の候補はリンクにせず、報告で「要確認」として残します。

## 開発

```bash
uv run pytest -q
```

テストは合成データとデモ本だけを使います。実在の書籍データ（PDF・抽出テキスト・中間 JSON）はリポジトリに入れないでください（`.gitignore` で `work/`・`output/`・`*.pdf` を除外しています）。設計は [docs/design.md](docs/design.md)、当初の要件は [docs/requirements.md](docs/requirements.md) を参照してください。

## ライセンス

[GNU AGPL v3.0](LICENSE)。PDF 処理に AGPL の [PyMuPDF](https://github.com/pymupdf/PyMuPDF) を使っているため、このツールも AGPL で公開しています。

本ツールは無保証です。作成した PDF の内容（リンク先の正しさを含む）は、利用者自身で確認してください。
