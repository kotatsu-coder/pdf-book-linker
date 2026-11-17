"""Local body references, semantic index checks and positioned outlines.

Coordinates always refer to the unrotated source. Editable JSON is never uploaded.
"""
import copy
import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import pymupdf as f
from . import stage2 as s2
from .pagemap import PageMap
from .storage import read_json, write_json
from .linking import save_pdf


def norm(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def parse_pagerefs(text, low, high):
    """Return source offsets; explicit page units prevent P100 / P2Y2 matches."""
    parts = [unicodedata.normalize('NFKC', c) for c in text]
    offsets = [i for i, p in enumerate(parts) for _ in p]
    t = ''.join(parts)
    digit = r'[0-9lIOo]'
    # Broken OCR may insert hyphens inside a Japanese page number. Never
    # interpret those as a range: only pp. explicitly permits a range.
    pattern = (rf'(?<![A-Za-z0-9])(?:(?P<jp>{digit}(?:[\s-]*{digit}){{0,3}})\s*-?\s*(?:ページ|頁)'
               rf'|(?P<prefix>[pP]{{1,2}}\.)\s*(?P<en>{digit}{{1,4}})'
               rf'(?:\s*[-–—~〜～]\s*(?P<end>{digit}{{1,4}}))?)(?![A-Za-z0-9])')
    out = []
    for m in re.finditer(pattern, t):
        raw = m['jp'] or m['en']
        if m['jp'] and not re.search(r'\d',raw): continue
        a = int(re.sub(r'[\s-]', '', raw).translate(s2.DIGITS))
        b = int((m['end'] or str(a)).translate(s2.DIGITS))
        damaged = bool(re.search(r'[lIOo-]', raw))
        valid = low <= a <= b <= high
        if m['end'] and len(m['prefix']) != 3: valid = False
        out.append(dict(start=offsets[m.start()], end=offsets[m.end()-1]+1,
                        number=a, range_end=b, ocr_fixed=damaged, valid=valid,
                        confidence=.75 if damaged else .99))
    return out


def body_rows(page, relative=False):
    groups=[]
    selected=[c for c in s2.chars(page,True) if (page.rect.height*.035 if relative else 100)<c['bbox'][1]<(page.rect.height*.965 if relative else page.rect.height-120) and c['bbox'][0]<page.rect.width*.94]
    for c in sorted(selected,key=lambda c:c['bbox'][3]):
        baseline=c['bbox'][3]
        if not groups or abs(baseline-groups[-1][0])>(page.rect.height*.00573 if relative else 16): groups.append([baseline,[c]])
        else: groups[-1][1].append(c)
    result=[]
    for _,group in groups:
        segment=[]
        for c in sorted((c for c in group if not c['c'].isspace()),key=lambda c:c['bbox'][0]):
            if segment:
                previous=segment[-1]; gap=c['bbox'][0]-previous['bbox'][2]
                ha=previous['bbox'][3]-previous['bbox'][1]; hb=c['bbox'][3]-c['bbox'][1]
                # OCR often turns the table's left rule into a tiny I/1 before 58頁.
                border=previous['c'] in '1Il|' and ha<hb*.65 and gap>hb*.3
                if gap>max(page.rect.width*.012,max(ha,hb)*1.5) or border:
                    result.append(segment);segment=[]
            segment.append(c)
        if segment: result.append(segment)
    return result


def external_page_reference(text,start,preceding=''):
    """Explicit book/edition citations must never resolve against the local PDF."""
    prefix=unicodedata.normalize('NFKC',text[max(0,start-100):start])
    strong=r'教科書|著書|第\s*[0-9一二三四五六七八九十]+\s*版|(?:19|20)\d{2}\s*\)\s*$'
    if re.search(strong,prefix): return True
    if re.search(r'本書|本章',prefix): return False
    if re.search(r'「[^」]{2,40}」[^\w]{0,5}$',prefix): return True
    return bool(re.search(strong,unicodedata.normalize('NFKC',preceding[-100:])+prefix))


def extract_pagerefs(doc, pages, excluded, pm, cfg):
    out = []
    for i in pages:
        if i in excluded: continue
        lines=body_rows(doc[i],cfg.get('normalized',False))
        # Join line-wrapped units only; each physical line gets a tight box.
        for k in range(len(lines)-1):
            t=''.join(c['c'] for c in lines[k])
            nxt=''.join(c['c'] for c in lines[k+1])
            tail=re.search(r'[0-9lIOo]{1,4}(?:ペ(?:ー(?:ジ)?)?)?$',t)
            wrapped=re.match(r'[0-9lIOo]{1,4}(?:ページ|頁)',(tail[0] if tail else '')+nxt[:12])
            vertical_gap=lines[k+1][0]['bbox'][1]-lines[k][-1]['bbox'][1] if lines[k] and lines[k+1] else 0
            lineheight=lines[k][-1]['bbox'][3]-lines[k][-1]['bbox'][1] if lines[k] else 0
            if tail and wrapped and wrapped.end()>len(tail[0]) and 0<vertical_gap<max(doc[i].rect.height*.04,3*lineheight):
                lines[k]=lines[k]+lines[k+1]; lines[k+1]=[]
        for lineindex,cs in enumerate(lines):
            if not cs: continue
            text = ''.join(c['c'] for c in cs)
            previous=''.join(''.join(ch['c'] for ch in line) for line in lines[max(0,lineindex-2):lineindex]
                             if line and 0<cs[0]['bbox'][1]-line[0]['bbox'][1]<doc[i].rect.height*.04
                             and abs(cs[0]['bbox'][0]-line[0]['bbox'][0])<doc[i].rect.width*.3)
            for n in parse_pagerefs(text, *cfg['print_range']):
                chunk = cs[n['start']:n['end']]
                physical=[]
                for char in chunk:
                    if not physical or abs(char['bbox'][3]-physical[-1][-1]['bbox'][3])>(doc[i].rect.height*.00717 if cfg.get('normalized') else 20): physical.append([])
                    physical[-1].append(char)
                r, ib, method = s2.tight(doc[i], physical[0], (doc[i].rect.width*cfg['padding_ratio'] if cfg.get('padding_ratio') else cfg.get('padding', 3)),scale=min(1,cfg.get('analysis_width',doc[i].rect.width)/doc[i].rect.width))
                c = s2.candidate('pageref', i, pm.by_label.get(str(n['number'])) if n['valid'] else None,
                                 r, ib, n['confidence'], text=text, token=text[n['start']:n['end']],
                                 print_page=str(n['number']), range_end=n['range_end'], method=method,
                                 ocr_fixed=n['ocr_fixed'], reason='explicit page unit; within mapped arabic range')
                c['id'] = f'pageref:{i}:{n["start"]}:{round(r[1])}'
                if not n['valid']:
                    c.update(status='rejected', confidence=0, reason='outside arabic page range or invalid range')
                elif n['ocr_fixed']:
                    c['reason'] = 'OCR digit/dash damage: image confirmation required'
                if external_page_reference(text,n['start'],previous):
                    c.update(target_pdf_index0=None,status='review' if n['valid'] else 'rejected',
                             confidence=.4 if n['valid'] else 0,external_citation=True,
                             reason='external book / edition citation; not an internal page reference')
                c['logical_reference_id']=c['id']
                out.append(c)
                for j,part in enumerate(physical[1:],1):
                    cc=copy.deepcopy(c); cc['rect'],cc['intended_box'],cc['method']=s2.tight(doc[i],part,doc[i].rect.width*cfg['padding_ratio'] if cfg.get('padding_ratio') else cfg.get('padding',3))
                    cc['id']+=f':line{j}'; out.append(cc)
    return out


def join_index_digits(cs, ratio=.6):
    """Remove OCR whitespace only where adjacent digits have a subdigit gap.

    Commas remain explicit delimiters, including when OCR omits whitespace.
    """
    result = []
    for k, c in enumerate(cs):
        if c['c'].isspace():
            a = next((x for x in reversed(result) if not x['c'].isspace()), None)
            b = next((x for x in cs[k+1:] if not x['c'].isspace()), None)
            if a and b and a['c'].isdecimal() and b['c'].isdecimal():
                width = min(a['bbox'][2]-a['bbox'][0], b['bbox'][2]-b['bbox'][0])
                if b['bbox'][0]-a['bbox'][2] < ratio*width: continue
        if result and not c['c'].isspace() and not result[-1]['c'].isspace():
            a=result[-1]
            if not a['c'].isdecimal() and c['c'].isdecimal():
                width=min(a['bbox'][2]-a['bbox'][0],c['bbox'][2]-c['bbox'][0])
                if c['bbox'][0]-a['bbox'][2]>=ratio*width:
                    result.append(dict(c=' ',bbox=[a['bbox'][2],c['bbox'][1],c['bbox'][0],c['bbox'][3]]))
            if a['c'].isdecimal() and c['c'].isdecimal():
                width=min(a['bbox'][2]-a['bbox'][0],c['bbox'][2]-c['bbox'][0])
                if c['bbox'][0]-a['bbox'][2]>=ratio*width:
                    result.append(dict(c=' ',bbox=[a['bbox'][2],c['bbox'][1],c['bbox'][0],c['bbox'][3]]))
        result.append(c)
    return result


def extract_index(doc, pages, pm, cfg):
    out = []
    lastterm=''
    for i in pages:
        lines=s2.rows(doc[i], ymin=cfg.get('ymin',100), ymax=cfg.get('ymax',doc[i].rect.height*.96),
                      columns=cfg.get('columns',2), include_spaces=True, tolerance_ratio=cfg.get('row_tolerance_ratio'),boundaries=cfg.get('column_boundaries'))
        pending={}; carry={}; ditto_root={}
        column=lambda cs: sum(cs[0]['bbox'][0]/doc[i].rect.width>=b for b in cfg['column_boundaries']) if cfg.get('column_boundaries') else int(cs[0]['bbox'][0]/doc[i].rect.width*cfg.get('columns',2))
        bases={col:min((s2.box(cs).x0 for cs in lines if column(cs)==col),default=0)
               for col in range(cfg.get('columns',2))}
        for original in lines:
            cs = join_index_digits(original, cfg.get('digit_gap_ratio', .6))
            text = ''.join(c['c'] for c in cs)
            qualified={}; volume=unicodedata.normalize('NFKC',cfg.get('volume','')).upper()
            col = column(cs)
            if cfg.get('row_tolerance_ratio') and norm(text).isdigit() and s2.box(cs).y0>doc[i].rect.height*.88:
                if s2.box(cs).x0<doc[i].rect.width*.15 or s2.box(cs).x1>doc[i].rect.width*.85: continue
            indented=cs[0]['bbox'][0]>bases[col]+(doc[i].rect.width*.00857 if cfg.get('row_tolerance_ratio') else 15)
            if re.fullmatch(r'[A-Za-zあ-んァ-ヶ]|アルファベット|かな',norm(text)):
                pending[col]=''; carry[col]=''; continue
            # Replace the figure/table marker without shifting source offsets.
            parse_text=re.sub(r'(?<=\d)[iｉ](?=\s*[,、，]|\s*$)',' ',text)
            if volume:
                chars=list(parse_text)
                for match in re.finditer(r'(?P<n>\d{1,4})\s*[（(]\s*(?P<v>[ⅠⅡＩIil1\s]{1,6})\s*[）)]',text):
                    target_volume=unicodedata.normalize('NFKC',match['v']).upper().replace('1','I').replace('L','I')
                    target_volume=re.sub(r'\s+','',target_volume)
                    if target_volume not in ('I','II'): continue
                    qualified[match.start('n')]=target_volume
                    chars[match.end('n'):match.end()]=[' ']*(match.end()-match.end('n'))
                parse_text=''.join(chars)
            nums = s2.parse_numbers(parse_text, *cfg['print_range'])
            # Only numbers in the terminal page list, never digits within terms.
            # An italic i marks figure/table pages in some medical indexes.
            def terminal_suffix(n):
                suffix=re.sub(r'^\s*[iｉ](?=\s*[,、，]|\s*$)','',parse_text[n['end']:])
                return re.fullmatch(r'[\d\s,lIOoiｉ、，~～〜–—-]*',suffix) and (not suffix.strip() or re.match(r'\s*[,、，]',suffix))
            nums = [n for n in nums if terminal_suffix(n)]
            if not nums:
                pending[col]=(pending.get(col,'') if indented else '')+text.strip()
                continue
            term = text[:nums[0]['start']].strip(' ,、，.．…・·•_')
            if cfg.get('normalized') or cfg.get('row_tolerance_ratio'):
                term=re.split(r'[.．…・·•_]{2,}',term)[0].strip()
            if term: term=(pending.get(col,'') if indented else '')+term
            else: term=pending.get(col,'') or carry.get(col,'') or lastterm
            if cfg.get('row_tolerance_ratio'):
                ditto=re.match(r'^(?:[―—─ー－]+|一(?=[の、,]))\s*',term)
                if ditto and ditto_root.get(col): term=ditto_root[col]+term[ditto.end():].lstrip('、, ')
                elif not ditto and term: ditto_root[col]=term
            pending[col]=''; carry[col]=term; lastterm=term
            for n in nums:
                chunk = cs[n['start']:n['end']]
                r, ib, method = s2.tight(doc[i], chunk, cfg.get('padding',3),scale=cfg.get('analysis_scale',1))
                c = s2.candidate('index', i, pm.by_label.get(str(n['number'])) if n['valid'] else None,
                                 r, ib, n['confidence'] if n['valid'] else .4, text=text,
                                 token=text[n['start']:n['end']], print_page=str(n['number']),
                                 term=term, method=method, ocr_fixed=n['ocr_fixed'])
                c['id'] = f'index:{i}:{round(r[0])}:{round(r[1])}'
                if volume:
                    c['volume']=qualified.get(n['start'])
                    if c['volume']!=volume:
                        c.update(target_pdf_index0=None,status='review',confidence=.4,
                                 reason='foreign volume is outside this PDF' if c['volume'] else 'volume qualifier missing; review required')
                out.append(c)
    return out


def semantic_index(doc, items, text_provider=None):
    @lru_cache(maxsize=32)
    def text(i): return norm(text_provider(i) if text_provider else doc[i].get_text())
    changed = 0
    for c in items:
        term = re.sub(r'^[^\w\u3040-\u9fff]+', '', norm(c['term']))
        # Short terms, punctuation fragments and mixed OCR garbage are not evidence.
        m = re.match(r'[\u3040-\u30ff\u3400-\u9fff]{2,}|[A-Za-z]{3,}', term)
        garbage=bool(re.search(r'[！!「」『』]',term))
        if not m or garbage or not isinstance(c['target_pdf_index0'],int):
            c['semantic_check'] = 'excluded: short/garbage term'; continue
        t = c['target_pdf_index0']
        corpus = text(t) + (text(t-1) if t else '')
        probes = m[0][:4]
        hit = probes in corpus or probes[:2] in corpus
        c['semantic_check'] = 'pass' if hit else 'review: term absent on target and preceding page'
        if not hit:
            if c['status']=='approved': changed += 1
            c.update(confidence=min(c['confidence'],.8), status='review', reason=c['semantic_check'])
    return changed


def toc_boxes(doc, toc):
    """Recover terminal digits from image components; bound title by their left edge."""
    groups = defaultdict(dict)
    for c in toc: groups[(c['source_pdf_index0'],c['print_page'])][c['role']] = c
    for (i, _), pair in groups.items():
        if not {'title','number'} <= pair.keys(): continue
        title, number = pair['title'], pair['number']
        p = doc[i]
        region = f.Rect(number['rect'])
        region.x0=max(0,title['rect'][0]-10); region.x1=p.rect.width-80
        components = ink_components(p,region)
        count=len(number['print_page'])
        terminal=sorted(components,key=lambda r:r.x1)[-count:]
        ink=None
        for r in terminal: ink = r if ink is None else ink | r
        if ink:
            number.update(rect=s2.padded(ink,3,p.rect), intended_box=list(ink), method='terminal_digit_image_strip',
                          digit_ink_box=list(ink),digit_scan_rect=list(region))
        limit = number['rect'][0]-2
        # Re-measure title ink in the space strictly before all terminal digits.
        actual=None
        for comp in components:
            if comp.x1 < limit:
                actual=comp if actual is None else actual|comp
        if actual: title.update(rect=s2.padded(actual,2,p.rect),intended_box=list(actual))
        title['rect'][2]=min(title['rect'][2],limit)


def ink_components(page, region):
    pix=page.get_pixmap(clip=region,colorspace=f.csGRAY,alpha=False)
    data=pix.samples
    dark={(x,y) for y in range(pix.height) for x in range(pix.width) if data[y*pix.stride+x]<95}
    result=[]; minimum=max(6,pix.height*.25)
    while dark:
        seed=dark.pop(); stack=[seed]; xs=[]; ys=[]
        while stack:
            x,y=stack.pop(); xs.append(x); ys.append(y)
            for dx,dy in ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)):
                q=x+dx,y+dy
                if q in dark: dark.remove(q); stack.append(q)
        if max(ys)-min(ys)+1>=minimum:
            result.append(f.Rect(pix.x+min(xs),pix.y+min(ys),pix.x+max(xs)+1,pix.y+max(ys)+1))
    return result


