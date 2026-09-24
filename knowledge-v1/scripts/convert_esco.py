import csv,json
from pathlib import Path
root=Path(r'C:\Users\hr206\OneDrive\Desktop\fuwuwaibao\knowledge-v1\data\tabiya-open-dataset\tabiya-esco-v1.1.1\csv'); out=[]
for fn,typ,label,desc in [('occupations.csv','occupation','PREFERREDLABEL','DESCRIPTION'),('skills.csv','skill','PREFERREDLABEL','DESCRIPTION')]:
 with (root/fn).open(encoding='utf-8-sig') as f:
  for r in csv.DictReader(f):
   if r.get(label): out.append({'id':'esco-'+r.get('ID',''),'type':typ,'title':r[label],'content':(r.get(desc) or r.get('DEFINITION') or r[label]).strip(),'source':{'name':'ESCO v1.1.1','url':'https://esco.ec.europa.eu/en/use-esco/download','license':'EU open data'},'confidence':0.9})
p=Path(r'C:\Users\hr206\OneDrive\Desktop\fuwuwaibao\knowledge-v1\data\esco-knowledge.jsonl'); p.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in out)+'\n',encoding='utf8'); print(len(out))
