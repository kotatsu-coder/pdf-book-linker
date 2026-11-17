"""Stable per-parity placement measured against original raster ink (offline)."""
import pymupdf as f

POSITIONS = ('bottom-centre', 'bottom-outer', 'top-outer', 'outer-edge')

def section_starts(primary,extra,count):
    if any(type(i) is not int or not 0<=i<count for i in extra): raise ValueError('nav.extra_starts が不正です')
    return sorted(set(primary)|set(extra))

def geometry(width, height):
    # 5.5% of a 175 mm displayed page = 9.6 mm. Clamps are relative to height.
    size = min(max(width * .055,16),150,height*.1)
    return dict(tap_size=size, gap=width*.012, line_width=width*.0015,
                gray=.7, opacity=.35, relative=True)


def _linear(v):
    return v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4


def luminance(rgb):
    """WCAG relative luminance of an sRGB colour given as 0..1 floats."""
    r,g,b = (_linear(c) for c in rgb)
    return .2126*r+.7152*g+.0722*b


def contrast(a, b):
    la,lb = sorted((luminance(a),luminance(b)),reverse=True)
    return (la+.05)/(lb+.05)


def parse_color(value):
    if isinstance(value,(list,tuple)) and len(value)==3:
        rgb=[float(v) for v in value]
        rgb=[v/255 for v in rgb] if max(rgb)>1 else rgb
    elif isinstance(value,str) and len(value.lstrip('#'))==6:
        h=value.lstrip('#'); rgb=[int(h[k:k+2],16)/255 for k in (0,2,4)]
    else:
        raise ValueError('nav.color は "auto"、"#RRGGBB"、[r,g,b] のいずれかです: '+str(value))
    if not all(0<=v<=1 for v in rgb): raise ValueError('nav.color が範囲外です: '+str(value))
    return tuple(rgb)


def button_style(background, cfg):
    """Pick stroke colour and opacity so the buttons stay faint but readable.

    The stroke is dark on light paper and white on dark backgrounds. With
    opacity "auto" the lowest opacity reaching the target contrast (WCAG
    ratio, nav.contrast, default 3.0) against the measured background is
    used, within 0.25-0.9.
    """
    target=float(cfg.get('contrast',3.0))
    colour=cfg.get('color','auto')
    if colour in (None,'auto'):
        dark,light=(.12,.12,.15),(1.,1.,1.)
        colour=dark if contrast(dark,background)>=contrast(light,background) else light
    else:
        colour=parse_color(colour)
    opacity=cfg.get('opacity','auto')
    if opacity in (None,'auto'):
        chosen=.9
        for k in range(25,91,5):
            o=k/100; blend=tuple(o*c+(1-o)*b for c,b in zip(colour,background))
            if contrast(blend,background)>=target: chosen=o; break
        opacity=chosen
    opacity=float(opacity)
    if not 0<opacity<=1: raise ValueError('nav.opacity は 0〜1 または "auto" です')
    blend=tuple(opacity*c+(1-opacity)*b for c,b in zip(colour,background))
    return dict(rgb=[round(v,4) for v in colour],opacity=round(opacity,3),
                background=[round(v,4) for v in background],contrast=round(contrast(blend,background),2))

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

UNIFORM_SPREAD = 40   # grey levels; a flat coloured background (e.g. a dark title page)


class Raster:
    def __init__(self, page):
        self.scale = 800/page.rect.width
        self.pix = page.get_pixmap(matrix=f.Matrix(self.scale,self.scale), colorspace=f.csGRAY, alpha=False)
        self.data = self.pix.samples
        self.rgb = page.get_pixmap(matrix=f.Matrix(self.scale,self.scale), colorspace=f.csRGB, alpha=False)
        self.bounds = page.rect
        # Page background = median grey of a thin frame around the page edge.
        w,h,s = self.pix.width,self.pix.height,self.pix.stride
        m = max(1,int(min(w,h)*.02)); frame = []
        for y in range(0,h,3):
            row = self.data[y*s:y*s+w]
            frame.extend(row[:m:2]); frame.extend(row[w-m::2])
            if y < m or y >= h-m: frame.extend(row[::4])
        self.page_background = sorted(frame)[len(frame)//2] if frame else 255
    def _window(self, rect, pad=0):
        r = f.Rect(rect)+(-pad,-pad,pad,pad)
        if not self.bounds.contains(r): return None
        r = r*self.scale
        return (max(0,int(r.x0)),max(0,int(r.y0)),min(self.pix.width,int(r.x1)+1),min(self.pix.height,int(r.y1)+1))
    def clear(self, rect, pad=0):
        """True when nothing is printed there.

        Either light paper, or a flat area in the page's own background colour
        (e.g. a dark title page). A flat block that differs from the page
        background, such as a solid printed box, is not free space.
        """
        w = self._window(rect,pad)
        if w is None: return False
        x0,y0,x1,y1 = w
        rows = [self.data[y*self.pix.stride+x0:y*self.pix.stride+x1] for y in range(y0,y1)]
        lo = min((min(r) for r in rows if r),default=255)
        if lo >= 170: return True
        hi = max((max(r) for r in rows if r),default=0)
        if hi-lo > UNIFORM_SPREAD: return False
        values = sorted(v for r in rows for v in r[::2])
        median = values[len(values)//2] if values else 255
        return abs(median-self.page_background) <= UNIFORM_SPREAD
    def background(self, rect):
        """Median sRGB colour (0..1) under rect."""
        w = self._window(rect)
        if w is None: return (1.,1.,1.)
        x0,y0,x1,y1 = w; s = self.rgb.samples; n = self.rgb.n; stride = self.rgb.stride
        chans = ([],[],[])
        for y in range(y0,y1,2):
            for x in range(x0,x1,2):
                k = y*stride+x*n
                for c in range(3): chans[c].append(s[k+c])
        if not chans[0]: return (1.,1.,1.)
        return tuple(sorted(c)[len(c)//2]/255 for c in chans)

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
                            parity=parity,size_ratio=cfg.get('size',.055),opacity=cfg.get('opacity','auto'),
                            color=cfg.get('color','auto'),contrast=cfg.get('contrast',3.0),
                            automatic_size_rule=dict(width_ratio=.055,min_pt=16,max_pt=150,height_limit_ratio=.1),
                            outer_anchor_ratio=outer_anchor,outer_anchor_samples=outer_stats)
        for i in ids:
            p=doc[i]; raster=Raster(p); geo=geometry(p.rect.width,p.rect.height)
            geo.update({k:cfg[k] for k in ('gray',) if k in cfg})
            requested=cfg.get('size')
            size=geo['tap_size'] if requested is None else (requested*p.rect.width if requested<=1 else requested)
            geo['tap_size']=size
            found=None; shift=0
            for delta in (0,-.01,.01,-.02,.02):
                rs=regions(p.rect.width,p.rect.height,selected,side,size,geo['gap'],delta*p.rect.width,outer_anchor)
                if all(raster.clear(r,p.rect.width*.002) for r in rs): found=rs; shift=delta; break
            measures.append(dict(page=i,side=side,position=selected,status='approved' if found else 'review',shift_ratio=shift,
                                 reason='clear original raster' if found else 'ink present; skipped'))
            if found:
                area=f.Rect(found[0])
                for r in found[1:]: area|=r
                geo.update(button_style(raster.background(area),cfg))
                placements[i]=(found,geo)
            f.TOOLS.store_shrink(100)
    return placements,dict(decisions=decision,measurements=measures,
                          parity_note='PDF index0 even/odd; physical outer side can be overridden with position per side')
