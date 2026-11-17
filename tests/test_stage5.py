from pathlib import Path
import pymupdf as f
import pytest
from pdf_book_linker.autoconfig import page_rows, printed_candidates, clean_title, index_boundaries, terminal_entry, toc_columns, toc_from_number_strip
from pdf_book_linker.autoconfig import fit_pagemap
from pdf_book_linker.autoconfig import review_toc_outliers
from pdf_book_linker.titlemap import estimate_offsets
from pdf_book_linker.pagemap import PageMap
from pdf_book_linker.navigation import plan, Raster, geometry, section_starts
from pdf_book_linker.batch import run, fingerprint, current, required_results
from pdf_book_linker.storage import write_json
from pdf_book_linker.stage3 import parse_pagerefs, extract_pagerefs
from pdf_book_linker.stage3 import external_page_reference

def test_split_folio_digits_and_leaders():
    with f.open() as d:
        p=d.new_page(width=1000,height=1400)
        ws=[(440,1340,460,1355,'....',0,0,0),(470,1340,480,1355,'3',0,0,1),
            (489,1340,499,1355,'9',0,0,2),(510,1340,530,1355,'....',0,0,3)]
        rows=page_rows(p,words=ws)
        assert printed_candidates(rows,200)[0]['number']==39
        ws[2]=(510,1340,520,1355,'9',0,0,2)
        assert '3 9' in page_rows(p,words=ws)[0]['text']
    assert printed_candidates([dict(text='88 強度近視',x=.04,right=.3,y=.03,rect=[0,0,40,10])],200)[0]['number']==88
    assert printed_candidates([dict(text='トーリック眼内レンズ91',x=.5,right=.98,y=.94,rect=[50,90,99,100])],200)[0]['number']==91

def test_title_offset_consensus_outliers_and_crosscheck():
    anchors=[(n,n+7) for n in (2,14,28,40,90,150)]+[(20,99),(30,88)]
    result=estimate_offsets(anchors,200)
    assert PageMap(result).by_label['93']==100
    assert result['evidence']['supporting_labels']==6
    obs=[[] for _ in range(200)]
    for i in (20,21,22): obs[i]=[dict(style='arabic',number=i-3)]
    assert estimate_offsets(anchors,200,observations=obs) is None
    assert estimate_offsets([(1,8),(10,17)],200) is None

def test_front_toc_outliers_do_not_discard_the_body_number_sequence():
    v=lambda n:dict(number=n,style='arabic',confidence=.99)
    obs=[[v(99)],[v(100)]]+[[v(n)] for n in range(1,101)]
    mapping,m=fit_pagemap(obs,len(obs));pm=PageMap(mapping)
    assert pm.by_label['1']==2 and pm.by_label['99']==100
    assert 0 not in pm.by_index and 1 not in pm.by_index
    assert m['outlier_anchors']==[0,1]

def test_toc_number_outlier_and_its_title_are_reviewed_without_dropping_duplicates():
    cs=[dict(id=f'toc:{i}',kind='toc',role='number',source_pdf_index0=3,print_page=str(n),status='approved',confidence=.99)
        for i,n in enumerate([2,2,10,104,18,22])]
    cs.append(dict(cs[3],id=cs[3]['id']+':title',role='title'))
    outliers=review_toc_outliers(cs)
    assert [c['print_page'] for c in outliers]==['104']
    assert cs[3]['status']==cs[-1]['status']=='review'
    assert all(c['status']=='approved' for i,c in enumerate(cs[:-1]) if i!=3)

def test_piecewise_title_offsets_leave_unobserved_gap():
    result=estimate_offsets([(n,n+7) for n in (1,10,20,30,40)]+[(n,n+9) for n in (60,70,80)],100)
    pm=PageMap(result)
    assert pm.by_label['20']==27 and pm.by_label['70']==79
    assert '50' not in pm.by_label

