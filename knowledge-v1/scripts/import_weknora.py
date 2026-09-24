import argparse,json,urllib.request
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--base-url',required=True); p.add_argument('--kb-id',required=True); p.add_argument('--batches',required=True); p.add_argument('--token'); a=p.parse_args(); headers={'Content-Type':'application/json'}; 
if a.token: headers['Authorization']='Bearer '+a.token
for f in sorted(Path(a.batches).glob('batch-*.json')):
 req=urllib.request.Request(a.base_url.rstrip('/')+'/api/knowledge-bases/'+a.kb_id+'/documents',data=f.read_bytes(),headers=headers,method='POST')
 try:
  with urllib.request.urlopen(req,timeout=120) as r: print(f.name,r.status,r.read().decode()[:200])
 except Exception as e: print(f.name,'ERROR',e)
