"""Stage 2: ローカル文字座標抽出、候補 JSON、ベクターナビと独立検証。"""
from collections import Counter
from functools import lru_cache
from pathlib import Path
import hashlib
import re
import unicodedata
import pymupdf as f
from .storage import read_json, write_json
from .pagemap import PageMap
from .linking import save_pdf
from .verification import image_hashes, close_rect

LEADERS = set('.．…•·・‥ー_ ')
DIGITS = str.maketrans({'l':'1','I':'1','O':'0','o':'0','｜':'1'})


def chars(page, include_spaces=False):
    return [c for b in page.get_text('rawdict')['blocks'] for l in b.get('lines', [])
            for s in l['spans'] for c in s['chars'] if include_spaces or c['c'].strip()]


def box(cs):
    r = f.Rect(cs[0]['bbox'])
    for c in cs[1:]: r |= f.Rect(c['bbox'])
    return r


def padded(r, pad, bounds):
    return list((f.Rect(r) + (-pad,-pad,pad,pad)) & bounds)


def rows(page, ymin=100, ymax=None, columns=1, include_spaces=False, tolerance_ratio=None, boundaries=None):
    """段ごとに文字の中心 y で束ねる。OCR のブロック順序には依存しない。"""
    groups = []
    for col in range(columns):
        selected = [c for c in chars(page, include_spaces) if ymin < f.Rect(c['bbox']).y0 < (ymax or page.rect.height-120)
                    and (sum(c['bbox'][0]/page.rect.width>=b for b in boundaries) if boundaries else int(min(columns-1, f.Rect(c['bbox']).x0 / page.rect.width * columns))) == col]
        for c in sorted(selected, key=lambda c: (f.Rect(c['bbox']).y0+f.Rect(c['bbox']).y1)/2):
            cy = (c['bbox'][1]+c['bbox'][3])/2
            if not groups or groups[-1][0] != col or abs(cy-groups[-1][1]) > (page.rect.height*tolerance_ratio if tolerance_ratio else 23):
                groups.append([col, cy, [c]])
            else: groups[-1][2].append(c)
    return [sorted(g[2], key=lambda c:c['bbox'][0]) for g in groups]


def ink_bounds(page, rect, scale=1, threshold=95):
    """解析用レンダーのみ。元画像は保存・置換しない。色付き扉も暗い印字を検出。"""
    clip = f.Rect(rect) & page.rect
    pix = page.get_pixmap(matrix=f.Matrix(scale,scale), clip=clip, colorspace=f.csGRAY, alpha=False)
    data = pix.samples
    xs, ys = [], []
    for y in range(pix.height):
        row = data[y*pix.stride:y*pix.stride+pix.width]
        positions = [x for x,v in enumerate(row) if v < threshold]
        if positions: xs.extend([min(positions),max(positions)]); ys.append(y)
    if not ys: return None
    return f.Rect((pix.x+min(xs))/scale,(pix.y+min(ys))/scale,
                  (pix.x+max(xs)+1)/scale,(pix.y+max(ys)+1)/scale)


def tight(page, cs, padding=3, scale=1):
    r = box(cs)
    # OCR の文字枠にはドットや余白が入りうるため、暗い印字の外接枠で仕上げる。
    ink = ink_bounds(page, r + (-3,-3,3,3),scale=scale)
    return padded(ink or r, padding, page.rect), list(ink or r), 'text_chars+image_threshold' if ink else 'text_chars'


