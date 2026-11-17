# pdf-book-linker

English | [日本語](README.ja.md)

A local tool that turns the PDF of a book you scanned yourself into something you can navigate like an e-book. It adds:

- **clickable links** from the table of contents, the index, and in-text page references ("see p. 45") to the right page,
- a **hierarchical outline** (PDF bookmarks), and
- faint **navigation buttons** for moving between chapters.

It is meant for reading on an iPad in apps such as Goodnotes, jumping back and forth between the contents, the text and the index. Textbooks, reference books, technical books, exam guides, game guides — any book with a table of contents and page numbers.

- Everything runs on your own computer. No book data is sent anywhere.
- The source PDF is never modified; the result is written to a new file.
- Page images are not re-compressed or re-rendered. The tool only adds links, bookmarks and the button outlines.

> [!IMPORTANT]
> **This tool is for personal use with books you legally own.** It has no feature for removing DRM or copy protection and is not intended for that. Do not distribute the PDFs you create.

> [!NOTE]
> The tool has mainly been developed and tested with **Japanese books**. English is checked with an English demo book (see below), but real English books have not been tested much yet and may need manual overrides. The command-line messages and reports are currently in Japanese.

## Features

| Feature | What it does |
|---|---|
| Table of contents | Each TOC line (both the title and the page number) links to its chapter |
| Part mini-TOCs | Small contents lists on part title pages are linked too |
| Index | Each number in an entry like "function 125, 128, 132" becomes its own link |
| In-text page references | "p.12", "pp.12-14", "page 12", "pages 12-14" (and Japanese forms such as 「45頁」) link to the page |
| Outline | A PDF outline built from front matter, chapters and index headings |
| Navigation buttons | ←← contents / ← start of chapter / → next chapter / →→ index, placed in each book's free margin; dark lines on light pages, white lines on dark pages |
| Page mapping | Works out how printed page numbers map to PDF pages |
| Verification | Checks that page images are unchanged and that link targets exist |

The ← button goes to the start of the current chapter; on a part title page it goes to the previous part.

## Requirements

- Developed and tested on Windows 11. macOS and Linux have not been tested, but OS-specific code paths are handled.
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- **A PDF with a text layer (OCR already applied).** If you cannot select text in your PDF, run OCR first — this tool does not OCR whole books.
- Optional: [Tesseract](https://github.com/tesseract-ocr/tesseract) (with the `jpn` language data) to help with books whose TOC headings or numbers are missing from the text layer. Not required.

## Installation

```bash
git clone https://github.com/kotatsu-coder/pdf-book-linker.git
cd pdf-book-linker
uv sync
uv run pdf-book-linker --help
```

If Japanese text is garbled in Windows PowerShell, run `$env:PYTHONIOENCODING = 'utf-8'` first.

## Try it with the demo book

You can generate a fictional game-guide PDF with no copyright issues and try everything on it. `--lang en` makes the English edition (omit it for the Japanese one):

```bash
uv run python tools/make_demo_book.py --lang en -o work/demo.pdf
uv run pdf-book-linker build work/demo.pdf -o output/demo_linked.pdf --report-root output/report
```

Open `output/demo_linked.pdf` in a PDF viewer or Goodnotes and tap the contents, the index and the buttons. A report and preview images are written to `output/report/`.

## Using it with your own books

### One book

```bash
uv run pdf-book-linker build "book.pdf" -o "output/book_linked.pdf" --report-root output/report
```

This detects the contents, index, part pages, page mapping and button positions, creates the linked PDF and verifies it. Check the result in `autoconfig_report.md` and the preview PNGs under `output/report/<book name>/`.

### A whole folder

```bash
uv run pdf-book-linker batch "books folder" --report-root output/report --work-root work
```

Every PDF in the folder (including subfolders) is analysed one at a time, and `output/report/summary.md` (plus `summary.csv` for Excel) lists the books worst first. **No PDFs are created at this stage** — run `build` for the books marked `ok`, or for the ones you have fixed.

- If you stop it, the same command resumes where it left off (`--force` starts over).
- With enough memory, `--jobs 2` to `4` processes books in parallel.

| Status | Meaning |
|---|---|
| `ok` | The automatic checks passed (a quick look is still recommended) |
| `needs review` | Some checks fell short. Look at the report and previews and add overrides if needed |
| `OCR required` | No or too little text layer. Run OCR first |
| `failed` | An error occurred during processing |

The match rates in the report are the tool's own consistency checks, not measured accuracy.

### Fixing books that don't work

You can override just the parts the automatic detection got wrong with a small JSON file. Pages are **0-based PDF page indexes** (the page your viewer calls "page 1" is 0).

```json
{
  "toc_pages": [4, 5],
  "index_pages": [233, 234, 235],
  "index": {"columns": 2},
  "nav": {"position": "bottom-outer", "size": 0.055, "contrast": 4.5}
}
```

```bash
uv run pdf-book-linker build "book.pdf" -o "output/book_linked.pdf" --config "book.override.json"
```

For `batch`, pass `--override-root <folder>` and name each file `<book slug>.json` (the slug is the name of the book's report folder).

Common overrides:

| To do this | Example |
|---|---|
| Set the contents / index pages | `"toc_pages": [4,5]`, `"index_page_range": [400, 420]` |
| Set the number of index columns | `"index": {"columns": 3}` |
| Set the printed-page mapping | `"pagemap": {"segments": [{"pdf_index0_start": 8, "pdf_index0_end": 218, "print_start": 1, "style": "arabic"}]}` |
| Button position | `"nav": {"position": {"left": "bottom-outer", "right": "outer-edge"}}` (`bottom-centre` / `bottom-outer` / `top-outer` / `outer-edge`) |
| Button size | `"nav": {"size": 0.05}` (a fraction of the page width) |
| Button visibility | `"nav": {"contrast": 4.5}` — higher is more visible, lower is fainter (default 3.0) |
| Button colour / opacity | `"nav": {"color": "#1a3c8c", "opacity": 0.4}` (default `"auto"` for both) |

All options and how detection works are described in [docs/reference.md](docs/reference.md) (Japanese).

## Limitations

- **Goodnotes behaviour is not guaranteed.** Basic links such as the table of contents have been confirmed to work in Goodnotes on the author's device, but behaviour across devices and app versions, after handwriting, or after re-exporting has not been fully tested. See [docs/goodnotes_checklist.md](docs/goodnotes_checklist.md) (Japanese) for a test checklist.
- Goodnotes has no "back" function, so there is no button to return to where you came from after following a link.
- Detection relies on the existing text layer. OCR errors, vertical text, two-page spreads and unusual layouts may need overrides.
- Figure and table references ("Fig. 3-2") are not linked yet.
- Entries that point to another PDF (such as a shared index across volumes) cannot be linked.
- Low-confidence candidates are not turned into links; they are listed as "needs review" in the report.

## Development

```bash
uv run pytest -q
```

Tests use only synthetic data and the demo book. Do not add real book data (PDFs, extracted text, intermediate JSON) to the repository — `work/`, `output/` and `*.pdf` are ignored by `.gitignore`. See [docs/design.md](docs/design.md) for the design and [docs/requirements.md](docs/requirements.md) for the original requirements (both in Japanese).

## License

[GNU AGPL v3.0](LICENSE). The tool uses [PyMuPDF](https://github.com/pymupdf/PyMuPDF), which is AGPL-licensed, so this tool is released under the AGPL as well.

This software comes with no warranty. Please check the PDFs you create yourself, including whether the links point to the right pages.
