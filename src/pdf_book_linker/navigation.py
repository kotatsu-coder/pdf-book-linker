"""Stable per-parity placement measured against original raster ink (offline)."""
import pymupdf as f

POSITIONS = ('bottom-centre', 'bottom-outer', 'top-outer', 'outer-edge')

def section_starts(primary,extra,count):
    if any(type(i) is not int or not 0<=i<count for i in extra): raise ValueError('nav.extra_starts が不正です')
    return sorted(set(primary)|set(extra))

def geometry(width, height):
    # 5.5% of a 175 mm displayed page = 9.6 mm. Clamps are relative to height.
    size = min(max(width * .055,16),150,height*.1)
    return dict(tap_size=size, gap=width*.012, line_width=width*.0012,
                gray=.7, opacity=.35, relative=True)

def regions(width, height, position, side, size, gap, shift=0, anchor=.5):
    outer_left = side == 'left'
    length = 4*size+3*gap
    if position == 'outer-edge':
        x = width*.008 if outer_left else width*.992-size
        y = height*anchor-length/2+shift
        return [f.Rect(x,y+j*(size+gap),x+size,y+j*(size+gap)+size) for j in range(4)]
    x = (width-length)/2 if position == 'bottom-centre' else (width*.025 if outer_left else width*.975-length)
    x += shift
    y = height*.012 if position == 'top-outer' else height*.988-size
    return [f.Rect(x+j*(size+gap),y,x+j*(size+gap)+size,y+size) for j in range(4)]

class Raster:
    def __init__(self, page):
        self.scale = 800/page.rect.width
        self.pix = page.get_pixmap(matrix=f.Matrix(self.scale,self.scale), colorspace=f.csGRAY, alpha=False)
        self.data = self.pix.samples
        self.bounds = page.rect
    def clear(self, rect, pad=0):
        r = f.Rect(rect)+(-pad,-pad,pad,pad)
        if not self.bounds.contains(r): return False
        r = r*self.scale
        x0,y0 = max(0,int(r.x0)),max(0,int(r.y0))
        x1,y1 = min(self.pix.width,int(r.x1)+1),min(self.pix.height,int(r.y1)+1)
        return all(min(self.data[y*self.pix.stride+x0:y*self.pix.stride+x1],default=255)>=170 for y in range(y0,y1))

def choose(stats, override=None):
    if override:
        if override not in POSITIONS: raise ValueError('nav.position: '+str(override))
        return override
    return max(POSITIONS,key=lambda k:(stats[k]['clear_fraction'],stats[k]['p10_clearance_ratio'],stats[k]['mean_clearance_ratio']))

def plan(doc, pages, cfg):
    pages=list(pages); decision={}; measures=[]; placements={}
    right=cfg.get('_right_parity',1)
    for parity,side in ((1-right,'left'),(right,'right')):
        ids=[i for i in pages if i%2==parity]
        sample=sorted({ids[round(j*(len(ids)-1)/min(15,len(ids)-1))] for j in range(min(16,len(ids)))}) if len(ids)>1 else ids
        stats={pos:[] for pos in POSITIONS}
        outer_stats={anchor:[] for anchor in (.18,.35,.5,.65,.82)}
        for i in sample:
            p=doc[i]; raster=Raster(p); geo=geometry(p.rect.width,p.rect.height)
            requested=cfg.get('size')
            size=geo['tap_size'] if requested is None else (requested*p.rect.width if requested<=1 else requested)
            for pos in POSITIONS:
                for anchor in (outer_stats if pos=='outer-edge' else (.5,)):
                    rs=regions(p.rect.width,p.rect.height,pos,side,size,geo['gap'],anchor=anchor)
                    clearance=-1.
                    for margin in (0,.002,.005,.01,.02,.03):
                        if all(raster.clear(r,p.rect.width*margin) for r in rs): clearance=margin
                        else: break
                    (outer_stats[anchor] if pos=='outer-edge' else stats[pos]).append(clearance)
            f.TOOLS.store_shrink(100)
        outer_anchor=max(outer_stats,key=lambda a:(sum(x>=.002 for x in outer_stats[a]),sum(outer_stats[a]),-abs(a-.5)))
        stats['outer-edge']=outer_stats[outer_anchor]
        summary={pos:dict(clear_fraction=sum(x>=.002 for x in xs)/max(1,len(xs)),
                     p10_clearance_ratio=sorted(xs)[int((len(xs)-1)*.1)] if xs else -1,
                     mean_clearance_ratio=sum(xs)/max(1,len(xs))) for pos,xs in stats.items()}
        override=cfg.get('position'); override=override.get(side) if isinstance(override,dict) else override
        selected=choose(summary,override)
        decision[side]=dict(position=selected,sample_pages=sample,candidates=summary,
                            parity=parity,size_ratio=cfg.get('size',.055),opacity=cfg.get('opacity',.35),
                            automatic_size_rule=dict(width_ratio=.055,min_pt=16,max_pt=150,height_limit_ratio=.1),
                            outer_anchor_ratio=outer_anchor,outer_anchor_samples=outer_stats)
        for i in ids:
            p=doc[i]; raster=Raster(p); geo=geometry(p.rect.width,p.rect.height)
            geo.update({k:cfg[k] for k in ('opacity','gray') if k in cfg})
            requested=cfg.get('size')
            size=geo['tap_size'] if requested is None else (requested*p.rect.width if requested<=1 else requested)
            geo['tap_size']=size
            found=None; shift=0
            for delta in (0,-.01,.01,-.02,.02):
                rs=regions(p.rect.width,p.rect.height,selected,side,size,geo['gap'],delta*p.rect.width,outer_anchor)
                if all(raster.clear(r,p.rect.width*.002) for r in rs): found=rs; shift=delta; break
            measures.append(dict(page=i,side=side,position=selected,status='approved' if found else 'review',shift_ratio=shift,
                                 reason='clear original raster' if found else 'ink present; skipped'))
            if found: placements[i]=(found,geo)
            f.TOOLS.store_shrink(100)
    return placements,dict(decisions=decision,measurements=measures,
                          parity_note='PDF index0 even/odd; physical outer side can be overridden with position per side')