def test_folios_resolve_running_head_on_following_page():
    anchors=[(n,n+12) for n in (2,8,20,40,60,80)]
    obs=[[] for _ in range(120)]
    for i in range(30,45): obs[i]=[dict(style='arabic',number=i-11)]
    result=estimate_offsets(anchors,120,observations=obs)
    assert PageMap(result).by_label['89']==100
    assert result['evidence']['heading_page_adjustment']==-1
    assert result['evidence']['readable_agreement']==1

def test_author_and_leader_are_not_heading():
    from pdf_book_linker.autoconfig import title_key,key
    assert clean_title('正常所見・・・・著者氏名')=='正常所見'
    assert clean_title('眼球 (著者氏名)')=='眼球'
    assert title_key('III. 撮り方のコツ')==key('撮り方のコツ')
    assert title_key('IVIg療法')==key('IVIg療法')
    assert terminal_entry(dict(text='Heading .... Author 093',rect=[0,0,100,10]))['labels']==['93']

def test_navigation_fixed_per_side_and_clear():
    assert geometry(100,200)['tap_size']==16
    assert geometry(10000,16000)['tap_size']==150
    assert section_starts([12,40,100],[1522],1732)==[12,40,100,1522]
    with pytest.raises(ValueError): section_starts([12],[1732],1732)
    with f.open() as d:
        for i in range(8):
            p=d.new_page(width=400,height=600)
            p.draw_rect(f.Rect(100,550,300,600),fill=(0,0,0))
            p.insert_text((30,120),'Synthetic body')
        placements,report=plan(d,range(8),dict(position='top-outer',size=.055,opacity=.25))
        assert len(placements)==8
        assert all(x['position']=='top-outer' for x in report['decisions'].values())
        for i,(rs,geo) in placements.items():
            assert geo['tap_size']==pytest.approx(22)
            assert all(Raster(d[i]).clear(r) for r in rs)
            assert list(rs[0])==list(placements[i%2][0][0])
        auto,decision=plan(d,range(8),{})
        assert all(v['position']!='bottom-centre' for v in decision['decisions'].values())
        assert all(Raster(d[i]).clear(r) for i,(rs,geo) in auto.items() for r in rs)

def test_gutter_detects_three_unequal_columns():
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        for y in range(70,540,15):
            for x in (30,145,260): p.insert_text((x,y),'LongSubject123ABCDE',fontsize=10)
        bounds=index_boundaries(p,p.get_text('words'))
        assert len(bounds)==2

def test_toc_author_column_does_not_become_second_title_column():
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        for y in range(100,400,30):
            p.insert_text((30,y),'Title ........ Author',fontsize=10)
            p.insert_text((360,y),'22',fontsize=10)
        assert toc_columns(p,p.get_text('words'),default=2)==1
        for y in range(100,400,30):
            p.insert_text((180,y),'24',fontsize=10)
            p.insert_text((220,y),'Right title',fontsize=10)
        assert toc_columns(p,p.get_text('words'),default=1)==2

def test_ragged_toc_numbers_are_still_one_title_column():
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        for j,y in enumerate(range(100,460,30)):
            p.insert_text((30,y),'Real title',fontsize=10)
            p.insert_text((400*(.43,.5,.65,.88)[j%4],y),str(j+1),fontsize=10)
        assert toc_columns(p,p.get_text('words'),default=2)==1

def test_number_strip_uses_title_row_and_real_number_rectangle():
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        p.insert_text((30,100),'Synthetic title ...... Author',fontsize=12)
        p.insert_text((30,140),'Another title ...... Author',fontsize=12)
        # Model an existing OCR layer whose digit coordinates are unusable.
        numbers=[dict(label='22',display_rect=[360,89,377,101]),dict(label='27',display_rect=[360,129,377,141])]
        entries=toc_from_number_strip(p,p.get_text('words'),numbers)
        assert [e['title'] for e in entries]==['Synthetic title','Another title']
        assert [e['labels'] for e in entries]==[['22'],['27']]
        assert entries[0]['number_rect']==[360,89,377,101]
        p.insert_text((290,100),'Author',fontsize=12)
        capped=toc_from_number_strip(p,p.get_text('words'),numbers,title_xmax=.65)
        assert all('Author' not in e['title'] for e in capped)
        p.insert_text((30,180),'索引',fontname='japan',fontsize=12)
        short=toc_from_number_strip(p,p.get_text('words'),[dict(label='30',display_rect=[360,169,377,181])])
        assert short[0]['title']=='索引'
        p.insert_text((30,234),'Wrapped parent title',fontsize=12)
        p.insert_text((30,257),'Following topic',fontsize=12)
        wrapped=toc_from_number_strip(p,p.get_text('words'),[dict(label='40',display_rect=[360,238,377,250])])
        assert wrapped[0]['title']=='Wrapped parent title'

