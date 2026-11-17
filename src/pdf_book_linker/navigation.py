"""Stable per-parity placement measured against original raster ink (offline)."""
import pymupdf as f

POSITIONS = ('bottom-centre', 'bottom-outer', 'top-outer', 'outer-edge')

def section_starts(primary,extra,count):
    if any(type(i) is not int or not 0<=i<count for i in extra): raise ValueError('nav.extra_starts が不正です')
    return sorted(set(primary)|set(extra))

STYLES = ('line', 'pill')
CROWDED = ('skip', 'overlay')


def display_base(width, height):
    """Page width as it is shown when fitted to a portrait tablet screen.

    Portrait pages fill the screen width; wide or square pages are limited by
    the height instead (about 3:4 on an iPad), so the buttons keep roughly the
    same physical size on screen whatever the PDF page size is.
    """
    return min(width, height*.75)


def geometry(width, height):
    # 5.5% of the displayed width is about 9 mm on an 11-inch iPad, close to
    # Material Design's 48 dp touch target (Apple's minimum is 44 pt, ~7 mm).
    base = display_base(width, height)
    size = min(max(base * .055,16),150)
    return dict(tap_size=size, gap=base*.012, line_width=base*.0015,
                gray=.7, opacity=.35, relative=True, base=base)


def size_from(cfg, key, default, base):
    value = cfg.get(key, default)
    value = float(value)
    if value <= 0: raise ValueError(f'nav.{key} は正の数です')
    return value*base if value <= 1 else value


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
    out=dict(rgb=[round(v,4) for v in colour],opacity=round(opacity,3),
             background=[round(v,4) for v in background],contrast=round(contrast(blend,background),2))
    style=cfg.get('style','line')
    if style not in STYLES: raise ValueError('nav.style は "line" か "pill" です: '+str(style))
    out['style']=style
    if style=='pill':
        # Like the translucent controls of e-book viewers: a rounded chip in
        # the contrasting colour, with the arrow in the page colour on top.
        out.update(pill_rgb=out['rgb'],pill_opacity=max(.45,out['opacity']),
                   rgb=[1.,1.,1.] if luminance(colour)<.5 else [.12,.12,.15],opacity=.95)
    return out


