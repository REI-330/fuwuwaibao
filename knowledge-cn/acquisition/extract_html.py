import argparse,json
from pathlib import Path
from bs4 import BeautifulSoup

p=argparse.ArgumentParser(); p.add_argument('--html',required=True); p.add_argument('--source-id',required=True); p.add_argument('--output',required=True); a=p.parse_args()
raw=Path(a.html).read_text(encoding='utf8',errors='replace'); soup=BeautifulSoup(raw,'html.parser')
for tag in soup(['script','style','noscript']): tag.decompose()
text='\n'.join(x.strip() for x in soup.get_text('\n').splitlines() if x.strip())
record={'id':a.source_id+'-document','type':'source_document','title':soup.title.get_text(strip=True) if soup.title else a.source_id,'content':text,'source':{'id':a.source_id},'record_kind':'STATISTIC_CANDIDATE','review_status':'PENDING'}
Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(record,ensure_ascii=False)+'\n',encoding='utf8'); print(json.dumps({'characters':len(text)},ensure_ascii=False))