def test_batch_resume_and_source_invalidation(tmp_path,capsys):
    folder=tmp_path/'source';folder.mkdir();source=folder/'blank.pdf'
    with f.open() as d:
        for _ in range(4):d.new_page()
        d.save(source)
    root=tmp_path/'reports';work=tmp_path/'work'
    assert run(folder,root,work)=={'OCR required':1}
    assert run(folder,root,work)=={'OCR required':1}
    assert '(cached)' in capsys.readouterr().out
    manifest=root/'blank/batch_identity.json'; sig=fingerprint(source)
    assert current(manifest,sig,[root/'blank/metrics.json'])
    source.touch()
    assert not current(manifest,fingerprint(source),[root/'blank/metrics.json'])
    assert (root/'summary.csv').exists()
    (folder/'another.pdf').write_bytes(source.read_bytes())
    assert run(folder,root,work,jobs=2)=={'OCR required':2}

def test_reference_table_rule_is_not_a_hundred_digit():
    assert not parse_pagerefs('o頁 I頁',1,500)
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        p.insert_text((290,200),'1',fontsize=5)
        p.insert_text((300,200),'58頁',fontname='japan',fontsize=12)
        pm=PageMap(dict(segments=[dict(pdf_index0_start=0,pdf_index0_end=399,print_start=1)]))
        refs=extract_pagerefs(d,[0],set(),pm,dict(normalized=True,print_range=[1,400],padding_ratio=.0017))
        assert [c['print_page'] for c in refs]==['58']

def test_external_book_citation_is_not_an_internal_page():
    for text in ['Ryanの教科書第4版(2006)48頁','「眼科学」p.1665']:
        n=parse_pagerefs(text,1,2000)[0]
        assert external_page_reference(text,n['start'])
    text='本書の「主訴」p.100を参照'
    n=parse_pagerefs(text,1,2000)[0]
    assert not external_page_reference(text,n['start'],'別の教科書第4版')
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        p.insert_text((40,100),'教科書第4版(2006)48頁',fontname='japan',fontsize=12)
        p.insert_text((40,140),'本書50頁参照',fontname='japan',fontsize=12)
        pm=PageMap(dict(segments=[dict(pdf_index0_start=0,pdf_index0_end=99,print_start=1)]))
        cs=extract_pagerefs(d,[0],set(),pm,dict(print_range=[1,100],normalized=True,padding_ratio=.0017))
        assert [(c['print_page'],c['target_pdf_index0']) for c in cs]==[('48',None),('50',49)]

def test_local_heading_ocr_is_cropped_and_cached(tmp_path,monkeypatch):
    from pdf_book_linker.localocr import HeadingOCR
    from types import SimpleNamespace
    executable=tmp_path/'local-tesseract.exe';executable.write_bytes(b'fake')
    monkeypatch.setattr('pdf_book_linker.localocr.shutil.which',lambda _:str(executable))
    calls=[]
    def invoke(args,**kwargs):
        calls.append(args)
        assert args[1:3]==['stdin','stdout']
        assert kwargs['input'].startswith(b'\x89PNG')
        return SimpleNamespace(returncode=0,stdout=b'Synthetic heading',stderr=b'')
    monkeypatch.setattr('pdf_book_linker.localocr.subprocess.run',invoke)
    reader=HeadingOCR(tmp_path,dict(size=123),dict(height_ratio=.14))
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        assert reader.text(p)=='Synthetic heading'
        assert reader.text(p)=='Synthetic heading'
    assert len(calls)==1 and reader.stats['cache_hits']==1
    cached=next((tmp_path/'heading_ocr').glob('*.json'))
    from pdf_book_linker.storage import read_json
    assert read_json(cached)['scope']==[0,0,1,.14]

