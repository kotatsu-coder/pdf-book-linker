import pymupdf as f
import pytest
from pdf_book_linker.stage3 import (parse_pagerefs, join_index_digits, semantic_index,
    navigation_starts, overrides, check_plan)
from pdf_book_linker.stage2 import candidate, apply_candidates, verify_candidates


@pytest.mark.parametrize('text,nums', [
    ('（17ページ）で述べ',[17]), ('１０５ ページ参照',[105]), ('66頁',[66]),
    ('see p.13 and P.１４',[13,14]), ('pp.125-128',[125]), ('pp.１２５～１２８',[125]),
    ('角膜炎(l74ページ参照l',[174]), ('I05ページ',[105]), ('O7ページ',[7]),
    ('P100 P2Y2 100mg 2025年 図3-2',[]), ('AP.12',[]),
])
def test_pageref_patterns(text,nums):
    assert [n['number'] for n in parse_pagerefs(text,1,227)]==nums


def test_pageref_invalid_and_damaged():
    assert not any(n['valid'] for n in parse_pagerefs('0ページ 228頁 pp.128-125 p.12-14',1,227))
    assert parse_pagerefs('l74ページ',1,227)[0]['confidence']<.95
    text='参照（１２５ページ）'
    n=parse_pagerefs(text,1,227)[0]
    assert text[n['start']:n['end']]=='１２５ページ'


def test_digit_gap_preserves_comma():
    def ch(c,x,w=10): return dict(c=c,bbox=[x,0,x+w,12])
    cs=[ch('7',0),ch(' ',10,2),ch('6',12),ch(',',23),ch('9',28)]
    assert ''.join(c['c'] for c in join_index_digits(cs))=='76,9'
    cs=[ch('7',0),ch(' ',10,10),ch('6',20)]
    assert ''.join(c['c'] for c in join_index_digits(cs))=='7 6'
    assert ''.join(c['c'] for c in join_index_digits([ch('5',0),ch('2',35)]))=='5 2'


def test_index_multiline_terms_and_digit_in_term():
    from pdf_book_linker.stage3 import extract_index
    from pdf_book_linker.pagemap import PageMap
    with f.open() as d:
        p=d.new_page(width=1000,height=1000)
        p.insert_text((100,150),'Bell 96',fontsize=18)
        p.insert_text((100,205),'Bielschowsky head tilt',fontsize=18)
        p.insert_text((135,255),'152,159',fontsize=18)
        p.insert_text((100,310),'Panel D-15',fontsize=18)
        p.insert_text((300,310),'222',fontsize=18)
        pm=PageMap({'segments':[dict(pdf_index0_start=0,pdf_index0_end=226,print_start=1)]})
        result=extract_index(d,[0],pm,dict(print_range=[1,227],columns=1))
        assert [x['print_page'] for x in result]==['96','152','159','222']
        assert all(x['term'].startswith('Bielschowsky') for x in result if x['print_page'] in ('152','159'))
        assert result[-1]['term'].endswith('15')


def test_part_navigation_and_chapter_option():
    toc=[dict(kind='toc',role='title',status='approved',confidence=1,level=lev,title=title,target_pdf_index0=p)
         for lev,title,p in [(1,'PART1 Intro',6),(2,'First',7),(1,'PART2 Body',36),(2,'01 Foo',37),(1,'Back',80)]]
    cfg=dict(nav=dict(index_page=90))
    assert navigation_starts(toc,cfg)==[6,36,80]
    assert navigation_starts(toc,{**cfg,'nav_unit':'chapter'})==[6,7,36,37,80]


def test_semantic_previous_and_garbage():
    with f.open() as d:
        p=d.new_page();p.insert_text((50,100),'Glaucoma')
        d.new_page();d.new_page()
        items=[dict(term=t,target_pdf_index0=p,status='approved',confidence=1) for t,p in [('Glaucoma',1),('Missing',2),('?)',2)]]
        assert semantic_index(d,items)==1
        assert [c['status'] for c in items]==['approved','review','approved']


def test_overrides_win_and_unknown_errors():
    from pdf_book_linker.storage import write_json
    # In-memory identity and confidence remain explicit; file behavior below.
    assert overrides([dict(id='x')],None)==[dict(id='x')]


def test_manual_overrides_apply(tmp_path):
    from pdf_book_linker.storage import write_json
    path=tmp_path/'patch.json'
    write_json(path,{'overrides':[dict(id='x',status='approved',confidence=1,target_pdf_index0=4)]})
    result=overrides([dict(id='x',status='review',confidence=.7)],path)
    assert result[0]['target_pdf_index0']==4 and result[0]['manual_override']
    with pytest.raises(ValueError,match='unknown'): overrides([],path)


def test_inline_toc_digit_ink_and_title_separation():
    from pdf_book_linker.stage3 import toc_boxes
    with f.open() as d:
        p=d.new_page(width=1000,height=1000)
        p.insert_text((100,200),'Title       22',fontsize=24)
        real=p.search_for('22')[0]
        title=candidate('toc',0,0,[98,173,real.x0+10,204],[100,175,real.x0+8,200],1,
                        role='title',print_page='22')
        num=candidate('toc',0,0,[real.x0+10,173,real.x1+2,204],list(real),1,
                      role='number',print_page='22')
        toc_boxes(d,[title,num])
        assert title['rect'][2]<num['rect'][0]
        assert num['rect'][0]<real.x0+3 and num['rect'][2]>real.x1-3


def test_index_headings_same_height_in_both_columns():
    from pdf_book_linker.stage3 import build_outline
    with f.open() as d:
        p=d.new_page(width=1750,height=2790)
        p.insert_text((480,200),'あ',fontname='japan',fontsize=34)
        p.insert_text((1180,200),'か',fontname='japan',fontsize=34)
        result,review=build_outline(d,[],dict(index_pages=[0],index=dict(columns=2)))
        assert [r[1] for r in result if r[0]==2]==['あ','か']


def test_wrapped_reference_has_two_tight_rectangles():
    from pdf_book_linker.stage3 import extract_pagerefs
    from pdf_book_linker.pagemap import PageMap
    with f.open() as d:
        p=d.new_page(width=600,height=800)
        p.insert_text((400,150),'118',fontsize=14)
        p.insert_text((50,185),'ページ参照',fontname='japan',fontsize=14)
        pm=PageMap({'segments':[dict(pdf_index0_start=0,pdf_index0_end=226,print_start=1)]})
        result=extract_pagerefs(d,[0],set(),pm,dict(print_range=[1,227]))
        assert len(result)==2
        assert len({c['logical_reference_id'] for c in result})==1
        assert all(f.Rect(c['rect']).width<100 for c in result)


def test_positioned_outline_roundtrip(tmp_path):
    src=tmp_path/'s.pdf';out=tmp_path/'o.pdf'
    with f.open() as d:
        d.new_page();d.save(src)
    outline=[[1,'Heading',1,125.5]]
    apply_candidates(src,out,[],outline,[out])
    assert verify_candidates(src,out,[],outline)['passed']


def test_overlapping_links_fail():
    with f.open() as d:
        d.new_page()
        items=[candidate('pageref',0,0,[10,10,50,50],[15,15,40,40],1),
               candidate('index',0,0,[20,20,60,60],[25,25,50,50],1)]
        with pytest.raises(ValueError,match='overlapping'):
            check_plan(d,items,[],dict(nav_unit='chapter',nav=dict(index_page=0,first_page=1,last_page=0)))
