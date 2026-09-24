import re,json
from pathlib import Path
from pypdf import PdfReader
pdf=next(Path('data/sources/moe-majors-2026').glob('*.pdf')); out=[]
for page_no,page in enumerate(PdfReader(pdf).pages,1):
 t=page.extract_text(extraction_mode='layout') or ''
 for line in t.splitlines():
  m=re.match(r'^\s*(\d{6}[A-Z]{0,2})\s+(.+?)\s*$',line)
  if m and not m.group(1).startswith(('0306','0307')):
   name=re.sub(r'\s{2,}.*$','',m.group(2)).strip()
   if name and not name.startswith(('—','学科')): out.append({'id':'moe-major-'+m.group(1),'major_code':m.group(1),'major_name':name,'page':page_no,'record_kind':'FACT_CANDIDATE','review_status':'PENDING','source':{'id':'moe-majors-2026','page':page_no}})
Path('data/candidates/moe-majors-2026-structured.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in out),encoding='utf8')
print(len(out))