def title_tight(page, cs, right, padding=3):
    """OCR がドット列を均等割りして字幅が縮む場合も、行画像の文字高さで復元。"""
    rough=box(cs)
    region=f.Rect(max(0,rough.x0-10),max(0,rough.y0-4),right-5,min(page.rect.height,rough.y1+4))
    pix=page.get_pixmap(clip=region,colorspace=f.csGRAY,alpha=False)
    data=pix.samples
    # 点線の点と、斜線・曲線を含む文字を連結成分の高さで分ける。
    dark={(x,y) for y in range(pix.height) for x in range(pix.width) if data[y*pix.stride+x]<95}
    accepted=[]; minimum=max(5,int(pix.height*.14))
    while dark:
        seed=dark.pop(); stack=[seed]; component=[seed]
        while stack:
            x,y=stack.pop()
            for dx,dy in [(-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)]:
                q=(x+dx,y+dy)
                if q in dark: dark.remove(q); stack.append(q); component.append(q)
        if max(y for x,y in component)-min(y for x,y in component)+1>=minimum:
            accepted.extend(component)
    if not accepted: return tight(page,cs,padding)
    ink=f.Rect(pix.x+min(x for x,y in accepted),pix.y+min(y for x,y in accepted),
               pix.x+max(x for x,y in accepted)+1,pix.y+max(y for x,y in accepted)+1)
    return padded(ink,padding,page.rect),list(ink),'text_row+image_components'


def parse_numbers(text, low, high):
    """位置付き数字候補。範囲全体を一リンクとし先頭へ移動。疑わしいものも返す。"""
    parts = [unicodedata.normalize('NFKC', c) for c in text]
    offsets = [i for i, part in enumerate(parts) for _ in part]
    t = ''.join(parts)
    pattern = r'(?<![A-Za-z0-9])([0-9lIOo]+)(?:\s*[-~～〜–—]\s*([0-9lIOo]+))?(?![A-Za-z0-9])'
    result=[]
    for m in re.finditer(pattern,t):
        a=int(m[1].translate(DIGITS)); b=int((m[2] or m[1]).translate(DIGITS))
        fixed = bool(re.search('[lIOo]',m[0]))
        valid = low <= a <= b <= high
        result.append({'start':offsets[m.start()],'end':offsets[m.end()-1]+1,'number':a,'range_end':b,
                       'confidence':.86 if fixed else .99,'valid':valid,'ocr_fixed':fixed})
    return result


def candidate(kind, page, target, rect, intended, confidence, **extra):
    return dict(kind=kind, source_pdf_index0=page, target_pdf_index0=target,
                rect=rect, intended_box=intended, confidence=confidence,
                status='approved' if confidence>=.95 and target is not None else 'review', **extra)


def extract_toc(doc, pages, pm, mini=False, padding=3, ymin=None):
    found=[]
    for i in pages:
        page=doc[i]
        for cs in rows(page, ymin=ymin if ymin is not None else page.rect.height*(.38 if mini else .035)):
            text=''.join(c['c'] for c in cs)
            jp=[c for c in cs if re.search('[\u3040-\u30ff\u3400-\u9fff]',c['c']) and c['c'] not in LEADERS]
            if not jp: continue
            # 装飾見出しと本文以外の行を除外。ページ番号は行の右端。
            nums=parse_numbers(text,1, max(int(k) for k in pm.by_label if k.isdecimal()))
            nums=[n for n in nums if n['end'] >= len(text)-2 and n['valid']]
            if not nums: continue
            n=nums[-1]; nc=cs[n['start']:n['end']]
            if box(nc).x0 < box(jp).x1: continue
            title_start=box(jp).x0
            if mini:
                prefix = re.match(r'^\d{1,2}', text)
                if prefix and prefix.end()<len(cs): title_start=cs[prefix.end()]['bbox'][0]
            title_cs=[c for c in cs if title_start <= c['bbox'][0] < box(nc).x0]
            # 長いドット列の前で切る。語中の中黒は残す。
            while title_cs and title_cs[-1]['c'] in LEADERS: title_cs.pop()
            run=0
            for k,c in enumerate(title_cs):
                run=run+1 if c['c'] in LEADERS else 0
                if run>=2: title_cs=title_cs[:k-1]; break
            if not title_cs: continue
            title=''.join(c['c'] for c in title_cs)
            ispart=bool(re.search(r'P\s*A\s*R\s*T', text, re.I))
            if ispart:
                title_cs=[c for c in cs if c['bbox'][0]<box(title_cs).x1 and c['c'] not in LEADERS]
                title=''.join(c['c'] for c in title_cs)
            for role, chunk in [('title',title_cs),('number',nc)]:
                r,ib,method=title_tight(page,chunk,box(nc).x0,padding) if role=='title' else tight(page,chunk,padding)
                found.append(candidate('part_toc' if mini else 'toc',i,pm.print_to_pdf(n['number']),r,ib,n['confidence'],
                                       title=title,print_page=str(n['number']),role=role,method=method,level=1 if ispart else 2))
    previous=0
    for c in found:
        target=c['target_pdf_index0']
        if (mini and target<=c['source_pdf_index0']) or (not mini and target<previous):
            c['confidence']=.5; c['status']='review'; c['reason']='行順とページ番号が不整合'
        if c['role']=='number': previous=max(previous,target)
    return found


