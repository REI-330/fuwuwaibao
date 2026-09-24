import argparse, csv, hashlib, json
from pathlib import Path

def make_id(prefix, value):
    return prefix + '-' + hashlib.sha1(value.encode('utf-8')).hexdigest()[:12]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    rows=[]
    for p in Path(a.input).rglob('*'):
        if p.suffix.lower()=='.jsonl':
            rows += [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
        elif p.suffix.lower()=='.json':
            data=json.loads(p.read_text(encoding='utf-8')); rows += data if isinstance(data,list) else [data]
        elif p.suffix.lower()=='.csv':
            with p.open(encoding='utf-8-sig', newline='') as f: rows += list(csv.DictReader(f))
    out=[]; seen=set()
    for r in rows:
        title=(r.get('title') or r.get('occupation') or r.get('preferredLabel') or r.get('name') or '').strip()
        content=(r.get('content') or r.get('description') or r.get('definition') or title).strip()
        if not title: continue
        key=hashlib.sha1((title+'\n'+content).encode()).hexdigest()
        if key in seen: continue
        seen.add(key)
        typ=r.get('type') or ('occupation' if r.get('occupation') or r.get('preferredLabel') else 'skill')
        out.append({'id':r.get('id') or make_id(typ,title),'type':typ,'title':title,'content':content,'occupation_ids':r.get('occupation_ids',[]),'skill_ids':r.get('skill_ids',[]),'relations':r.get('relations',[]),'stage':r.get('stage'),'source':r.get('source',{'name':'unknown','url':'','license':'verify'}),'updated_at':r.get('updated_at'),'confidence':float(r.get('confidence',0.7))})
    Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in out)+'\n',encoding='utf-8'); print(json.dumps({'input_rows':len(rows),'output_rows':len(out),'deduped':len(rows)-len(out)},ensure_ascii=False))
if __name__=='__main__': main()
