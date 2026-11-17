"""日本語の CLI。"""
import argparse
import json
from pathlib import Path
import sys
from .storage import read_json, write_json
from .pagemap import PageMap
from .inspection import inspect_pdf
from .linking import apply_pdf
from .verification import verify_pdf
from .extraction import extract_pdf


class JapaneseParser(argparse.ArgumentParser):
    def format_help(self):
        return (super().format_help().replace("usage:", "使い方:")
                .replace("positional arguments:", "位置引数:")
                .replace("options:", "オプション:")
                .replace("show this help message and exit", "このヘルプを表示して終了します"))

    def error(self, message):
        self.print_help(sys.stderr)
        self.exit(2, "エラー: 引数が不足または不正です。上の使い方を確認してください。\n")


def main(argv=None):
    parser = JapaneseParser(description="スキャン本の PDF に内部リンク・しおり・ナビボタンを追加します")
    commands = parser.add_subparsers(dest="command", required=True, title="コマンド")
    inspect = commands.add_parser("inspect", help="PDF 構造を検査します")
    inspect.add_argument("pdf", help="入力 PDF")
    inspect.add_argument("--work-dir", help="作業ディレクトリ（既定: work/<入力の名前>）")
    for name, help_text in [("apply", "手動目次仕様を埋め込みます"), ("verify", "元 PDF と比較検証します"), ("extract", "検証用ページと仕様を抜粋します")]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("pdf", help="対象 PDF")
        command.add_argument("--spec", required=True, help="目次 JSON")
        command.add_argument("--pagemap", required=True, help="ページ対応 JSON")
        if name in ("apply", "extract"):
            command.add_argument("-o", "--output", required=True, help="別名の出力 PDF（既存ファイル不可）")
        else:
            command.add_argument("--source", required=True, help="比較元 PDF")
        command.add_argument("--report", help="結果 JSON（既定: 仕様と同じディレクトリ）")
        if name == "extract":
            command.add_argument("--pages", required=True, help="抜粋する pdf_index0 のカンマ区切り")
    stage2 = commands.add_parser("stage2", help="自動目次・索引・ナビを生成し検証します")
    stage2.add_argument("--config", required=True, help="Stage 2 設定 JSON（パス・ページ範囲・外観）")
    stage2.add_argument("--reuse-candidates", action="store_true", help="編集済み stage2_candidates.json を再利用します")
    stage3 = commands.add_parser("stage3", help="本文ページ参照・PARTナビ・詳細しおりを生成します")
    stage3.add_argument("--config", required=True)
    stage3.add_argument("--reuse-candidates", action="store_true")
    batch = commands.add_parser('batch',help='全PDFの設定と評価を再開可能な子プロセスで生成します（PDF作成なし）')
    batch.add_argument('folder')
    batch.add_argument('--report-root',required=True)
    batch.add_argument('--work-root',default='work/stage5')
    batch.add_argument('--override-root')
    batch.add_argument('--force',action='store_true')
    batch.add_argument('--jobs',type=int,default=1,help='同時に処理する書籍数（1〜4、既定1）')
    for name in ('autoconfig', 'build'):
        command = commands.add_parser(name, help='書籍ごとの設定をオフラインで自動検出します')
        command.add_argument('pdf')
        command.add_argument('--work-root', default='work/auto')
        command.add_argument('--report-root', help='書籍別 PNG/MD/JSON の保存先')
        command.add_argument('--config', help='手動上書き JSON（指定値を優先）')
        if name == 'build': command.add_argument('-o', '--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command=='batch':
            from .batch import run
            print(json.dumps(run(args.folder,args.report_root,args.work_root,args.override_root,args.force,args.jobs),ensure_ascii=False))
            return 0
        if args.command in ('autoconfig', 'build'):
            from .autoconfig import autoconfig, build, OCRRequired, OCR_EXIT
            try:
                if args.command == 'build':
                    result = build(args.pdf, args.output, args.work_root, args.report_root, args.config)
                else:
                    _, result = autoconfig(args.pdf, args.work_root, args.report_root, args.config)
            except OCRRequired:
                print('OCR が必要', file=sys.stderr)
                return OCR_EXIT
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result.get('status')=='OCR required': return OCR_EXIT
            return 1 if result.get('passed') is False else 0
        if args.command in ("stage2", "stage3"):
            if args.command == "stage3":
                from .stage3 import run
            else:
                from .stage2 import run
            report = run(args.config, args.reuse_candidates)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 1
        if args.command == "inspect":
            report = inspect_pdf(args.pdf)
            location = Path(args.work_dir) if args.work_dir else Path("work") / Path(args.pdf).stem
            write_json(location / "inspect.json", report)
            print(f"検査完了: {report['page_count']} ページ、既存リンク {report['link_count']} 件")
            return 0
        spec = read_json(args.spec)
        pagemap = PageMap(read_json(args.pagemap))
        directory = Path(args.spec).parent
        if args.command == "extract":
            toc, mapping, report = extract_pdf(args.pdf, args.output, [int(v) for v in args.pages.split(",")], spec, pagemap)
            write_json(directory / "extract_toc.json", toc)
            write_json(directory / "extract_pagemap.json", mapping)
        elif args.command == "apply":
            report = apply_pdf(args.pdf, args.output, spec, pagemap)
        else:
            report = verify_pdf(args.pdf, args.source, spec, pagemap)
        write_json(args.report or directory / (args.command + ".json"), report)
        summary = {key: value for key, value in report.items() if key not in ("resolved", "review", "image_checks")}
        print("処理結果:\n" + json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if report.get("passed") is False else 0
    except (ValueError, OSError, KeyError, TypeError, IndexError, RuntimeError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
