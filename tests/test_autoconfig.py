import importlib.util
from pathlib import Path
import pymupdf as f
import pytest
from pdf_book_linker.autoconfig import (page_rows, terminal_entry, printed_candidates,
    fit_pagemap, nav_geometry, hierarchy, autoconfig, build, slug, key)
from pdf_book_linker.cli import main
from pdf_book_linker.storage import read_json

@pytest.mark.parametrize('text,labels',[
    ('第1章 眼科......12',['12']),('A anatomy ●23',['23']),
    ('角膜炎 12, 14, 20',['12','14','20']),('はじめに iii',['iii']),
    ('PART2 眼科......3ー',['31']),('PART3 網膜......ー6ー',['161']),
    ('INDEX',None),('薬剤 100mg',None),('2026年',None)])
def test_terminal_lines(text,labels):
    row=dict(text=text,rect=[0,0,100,10],x=.1,y=.2,size=.02,col=0)
    entry=terminal_entry(row,1000)
    assert (entry['labels'] if entry else None)==labels

@pytest.mark.parametrize('text,style,num',[
    ('B眼球の構造と機能●3','arabic',3),('A 主訴 ●23','arabic',23),
    ('１２３','arabic',123),('iv','roman',4),('xii','roman',12)])
def test_running_numbers(text,style,num):
    rows=[dict(text=text,y=.94,rect=[0,90,40,100])]
    result=printed_candidates(rows,500)
    assert result[0]['number']==num and result[0]['style']==style
    assert not printed_candidates([dict(rows[0],y=.5)],500)

def test_segments_roman_plates_jumps_conflicts():
    def v(n,style='arabic'):return dict(number=n,style=style,confidence=.99)
    obs=[[v(1,'roman')],[v(2,'roman')],[],[v(1)],[v(2)],[],[v(3)],[v(4)],[v(6)],[v(7)],[]]
    mapping,metrics=fit_pagemap(obs,len(obs))
    from pdf_book_linker.pagemap import PageMap
    pm=PageMap(mapping)
    assert pm.by_label['i']==0 and pm.by_label['ii']==1
    assert pm.by_label['3']==6 and pm.by_label['6']==8
    assert 5 not in pm.by_index and '5' not in pm.by_label
    assert metrics['consistency']==100

@pytest.mark.parametrize('width,height',[(1750,2790),(384,596),(493,730)])
def test_nav_scaling(width,height):
    cfg=nav_geometry(width,height)
    assert cfg['tap_size']/width==pytest.approx(.06285)
    assert 4*cfg['tap_size']+3*cfg['gap']<width*.5
    assert cfg['line_width']<cfg['tap_size']/20

def test_hierarchy_and_two_columns():
    entries=[dict(title=t,x=x,size=.02,col=0) for t,x in [('PART 1',.1),('A 解剖',.1),('1.2 網膜',.14),('① 視神経',.14)]]
    hierarchy(entries); assert [e['level'] for e in entries]==[1,2,3,3]
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        for x,t in [(30,'Alpha 12'),(230,'Beta 24')]:p.insert_text((x,100),t)
        rows=page_rows(p,2)
        assert [terminal_entry(r)['labels'] for r in rows]==[['12'],['24']]

@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_rotated_rows(rotation):
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        p.insert_text((30,100),'Alpha 12')
        # Simulate upright text in a rotated page by rotating insertion as well.
        p.set_rotation(rotation)
        rows=page_rows(p)
        assert all(0<=r['y']<=1 for r in rows)
        assert all(f.Rect(0,0,400,600).contains(f.Rect(r['rect'])) for r in rows)

def test_ocr_needed_and_source_guard(tmp_path,capsys):
    source=tmp_path/'blank.pdf'
    with f.open() as d:
        for _ in range(4):d.new_page()
        d.save(source)
    assert main(['autoconfig',str(source),'--work-root',str(tmp_path/'auto')])==3
    assert 'OCR が必要' in capsys.readouterr().err
    with pytest.raises(ValueError,match='上書き'):
        build(source,source)