def manual_toc(doc, spec, pm, padding):
    result=[]
    for e in spec['entries']:
        if e.get('status')!='approved': continue
        p=doc[e['source']['pdf_index0']]; region=f.Rect(e['source']['rect'])
        cs=sorted([c for c in chars(p) if region.contains(f.Point((c['bbox'][0]+c['bbox'][2])/2,(c['bbox'][1]+c['bbox'][3])/2))],key=lambda c:c['bbox'][0])
        # 通常行の先頭章番号は除く。PART 見出しはそのまま。
        ispart=e['title'].startswith('PART')
        if not ispart:
            japanese=[c for c in cs if re.search('[\u3040-\u30ff\u3400-\u9fff]',c['c']) and c['c'] not in LEADERS]
            if japanese: cs=[c for c in cs if c['bbox'][0]>=japanese[0]['bbox'][0]]
        # 右端のページ数は OCR 誤りがあるため印字領域を画像でも測定。
        right=max(c['bbox'][2] for c in cs)
        num_width=max(45,len(e['print_page'])*25+10)
        nc=[c for c in cs if c['bbox'][0]>right-num_width]
        title_cs=[c for c in cs if c['bbox'][0]<min(c['bbox'][0] for c in nc)]
        run=0
        for k,c in enumerate(title_cs):
            run=run+1 if c['c'] in LEADERS else 0
            if run>=2: title_cs=title_cs[:k-1]; break
        while title_cs and title_cs[-1]['c'] in LEADERS: title_cs.pop()
        for role,chunk in [('title',title_cs),('number',nc)]:
            r,ib,method=title_tight(p,chunk,box(nc).x0,padding) if role=='title' else tight(p,chunk,padding)
            result.append(candidate('toc',p.number,pm.print_to_pdf(e['print_page']),r,ib,1,
                title=e['title'],print_page=e['print_page'],level=e['level'],role=role,method=method,manual_override=True))
    return result


def extract_index(doc,pages,pm,cfg):
    result=[]
    low,high=cfg['print_range']
    for i in pages:
        for cs in rows(doc[i],ymin=cfg.get('ymin',100),ymax=cfg.get('ymax',doc[i].rect.height*.96),columns=cfg.get('columns',2), include_spaces=True):
            spaced=[]
            for c in cs:
                if spaced and c['bbox'][0]-spaced[-1]['bbox'][2]>8:
                    prev=spaced[-1]['bbox']; now=c['bbox']
                    spaced.append({'c':' ', 'bbox':[prev[2],now[1],now[0],now[3]]})
                spaced.append(c)
            cs=spaced
            text=''.join(c['c'] for c in cs)
            nums=parse_numbers(text,low,high)
            for n in nums:
                chunk=cs[n['start']:n['end']]
                prefix=text[:n['start']]; suffix=text[n['end']:]
                # 行末のページ列だけ。見出し内の数字・単位・括弧は review にする。
                tail_ok=bool(re.fullmatch(r'[\d\s,lIOo、，~～〜–—-]*',suffix))
                gap=n['start']==0 or chunk[0]['bbox'][0]-cs[n['start']-1]['bbox'][2]>8 or bool(re.search(r'[,、，]\s*$',prefix)) or prefix.endswith(' ')
                conf=n['confidence'] if n['valid'] and tail_ok and gap else .4
                r,ib,method=tight(doc[i],chunk,cfg.get('padding',3))
                result.append(candidate('index',i,pm.by_label.get(str(n['number'])) if n['valid'] else None,r,ib,conf,
                    text=text,token=text[n['start']:n['end']],print_page=str(n['number']),method=method,ocr_fixed=n['ocr_fixed']))
    return result


