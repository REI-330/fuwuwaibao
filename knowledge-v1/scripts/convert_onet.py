import csv,json,argparse
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--onet-dir',required=True); p.add_argument('--output',required=True); a=p.parse_args(); d=Path(a.onet_dir); out=[]
def add(path,typ,fields):
 with path.open(encoding='utf8') as f:
  for r in csv.DictReader(f,delimiter='\t'):
   title=(r.get(fields[0]) or '').strip(); desc=(r.get(fields[1]) or title).strip()
   if title: out.append({'id':typ+'-'+(r.get(fields[2]) or title).replace(' ','-').lower(),'type':typ,'title':title,'content':desc,'source':{'name':'O*NET 31.0','url':'https://www.onetcenter.org/database.html','license':'CC BY 4.0'},'confidence':0.95})
add(d/'Occupation Data.txt','occupation',('Title','Description','O*NET-SOC Code'))
add(d/'Essential Skills.txt','skill',('Element Name','Description','Element ID'))
Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in out)+'\n',encoding='utf8'); print(json.dumps({'records':len(out)}))
