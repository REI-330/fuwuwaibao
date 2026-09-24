import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--input',required=True); p.add_argument('--output',required=True); p.add_argument('--batch-size',type=int,default=100); a=p.parse_args(); out=Path(a.output); out.mkdir(parents=True,exist_ok=True); batch=[]; n=0; files=0
for line in Path(a.input).open(encoding='utf8'):
 if line.strip(): batch.append(json.loads(line))
 if len(batch)>=a.batch_size:
  files+=1; (out/f'batch-{files:05d}.json').write_text(json.dumps({'documents':batch},ensure_ascii=False),encoding='utf8'); n+=len(batch); batch=[]
if batch:
 files+=1; (out/f'batch-{files:05d}.json').write_text(json.dumps({'documents':batch},ensure_ascii=False),encoding='utf8'); n+=len(batch)
(out/'manifest.json').write_text(json.dumps({'documents':n,'batches':files,'format':'WeKnora REST import payload'},ensure_ascii=False,indent=2),encoding='utf8'); print(json.dumps({'documents':n,'batches':files}))
