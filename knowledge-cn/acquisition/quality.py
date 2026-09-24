"""Validate source evidence and candidate records without promoting them."""
import argparse, json
from pathlib import Path

def main():
    p=argparse.ArgumentParser(); p.add_argument('--root',default='data'); a=p.parse_args(); root=Path(a.root); stats={'sources':0,'active':0,'pending_candidates':0,'metadata_complete':0,'errors':[],'config_warnings':[]}
    cfg=root.parent/'acquisition'/'sources.json'
    registry={x['id']:x for x in json.loads(cfg.read_text(encoding='utf8'))} if cfg.exists() else {}
    eligible_ids={k for k,v in registry.items() if v.get('mode')=='public_document'}
    for health in root.glob('sources/*/health.json'):
        if health.parent.name not in eligible_ids:
            continue
        stats['sources']+=1; h=json.loads(health.read_text(encoding='utf8')); stats['active']+=h.get('state')=='ACTIVE'
        latest=health.parent/'latest.json'
        if not latest.exists(): stats['errors'].append(str(latest)); continue
        m=json.loads(latest.read_text(encoding='utf8'))
        required=['source_name','source_organization','source_type','source_level','source_url','fetched_at','raw_content_hash','license_or_permission']
        stats['metadata_complete']+=all(m.get(x) for x in required)
    for f in root.glob('candidates/*.jsonl'):
        if f.stem.replace('-structured','') not in eligible_ids:
            continue
        stats['pending_candidates']+=sum(1 for line in f.open(encoding='utf8') if line.strip())
    if cfg.exists():
        for s in json.loads(cfg.read_text(encoding='utf8')):
            for k in ('id','source_name','source_type','source_level','mode'):
                if not s.get(k): stats['config_warnings'].append({'id':s.get('id','?'),'missing':k})
            if s.get('mode')=='discovery_required' and not s.get('license_or_permission'):
                stats['config_warnings'].append({'id':s.get('id','?'),'missing':'license_or_permission'})
    stats['active_rate']=stats['active']/stats['sources'] if stats['sources'] else 0
    stats['metadata_rate']=stats['metadata_complete']/stats['sources'] if stats['sources'] else 0
    print(json.dumps(stats,ensure_ascii=False,indent=2))
    raise SystemExit(0 if not stats['errors'] else 1)
if __name__=='__main__': main()
