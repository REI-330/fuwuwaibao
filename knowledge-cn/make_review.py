import json
from pathlib import Path
root=Path(r'C:\Users\hr206\OneDrive\Desktop\fuwuwaibao\knowledge-cn')
m=json.loads((root/'data/sources/moe-majors-2026/latest.json').read_text(encoding='utf8'))
rows=[json.loads(x) for x in (root/'data/candidates/moe-majors-2026-structured.jsonl').read_text(encoding='utf8').splitlines() if x.strip()]
out=[{'record_id':r['id'],'raw_content_hash':m['raw_content_hash'],'decision':'PENDING','reviewer':'','reviewed_at':'','evidence_locator':f"PDF page {r['page']}"} for r in rows]
(root/'data/reviews').mkdir(exist_ok=True)
(root/'data/reviews/moe-majors-2026.template.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
print(len(out),m['raw_content_hash'])