def overlay_style(page_background, cfg):
    """Style for crowded pages (nav.crowded="overlay"): an opaque chip in the
    page colour hides the print underneath, and the arrow is drawn on it."""
    out=button_style(page_background,{k:v for k,v in cfg.items() if k!='style'})
    out.update(style='overlay',backdrop=True,pill_rgb=[round(v,4) for v in page_background],pill_opacity=.92)
    return out

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
        # Page background = median over a sparse grid of the whole page: paper
        # on text pages, the fill colour on full-bleed title pages. (A border
        # frame alone is fooled by thumb-index tabs and edge decorations.)
        w,h,s = self.pix.width,self.pix.height,self.pix.stride
        grey=[]; chans=([],[],[]); rs,n,rstride = self.rgb.samples,self.rgb.n,self.rgb.stride
        for y in range(0,h,7):
            for x in range(0,w,7):
                grey.append(self.data[y*s+x]); k=y*rstride+x*n
                for c in range(3): chans[c].append(rs[k+c])
        self.page_background = sorted(grey)[len(grey)//2] if grey else 255
        self.page_rgb = tuple(sorted(c)[len(c)//2]/255 for c in chans) if chans[0] else (1.,1.,1.)
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
        if abs(median-self.page_background) > UNIFORM_SPREAD: return False
        # A full-bleed background reaches the page edge; a photo or box inside
        # the margins does not. Extend the area to the nearest edges and recheck.
        b = self.bounds; r = f.Rect(rect)
        ext = f.Rect(b.x0 if r.x0 < b.width*.12 else r.x0, b.y0 if r.y0 < b.height*.12 else r.y0,
                     b.x1 if r.x1 > b.width*.88 else r.x1, b.y1 if r.y1 > b.height*.88 else r.y1)
        if ext == r: return True
        x0,y0,x1,y1 = (max(0,int(ext.x0*self.scale)),max(0,int(ext.y0*self.scale)),
                       min(self.pix.width,int(ext.x1*self.scale)+1),min(self.pix.height,int(ext.y1*self.scale)+1))
        edge = [v for y in range(y0,y1,2) for v in self.data[y*self.pix.stride+x0:y*self.pix.stride+x1:2]]
        return bool(edge) and max(edge)-min(edge) <= UNIFORM_SPREAD
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
            size=geo['tap_size'] if cfg.get('size') is None else size_from(cfg,'size',.055,geo['base'])
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
                            style=cfg.get('style','line'),crowded=cfg.get('crowded','skip'),min_size=cfg.get('min_size',.045),
                            automatic_size_rule=dict(display_width='min(page width, 0.75 x page height)',ratio=.055,
                                                     min_ratio_when_crowded=cfg.get('min_size',.045),min_pt=16,max_pt=150),
                            outer_anchor_ratio=outer_anchor,outer_anchor_samples=outer_stats)
        crowded=cfg.get('crowded','skip')
        if crowded not in CROWDED: raise ValueError('nav.crowded は "skip" か "overlay" です: '+str(crowded))
        # A position fixed in the settings is respected: only shrink there.
        others=[] if override else sorted((x for x in POSITIONS if x!=selected),
                      key=lambda k:(summary[k]['clear_fraction'],summary[k]['p10_clearance_ratio']),reverse=True)
        for i in ids:
            p=doc[i]; raster=Raster(p); geo=geometry(p.rect.width,p.rect.height)
            geo.update({k:cfg[k] for k in ('gray',) if k in cfg})
            W,H=p.rect.width,p.rect.height
            size=geo['tap_size'] if cfg.get('size') is None else size_from(cfg,'size',.055,geo['base'])
            minimum=min(size,size_from(cfg,'min_size',.045,geo['base']))
            sizes=[size]
            while sizes[-1]*.88>minimum: sizes.append(sizes[-1]*.88)
            if sizes[-1]>minimum: sizes.append(minimum)
            found=None; shift=0; used=(selected,size); fallback='none'
            # Same place first (only smaller), so the buttons stay where the
            # reader expects them; another place only when the page is full there.
            for pos in [selected]+others:
                for sz in sizes:
                    anchor=outer_anchor if pos=='outer-edge' else .5
                    for delta in (0,-.01,.01,-.02,.02):
                        rs=regions(W,H,pos,side,sz,geo['gap']*sz/size,delta*W,anchor)
                        if all(raster.clear(r,W*.002) for r in rs):
                            found=rs; shift=delta; used=(pos,sz)
                            fallback='none' if (pos,sz)==(selected,size) else ('smaller' if pos==selected else 'other-position')
                            break
                    if found: break
                if found: break
            if not found and crowded=='overlay':
                found=regions(W,H,selected,side,size,geo['gap'],0,outer_anchor); used=(selected,size); fallback='overlay'
            status='approved' if found else 'review'
            reason={'none':'clear original raster','smaller':'clear after shrinking','other-position':'clear at another position',
                    'overlay':'no clear margin; drawn on an opaque chip over the print (nav.crowded=overlay)'}[fallback] if found else 'ink present; skipped'
            measures.append(dict(page=i,side=side,position=used[0],status=status,shift_ratio=shift,
                                 size_ratio=round(used[1]/geo['base'],4),fallback=fallback if found else 'skipped',reason=reason))
            if found:
                geo['tap_size']=used[1]; geo['line_width']*=used[1]/size
                area=f.Rect(found[0])
                for r in found[1:]: area|=r
                geo.update(overlay_style(raster.page_rgb,cfg) if fallback=='overlay' else button_style(raster.background(area),cfg))
                placements[i]=(found,geo)
            f.TOOLS.store_shrink(100)
    return placements,dict(decisions=decision,measurements=measures,
                          parity_note='PDF index0 even/odd; physical outer side can be overridden with position per side')