def nav_targets(page,starts,toc,index):
    starts=sorted(set(starts))
    if page in starts:
        # On a section's own start page (e.g. a PART title page), "←" goes to the
        # previous section; on the very first section it falls back to the TOC.
        current=max((s for s in starts if s<page),default=toc)
    else:
        current=max((s for s in starts if s<page),default=starts[0])
    nxt=next((s for s in starts if s>page),index)
    return [toc,current,nxt,index]


def nav_candidates(doc, starts, cfg):
    out=[]; measurements=[]
    size=cfg.get('tap_size',110); gap=cfg.get('gap',35)
    if size<110: raise ValueError('ナビのタップ矩形は 110 pt 以上必要です')
    for i in range(cfg['first_page'],cfg['last_page']+1):
        p=doc[i]; x=(p.rect.width-(4*size+3*gap))/2
        # 印刷ノンブルの左右を避けた中央帯を全対象ページで測る。
        y=p.rect.height-cfg.get('bottom_offset',140)
        region=f.Rect(x,y,x+4*size+3*gap,y+size)
        ink=ink_bounds(p,region,scale=1,threshold=cfg.get('ink_threshold',95))
        if ink: raise ValueError(f'ナビ配置が印字と重なります: {i}, {list(ink)}')
        measurements.append({'pdf_index0':i,'tested_empty_rect':list(region),'ink_found':False})
        for j,target in enumerate(nav_targets(i,starts,cfg['toc_page'],cfg['index_page'])):
            r=[x+j*(size+gap),y,x+j*(size+gap)+size,y+size]
            out.append(candidate('nav',i,target,r,r,1,button=j,method='vector',style=cfg))
    return out,measurements


def validate_plan(doc, items, cfg):
    """保存前に全候補と、設定から独立して期待するナビを照合する。"""
    errors=[]; selected=approved(items); seen=set()
    for c in selected:
        i=c['source_pdf_index0']; target=c['target_pdf_index0']; r=f.Rect(c['rect'])
        if not 0<=i<len(doc) or not isinstance(target,int) or not 0<=target<len(doc):
            errors.append('候補のページが範囲外'); continue
        if r.is_empty or not doc[i].rect.contains(r): errors.append('候補の矩形が範囲外')
        key=(i,tuple(round(v,2) for v in r))
        if key in seen: errors.append('候補が重複')
        seen.add(key)
    navcfg=cfg['nav']; first,last=navcfg['first_page'],navcfg['last_page']
    starts=[c['target_pdf_index0'] for c in selected if c['kind']=='toc' and c['role']=='title'
            and c['target_pdf_index0']<navcfg['index_page']]
    nav=[c for c in selected if c['kind']=='nav']
    if len(nav)!=4*(last-first+1): errors.append('ナビの総数が対象ページ数×4と不一致')
    for i in range(first,last+1):
        buttons=sorted([c for c in nav if c['source_pdf_index0']==i],key=lambda c:c['button'])
        if [c['button'] for c in buttons]!=[0,1,2,3]: errors.append(f'ナビの欠落・重複: {i}'); continue
        if [c['target_pdf_index0'] for c in buttons]!=nav_targets(i,starts,navcfg['toc_page'],navcfg['index_page']):
            errors.append(f'ナビ移動先が章構造と不一致: {i}')
        region=f.Rect(buttons[0]['rect'])
        for c in buttons: region |= f.Rect(c['rect'])
        if ink_bounds(doc[i],region,threshold=navcfg.get('ink_threshold',95)): errors.append(f'ナビが原稿印字に重なる: {i}')
        for c in buttons:
            r=f.Rect(c['rect'])
            if r.width<110 or r.height<110: errors.append(f'ナビの面積不足: {i}')
    if errors: raise ValueError(' / '.join(errors))
    return {'pages_checked':last-first+1,'expected_buttons':len(nav),'targets_correct':True,'margin_clear':True}