def navigation_starts(toc, cfg):
    unit = cfg.get('nav_unit', 'part')
    if unit not in ('part','chapter'): raise ValueError('nav_unit must be part or chapter')
    entries = [c for c in s2.approved(toc) if c['kind']=='toc' and c['role']=='title']
    if unit=='chapter': return sorted({c['target_pdf_index0'] for c in entries if c['target_pdf_index0']<cfg['nav']['index_page']})
    parts = [c['target_pdf_index0'] for c in entries if re.match(r'PART\s*[0-9lI]',norm(c['title']),re.I)]
    # PART target from TOC is its title/mini-TOC page; first chapter is fallback.
    if not parts: raise ValueError('PART starts missing; supply approved TOC or choose chapter')
    back = [c['target_pdf_index0'] for c in entries if c['level']==1 and c['target_pdf_index0']>max(parts)]
    return sorted(set(parts + ([min(back)] if back else [])))


def heading_y(page, title):
    key = re.sub(r'^(?:PART\d+|\d{2})\s*', '', title)
    probes = [key, key[:4]] if len(key)>=4 else [key]
    for probe in probes:
        hits = page.search_for(probe)
        hits = [r for r in hits if 100 < r.y0 < page.rect.height*.18]
        if hits: return min(r.y0 for r in hits)
    return 0