@pytest.fixture(scope='module',params=['ja','en'])
def demo(tmp_path_factory,request):
    tmp=tmp_path_factory.mktemp('demo4_'+request.param)
    spec=importlib.util.spec_from_file_location('demo_generator_'+request.param,Path(__file__).parents[1]/'tools/make_demo_book.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    mod.set_language(request.param)
    mod.INDEX_LAYOUT[:] = [[], []]
    with f.open() as draft:
        for i,item in enumerate(mod.plan):mod.render(draft,i,item)
        mod.build_index(draft)
    source=tmp/'demo.pdf'
    with f.open() as final:
        for i,item in enumerate(mod.plan):mod.render(final,i,item)
        mod.to_scanned(final,source,quality=65)
    cfg,metrics=autoconfig(source,tmp/'auto')
    items=read_json(Path(cfg['work_dir'])/'stage3_candidates.json')['candidates']
    return tmp,source,mod,cfg,metrics,items

def test_demo_exact_ground_truth(demo):
    tmp,source,mod,cfg,metrics,items=demo
    assert cfg['toc_pages']==[3] and cfg['part_pages']==[4,11,20] and cfg['index_pages']==[27,28]
    toc=[c for c in items if c['kind']=='toc' and c['role']=='number']
    labels=['iii']+[str(v) for k,v in mod.starts.items()]
    assert sorted(c['print_page'] for c in toc)==sorted(labels)
    assert all(c['target_pdf_index0']==(2 if c['print_page']=='iii' else int(c['print_page'])+3) and c['status']=='approved' for c in toc)
    expected=[]
    for page,layout in enumerate(mod.INDEX_LAYOUT):
        for col,y,kind,term,nums in layout:
            if kind=='entry': expected += [(key(term),str(n),n+3) for n in nums]
    actual=[(key(c['term']),c['print_page'],c['target_pdf_index0']) for c in items if c['kind']=='index']
    assert sorted(actual)==sorted(expected)
    assert all(c['status']=='approved' for c in items if c['kind']=='index')
    # Derive logical reference truth from authored placeholders, independently of extraction.
    expected_refs=[]
    import re
    for i,item in enumerate(mod.plan):
        if item[0] in ('chA','chB'):
            authored=item[1][3 if item[0]=='chA' else 5][1]
            # "pp.{p:a}-{p:b}" is one reference to the start of the range.
            authored=re.sub(r'(pp\.\{p:[^}]+\})-\{p:[^}]+\}',r'\1',authored)
            expected_refs += [(i,mod.starts[k]+3) for k in re.findall(r'\{p:([^}]+)\}',authored)]
    refs={c['logical_reference_id']:(c['source_pdf_index0'],c['target_pdf_index0']) for c in items if c['kind']=='pageref'}
    assert sorted(refs.values())==sorted(expected_refs)
    assert metrics['toc_target_hit_pct']==metrics['index_target_hit_pct']==100

def test_demo_build_and_verify(demo):
    tmp,source,mod,cfg,metrics,items=demo
    report=build(source,tmp/'linked.pdf',tmp/'auto')
    assert report['passed'] and report['images_unchanged']


def test_reference_table_column(tmp_path):
    source=tmp_path/'table.pdf'
    with f.open() as doc:
        for i in range(7):
            p=doc.new_page(width=400,height=600)
            p.insert_text((30,120),'Synthetic reference table book text',fontsize=11)
            p.insert_text((350,580),str(i+1),fontsize=10)
            if i==1:
                p.insert_text((80,180),'参照頁',fontname='japan',fontsize=12)
                p.insert_text((88,220),'4',fontsize=12)
        doc.save(source)
    cfg,metrics=autoconfig(source,tmp_path/'auto')
    items=read_json(Path(cfg['work_dir'])/'stage3_candidates.json')['candidates']
    table=[c for c in items if c.get('method')=='reference_table_column']
    assert [(c['source_pdf_index0'],c['target_pdf_index0']) for c in table]==[(1,3)]


def test_english_page_references_need_word_gaps():
    from pdf_book_linker.stage3 import parse_pagerefs
    # Rows are built without spaces; gaps after "see" restore the word boundary.
    text='seep.12andpage14'
    assert [n['number'] for n in parse_pagerefs(text,1,99)]==[]
    gaps={2,6,9,13}  # after "see", "12", "and", "page"
    assert [n['number'] for n in parse_pagerefs(text,1,99,gaps=gaps)]==[12,14]
    rng=parse_pagerefs('pages 10-12, page 3-4',1,99)
    assert [(n['number'],n['range_end'],n['valid']) for n in rng]==[(10,12,True),(3,4,False)]
    assert not parse_pagerefs('homepage 12',1,99)
