import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--knowledge',required=True); p.add_argument('--questions',required=True); a=p.parse_args()
ks=[json.loads(x) for x in Path(a.knowledge).read_text(encoding='utf8').splitlines() if x.strip()]; qs=[json.loads(x) for x in Path(a.questions).read_text(encoding='utf8').splitlines() if x.strip()]; hit=0
for q in qs:
 terms=[str(t).lower() for t in q.get('terms',[])]; hit+=any(all(t in (k.get('title','')+' '+k.get('content','')).lower() for t in terms) for k in ks)
print(json.dumps({'knowledge_count':len(ks),'question_count':len(qs),'keyword_hit_rate':hit/len(qs) if qs else 0},ensure_ascii=False))