def draw_button(p,c):
    r=f.Rect(c['rect']); x,y=(r.x0+r.x1)/2,(r.y0+r.y1)/2
    j=c['button']; direction=-1 if j<2 else 1; gray=c['style'].get('gray',.8)
    shape=p.new_shape()
    scale=r.width/110
    for shift in ([-11,11] if j in (0,3) else [0]):
        xx=x+shift*scale
        shape.draw_polyline([(xx-direction*12*scale,y-18*scale),(xx+direction*12*scale,y),(xx-direction*12*scale,y+18*scale)])
    colour=tuple(c['style']['rgb']) if c['style'].get('rgb') else (gray,gray,gray)
    shape.finish(color=colour,width=c['style'].get('line_width',2.5),stroke_opacity=c['style'].get('opacity',1))
    shape.commit(overlay=True)


def approved(items):
    return [c for c in items if c['status']=='approved' and c['confidence']>=.95]


def apply_candidates(source, output, items, outline, allowed):
    output=Path(output).resolve(); source=Path(source).resolve()
    if output==source or output not in [Path(x).resolve() for x in allowed]: raise ValueError('出力は設定の許可名だけ指定できます')
    if output.exists() and output.samefile(source): raise ValueError('入力 PDF と同一実体です')
    output.parent.mkdir(parents=True,exist_ok=True)
    with f.open(source) as d:
        for c in approved(items):
            p=d[c['source_pdf_index0']]
            if c['kind']=='nav': draw_button(p,c)
            p.insert_link({'kind':f.LINK_GOTO,'from':f.Rect(c['rect']),'page':c['target_pdf_index0'],'to':f.Point(0,0)})
        d.set_toc(outline)
        for row, spec in zip(d.get_toc(simple=False), outline):
            target=d[row[2]-1]; point=f.Point(0, spec[3] if len(spec)>3 else 0)*~target.transformation_matrix
            d.xref_set_key(row[3]['xref'],'A',f'<</S/GoTo/D[{target.xref} 0 R /XYZ {point.x:.8f} {point.y:.8f} 0]>>')
        save_pdf(d,output)


def verify_candidates(source,output,items,outline,page_indices=None,strict=False):
    errors=[]; checks=[]; expected=approved(items)
    with f.open(source) as a,f.open(output) as b:
        ids=page_indices or list(range(len(a)))
        if len(ids)!=len(b): errors.append('ページ数不一致')
        actual=[p.get_links() for p in b]
        @lru_cache(maxsize=8)
        def source_boxes(index): return [f.Rect(ch['bbox']) for ch in chars(a[index])]
        @lru_cache(maxsize=2)
        def source_raster(index):
            from .navigation import Raster
            return Raster(a[index])
        if sum(map(len,actual))!=len(expected): errors.append('リンク総数不一致')
        for i,src in enumerate(ids):
            same=image_hashes(a,a[src])==image_hashes(b,b[i]); checks.append(same)
            if not same: errors.append(f'画像変更: {i}')
            if a[src].cropbox!=b[i].cropbox or a[src].mediabox!=b[i].mediabox or a[src].rotation!=b[i].rotation: errors.append(f'ページ形状変更: {i}')
            seen=set()
            previous=[]
            for l in actual[i]:
                key=tuple(round(v,2) for v in l['from'])
                if key in seen: errors.append(f'重複: {i}')
                seen.add(key)
                if strict and any(l['from'].intersects(r) for r in previous): errors.append(f'リンク重なり: {i}')
                previous.append(l['from'])
                if l['kind']!=f.LINK_GOTO or not 0<=l.get('page',-1)<len(b): errors.append(f'移動先不正: {i}')
        for c in expected:
            matches=[l for l in actual[c['source_pdf_index0']] if close_rect(l['from'],c['rect'])]
            if len(matches)!=1 or matches[0].get('page')!=c['target_pdf_index0']:
                errors.append(f'仕様不一致: {c}')
            elif matches[0].get('to')!=f.Point(0,0): errors.append('移動先座標不一致')
            if c['kind']!='nav':
                src=ids[c['source_pdf_index0']]
                if not any(f.Rect(c['intended_box']).intersects(r) for r in source_boxes(src)):
                    errors.append('印字枠が元文字座標と重なりません')
            else:
                if len(b[c['source_pdf_index0']].get_drawings())<=len(a[ids[c['source_pdf_index0']]].get_drawings()):
                    errors.append('ナビのベクター描画がありません')
                if strict:
                    src=ids[c['source_pdf_index0']]
                    if not source_raster(src).clear(f.Rect(c['rect'])*a[src].rotation_matrix):
                        errors.append(f'ナビが元の印字と重なっています: {src}')
            if c['kind']!='nav' and not f.Rect(c['rect']).contains(f.Rect(c['intended_box'])): errors.append('意図した印字枠を包含しません')
        if b.get_toc()!=[r[:3] for r in outline]: errors.append('アウトライン不一致')
        for row, spec in zip(b.get_toc(simple=False), outline):
            y=spec[3] if len(spec)>3 else 0
            if abs(row[3].get('to', f.Point(-1,-1)).y-y)>.05: errors.append('アウトラインの座標不一致')
    return dict(passed=not errors,errors=errors,page_count=len(ids),link_counts=dict(Counter(c['kind'] for c in expected)),
                review_counts=dict(Counter(c['kind'] for c in items if c not in expected)),images_unchanged=all(checks),image_pages_checked=len(checks),
                source_size_bytes=Path(source).stat().st_size,output_size_bytes=Path(output).stat().st_size)


