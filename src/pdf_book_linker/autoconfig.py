"""Offline, normalized-coordinate book detection and reviewable Stage 3 planning."""
import hashlib
import json
import os
import re
import time
import unicodedata
from collections import Counter, defaultdict
from functools import lru_cache
from bisect import bisect_left, bisect_right
from pathlib import Path

import pymupdf as f
from . import stage2 as s2
from . import stage3 as s3
from .pagemap import PageMap, roman
from .storage import read_json, write_json
from . import navigation

VERSION = 5
OCR_EXIT = 3

class OCRRequired(ValueError):
    pass

def normalized(text):
    return unicodedata.normalize('NFKC', text)

def key(text):
    return re.sub(r'[^\w]', '', normalized(text)).lower()

def slug(path):
    return re.sub(r'[^\w.-]+', '_', Path(path).stem).strip('_')

def page_rows(page, columns=1, words=None):
    """Group by baseline within a column; all thresholds are page-relative."""
    w, h = page.rect.width, page.rect.height
    words = page.get_text('words') if words is None else words
    if page.rotation:
        words = [(*list(f.Rect(x[:4])*page.rotation_matrix),*x[4:]) for x in words]
    rows = []
    for col in range(columns):
        groups = []
        selected = [x for x in words if min(columns-1, int(x[0]/w*columns)) == col]
        for word in sorted(selected, key=lambda x: (x[3]+x[1])/2):
            cy = (word[1]+word[3])/2
            if not groups or abs(cy-groups[-1][0]) > h*.0065:
                groups.append([cy, [word]])
            else:
                groups[-1][1].append(word)
        for _, items in groups:
            items.sort(key=lambda x:x[0])
            rect = f.Rect(items[0][:4])
            for x in items[1:]: rect |= f.Rect(x[:4])
            pieces=[]
            for j,word in enumerate(items):
                if j:
                    previous=items[j-1]
                    cw=min((word[2]-word[0])/max(1,len(word[4])),(previous[2]-previous[0])/max(1,len(previous[4])))
                    digit_pair=normalized(word[4]).isdigit() and normalized(previous[4]).isdigit()
                    pieces.append(' ' if word[0]-previous[2]>cw*(1.2 if digit_pair else .55) else '')
                pieces.append(word[4])
            rows.append(dict(text=''.join(pieces), rect=list(rect*page.derotation_matrix), display_rect=list(rect),
                             x=rect.x0/w, right=rect.x1/w, y=rect.y0/h, size=rect.height/h, col=col))
    return rows

DIGIT_TOKEN = r'[0-9lIOo]{1,4}'
TAIL = re.compile(r'(?P<nums>'+DIGIT_TOKEN+r'(?:(?:\s*[,、，\-–〜～]\s*|\s+)'+DIGIT_TOKEN+r')*|[ivxlcdmIVXLCDM]{1,8})\s*$')

def terminal_entry(row, max_page=9999):
    text = normalized(row['text'])
    original_text=text
    # A terminal prolonged mark is a frequent OCR form of a vertical '1'.
    # It is accepted only beside digits; monotonic/map evidence gates approval.
    text=re.sub(r'(?<!ー)ー(?=\d{1,2}ー$)','1',text)
    text=re.sub(r'(?<=\d)ー$','1',text)
    # Keep geometrically separated digit groups distinct (chapter marker vs target).
    m = TAIL.search(text)
    if not m: return None
    title = clean_title(text[:m.start()])
    if not key(title) or not re.search(r'[A-Za-z\u3040-\u9fff]', title): return None
    if not m['nums'][0].isdigit() and m.start()>0 and text[m.start()-1].isascii() and text[m.start()-1].isalpha(): return None
    raw = m['nums']
    nums = [] if re.fullmatch(r'[ivxlcdm]+',raw) else [n.translate(s2.DIGITS) for n in re.findall(DIGIT_TOKEN,raw)]
    if nums and not all(0 < int(n) <= max_page for n in nums): return None
    nums=[str(int(n)) for n in nums]
    return dict(title=title, labels=nums or [raw.lower()], multi=len(nums)>1,
                token=original_text[m.start('nums'):m.end('nums')], **row)

def printed_candidates(rows, count):
    result=[]
    for row in rows:
        if not (row['y'] < .085 or row['y'] > .88): continue
        text=normalized(row['text']).strip()
        # Decorative dot leaders surround some centred folios. They are not digits.
        text=text.strip(' .．…・·●•‥')
        m=re.search(r'(\d{1,4}|[ivxlcdmIVXLCDM]{1,8})\s*$', text)
        leading=False
        if not m and row.get('x',1)<.15:
            m=re.match(r'(\d{1,4})(?=\s|[^\x00-\x7f])',text);leading=bool(m)
        if not m: continue
        lab=m[1]
        if lab.isdigit():
            n=int(lab); style='arabic'
            if not 0<n<=count*2: continue
        else:
            lab=lab.lower(); n=next((v for v in range(1,100) if roman(v)==lab),None); style='roman'
            if n is None: continue
        bare=bool(re.fullmatch(r'[\s\d ivxlcdmIVXLCDM]+',text))
        if not bare and not leading and row.get('right',0)<.88 and not re.search(r'[●•・]|\s\d+\s*$',text): continue
        result.append(dict(number=n,style=style,confidence=.99 if bare else .94,rect=row['rect']))
    return result