def test_batch_missing_previews_or_broken_manifest_are_not_current(tmp_path):
    manifest=tmp_path/'batch_identity.json';write_json(manifest,dict(version=1))
    write_json(tmp_path/'metrics.json',dict(status='ok',pagemap={},nav_preview_pages=[12,13]))
    for name in ['config.json','autoconfig_report.md','navigation.json','preview_0012.png']:
        (tmp_path/name).write_text('synthetic',encoding='utf-8')
    assert not current(manifest,dict(version=1),required_results(tmp_path))
    (tmp_path/'preview_0013.png').write_text('synthetic',encoding='utf-8')
    assert current(manifest,dict(version=1),required_results(tmp_path))
    manifest.write_text('{broken',encoding='utf-8')
    assert not current(manifest,dict(version=1),required_results(tmp_path))


def test_inline_manual_map_refreshes_reference_range(tmp_path):
    from pdf_book_linker.autoconfig import autoconfig
    source=tmp_path/'manual.pdf'
    with f.open() as d:
        for i in range(7):
            p=d.new_page(width=400,height=600)
            p.insert_text((30,100),'Synthetic book with enough text for each page')
            p.insert_text((350,580),str(i+1))
        d.save(source)
    override=tmp_path/'override.json'
    write_json(override,dict(pagemap=dict(segments=[dict(pdf_index0_start=0,pdf_index0_end=6,print_start=100)]),index_page_range=[5,6]))
    cfg,m=autoconfig(source,tmp_path/'auto',override=override)
    assert m['pagemap']['strategy']=='manual_override'
    assert cfg['pageref']['print_range']==[100,106]
    assert cfg['index']['print_range']==[100,104]
    assert cfg['index_pages']==[5,6]

def test_index_ditto_keeps_parent_and_does_not_change_ikasei():
    from pdf_book_linker.stage3 import extract_index
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        for y,label in [(100,'角膜 12'),(130,'ーの検査 14'),(160,'ーの治療 16'),(190,'一過性黒内障 18')]:
            p.insert_text((40,y),label,fontname='japan',fontsize=12)
        pm=PageMap(dict(segments=[dict(pdf_index0_start=0,pdf_index0_end=29,print_start=1)]))
        cs=extract_index(d,[0],pm,dict(columns=1,ymin=50,ymax=250,print_range=[1,30],row_tolerance_ratio=.00824))
        assert [c['term'] for c in cs]==['角膜','角膜の検査','角膜の治療','一過性黒内障']
        p.insert_text((40,220),'網膜 20i,22i',fontname='japan',fontsize=12)
        cs=extract_index(d,[0],pm,dict(columns=1,ymin=200,ymax=250,print_range=[1,30],row_tolerance_ratio=.00824))
        assert [c['print_page'] for c in cs]==['20','22']

def test_common_index_never_links_foreign_volume_to_local_page():
    from pdf_book_linker.stage3 import extract_index
    with f.open() as d:
        p=d.new_page(width=400,height=600)
        p.insert_text((40,100),'角膜 12(I),14(II)',fontname='japan',fontsize=12)
        pm=PageMap(dict(segments=[dict(pdf_index0_start=0,pdf_index0_end=29,print_start=1)]))
        refs=extract_index(d,[0],pm,dict(columns=1,ymin=50,ymax=250,print_range=[1,30],volume='I'))
        assert [(c['print_page'],c['volume'],c['target_pdf_index0']) for c in refs]==[('12','I',11),('14','II',None)]
        assert refs[1]['status']=='review'