def run(config_path, reuse=False):
    cfg=read_json(config_path); work=Path(cfg['work_dir']); source=cfg['source']; pm=PageMap(read_json(cfg['pagemap']))
    source_path=Path(source).resolve()
    outputs=[Path(cfg[key]).resolve() for key in ('output','extract_output')]
    for output in outputs:
        if output==source_path or (output.exists() and output.samefile(source_path)):
            raise ValueError('全体版・抜粋とも元 PDF と同一ファイルには保存できません')
    if outputs[0]==outputs[1] or (all(p.exists() for p in outputs) and outputs[0].samefile(outputs[1])):
        raise ValueError('全体版と抜粋は別ファイルを指定してください')
    before=hashlib.sha256(Path(source).read_bytes()).hexdigest()
    with f.open(source) as d:
        ids=cfg['extract_pages']
        if not ids or len(ids)!=len(set(ids)) or any(type(i) is not int or not 0<=i<len(d) for i in ids):
            raise ValueError('抜粋ページは重複のない範囲内の index0 を指定してください')
        if reuse: allitems=read_json(work/'stage2_candidates.json')['candidates']
        else:
            auto=extract_toc(d,cfg['toc_pages'],pm,padding=cfg.get('padding',3),ymin=cfg.get('toc_ymin')); write_json(work/'toc_auto.json',{'candidates':auto})
            manual=read_json(cfg['manual_toc']) if cfg.get('manual_toc') else None
            toc=manual_toc(d,manual,pm,cfg.get('padding',3)) if manual else auto
            minis=extract_toc(d,cfg['part_pages'],pm,True,cfg.get('padding',3),ymin=cfg.get('part_ymin'))
            index=extract_index(d,cfg['index_pages'],pm,cfg['index'])
            write_json(work/'index_links.json',{'schema_version':2,'candidates':index})
            starts=[c['target_pdf_index0'] for c in toc if c['role']=='title' and c['target_pdf_index0']<cfg['nav']['index_page']]
            nav,margin=nav_candidates(d,starts,cfg['nav']); write_json(work/'nav_margin.json',{'measurements':margin})
            manual_reviews=[]
            for e in (manual or {}).get('entries',[]):
                if e.get('status')=='review':
                    manual_reviews.append(candidate('toc',e['source']['pdf_index0'],None,e['source']['rect'],e['source']['rect'],.4,
                        title=e['title'],print_page=e['print_page'],role='title',method='manual_review',reason=e.get('note','')))
            allitems=toc+minis+index+nav+manual_reviews
            write_json(work/'part_toc_links.json',{'candidates':minis})
            # タイトル文字列は OCR 差を残して比較。元ページ・印刷番号で対応付け。
            diff=[]
            for c in toc:
                if c['role']!='title': continue
                hits=[a for a in auto if a['role']=='title' and a['source_pdf_index0']==c['source_pdf_index0'] and a['print_page']==c['print_page']]
                if not hits or hits[0]['title']!=c['title']: diff.append({'manual_title':c['title'],'print_page':c['print_page'],'auto_title':hits[0]['title'] if hits else None})
            write_json(work/'toc_diff.json',{'manual_entries':len(toc)//2,'manual_review_entries':len(manual_reviews),
                'auto_entries':len(auto)//2,'auto_review_entries':sum(c['status']=='review' and c['role']=='title' for c in auto),
                'matched_page_targets':len(toc)//2-sum(x['auto_title'] is None for x in diff),
                'target_differences':sum(x['auto_title'] is None for x in diff),
                'title_differences':sum(x['auto_title'] is not None for x in diff),'differences':diff})
            write_json(work/'stage2_candidates.json',{'schema_version':2,'candidates':allitems})
        outline=[[c['level'],c['title'],c['target_pdf_index0']+1] for c in approved(allitems) if c['kind']=='toc' and c['role']=='title']
    with f.open(source) as original:
        navcheck=validate_plan(original,allitems,cfg)
    apply_candidates(source,cfg['output'],allitems,outline,[cfg['output'],cfg['extract_output']])
    report=verify_candidates(source,cfg['output'],allitems,outline)
    report['navigation_check']=navcheck
    report['box_methods']=dict(Counter(c['method'] for c in approved(allitems)))
    report['outline_count']=len(outline)
    # 抜粋は完成版からコピーし、全リンクを明示的に付け直す。
    ids=cfg['extract_pages']; remap={v:k for k,v in enumerate(ids)}; subset=[]; dropped=Counter()
    import copy
    for c in approved(allitems):
        if c['source_pdf_index0'] not in remap: continue
        if c['target_pdf_index0'] not in remap: dropped[c['kind']]+=1; continue
        cc=copy.deepcopy(c); cc['source_pdf_index0']=remap[c['source_pdf_index0']]; cc['target_pdf_index0']=remap[c['target_pdf_index0']]; subset.append(cc)
    with f.open(cfg['output']) as full,f.open() as small:
        for i in ids: small.insert_pdf(full,from_page=i,to_page=i,links=False)
        for c in subset: small[c['source_pdf_index0']].insert_link({'kind':f.LINK_GOTO,'from':f.Rect(c['rect']),'page':c['target_pdf_index0'],'to':f.Point(0,0)})
        small.set_toc([]); save_pdf(small,cfg['extract_output'])
    ex=verify_candidates(source,cfg['extract_output'],subset,[],ids); ex['dropped_links']=dict(dropped); ex['source_indices']=ids
    write_json(work/'stage2_extract_verify.json',ex)
    with f.open(cfg['output']) as result:
        preview=Path(cfg['preview_dir']); preview.mkdir(parents=True,exist_ok=True)
        for i in cfg['preview_pages']:
            p=result[i]
            for c in approved(allitems):
                if c['source_pdf_index0']==i: p.draw_rect(f.Rect(c['rect']),color=(1,0,0),width=2,overlay=True)
            p.get_pixmap(matrix=f.Matrix(.45,.45)).save(preview/f'page_{i:03}.png')
    report['source_sha256_before']=before; report['source_sha256_after']=hashlib.sha256(Path(source).read_bytes()).hexdigest()
    report['source_unchanged']=report['source_sha256_before']==report['source_sha256_after']
    report['passed']=report['passed'] and report['source_unchanged'] and ex['passed']
    write_json(work/'stage2_verify.json',report)
    return report
