"""Optional local heading-only OCR using an already installed Tesseract."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import csv
import io
import pymupdf as f
from .storage import read_json,write_json

class HeadingOCR:
    def __init__(self,work,identity,settings=True):
        settings={} if settings is True else settings
        self.settings=settings
        self.executable=shutil.which(settings.get('executable','tesseract'))
        self.root=Path(work)/'heading_ocr';self.root.mkdir(parents=True,exist_ok=True)
        identity=[identity,settings,self.executable,Path(self.executable).stat().st_mtime_ns if self.executable else None]
        self.signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:16]
        self.stats=dict(enabled=True,available=bool(self.executable),scope='top heading only',renders=0,cache_hits=0,errors=[])
    def text(self,page):
        if not self.executable: return ''
        path=self.root/f'{page.number:05}_{self.signature}.json'
        if path.exists(): self.stats['cache_hits']+=1;return read_json(path)['text']
        height=self.settings.get('height_ratio',.14)
        if not 0<height<=.3: raise ValueError('heading_ocr.height_ratio は 0〜0.3 です')
        scale=self.settings.get('width',1400)/page.rect.width
        pix=page.get_pixmap(matrix=f.Matrix(scale,scale),clip=f.Rect(0,0,page.rect.width,page.rect.height*height))
        try:
            proc=subprocess.run([self.executable,'stdin','stdout','-l',self.settings.get('language','jpn+eng'),'--psm','11'],
                                input=pix.tobytes('png'),capture_output=True,timeout=30)
            if proc.returncode: raise ValueError(proc.stderr.decode('utf-8',errors='replace')[-500:])
            text=proc.stdout.decode('utf-8',errors='replace')
            write_json(path,dict(text=text,scope=[0,0,1,height],engine='local Tesseract'))
            self.stats['renders']+=1
            return text
        except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
            self.stats['errors'].append(dict(page=page.number,reason=str(exc)))
            return ''

class TocNumberOCR(HeadingOCR):
    """Repair an OCR layer that incorrectly typesets a folio column vertically."""
    def __init__(self,work,identity,settings=True):
        super().__init__(work,identity,settings)
        self.root=Path(work)/'toc_number_ocr';self.root.mkdir(parents=True,exist_ok=True)
        self.stats['scope']=[self.settings.get('x0_ratio',.9),.08,1,.96]
    def numbers(self,page):
        if not self.executable: return []
        path=self.root/f'{page.number:05}_{self.signature}.json'
        if path.exists(): self.stats['cache_hits']+=1;return read_json(path)['numbers']
        scale=2000/page.rect.width
        left=self.settings.get('x0_ratio',.9)
        if not .5<=left<1: raise ValueError('toc_number_ocr.x0_ratio は0.5以上1未満です')
        self.stats['scope']=[left,.08,1,.96]
        pix=page.get_pixmap(matrix=f.Matrix(scale,scale),clip=f.Rect(page.rect.width*left,page.rect.height*.08,page.rect.width,page.rect.height*.96))
        try:
            proc=subprocess.run([self.executable,'stdin','stdout','-l','eng','--psm','6','-c','tessedit_char_whitelist=0123456789','tsv'],input=pix.tobytes('png'),capture_output=True,timeout=30)
            if proc.returncode: raise ValueError(proc.stderr.decode('utf-8',errors='replace')[-500:])
            numbers=[]
            for row in csv.DictReader(io.StringIO(proc.stdout.decode('utf-8',errors='replace')),delimiter='\t'):
                token=row.get('text','').strip()
                if not token.isdecimal() or float(row['conf'])<60 or not 0<int(token)<10000: continue
                x,y,w,h=[int(row[k]) for k in ('left','top','width','height')]
                numbers.append(dict(label=str(int(token)),display_rect=[(pix.x+x)/scale,(pix.y+y)/scale,(pix.x+x+w)/scale,(pix.y+y+h)/scale],ocr_confidence=float(row['conf'])))
            write_json(path,dict(numbers=numbers,engine='local Tesseract',scope=[left,.08,1,.96]))
            self.stats['renders']+=1
            return numbers
        except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
            self.stats['errors'].append(dict(page=page.number,reason=str(exc)));return []