def group_color_box(page, rect):
    region=(f.Rect(rect)+(-15,-65,15,45)) & page.rect
    pix=page.get_pixmap(clip=region,alpha=False); rgb=pix.samples
    positions=[]
    for y in range(pix.height):
        for x in range(pix.width):
            k=y*pix.stride+x*3
            if rgb[k+1]>rgb[k]+25 and rgb[k+2]>rgb[k]+15: positions.append((x,y))
    if len(positions)<8: return None
    return f.Rect(pix.x+min(x for x,y in positions),pix.y+min(y for x,y in positions),
                  pix.x+max(x for x,y in positions)+1,pix.y+max(y for x,y in positions)+1)


def build_outline(doc, toc, cfg):
    out = []; review = []
    entries = [copy.deepcopy(c) for c in s2.approved(toc) if c['role']=='title']
    frontmatter=cfg.get('outline',{}).get('front_matter')
    if frontmatter is None:
        firsttoc=min(cfg.get('toc_pages',[0]))
        frontmatter=[dict(title='表紙',pdf_index0=0)]
        preface=next((i for i in range(firsttoc) if doc[i].search_for('はじめに')),None)
        if preface is not None: frontmatter.append(dict(title='はじめに',pdf_index0=preface))
        if firsttoc: frontmatter.append(dict(title='目次',pdf_index0=firsttoc))
    for front in frontmatter:
        out.append([1, front['title'], front['pdf_index0']+1, heading_y(doc[front['pdf_index0']],front['title'])])
    for c in entries:
        title = c['title']; i = c['target_pdf_index0']
        if c['level']==2:
            # Chapter numbers are printed on the main TOC, except PART1 essays.
            page = doc[c['source_pdf_index0']]
            matches = [cs for cs in s2.rows(page, ymin=50) if abs(s2.box(cs).y0-f.Rect(c['rect']).y0)<40]
            for cs in matches:
                m = re.match(r'\s*(\d{2})', ''.join(x['c'] for x in cs))
                if m: title = m[1]+' '+title; break
        out.append([c['level'],title,i+1,heading_y(doc[i],title)])
    if cfg.get('outline',{}).get('include_index_groups', True):
        for i in cfg['index_pages']:
            rowsets=s2.rows(doc[i],ymin=100,columns=cfg['index'].get('columns',2),include_spaces=True)
            # OCR can put a heading on the preceding entry's baseline. Its
            # original text-line boundaries retain the separate centered glyph.
            for block in doc[i].get_text('rawdict')['blocks']:
                for line in block.get('lines',[]):
                    cs=[c for span in line['spans'] for c in span['chars']]
                    t=norm(''.join(c['c'] for c in cs))
                    if re.fullmatch(r'[A-Za-zあ-んぁ-ょァ-ヶ\u309a＜<]',t):
                        r=s2.box(cs)
                        # Index group headings are teal in this book; a tiny
                        # single OCR span within a black entry is not a heading.
                        cb=group_color_box(doc[i],r)
                        corrected=any(x['pdf_index0']==i and norm(x['raw'])==t for x in cfg.get('outline',{}).get('group_corrections',[]))
                        if cb and (corrected or abs((cb.y0+cb.y1-r.y0-r.y1)/2)<max(20,r.height*.8)):
                            rowsets.append(cs)
            rowsets.sort(key=lambda cs:(int(s2.box(cs).x0/doc[i].rect.width*2),s2.box(cs).y0))
            seen=[]
            for cs in rowsets:
                t = norm(''.join(c['c'] for c in cs))
                # Centered alphabet/kana headings, not short left-aligned entry terms.
                r = s2.box(cs); center = (r.x0+r.x1)/2 / doc[i].rect.width
                centered = any(abs(center-v)<.035 for v in cfg.get('outline',{}).get('group_centers',(.28,.31,.68,.71)))
                if not centered or r.y0<100: continue
                col=int(r.x0/doc[i].rect.width*2)
                if len(t)==1 or any(x['pdf_index0']==i and norm(x['raw'])==t for x in cfg.get('outline',{}).get('group_corrections',[])):
                    colorbox=group_color_box(doc[i],r)
                    if colorbox: r=colorbox
                if any(col==oldcol and abs(r.y0-y)<25 for oldcol,y in seen): continue
                correction=next((x for x in cfg.get('outline',{}).get('group_corrections',[]) if x['pdf_index0']==i and norm(x['raw'])==t),None)
                if correction:
                    t=correction['title']
                if centered and (t in ('アルファベット','かな') or re.fullmatch(r'[A-Za-zあ-んぁ-ょァ-ヶ]',t)):
                    title = unicodedata.normalize('NFKC',t).translate(str.maketrans('ぁぃぅぇぉゃゅょ','あいうえおやゆよ'))
                    if re.fullmatch('[A-Za-z]',title): title=title.upper()
                    if title=='ヘ': title='へ'
                    out.append([2,title,i+1,r.y0])
                    seen.append((col,r.y0))
                elif centered and len(t)<5:
                    review.append(dict(pdf_index0=i,text=t,rect=list(r),reason='uncertain index group OCR'))
    # Preserve index reading order: columns before y, unlike ordinary page positions.
    out.sort(key=lambda r:r[2])
    return out, review


