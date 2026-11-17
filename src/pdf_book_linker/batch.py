"""Resumable, isolated offline autoconfig for a local folder of PDFs."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .autoconfig import autoconfig, slug, OCRRequired
from .storage import read_json, write_json

OK_DEFINITION='ok: ページ対応 ≥90%、目次照合 ≥85%、索引照合 ≥80%（候補なしは除外）、非ナビ候補 review 比率 ≤10%、ナビ省略 ≤5%。目次検出必須。照合は自己評価の代理指標で、正解率や Goodnotes 動作の保証ではありません。'

def fingerprint(source, override=None):
    st=source.stat()
    code=hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob('*.py')): code.update(p.read_bytes())
    return dict(source=str(source.resolve()),size=st.st_size,mtime_ns=st.st_mtime_ns,
                code=code.hexdigest(),override=override.read_text(encoding='utf-8') if override else None)

def current(manifest, signature, required):
    try: return manifest.exists() and read_json(manifest)==signature and all(p.exists() for p in required)
    except (OSError,ValueError): return False

def required_results(book):
    required=[book/'metrics.json',book/'config.json',book/'autoconfig_report.md']
    try:
        m=read_json(book/'metrics.json')
        if m.get('status')=='failed': return required+[book/'retry_failed_result']
        if 'pagemap' in m:
            required.append(book/'navigation.json')
            ids=m.get('nav_preview_pages',[])+m.get('toc_pages',[])[:1]+m.get('part_pages',[])[:1]+m.get('index_pages',[])[:1]
            required.extend(book/f'preview_{i:04}.png' for i in ids)
    except (OSError,ValueError): return required+[book/'invalid_metrics']
    return required

def reasons(m):
    if m.get('reason'): return m['reason']
    if m.get('status')=='OCR required': return 'OCR が必要'
    out=[]
    if m.get('pagemap',{}).get('coverage',0)<90: out.append('ページ対応不足')
    if not m.get('toc_pages'): out.append('目次未確定')
    elif (m.get('toc_target_hit_pct') or 0)<85: out.append('目次照合低値')
    if m.get('index_target_hit_pct') is not None and m['index_target_hit_pct']<80: out.append('索引照合低値')
    if m.get('review_ratio',0)>.1: out.append('review 多数')
    if m.get('index_foreign_volume_links',0): out.append('別巻索引参照あり')
    if m.get('nav_review_pages',0): out.append('一部ナビ省略')
    missing=m.get('partial_text_layer_missing_pages',[])
    if len(missing)>m.get('pages',1)*.1: out.append('部分OCR欠落')
    return ' / '.join(out)

def rank(row):
    m=row[1]; status=m.get('status','failed')
    quality=min(m.get('pagemap',{}).get('coverage',0),m.get('toc_target_hit_pct') or 0,
                100 if m.get('index_target_hit_pct') is None else m['index_target_hit_pct'])
    return ({'failed':0,'needs review':1,'OCR required':2,'ok':3}.get(status,0),quality,-m.get('review_ratio',0),row[0])

def summary(root, results):
    rows=[]
    for name,m in sorted(results,key=rank):
        rows.append(dict(book=name,status=m.get('status','failed'),reason=reasons(m),pages=m.get('pages',''),
          pagemap=m.get('pagemap',{}).get('coverage',0),toc_hit=m.get('toc_target_hit_pct'),index_hit=m.get('index_target_hit_pct'),
          review_ratio=m.get('review_ratio'),pagerefs=m.get('pagerefs',0),nav_skipped=m.get('nav_review_pages',0),
          strategy=m.get('pagemap',{}).get('strategy',''),seconds=m.get('runtime',0)))
    with (root/'summary.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else ['book','status','reason']);writer.writeheader();writer.writerows(rows)
    counts=Counter(m.get('status','failed') for _,m in results)
    report='# Stage 5 全冊評価\n\n'+OK_DEFINITION+'\n\n状態: '+json.dumps(dict(counts),ensure_ascii=False)+'\n\n'
    report+='キャッシュは元ファイルの絶対パス・サイズ・更新時刻、全実装ファイル、override の内容で失効。各冊を子プロセスで処理し、完了後にメモリを解放。PDF は生成しません。\n\n'
    report+='|書籍|状態|理由|頁|対応%|目次%|索引%|review比率|本文参照|ナビ省略|方式|\n|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|\n'
    for r in rows:
        report+='|'+'|'.join(str(r[k]).replace('|','/') for k in ('book','status','reason','pages','pagemap','toc_hit','index_hit','review_ratio','pagerefs','nav_skipped','strategy'))+'|\n'
    (root/'summary.md').write_text(report,encoding='utf-8')

def run(folder, report_root, work_root='work/stage5', override_root=None, force=False, jobs=1):
    folder=Path(folder).resolve(); root=Path(report_root).resolve(); root.mkdir(parents=True,exist_ok=True)
    sources=sorted(p for p in folder.rglob('*') if p.suffix.lower()=='.pdf' and root not in p.parents)
    if not sources: raise ValueError('PDF が見つかりません')
    keys=[slug(p) for p in sources]
    if len(keys)!=len(set(keys)): raise ValueError('同名キャッシュが衝突します。別フォルダ単位で実行してください')
    if not 1<=jobs<=4: raise ValueError('--jobs は1〜4です（各プロセスのメモリに注意）')
    def evaluate(source):
        book=root/slug(source); book.mkdir(parents=True,exist_ok=True)
        override=Path(override_root)/(slug(source)+'.json') if override_root else None
        if override and not override.exists(): override=None
        signature=fingerprint(source,override); manifest=book/'batch_identity.json'; metrics=book/'metrics.json'
        cached=not force and current(manifest,signature,required_results(book))
        if not cached:
            cmd=[sys.executable,'-m','pdf_book_linker.batch','--worker',str(source),'--report-root',str(root),'--work-root',str(Path(work_root).resolve())]
            if override: cmd+=['--override',str(override.resolve())]
            started=time.perf_counter()
            proc=subprocess.run(cmd,capture_output=True,encoding='utf-8',errors='replace')
            if proc.returncode not in (0,3):
                m=dict(status='failed',reason=proc.stderr[-2000:] or f'worker exit {proc.returncode}',runtime=round(time.perf_counter()-started,2))
                write_json(metrics,m)
            write_json(manifest,signature)
        return source.stem,read_json(metrics),cached
    results=[]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures=[pool.submit(evaluate,p) for p in sources]
        for number,future in enumerate(as_completed(futures),1):
            name,m,cached=future.result();results.append((name,m));summary(root,results)
            print(f'[{number}/{len(sources)}] {name}: {m.get("status")}'+(' (cached)' if cached else ''),flush=True)
    return dict(Counter(m.get('status','failed') for _,m in results))

def worker():
    parser=argparse.ArgumentParser();parser.add_argument('--worker',required=True);parser.add_argument('--report-root',required=True);parser.add_argument('--work-root',required=True);parser.add_argument('--override')
    a=parser.parse_args()
    try:
        _,metrics=autoconfig(a.worker,a.work_root,a.report_root,a.override)
        if metrics.get('status')=='OCR required': return 3
    except OCRRequired: return 3
    return 0

if __name__=='__main__': sys.exit(worker())
