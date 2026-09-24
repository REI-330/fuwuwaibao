"""Produce a deterministic release-readiness summary for the CN knowledge base."""
import argparse, json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',default='.')
    a=ap.parse_args(); root=Path(a.root); cand=root/'data'/'candidates'; reviewed=root/'data'/'reviewed'
    out={'sources':{},'totals':{'candidates':0,'approved':0,'eligible':0}}
    cfg=root/'acquisition'/'sources.json'
    registry={x['id']:x for x in json.loads(cfg.read_text(encoding='utf-8'))} if cfg.exists() else {}
    eligible_ids={k for k,v in registry.items() if v.get('mode')=='public_document'}
    for p in sorted(cand.glob('*.jsonl')):
        if p.stem.replace('-structured','') not in eligible_ids:
            continue
        rows=[json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
        rp=reviewed/p.name
        approved=eligible=0
        if rp.exists():
            rr=[json.loads(x) for x in rp.read_text(encoding='utf-8').splitlines() if x.strip()]
            approved=sum(x.get('review_status')=='APPROVED' for x in rr)
            eligible=sum(x.get('eligible_for_import') is True for x in rr)
        out['sources'][p.stem]={'candidates':len(rows),'approved':approved,'eligible':eligible}
        out['totals']['candidates']+=len(rows); out['totals']['approved']+=approved; out['totals']['eligible']+=eligible
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