def check_plan(doc, items, outline, cfg):
    selected = s2.approved(items); errors = []
    bypage = defaultdict(list)
    for c in selected:
        i=c['source_pdf_index0']; t=c['target_pdf_index0']; r=f.Rect(c['rect'])
        if not (0<=i<len(doc) and isinstance(t,int) and 0<=t<len(doc)): errors.append('page outside document')
        elif r.is_empty or not doc[i].rect.contains(r): errors.append('rectangle outside page')
        if not r.contains(f.Rect(c['intended_box'])): errors.append('ink outside link box')
        if c.get('digit_ink_box') and not r.contains(f.Rect(c['digit_ink_box'])): errors.append('digit ink clipped')
        if c.get('digit_scan_rect'):
            components=sorted(ink_components(doc[i],c['digit_scan_rect']),key=lambda b:b.x1)[-len(c['print_page']):]
            if any(not r.contains(ink) for ink in components): errors.append('image verification: digit ink clipped')
        for prev in bypage[i]:
            if r.intersects(f.Rect(prev['rect'])): errors.append(f'overlapping/duplicate links: {i} {c["kind"]}/{prev["kind"]}')
        bypage[i].append(c)
    last_level=0; last_page=0
    for row in outline:
        level,title,page,y=row
        if level<1 or level>last_level+1 or page<last_page or not 1<=page<=len(doc) or not 0<=y<doc[page-1].rect.height:
            errors.append(f'invalid outline: {title}')
        last_level=level; last_page=page
    toc=[c for c in selected if c['kind']=='toc']
    starts=navigation_starts(toc,cfg)
    navcfg=cfg['nav']
    for i in range(navcfg['first_page'],navcfg['last_page']+1):
        buttons=sorted([c for c in bypage[i] if c['kind']=='nav'],key=lambda c:c['button'])
        if [c['button'] for c in buttons]!=[0,1,2,3] or [c['target_pdf_index0'] for c in buttons]!=s2.nav_targets(i,starts,navcfg['toc_page'],navcfg['index_page']):
            errors.append(f'wrong navigation targets: {i}')
        if buttons:
            region=f.Rect(buttons[0]['rect'])
            for button in buttons:
                r=f.Rect(button['rect']);region|=r
                if r.width<110 or r.height<110: errors.append(f'nav tap area too small: {i}')
            if s2.ink_bounds(doc[i],region,threshold=navcfg.get('ink_threshold',95)):
                errors.append(f'nav overlaps original print: {i}')
    if errors: raise ValueError('\n'.join(errors[:30]))
    return dict(passed=True, overlap_duplicate_check=True, digit_ink_coverage=True, outline_order_levels_targets=True,
                nav_unit=cfg.get('nav_unit','part'), nav_starts=starts, last_part_next='first back-matter section, then index')