def fit_pagemap(observations, count):
    """Select locally supported offsets, interpolate only between agreeing anchors."""
    votes=Counter((v['style'],i-v['number']) for i,vs in enumerate(observations) for v in vs)
    anchors={}; conflicts=[]
    for i,vs in enumerate(observations):
        ranked=[]
        for v in vs:
            off=i-v['number']; style=v['style']
            local=sum(any(q['style']==style and j-q['number']==off for q in observations[j])
                      for j in range(max(0,i-5),min(count,i+6)))
            ranked.append((local,votes[style,off],v['confidence'],v))
        ranked.sort(key=lambda a:a[:3],reverse=True)
        if ranked and ranked[0][0]>=2:
            v=ranked[0][3]; anchors[i]=(v['style'],v['number'])
            if len({(v['style'],v['number']) for v in vs})>1: conflicts.append(i)
    # Do not let two high TOC numbers greedily invalidate a thousand body pages.
    # Find the longest strictly increasing observed sequence for each number style.
    outliers=[]
    for style in ('roman','arabic'):
        sequence=[(i,n) for i,(s,n) in sorted(anchors.items()) if s==style]
        tails=[]; tail_positions=[]; previous=[]
        for position,(i,n) in enumerate(sequence):
            at=bisect_left(tails,n)
            previous.append(tail_positions[at-1] if at else -1)
            if at==len(tails): tails.append(n);tail_positions.append(position)
            else: tails[at]=n;tail_positions[at]=position
        keep=set(); position=tail_positions[-1] if tail_positions else -1
        while position>=0:
            keep.add(sequence[position][0]);position=previous[position]
        for i,n in sequence:
            if i not in keep: outliers.append(i);anchors.pop(i)
    mapped=dict(anchors)
    pairs=sorted(anchors)
    for a,b in zip(pairs,pairs[1:]):
        sa,na=anchors[a]; sb,nb=anchors[b]
        if sa==sb and nb-na==b-a:
            for i in range(a,b+1): mapped[i]=(sa,na+i-a)
    # Extend a supported run to print page 1, at most two missing pages.
    for style in ('roman','arabic'):
        first=next((i for i in pairs if anchors[i][0]==style),None)
        if first is not None:
            n=anchors[first][1]
            if n<=3:
                for j in range(1,n):
                    if first-j>=0 and first-j not in mapped: mapped[first-j]=(style,n-j)
    segments=[]; used=set(); last_number={}
    for i,(style,n) in sorted(mapped.items()):
        if (style,n) in used or n<=last_number.get(style,0):
            conflicts.append(i); continue
        used.add((style,n)); last_number[style]=n
        if segments and segments[-1]['style']==style and segments[-1]['pdf_index0_end']==i-1 and segments[-1]['print_start']+i-segments[-1]['pdf_index0_start']==n:
            segments[-1]['pdf_index0_end']=i
        else:
            segments.append(dict(pdf_index0_start=i,pdf_index0_end=i,print_start=n,style=style))
    pm=PageMap(dict(segments=segments))
    agreeing=sum(any(pm.by_index.get(i)==(str(v['number']) if v['style']=='arabic' else roman(v['number'])) for v in vs) for i,vs in enumerate(observations) if vs)
    total=sum(bool(v) for v in observations)
    return dict(schema_version=1,segments=segments), dict(coverage=round(100*len(pm.by_index)/count,2),
        consistency=round(100*agreeing/total,2) if total else 0, conflicts=sorted(set(conflicts)),anchors=len(anchors),
        confidence=round(agreeing/total,3) if total else 0,outlier_anchors=outliers)

def hierarchy(entries):
    """Markers first, then indentation and font size clusters."""
    if not entries: return
    sizes=sorted({round(e['size'],2) for e in entries},reverse=True)
    base=min(e['x']-e['col']*.5 for e in entries)
    raw=[]
    for e in entries:
        title=normalized(e['title'])
        if re.match(r'(?:PART|WORLD|第\s*\d+\s*[部編章])',title,re.I): level=1
        elif re.match(r'(?:[A-Z](?:\s|[^a-zA-Z])|STAGE|\d{2}\s)',title): level=2
        elif re.match(r'[①-⑳〇○]',e.get('text',e['title']).lstrip()) or re.match(r'(?:[1-9]\d*\.\d+|[①-⑳〇○])',title): level=3
        else: level=1+min(2,round(max(0,e['x']-e['col']*.5-base)/.035))
        if len(sizes)>1 and e['size'] < max(sizes)*.75: level=max(level,2)
        raw.append(level)
    distinct=sorted(set(raw)); levels={n:i+1 for i,n in enumerate(distinct)}
    for e,n in zip(entries,raw): e['level']=levels[n]

def nav_geometry(width,height):
    return dict(tap_size=width*.06285,gap=width*.02,bottom_offset=height*.0395,
                gray=.8,line_width=width*.00143,relative=True)

def merge(base, patch):
    for k,v in patch.items():
        if isinstance(v,dict) and isinstance(base.get(k),dict): merge(base[k],v)
        else: base[k]=v
    return base

def memory_peak_mb():
    try:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in ('peak_working','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
            c=Counters(); c.cb=ctypes.sizeof(c)
            handle=ctypes.windll.kernel32.GetCurrentProcess
            handle.restype=wintypes.HANDLE
            query=ctypes.windll.psapi.GetProcessMemoryInfo
            query.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
            if not query(handle(),ctypes.byref(c),c.cb): return None
            return round(c.peak_working/1024**2,2)
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2)
    except (ImportError,OSError,AttributeError): return None

