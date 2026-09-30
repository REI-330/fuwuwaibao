"""Import only reviewed records via WeKnora's documented manual API."""
import argparse,json,time
from pathlib import Path
import requests
parser=argparse.ArgumentParser(); parser.add_argument('--input',required=True); parser.add_argument('--base-url',required=True); parser.add_argument('--kb-id',required=True); parser.add_argument('--api-key',required=True); parser.add_argument('--proxy'); parser.add_argument('--dry-run',action='store_true'); args=parser.parse_args()
records=[json.loads(l) for l in Path(args.input).read_text(encoding='utf8').splitlines() if l.strip()]
eligible=[r for r in records if r.get('eligible_for_import') and r.get('review_status') in ('APPROVED','WAIVED')]
print(json.dumps({'records':len(records),'eligible':len(eligible),'dry_run':args.dry_run},ensure_ascii=False))
if args.dry_run: raise SystemExit(0)
s=requests.Session(); s.trust_env=False; s.headers.update({'X-API-Key':args.api_key,'Content-Type':'application/json','User-Agent':'fuwuwaibao-knowledge-cn/1.0'}); s.proxies={'http':args.proxy,'https':args.proxy} if args.proxy else {}
for i,r in enumerate(eligible,1):
    payload={'title':r['title'],'content':r['content'],'status':'publish','channel':'official-source'}  # 实测：manual 接口只接受 publish，传 published 会 400（状态仅支持 draft 或 publish）
    response=s.post(args.base_url.rstrip('/')+f'/api/v1/knowledge-bases/{args.kb_id}/knowledge/manual',json=payload,timeout=90)
    print(i,response.status_code,r['id']); response.raise_for_status(); time.sleep(.05)