def overrides(items, path):
    """Patch stable IDs, reject unknown IDs rather than silently ignore mistakes."""
    if not path: return items
    patches=read_json(path).get('overrides',[])
    for patch in patches:
        hits=[c for c in items if c.get('id')==patch['id']]
        if len(hits)!=1: raise ValueError(f'unknown/ambiguous override: {patch["id"]}')
        hits[0].update(patch); hits[0]['manual_override']=True
    return items


def run(config_path, reuse=False):
    cfg=read_json(config_path)
    if cfg.get('automatic'):
        from .autoconfig import build
        return build(cfg['source'],cfg['output'],Path(cfg['work_dir']).parent,override=config_path)
    work=Path(cfg['work_dir']); source=Path(cfg['source']); pm=PageMap(read_json(cfg['pagemap']))
    outputs=[Path(cfg[k]).resolve() for k in ('output','extract_output')]
    if outputs[0]==outputs[1] or any(p==source.resolve() or (p.exists() and p.samefile(source)) for p in outputs):
        raise ValueError('元 PDF と全体・抜粋の出力は別ファイルが必要です')
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    with f.open(source) as d:
        ids=cfg['extract_pages']
        if not ids or len(ids)!=len(set(ids)) or any(type(i)is not int or not 0<=i<len(d) for i in ids): raise ValueError('invalid extract pages')
        if reuse:
            allitems=read_json(work/'stage3_candidates.json')['candidates']
            outline=read_json(work/'outline.json')['entries']
            index_report=work/'index_links_stage3.json'
            changed=read_json(index_report).get('semantic_demoted') if index_report.exists() else None
        else:
            auto=s2.extract_toc(d,cfg['toc_pages'],pm,padding=cfg.get('padding',3),ymin=cfg.get('toc_ymin'))
            toc=s2.manual_toc(d,read_json(cfg['manual_toc']),pm,cfg.get('padding',3)) if cfg.get('manual_toc') else auto
            toc_boxes(d,toc)
            minis=s2.extract_toc(d,cfg['part_pages'],pm,True,cfg.get('padding',3),ymin=cfg.get('part_ymin'))
            index=extract_index(d,cfg['index_pages'],pm,cfg['index'])
            changed=semantic_index(d,index)
            refs=extract_pagerefs(d,range(cfg['pageref']['first_page'],cfg['pageref']['last_page']+1),
                                 set(cfg['toc_pages']+cfg['part_pages']+cfg['index_pages']),pm,cfg['pageref'])
            refs=overrides(refs,cfg.get('pageref_overrides'))
            nav,margin=s2.nav_candidates(d,navigation_starts(toc,cfg),cfg['nav'])
            outline,oreview=build_outline(d,toc,cfg)
            if cfg.get('outline_overrides'): outline=read_json(cfg['outline_overrides'])['entries']
            write_json(work/'outline.json',dict(entries=outline,review=oreview,goodnotes_y='未検証'))
            write_json(work/'pageref_links.json',dict(candidates=refs))
            write_json(work/'index_links_stage3.json',dict(candidates=index,semantic_demoted=changed))
            write_json(work/'nav_margin_stage3.json',dict(measurements=margin))
            allitems=toc+minis+index+refs+nav
            for e in (read_json(cfg['manual_toc']) if cfg.get('manual_toc') else {}).get('entries',[]):
                if e.get('status')=='review':
                    allitems.append(s2.candidate('toc',e['source']['pdf_index0'],None,e['source']['rect'],e['source']['rect'],.4,
                                                title=e['title'],print_page=e['print_page'],role='title',method='manual_review',
                                                reason=e.get('note','manual TOC review')))
        allitems=overrides(allitems,cfg.get('pageref_overrides'))
        allitems=overrides(allitems,cfg.get('candidate_overrides'))
        if cfg.get('outline_overrides'): outline=read_json(cfg['outline_overrides'])['entries']
        write_json(work/'pageref_links.json',dict(candidates=[c for c in allitems if c['kind']=='pageref']))
        write_json(work/'stage3_candidates.json',dict(schema_version=3,candidates=allitems))
        check=check_plan(d,allitems,outline,cfg)
    s2.apply_candidates(source,cfg['output'],allitems,outline,[cfg['output'],cfg['extract_output']])
    report=s2.verify_candidates(source,cfg['output'],allitems,outline,strict=True)
    remap={v:k for k,v in enumerate(ids)}; subset=[]; dropped=Counter()
    for c in s2.approved(allitems):
        if c['source_pdf_index0'] not in remap: continue
        if c['target_pdf_index0'] not in remap: dropped[c['kind']]+=1; continue
        cc=copy.deepcopy(c); cc['source_pdf_index0']=remap[c['source_pdf_index0']]; cc['target_pdf_index0']=remap[c['target_pdf_index0']]; subset.append(cc)
    smalloutline=[]; prevlevel=0
    for level,title,page,y in outline:
        if page-1 in remap:
            level=min(level,prevlevel+1); smalloutline.append([level,title,remap[page-1]+1,y]); prevlevel=level
    with f.open(cfg['output']) as full, f.open() as small:
        for i in ids: small.insert_pdf(full,from_page=i,to_page=i,links=False)
        for c in subset: small[c['source_pdf_index0']].insert_link({'kind':f.LINK_GOTO,'from':f.Rect(c['rect']),'page':c['target_pdf_index0'],'to':f.Point(0,0)})
        small.set_toc(smalloutline)
        save_pdf(small,cfg['extract_output'])
    ex=s2.verify_candidates(source,cfg['extract_output'],subset,smalloutline,ids,strict=True)
    ex.update(dropped_links=dict(dropped),source_indices=ids)
    write_json(work/'stage3_extract_verify.json',ex)
    with f.open(cfg['output']) as result:
        preview=Path(cfg['preview_dir']); preview.mkdir(parents=True,exist_ok=True)
        for i in cfg['preview_pages']:
            p=result[i]
            for c in s2.approved(allitems):
                if c['source_pdf_index0']==i: p.draw_rect(f.Rect(c['rect']),color=(1,0,0),width=2,overlay=True)
            p.get_pixmap(matrix=f.Matrix(.45,.45)).save(preview/f'page_{i:03}.png')
    report.update(plan_check=check,outline_count=len(outline),semantic_index_demoted=changed,
                  source_sha256_before=before,source_sha256_after=hashlib.sha256(source.read_bytes()).hexdigest(),extract_passed=ex['passed'])
    report['source_unchanged']=report['source_sha256_before']==report['source_sha256_after']
    report['passed']=report['passed'] and ex['passed'] and report['source_unchanged']
    report['pageref_counts']=dict(Counter(c['status'] for c in allitems if c['kind']=='pageref'))
    write_json(work/'stage3_verify.json',report)
    return report
