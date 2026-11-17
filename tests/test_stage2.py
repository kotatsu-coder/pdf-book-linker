"""Stage 2 の独立した合成入力テスト。書籍データは使用しない。"""
import pymupdf as f
import pytest
from pdf_book_linker.stage2 import (parse_numbers, nav_targets, chars, tight, extract_index,
                                  extract_toc, nav_candidates, candidate, apply_candidates, verify_candidates)
from pdf_book_linker.pagemap import PageMap

@pytest.mark.parametrize('text,values', [
    ('71, 78',[71,78]), ('125-128',[125]), ('125～128',[125]),
    ('１２５〜１２８, ７１',[125,71]), ('l25, I28, O7',[125,128,7]),
    ('125–128',[125]), ('term 71, 78',[71,78]),
])
def test_numbers(text,values):
    assert [c['number'] for c in parse_numbers(text,1,227)]==values


def test_reject_numbers():
    a=parse_numbers('0, 228, 128-125, 125-999',1,227)
    assert len(a)==4 and not any(c['valid'] for c in a)
    assert parse_numbers('lgG4 MRD-1) 103',1,227)[-1]['number']==103
    assert parse_numbers('l25',1,227)[0]['confidence']<.95


def test_normalized_offsets():
    s='… ７１, ７８'
    a=parse_numbers(s,1,227)
    assert [s[c['start']:c['end']] for c in a]==['７１','７８']


def test_tight_box():
    with f.open() as d:
        p=d.new_page(width=600,height=800)
        p.insert_text((50,100),'Title ....... 71',fontsize=18)
        cs=chars(p); title=cs[:5]; num=cs[-2:]
        r, ink, method=tight(p,title)
        nr,_,_=tight(p,num)
        assert r[2]<nr[0]-20
        assert r[2]-r[0]<60
        assert f.Rect(r).contains(f.Rect(ink))
        assert method=='text_chars+image_threshold'
        assert min(r)>=0


def test_index_columns_wrap_and_lists():
    with f.open() as d:
        p=d.new_page(width=600,height=800)
        for x,y,text in [(40,150,'Term 71, 78'),(50,180,'125-128'),(340,150,'Other 12'),(340,180,'Term 999')]:
            p.insert_text((x,y),text,fontsize=12)
        pm=PageMap({'segments':[dict(pdf_index0_start=0,pdf_index0_end=226,print_start=1)]})
        a=extract_index(d,[0],pm,dict(print_range=[1,227],columns=2,ymax=700))
        assert sorted(int(c['print_page']) for c in a if c['status']=='approved')==[12,71,78,125]
        assert any(c['print_page']=='999' and c['status']=='review' for c in a)
        left=[c for c in a if c['print_page'] in ('71','78')]
        assert f.Rect(left[0]['rect']).x1<=f.Rect(left[1]['rect']).x0


def test_nav_targets_boundaries():
    starts=[6,7,16,36,37]
    assert nav_targets(6,starts,4,50)==[4,4,7,50]     # first start page: ← falls back to TOC
    assert nav_targets(15,starts,4,50)==[4,7,16,50]
    assert nav_targets(36,starts,4,50)==[4,16,37,50]  # on a start page: ← = previous section
    assert nav_targets(49,starts,4,50)==[4,37,50,50]


def test_nav_margin_rejects_ink():
    with f.open() as d:
        p=d.new_page(width=1000,height=1000)
        p.draw_rect(f.Rect(480,900,520,940),fill=(0,0,0))
        cfg=dict(first_page=0,last_page=0,toc_page=0,index_page=0,tap_size=110,bottom_offset=115)
        with pytest.raises(ValueError,match='重なり'): nav_candidates(d,[0],cfg)


def test_apply_verify_detects_missing_link_and_image_preservation(tmp_path):
    source=tmp_path/'source.pdf'; output=tmp_path/'result.pdf'
    with f.open() as d:
        p=d.new_page(width=1000,height=1000); p.insert_text((100,200),'71')
        pix=f.Pixmap(f.csRGB,f.IRect(0,0,10,10)); pix.clear_with(255); p.insert_image(f.Rect(10,10,20,20),pixmap=pix)
        d.new_page(width=1000,height=1000); d.save(source)
    with f.open(source) as d:
        cs=chars(d[0]); r,ib,method=tight(d[0],cs)
        items=[candidate('index',0,1,r,ib,1,method=method)]
    apply_candidates(source,output,items,[],[output])
    apply_candidates(source,output,items,[],[output])
    assert verify_candidates(source,output,items,[])['passed']
    assert not verify_candidates(source,output,[],[])['passed']
    with pytest.raises(ValueError): apply_candidates(source,source,items,[],[source])


def test_image_fallback_recovers_compressed_ocr_width():
    from pdf_book_linker.stage2 import title_tight
    with f.open() as d:
        p=d.new_page(width=600,height=800)
        p.insert_text((50,150),'Introduction ............... 71',fontsize=20)
        cs=chars(p)[:12]
        for c in cs:
            b=list(c['bbox']);b[0]=50+(b[0]-50)*.5;b[2]=50+(b[2]-50)*.5;c['bbox']=b
        r,_,method=title_tight(p,cs,p.search_for('71')[0].x0)
        actual=p.search_for('Introduction')[0]
        assert r[2]>=actual.x1-2
        assert r[2]<actual.x1+10
        assert method=='text_row+image_components'


def test_extract_output_cannot_replace_source(tmp_path):
    from pdf_book_linker.stage2 import run
    from pdf_book_linker.storage import write_json
    source=tmp_path/'source.pdf'
    with f.open() as d:
        d.new_page(); d.save(source)
    mapping=tmp_path/'map.json'; write_json(mapping,{'segments':[]})
    config=tmp_path/'config.json'
    write_json(config,dict(source=str(source),work_dir=str(tmp_path),pagemap=str(mapping),output=str(tmp_path/'other.pdf'),extract_output=str(source)))
    before=source.read_bytes()
    with pytest.raises(ValueError,match='元 PDF'): run(config)
    assert source.read_bytes()==before