def source_hash(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
    return digest.hexdigest()

def title_key(title):
    return key(re.sub(r'^(?:(?:PART|WORLD|STAGE)\s*\d+|第\d+[章部編]|[IVX]{1,6}[.、]|\d+[-.\d]*|[A-Z](?=\s))\s*','',clean_title(title),flags=re.I))

def clean_title(text):
    text=normalized(text).strip(' .．…・·●•_\t')
    # A leader separates the subject from the author, never part of the heading.
    text=re.split(r'[.．…・·•_]{2,}|[─━―]{2,}',text)[0]
    text=re.sub(r'[（(][^()（）]{2,14}[）)]\s*$','',text)
    return text.strip(' .．…・·●•_\t')

def index_boundaries(page, words):
    """Find gutters, rather than assuming that columns start at exact thirds."""
    w,h=page.rect.width,page.rect.height
    ws=[a for a in words if .08*h<a[1]<.94*h]
    if not ws: return []
    def gutter(lo,hi):
        candidates=[j/200 for j in range(int(lo*200),int(hi*200)+1)]
        x=min(candidates,key=lambda x:(sum(a[0]<w*x<a[2] for a in ws),abs(x-(lo+hi)/2)))
        return x,sum(a[0]<w*x<a[2] for a in ws)/len(ws)
    mid,score=gutter(.44,.56)
    a,sa=gutter(.27,.4); b,sb=gutter(.59,.73)
    starts=Counter(round(q[0]/w,2) for q in ws)
    mode=lambda lo,hi: max((n for x,n in starts.items() if lo<=x<=hi),default=0)
    first=mode(.02,.2); second=mode(.28,.44); third=mode(.58,.76); half=mode(.46,.57)
    if max(sa,sb)<.025 and (score>.025 or min(second,third)>=first*.45 and half<min(second,third)*.8): return [a,b]
    if score<.04: return [mid]
    return []

def toc_columns(page,words,default=1):
    """A single right-aligned target column must not split titles and authors."""
    xs=[(q[0]+q[2])/2/page.rect.width for q in words
        if normalized(q[4]).isdigit() and .08<q[1]/page.rect.height<.95]
    right=sum(x>.75 for x in xs); half=sum(.38<x<.58 for x in xs)
    third=sum(.24<x<.36 for x in xs); middle=sum(.58<x<.74 for x in xs)
    starts=Counter(round(q[0]/page.rect.width,2) for q in words if .08<q[1]/page.rect.height<.95
                   and len(key(q[4]))>=2 and re.search(r'[A-Za-z\u3040-\u9fff]',q[4])
                   and not re.fullmatch(r'[ivxlcdm]+',normalized(q[4]),re.I))
    mode=lambda lo,hi:max((n for x,n in starts.items() if lo<=x<=hi),default=0)
    support=max(3,mode(.04,.3)*.3)
    two_text=mode(.48,.63)>=support
    three_text=min(mode(.31,.47),mode(.61,.8))>=support
    if right>=3:
        if min(third,middle)>=max(3,right*.45) and three_text: return 3
        if half>=max(3,right*.45) and two_text: return 2
        if not two_text and not three_text: return 1
        if max(half,third)<right*.35: return 1
    return default

def toc_from_number_strip(page,words,numbers,title_xmax=None):
    titlewords=[w for w in words if (f.Rect(w[:4])*page.rotation_matrix).x1<page.rect.width*.9]
    rows=page_rows(page,1,titlewords);entries=[]
    titlechars=[(c,f.Rect(c['bbox'])*page.rotation_matrix) for c in s2.chars(page,True)] if title_xmax else []
    for number in numbers:
        nr=f.Rect(number['display_rect']);cy=(nr.y0+nr.y1)/2
        possible=[r for r in rows if r['x']<.55 and len(key(clean_title(r['text'])))>=2
                  and -page.rect.height*.03<(r['display_rect'][1]+r['display_rect'][3])/2-cy<page.rect.height*.006]
        if not possible: continue
        row=min(possible,key=lambda r:abs((r['display_rect'][1]+r['display_rect'][3])/2-cy))
        title=row['text']
        if title_xmax:
            cells=[(c,r) for c,r in titlechars if (r.x0+r.x1)/2<page.rect.width*title_xmax
                   and row['display_rect'][1]-page.rect.height*.003<(r.y0+r.y1)/2<row['display_rect'][3]+page.rect.height*.003]
            title=''.join(c['c'] for c,r in sorted(cells,key=lambda item:item[1].x0))
        entries.append(dict(row,title=clean_title(title),labels=[number['label']],multi=False,token=number['label'],
                            number_rect=list(nr*page.derotation_matrix),ocr_number_confidence=number.get('ocr_confidence',100)))
    return entries

def review_toc_outliers(items):
    """Keep duplicate starts, but never approve a stray 104 between 10 and 18."""
    seq=[c for c in items if c['kind']=='toc' and c.get('role')=='number' and str(c.get('print_page','')).isdigit()]
    tails=[];positions=[];previous=[]
    for i,c in enumerate(seq):
        n=int(c['print_page']);at=bisect_right(tails,n)
        previous.append(positions[at-1] if at else -1)
        if at==len(tails):tails.append(n);positions.append(i)
        else:tails[at]=n;positions[at]=i
    keep=set();i=positions[-1] if positions else -1
    while i>=0:keep.add(i);i=previous[i]
    outliers=[c for i,c in enumerate(seq) if i not in keep];bad={c['id'] for c in outliers}
    for c in items:
        if c['kind']=='toc' and (c['id'] in bad or c['id'].removesuffix(':title') in bad):
            c.update(status='review',confidence=min(c['confidence'],.5),reason='TOC page label out of reading order; OCR review required')
    return [dict(id=c['id'],page=c['source_pdf_index0'],print_page=c['print_page']) for c in outliers]

def infer_title_map(doc,features,toc,index,cache,observations=None):
    from .titlemap import estimate_offsets
    probes=set()
    for i in toc:
        for columns in (1,2,3):
            for row in page_rows(doc[i],columns,cache.words(doc[i])):
                e=terminal_entry(row,len(doc)*2)
                if e and len(e['labels'])==1 and e['labels'][0].isdigit() and len(title_key(e['title']))>=4:
                    probes.add((int(e['labels'][0]),title_key(e['title'])))
    matches=defaultdict(set)
    for p in doc:
        if p.number<=max(toc,default=-1) or p.number in index: continue
        heading=key(''.join(w[4] for w in cache.words(p) if (f.Rect(w[:4])*p.rotation_matrix).y0<p.rect.height*.4))
        for label,probe in probes:
            grams=[probe[j:j+2] for j in range(len(probe)-1)]
            if probe in heading or (len(probe)>=6 and sum(g in heading for g in grams)/len(grams)>=.85):
                matches[(label,probe)].add(p.number)
    anchors=sorted({(label,i) for (label,probe),ids in matches.items() if len(ids)<=20 for i in ids})
    if observations:
        # TOC/index page lists and isolated table numbers are not folio evidence.
        observations=[[v for v in vs if i not in toc+index and
                       sum(any(q['style']==v['style'] and j-q['number']==i-v['number'] for q in observations[j])
                           for j in range(max(0,i-5),min(len(doc),i+6)))>=2]
                      for i,vs in enumerate(observations)]
    return estimate_offsets(anchors,len(doc),observations=observations)

def target_hit(title, corpus):
    probe=title_key(title); corpus=key(corpus)
    if not probe: return False
    if probe in corpus: return True
    if len(probe)<4: return False
    # Character bigram recall tolerates moderate OCR errors without using a two-character prefix alone.
    grams=[probe[i:i+2] for i in range(len(probe)-1)]
    return sum(g in corpus for g in grams)/len(grams)>=.7

def token_rect(page, row, token):
    clip=f.Rect(row['rect'])+(-.2,-.2,.2,.2)
    hits=page.search_for(token,clip=clip)
    if token.isdigit():
        eligible=[f.Rect(w[:4]) for w in page.get_text('words',clip=clip)
                  if re.search(r'(?<![0-9])'+re.escape(token)+r'(?![0-9])',normalized(w[4]))]
        hits=[r for r in hits if any(b.intersects(r) for b in eligible)]
    if hits: return list(hits[-1])
    if token.isdigit() or re.fullmatch(r'[0-9lIOoー]{1,4}',token):
        cs=[c for c in s2.chars(page) if clip.contains(f.Point((c['bbox'][0]+c['bbox'][2])/2,(c['bbox'][1]+c['bbox'][3])/2))]
        cs.sort(key=lambda c:(f.Rect(c['bbox'])*page.rotation_matrix).x0)
        parts=[normalized(c['c']) for c in cs]
        offsets=[i for i,part in enumerate(parts) for _ in part]
        content=''.join(parts)
        matches=list(re.finditer(r'(?<![0-9])'+re.escape(token)+r'(?![0-9])',content))
        if matches:
            m=matches[-1]; row['_char_fallback']=True
            return list(s2.box(cs[offsets[m.start()]:offsets[m.end()-1]+1]))
    # Unknown character alignment is review-only.
    return None

class PageCache:
    def __init__(self, source, work):
        self.root=work/'words'; self.root.mkdir(parents=True,exist_ok=True)
        st=source.stat(); self.identity=dict(path=str(source.resolve()),size=st.st_size,mtime_ns=st.st_mtime_ns,version=VERSION)
        manifest=work/'cache_identity.json'
        self.valid=manifest.exists() and read_json(manifest)==self.identity
        if not self.valid:
            for p in self.root.glob('*.json'): p.unlink()
            write_json(manifest,self.identity)
    def words(self,page):
        path=self.root/f'{page.number:05}.json'
        if path.exists(): return read_json(path)
        words=page.get_text('words'); write_json(path,words); return words

def autoconfig(source, work_root='work/auto', report_root=None, override=None):
    start=time.perf_counter(); source=Path(source).resolve(); work=Path(work_root)/slug(source)
    work.mkdir(parents=True,exist_ok=True); reportdir=Path(report_root)/slug(source) if report_root else work
    reportdir.mkdir(parents=True,exist_ok=True)
    with f.open(source) as doc:
        cache=PageCache(source,work); count=len(doc); observations=[]; features=[]; textpages=0
        for p in doc:
            words=cache.words(p); rows=page_rows(p,words=words)
            textpages+=sum(len(x[4].strip()) for x in words)>=10
            observations.append(printed_candidates(rows,count))
            options=[]
            for cols in (1,2,3):
                rr=page_rows(p,cols,words); entries=[e for r in rr if .035<r['y']<.965 and (e:=terminal_entry(r,count*2))]
                options.append((sum(len(e['labels']) for e in entries),cols,entries))
            _,cols,entries=max(options,key=lambda x:(x[0],-x[1]))
            text=''.join(x[4] for x in words)
            toptext=''.join(r['text'] for r in rows if r['y']<.2 and len(key(r['text']))<45)
            labels=[int(e['labels'][0]) for e in entries if e['labels'][0].isdigit()]
            increasing=sum(a<=b for a,b in zip(labels,labels[1:]))/max(1,len(labels)-1)
            mini_layout=len(entries)>=2 and min((e['y'] for e in entries),default=0)>.37 and sum(.08<r['y']<.35 for r in rows)<=3
            features.append(dict(page=p.number,mini_layout=mini_layout,columns=cols,entries=entries,increasing=increasing,
                toc_heading=bool(re.search(r'目\s*次|もくじ|CONTENTS',toptext,re.I)),
                index_heading=bool(re.search(r'索\s*引|さくいん|INDEX',toptext,re.I)),
                part_heading=bool(re.search(r'(?:PART|WORLD)\s*\d|第\s*\d+\s*[部編章]',text,re.I)),
                chars=sum(len(x[4]) for x in words)))
        metrics=dict(pages=count,text_layer_pages=textpages,text_layer_pct=round(100*textpages/count,2))
        if textpages<count*.5:
            metrics.update(status='OCR required',reason='OCR が必要',runtime=round(time.perf_counter()-start,2))
            write_json(work/'metrics.json',metrics); write_json(reportdir/'metrics.json',metrics)
            for root in {work.resolve(),reportdir.resolve()}:
                write_json(root/'config.json',dict(schema_version=4,automatic=True,source=str(source),work_dir=str(work),ocr_required=True))
                (root/'autoconfig_report.md').write_text(f'# {source.stem}\n\nOCR が必要。テキスト層あり {textpages}/{count} ページ。終了コード {OCR_EXIT}。\n',encoding='utf-8')
            raise OCRRequired('OCR が必要')
        mapping,mapmetrics=fit_pagemap(observations,count); mapmetrics['strategy']='printed_folio_consensus'
        write_json(work/'pagemap.json',mapping); pm=PageMap(mapping)
        toc=[]; index=[]; parts=[]
        for ft in features:
            i=ft['page']; es=ft['entries']; n=len(es)
            if i<max(12,count*.16) and not ft['mini_layout'] and n>=3 and ft['increasing']>=.65 and (ft['toc_heading'] or n>=7): toc.append(i)
            if i>count*.87 and n>=5 and (ft['index_heading'] or sum(e['multi'] for e in es)>=2 or (n>=18 and ft['increasing']<.7)): index.append(i)
        # Extend contiguous multi-page tables even when only the first has a heading.
        for selected,minimum in ((toc,3),(index,5)):
            for _ in range(4):
                for ft in features:
                    i=ft['page']
                    if i not in selected and not (selected is toc and ft['mini_layout']) and len(ft['entries'])>=minimum and any(abs(i-j)==1 for j in selected):
                        if selected is index or (ft['increasing']>=.65 and (ft['toc_heading'] or not ft['part_heading'])): selected.append(i)
            selected.sort()
        # Keep the front table cluster and the final back-index cluster, suppress isolated body lists.
        def clusters(ids):
            groups=[]
            for i in sorted(ids):
                if not groups or i>groups[-1][-1]+2: groups.append([i])
                else: groups[-1].append(i)
            return groups
        if toc:
            groups=clusters(toc)
            headed=[g for g in groups if any(features[i]['toc_heading'] for i in g)]
            toc=max(headed or groups,key=lambda g:sum(len(features[i]['entries']) for i in g))
        if index:
            groups=clusters(index)
            headed=[g for g in groups if any(features[i]['index_heading'] for i in g)]
            index=max(headed or groups,key=lambda g:(max(g),sum(len(features[i]['entries']) for i in g)))
        for i in toc:
            ft=features[i]; cols=toc_columns(doc[i],cache.words(doc[i]),ft['columns'])
            if cols!=ft['columns']:
                ft['columns']=cols
                ft['entries']=[e for r in page_rows(doc[i],cols,cache.words(doc[i])) if .035<r['y']<.965 and (e:=terminal_entry(r,count*2))]
                labels=[int(e['labels'][0]) for e in ft['entries'] if e['labels'][0].isdigit()]
                ft['increasing']=sum(a<=b for a,b in zip(labels,labels[1:]))/max(1,len(labels)-1)
        for ft in features:
            i=ft['page']
            if max(toc,default=0)<i<min(index,default=int(count*.96)) and len(ft['entries'])>=2 and (ft['mini_layout'] or ft['part_heading']) and (ft['mini_layout'] or ft['chars']<1400) and ft['increasing']>=.7 and (ft['mini_layout'] or sum(.08<r['y']<.35 for r in page_rows(doc[i],words=cache.words(doc[i])))<=4): parts.append(i)
        if mapmetrics['coverage']<90:
            inferred=infer_title_map(doc,features,toc,index,cache,observations)
            if inferred:
                mapping=inferred; pm=PageMap(mapping); write_json(work/'pagemap.json',mapping)
                ev=inferred['evidence']
                mapmetrics.update(coverage=round(100*len(pm.by_index)/count,2),consistency=100*ev['readable_agreement'] if ev['readable_agreement'] is not None else None,
                                  strategy='toc_heading_consensus',confidence=ev['confidence'],inferred_from_titles=ev)
        low=1; high=max((int(k) for k in pm.by_label if k.isdecimal()),default=count)
        index_labels=[int(pm.by_index[i]) for i in index if i in pm.by_index and pm.by_index[i].isdigit()]
        body_high=min(high,min(index_labels)-1) if index_labels else high
        body_high=max(1,body_high)
        override_data=read_json(override) if override else {}
        cfg=dict(schema_version=4,automatic=True,source=str(source),work_dir=str(work),pagemap=str(work/'pagemap.json'),
                 toc_pages=toc,part_pages=parts,index_pages=index,toc_ymin_ratio=.035,part_ymin_ratio=.12,output=str(work/'linked.pdf'),
                 index=dict(print_range=[low,body_high],columns=2,ymin_ratio=.035,ymax_ratio=.965),
                 pageref=dict(print_range=[low,high]),nav=nav_geometry(doc[0].rect.width,doc[0].rect.height),nav_unit='chapter',
                 confidence=dict(pagemap=mapmetrics['confidence'],toc=.96 if toc else 0,index=.95 if index else 0,part=.85 if parts else 0))
        if override: merge(cfg,override_data)
        cfg['source']=str(source)
        work=Path(cfg['work_dir']); work.mkdir(parents=True,exist_ok=True)
        # Manual paths and detected page selections take precedence.
        if isinstance(cfg['pagemap'],dict):
            manual_path=work/'manual_pagemap.json';write_json(manual_path,cfg['pagemap']);cfg['pagemap']=str(manual_path)
        pm=PageMap(read_json(cfg['pagemap'])); toc=cfg['toc_pages']; parts=cfg['part_pages']; index=cfg['index_pages']
        for kind in ('toc','part','index'):
            if kind+'_page_range' in override_data:
                a,b=override_data[kind+'_page_range']
                if type(a) is not int or type(b) is not int or not 0<=a<=b<count: raise ValueError(kind+'_page_range が不正です')
                cfg[kind+'_pages']=list(range(a,b+1))
        toc=cfg['toc_pages'];parts=cfg['part_pages'];index=cfg['index_pages']
        mapmetrics['coverage']=round(100*len(pm.by_index)/count,2)
        if 'pagemap' in override_data:
            mapmetrics['manual_override']=True
            mapmetrics.update(automatic_confidence=mapmetrics['confidence'],confidence=1,
                              previous_automatic_strategy=mapmetrics['strategy'],strategy='manual_override',
                              manual_pagemap=str(cfg['pagemap']),consistency=None)
            cfg['confidence']['pagemap']=1
            numeric=[int(lab) for lab in pm.by_label if lab.isdecimal()]
            if numeric:
                lo,hi=min(numeric),max(numeric)
                if 'print_range' not in override_data.get('pageref',{}): cfg['pageref']['print_range']=[lo,hi]
                if 'print_range' not in override_data.get('index',{}):
                    indexnums=[int(pm.by_index[i]) for i in index if i in pm.by_index and pm.by_index[i].isdecimal()]
                    cfg['index']['print_range']=[lo,max(lo,min(indexnums)-1) if indexnums else hi]
        for field,kind in (('toc_pages','toc'),('part_pages','part'),('index_pages','index')):
            if field in override_data: cfg['confidence'][kind]=1
        items=[]; selected_entries=[]
        heading_ocr=None
        number_ocr=None
        if cfg.get('heading_ocr'):
            from .localocr import HeadingOCR
            heading_ocr=HeadingOCR(work,cache.identity,cfg['heading_ocr'])
        if cfg.get('toc_number_ocr'):
            from .localocr import TocNumberOCR
            number_ocr=TocNumberOCR(work,cache.identity,cfg['toc_number_ocr'])
        @lru_cache(maxsize=64)
        def text(i): return ' '.join(w[4] for w in cache.words(doc[i])) if 0<=i<count else ''
        def add_entry(kind,i,e,label,term=None):
            target=pm.by_label.get(label); r=e.get('number_rect') or token_rect(doc[i],e,label)
            if r is None and len(e['labels'])==1: r=token_rect(doc[i],e,e['token'])
            confident=target is not None and r is not None
            hit=target_hit(term or e['title'],text(target)+text(target-1 if kind=='index' else target+1)) if target is not None else False
            raw_hit=hit
            if not hit and kind=='toc' and target is not None and heading_ocr:
                hit=target_hit(term or e['title'],heading_ocr.text(doc[target]))
            # A clean terminal page label plus a consistent printed-page map is
            # independent evidence; fuzzy title matching remains an evaluation metric.
            ordered=features[i]['increasing']>=.7
            conf=.99 if confident and hit else (.97 if confident and kind in ('toc','part_toc') and ordered and mapmetrics['confidence']>=.95 else .8)
            if e.get('number_rect') and e.get('ocr_number_confidence',0)<85: conf=min(conf,.8)
            rect=r or e['rect']
            c=s2.candidate(kind,i,target,rect,rect,conf,title=e['title'],term=term or e['title'],print_page=label,
                           role='number',level=e.get('level',1),method='normalized_words',semantic_hit=hit,
                           semantic_hit_text_layer=raw_hit,semantic_hit_header_ocr=hit and not raw_hit,
                           toc_number_ocr_confidence=e.get('ocr_number_confidence'),
                           reason='target text match' if hit else 'target absent / OCR / page map uncertainty')
            c['id']=f'{kind}:{i}:{len(items)}'; items.append(c)
            if kind in ('toc','part_toc') and r:
                display=f.Rect(e.get('display_rect',e['rect'])); number=f.Rect(r)*doc[i].rotation_matrix
                titlebox=f.Rect(display.x0,display.y0,max(display.x0,number.x0-doc[i].rect.width*.002),display.y1)
                if not titlebox.is_empty:
                    tr=list(titlebox*doc[i].derotation_matrix)
                    titlec=dict(c,role='title',rect=tr,intended_box=tr,id=c['id']+':title')
                    items.append(titlec)
            return c
        charindex=[]
        indexcache=work/'index_cache'; indexcache.mkdir(parents=True,exist_ok=True)
        extraction_version=hashlib.sha256(Path(s3.__file__).read_bytes()+Path(s2.__file__).read_bytes()).hexdigest()
        mapping_digest=hashlib.sha256(json.dumps(read_json(cfg['pagemap']),sort_keys=True).encode()).hexdigest()
        for kind,pages in (('toc',toc),('part_toc',parts),('index',index)):
            for i in pages:
                ft=features[i]; es=ft['entries']
                columns=override_data.get(kind if kind=='index' else 'toc',{}).get('columns')
                ymin=cfg['index'].get('ymin_ratio',.035) if kind=='index' else cfg.get('part_ymin_ratio' if kind=='part_toc' else 'toc_ymin_ratio',.035)
                ymax=cfg['index'].get('ymax_ratio',.965) if kind=='index' else .965
                if columns:
                    es=[e for r in page_rows(doc[i],columns,cache.words(doc[i])) if ymin<r['y']<ymax and (e:=terminal_entry(r,count*2))]
                else: es=[e for e in es if ymin<e['y']<ymax]
                if kind in ('toc','part_toc') and doc[i].rotation==0:
                    charrows=[]
                    for cs in s2.rows(doc[i],ymin=doc[i].rect.height*ymin,ymax=doc[i].rect.height*ymax,
                                      columns=columns or ft['columns'],include_spaces=False,tolerance_ratio=.00824):
                        rect=s2.box(cs); row=dict(text=''.join(c['c'] for c in cs),rect=list(rect),display_rect=list(rect),
                            x=rect.x0/doc[i].rect.width,y=rect.y0/doc[i].rect.height,size=rect.height/doc[i].rect.height,
                            col=min((columns or ft['columns'])-1,int(rect.x0/doc[i].rect.width*(columns or ft['columns']))))
                        entry=terminal_entry(row,count*2)
                        if entry: charrows.append(entry)
                    es=charrows
                if kind=='toc' and number_ocr:
                    repaired=toc_from_number_strip(doc[i],cache.words(doc[i]),number_ocr.numbers(doc[i]),number_ocr.settings.get('title_xmax_ratio'))
                    if repaired:
                        es=repaired
                        labels=[int(e['labels'][0]) for e in es]
                        ft['increasing']=sum(a<=b for a,b in zip(labels,labels[1:]))/max(1,len(labels)-1)
                es=[e for e in es if not (all(not lab.isdigit() for lab in e['labels']) and not any(lab in pm.by_label for lab in e['labels']))]
                hierarchy(es)
                if kind=='index' and doc[i].rotation==0:
                    icfg=dict(cfg['index'],ymin=doc[i].rect.height*cfg['index'].get('ymin_ratio',.035),
                              ymax=doc[i].rect.height*cfg['index'].get('ymax_ratio',.965),
                              padding=doc[i].rect.width*.0017,row_tolerance_ratio=.00824)
                    icfg['columns']=ft['columns'] if 'columns' not in override_data.get('index',{}) else cfg['index']['columns']
                    icfg['digit_gap_ratio']=cfg['index'].get('digit_gap_ratio',1.2)
                    icfg['analysis_scale']=min(1,800/doc[i].rect.width)
                    if 'columns' not in override_data.get('index',{}):
                        boundaries=index_boundaries(doc[i],cache.words(doc[i]))
                        icfg.update(column_boundaries=boundaries,columns=len(boundaries)+1)
                    signature=hashlib.sha256(json.dumps([cache.identity,icfg,mapping_digest,extraction_version],sort_keys=True).encode()).hexdigest()[:16]
                    indexpath=indexcache/f'{i:05}_{signature}.json'
                    if indexpath.exists(): rawindex=read_json(indexpath)
                    else:
                        rawindex=s3.extract_index(doc,[i],pm,icfg)
                        write_json(indexpath,rawindex)
                    charindex.extend(rawindex)
                    continue
                for e in es:
                    if kind=='part_toc' and all(pm.by_label.get(lab,-1)<=i for lab in e['labels']): continue
                    for lab in e['labels']:
                        c=add_entry(kind,i,e,lab)
                        if kind=='toc': selected_entries.append(c)
        s3.semantic_index(doc,charindex,text_provider=text)
        for c in charindex:
            t=c['target_pdf_index0']
            c['semantic_hit']=target_hit(c['term'],text(t)+text(t-1)) if t is not None else False
            items.append(c)
        excluded=set(toc+parts+index)
        refcfg=dict(cfg['pageref'],normalized=True,padding_ratio=.0017,analysis_width=800)
        charrefs=s3.extract_pagerefs(doc,[i for i in range(count) if doc[i].rotation==0],excluded,pm,refcfg)
        items.extend(charrefs)
        for p in doc:
            if p.number in excluded: continue
            words=cache.words(p); rows=page_rows(p,words=words)
            tableheads=[r for r in rows if '参照頁' in normalized(r['text']) or '参照ページ' in normalized(r['text'])]
            for row in rows:
                if not .075<row['y']<.91: continue
                for ref in (s3.parse_pagerefs(row['text'],*cfg['pageref']['print_range']) if p.rotation else []):
                    token=row['text'][ref['start']:ref['end']]; r=token_rect(p,row,token)
                    target=pm.by_label.get(str(ref['number'])) if ref['valid'] else None
                    rect=r or row['rect']; conf=ref['confidence'] if r and target is not None else .6
                    external=s3.external_page_reference(row['text'],ref['start'])
                    if external: target=None;conf=.4
                    items.append(s2.candidate('pageref',p.number,target,rect,rect,conf,token=token,print_page=str(ref['number']),method='explicit_page_unit',external_citation=external))
            if tableheads:
                # Only bare numbers geometrically below the reference-column heading.
                matches=p.search_for('参照頁') or p.search_for('参照ページ')
                for hr in matches:
                        stop=min((q.y0 for q in matches if q.y0>hr.y1 and abs(q.x0-hr.x0)<p.rect.width*.02),default=p.rect.height*.94)
                        for word in words:
                            if not hr.y1<word[1]<stop or abs((word[0]+word[2]-hr.x0-hr.x1)/2)>hr.width/2+p.rect.width*.005: continue
                            if not re.fullmatch(r'\d{1,4}',normalized(word[4])): continue
                            lab=str(int(normalized(word[4]))); target=pm.by_label.get(lab); rect=list(word[:4])
                            items.append(s2.candidate('pageref',p.number,target,rect,rect,.97 if target is not None else .5,token=lab,print_page=lab,method='reference_table_column'))
        if cfg.get('manual_toc'):
            items=[c for c in items if c['kind']!='toc']
            manual=s2.manual_toc(doc,read_json(cfg['manual_toc']),pm,doc[0].rect.width*.0017)
            items.extend(manual)
            selected_entries=[c for c in manual if c['role']=='title']
        tocorder=[] if cfg.get('manual_toc') else review_toc_outliers(items)
        # Normalize overlaps into review instead of inserting ambiguous rectangles.
        boxes=defaultdict(list)
        for c in items:
            if c['status']!='approved': continue
            r=f.Rect(c['rect'])
            if any(r.intersects(b) for b in boxes[c['source_pdf_index0']]):
                c.update(status='review',confidence=.7,reason='overlapping candidate')
            else: boxes[c['source_pdf_index0']].append(r)
        starts=navigation.section_starts([c['target_pdf_index0'] for c in selected_entries if c['status']=='approved'],cfg['nav'].get('extra_starts',[]),count)
        navreview=0; navmeasure=[]; navreport={}
        if toc and starts:
            end=min(index) if index else count
            navcfg=dict(cfg['nav'])
            firstlabel=next(((i,int(lab)) for i,lab in pm.by_index.items() if lab.isdigit()),None)
            if firstlabel: navcfg['_right_parity']=(firstlabel[0]+1-firstlabel[1])%2
            placements,navreport=navigation.plan(doc,range(cfg['nav'].get('first_page',max(toc)+1),cfg['nav'].get('last_page',end-1)+1),navcfg)
            navmeasure=navreport['measurements']; navreview=sum(m['status']=='review' for m in navmeasure)
            for i,(regions,geo) in placements.items():
                p=doc[i]
                if any(any((region*p.derotation_matrix).intersects(b) for b in boxes[i]) for region in regions):
                    navreview+=1
                    next(m for m in navmeasure if m['page']==i).update(status='review',reason='link rectangle collision; skipped')
                    continue
                for j,t in enumerate(s2.nav_targets(i,starts,min(toc),min(index) if index else min(toc))):
                    r=list(regions[j]*p.derotation_matrix)
                    items.append(s2.candidate('nav',i,t,r,r,1,button=j,method='normalized_margin',style=geo))
        outline=[]; prev=0
        for c in sorted((c for c in selected_entries if c['status']=='approved'),key=lambda c:c['target_pdf_index0']):
            level=min(c['level'],prev+1); outline.append([level,c['title'],c['target_pdf_index0']+1,0]); prev=level
        write_json(work/'stage3_candidates.json',dict(schema_version=4,candidates=items)); write_json(work/'outline.json',dict(entries=outline))
        write_json(work/'nav_margin_stage3.json',navreport)
        write_json(reportdir/'navigation.json',navreport)
        write_json(work/'decisions.json',dict(pagemap=mapmetrics,pages=[{k:v for k,v in ft.items() if k!='entries'} for ft in features]))
        tocitems=[c for c in items if c['kind']=='toc' and c.get('role')=='number']; idxitems=[c for c in items if c['kind']=='index']
        pct=lambda seq: round(100*sum(c.get('semantic_hit',False) for c in seq)/len(seq),2) if seq else None
        metrics.update(pagemap=mapmetrics,toc_pages=toc,part_pages=parts,index_pages=index,toc_entries=len(tocitems),
            levels=len({c['level'] for c in tocitems}),toc_target_hit_pct=pct(tocitems),index_target_hit_pct=pct(idxitems),
            toc_text_layer_hit_pct=round(100*sum(c.get('semantic_hit_text_layer',False) for c in tocitems)/len(tocitems),2) if tocitems else None,
            toc_order_review=tocorder,
            index_entries=len({(c['source_pdf_index0'],c['term']) for c in idxitems}),index_links=len(idxitems),
            pagerefs=sum(c['kind']=='pageref' for c in items),review_counts=dict(Counter(c['kind'] for c in items if c['status']!='approved')),
            pageref_status_counts=dict(Counter(c['status'] for c in items if c['kind']=='pageref')),
            pageref_external_citations=sum(bool(c.get('external_citation')) for c in items if c['kind']=='pageref'),
            nav_review_pages=navreview,link_counts=dict(Counter(c['kind'] for c in s2.approved(items))),runtime=round(time.perf_counter()-start,2),peak_memory_mb=memory_peak_mb())
        localindex=[c for c in idxitems if not cfg['index'].get('volume') or c.get('volume')==cfg['index']['volume']]
        metrics['index_local_volume_hit_pct']=pct(localindex)
        metrics['index_foreign_volume_links']=sum(c.get('volume') is not None and c.get('volume')!=cfg['index'].get('volume') for c in idxitems)
        metrics['review_ratio']=round(sum(c['status']!='approved' for c in items if c['kind']!='nav')/max(1,sum(c['kind']!='nav' for c in items)),4)
        if heading_ocr: metrics['heading_ocr']=heading_ocr.stats
        if number_ocr: metrics['toc_number_ocr']=number_ocr.stats
        metrics['nav_decisions']=navreport.get('decisions',{})
        metrics['partial_text_layer_missing_pages']=[ft['page'] for ft in features if ft['chars']<10]
        metrics['status']='ok' if toc and mapmetrics['coverage']>=90 and (pct(tocitems) or 0)>=85 and (not idxitems or pct(idxitems)>=80) and metrics['review_ratio']<=.10 and navreview/max(1,len(navmeasure))<=.05 else 'needs review'
        tail_missing=sum(features[i]['chars']<10 for i in range(max(0,count-12),count))
        if tail_missing>=8:
            metrics.update(status='OCR required',reason='OCR が必要（末尾12頁のうち8頁以上でテキスト層欠落。索引の有無・範囲は画像確認が必要）')
        write_json(work/'config.json',cfg); write_json(work/'metrics.json',metrics)
        write_json(reportdir/'config.json',cfg); write_json(reportdir/'metrics.json',metrics)
        report=f"# {source.stem}\n\n完全オフライン・ルール判定。ページ番号は PDF index0。Goodnotes 実機未検証。\n\n"
        report+=f"- テキスト層: {textpages}/{count} ページ\n- 目次: {toc} / {len(tocitems)}項目 / {metrics['levels']}階層\n- 扉・小目次: {parts}\n- 索引: {index} / {len(idxitems)}リンク候補\n- ページ対応: 被覆率 {mapmetrics['coverage']}%、観測一致率 {mapmetrics['consistency']}%、競合 {mapmetrics['conflicts']}\n- 判断信頼度: {cfg['confidence']}（経験則スコア、確率ではありません）\n- 目次移動先タイトル一致: {pct(tocitems)}%（移動先または次頁）\n- 索引語一致: {pct(idxitems)}%（移動先または前頁）\n- 本文参照: {metrics['pagerefs']}\n- 要確認: {metrics['review_counts']} / ナビ余白不足 {navreview}頁\n- 時間: {metrics['runtime']}秒\n- 状態: {metrics['status']}\n\n"
        doubts=[]
        if mapmetrics['coverage']<50: doubts.append('印刷番号の観測が不足し、見出し照合からも安定したページ対応を確定できません。pagemap の手動指定が必要です。')
        elif mapmetrics['coverage']<90: doubts.append('ページ対応の未確定区間が多いため、欠落番号・飛びページを確認してください。')
        if not toc: doubts.append('目次を確定できませんでした。目次ページ範囲を指定してください。')
        elif (pct(tocitems) or 0)<80: doubts.append('目次と移動先見出しの照合が弱めです。OCR誤字、著者名や装飾の混入、段組境界とページ対応を確認してください。')
        if idxitems and (pct(idxitems) or 0)<80: doubts.append('索引語の照合が弱めです。複合語の分断、継続行の結合、OCR誤字、前頁参照とページ対応を確認してください。')
        if navreview: doubts.append(f'{navreview}ページでは余白を確保できず、ナビを省略しました。')
        if doubts: report+='\nこの本の要確認点:\n'+''.join('- '+d+'\n' for d in doubts)+'\n'
        report+='一致率は全候補を分母とし、未解決も不一致。文字正規化と二文字連鎖の70%以上一致で評価。正解率の保証ではありません。\n\n疑問点: OCR 誤字、ページ番号の欠落・重複、縦書き、見開き、段組境界、数字だけの行は確認が必要です。低信頼候補は埋め込みません。検出なしは「存在しない」とは断定しません。\n\n編集: config.json のページ選択・pagemap パスを修正し --config で再実行。候補の個別補正は candidate_overrides を使用。\n'
        for root in {work.resolve(),reportdir.resolve()}: (root/'autoconfig_report.md').write_text(report,encoding='utf-8')
        report+='\nナビ配置・余白統計: navigation.json（左右別に固定、原稿のインクを検出したページのみ微調整または省略）。\n'
        report+=f"\nページ対応方式: {mapmetrics['strategy']} / 経験的信頼スコア {mapmetrics['confidence']}。\n本文参照の内訳: {metrics['pageref_status_counts']}（候補総数は埋め込み件数ではありません）。\n"
        if heading_ocr:
            report+=f"\n既存文字層だけの目次照合 {metrics['toc_text_layer_hit_pct']}%、上部見出しのローカルOCR併用後 {metrics['toc_target_hit_pct']}%。OCR範囲・実行回数: {heading_ocr.stats}。元PDFにOCR層は追加していません。\n"
        if number_ocr: report+=f"\n目次右端10%の数字だけをローカルOCRで再計測: {number_ocr.stats}。本文・索引・元PDFの文字層は変更しません。\n"
        if cfg['index'].get('volume'): report+=f"\n共通索引: 自巻 {cfg['index']['volume']} の照合 {metrics['index_local_volume_hit_pct']}%、別巻 {metrics['index_foreign_volume_links']}参照は内部PDFの範囲外のためreview。全候補の索引照合率は別巻も分母に残しています。\n"
        for root in {work.resolve(),reportdir.resolve()}: (root/'autoconfig_report.md').write_text(report,encoding='utf-8')
        body_nav={c['source_pdf_index0'] for c in items if c['kind']=='nav' and features[c['source_pdf_index0']]['chars']>=300 and c['source_pdf_index0'] not in parts}
        body_previews=[min((i for i in body_nav if i%2==parity),key=lambda i:abs(i-count//2),default=None) for parity in (0,1)]
        metrics['nav_preview_pages']=[i for i in body_previews if i is not None]
        write_json(work/'metrics.json',metrics);write_json(reportdir/'metrics.json',metrics)
        previewids=list(dict.fromkeys(toc[:1]+parts[:1]+index[:1]+metrics['nav_preview_pages']))
        for old in reportdir.glob('preview_*.png'): old.unlink()
        for i in previewids:
            # Reopen for previews so render-only overlays never affect the source or planning document.
            with f.open(source) as preview:
                p=preview[i]
                for c in s2.approved(items):
                    if c['kind']=='nav' and c['source_pdf_index0']==i: s2.draw_button(p,c)
                scale=800/p.rect.width
                p.get_pixmap(matrix=f.Matrix(scale,scale)).save(reportdir/f'preview_{i:04}.png')
    return cfg,metrics

def build(source, output, work_root='work/auto', report_root=None, override=None):
    source=Path(source).resolve(); output=Path(output).resolve()
    if source==output or (output.exists() and output.samefile(source)): raise ValueError('元 PDF は上書きできません')
    # Legacy full manual config retains the original Stage 3 path.
    if override and not read_json(override).get('automatic',False) and 'extract_output' in read_json(override):
        cfg=read_json(override); cfg.update(source=str(source),output=str(output)); path=Path(cfg['work_dir'])/'build_config.json'; write_json(path,cfg)
        return s3.run(path)
    cfg,metrics=autoconfig(source,work_root,report_root,override)
    if metrics.get('status')=='OCR required': raise OCRRequired(metrics.get('reason','OCR が必要'))
    return build_prepared(cfg,output,report_root)

def build_prepared(cfg,output,report_root=None):
    source=Path(cfg['source']).resolve(); output=Path(output).resolve()
    if source==output or (output.exists() and output.samefile(source)): raise ValueError('元 PDF は上書きできません')
    work=Path(cfg['work_dir']); items=read_json(work/'stage3_candidates.json')['candidates']; outline=read_json(work/'outline.json')['entries']
    items=s3.overrides(items,cfg.get('pageref_overrides'))
    items=s3.overrides(items,cfg.get('candidate_overrides'))
    if cfg.get('outline_overrides'): outline=read_json(cfg['outline_overrides'])['entries']
    before=source_hash(source)
    s2.apply_candidates(source,output,items,outline,[str(output)])
    report=s2.verify_candidates(source,output,items,outline,strict=True)
    report['source_unchanged']=before==source_hash(source)
    report['passed']=report['passed'] and report['source_unchanged']
    write_json(work/'stage3_verify.json',report)
    if report_root: write_json(Path(report_root)/slug(source)/'verify.json',report)
    return report
