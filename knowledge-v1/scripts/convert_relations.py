import csv,json
from pathlib import Path
root=Path(r'C:\Users\hr206\OneDrive\Desktop\fuwuwaibao\knowledge-v1\data\tabiya-open-dataset\tabiya-esco-v1.1.1\csv'); out=[]
with (root/'occupation_skill_relations.csv').open(encoding='utf-8-sig') as f:
 for i,r in enumerate(csv.DictReader(f)):
  out.append({'id':f"esco-rel-{i}",'type':'rule','title':f"{r['OCCUPATIONID']} requires {r['SKILLID']}",'content':f"Occupation {r['OCCUPATIONID']} has {r['RELATIONTYPE']} skill {r['SKILLID']}.",'occupation_ids':['esco-'+r['OCCUPATIONID']],'skill_ids':['esco-'+r['SKILLID']],'relations':[{'predicate':'requires_skill','target_id':'esco-'+r['SKILLID']}],'source':{'name':'ESCO v1.1.1','url':'https://esco.ec.europa.eu/en/use-esco/download','license':'EU open data'},'confidence':0.95})
p=Path(r'C:\Users\hr206\OneDrive\Desktop\fuwuwaibao\knowledge-v1\data\esco-relations.jsonl'); p.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in out)+'\n',encoding='utf8'); print(len(out))
