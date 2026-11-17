"""TOC heading anchors and conservative offset consensus without printed folios."""
from collections import defaultdict,Counter

def estimate_offsets(anchors, count, end=None, observations=None):
    """Anchors are (printed number, PDF index). Repeated running heads may vote.

    At least three distinct TOC labels support each segment. Outliers and
    overlapping segments are retained as evidence, never silently patched.
    """
    groups=defaultdict(set)
    for number,index in anchors:
        if number>0 and index>=number-1: groups[index-number].add((number,index))
    ranked=sorted(groups,key=lambda off:len(groups[off]),reverse=True)
    if not ranked or len(groups[ranked[0]])<3: return None
    best=ranked[0]; total=len({n for n,i in anchors}); support=len(groups[best])
    if support<5 and support/max(1,total)<.65: return None
    selected=[best]
    # Piecewise offsets require nonoverlapping title ranges and PDF ranges.
    for off in ranked[1:]:
        if len(groups[off])<3: continue
        a,b=min(groups[off]),max(groups[off])
        if all(b[0]<min(groups[o])[0] and b[1]<min(groups[o])[1] or
               a[0]>max(groups[o])[0] and a[1]>max(groups[o])[1] for o in selected): selected.append(off)
    selected.sort(key=lambda o:min(groups[o]))
    heading_adjustment=0
    if observations and len(selected)==1:
        folios=Counter(i-v['number'] for i,vs in enumerate(observations) for v in vs if v['style']=='arabic')
        if folios:
            folio_offset,number=folios.most_common(1)[0]
            delta=folio_offset-selected[0]
            # A running head can first be OCR-readable on the following page.
            # Direct folio evidence determines the mapping in this case.
            if number>=5 and number/sum(folios.values())>=.8 and abs(delta)==1:
                groups[folio_offset]={(n,i+delta) for n,i in groups[selected[0]]}
                selected=[folio_offset];heading_adjustment=delta
    segments=[]; checks=[]
    for j,off in enumerate(selected):
        start=max(0,off+1) if j==0 else min(i for n,i in groups[off])
        stop=(min(i for n,i in groups[selected[j+1]])-1 if j+1<len(selected) else (count-2 if end is None else end))
        # A gap at an offset change remains unmapped unless bounded by evidence.
        if j+1<len(selected): stop=max(i for n,i in groups[off])
        if stop<start: continue
        segments.append(dict(pdf_index0_start=start,pdf_index0_end=stop,print_start=start-off,style='arabic'))
        if observations:
            for i in range(start,stop+1):
                vs=[v['number'] for v in observations[i] if v['style']=='arabic']
                if vs: checks.append(i-off in vs)
    agreement=sum(checks)/len(checks) if checks else None
    if len(checks)>=3 and agreement<.8: return None
    confidence=.95 if heading_adjustment else (.97 if support>=5 else .95)
    return dict(schema_version=1,segments=segments,evidence=dict(strategy='toc_heading_consensus',
                offsets=[dict(offset=o,anchors=len(groups[o])) for o in selected],
                supporting_labels=support,total_matching_labels=total,readable_crosschecks=len(checks),
                heading_page_adjustment=heading_adjustment,
                anchor_pairs=[dict(print_page=n,pdf_index0=i,observed_heading_pdf_index0=i-heading_adjustment,offset=o) for o in selected for n,i in sorted(groups[o])],
                readable_agreement=agreement,confidence=confidence,
                limitation='Constant offset extrapolated to back matter; unobserved inserted plates require review.'))
