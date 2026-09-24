"""Prepare evidence with provenance. Review decisions are per record and snapshot."""
import argparse
import json
import os
from pathlib import Path


def prepare(records, metadata, reviews):
    source_id = metadata.get('id') or metadata.get('source_id')
    if not source_id or not metadata.get('raw_content_hash'):
        raise ValueError('Source identity and hash required')
    decisions = {r['record_id']: r for r in reviews}
    result = []
    for original in records:
        record = dict(original)
        record['source'] = dict(metadata, id=source_id, page=original.get('source', {}).get('page'))
        record['review_status'] = 'PENDING'
        record['record_kind'] = metadata['record_kind']
        record['eligible_for_import'] = False
        decision = decisions.get(record['id'])
        if decision:
            if decision.get('raw_content_hash') != metadata['raw_content_hash']:
                raise ValueError('Review snapshot mismatch')
            if not all(decision.get(k) for k in ('reviewer', 'reviewed_at', 'evidence_locator')):
                raise ValueError('Review requires reviewer, timestamp and locator')
            record['review'] = decision
            if decision.get('decision') == 'APPROVED':
                record['review_status'] = 'APPROVED'
                record['eligible_for_import'] = True
        result.append(record)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidates', 'metadata', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--reviews')
    args = parser.parse_args()
    records = [json.loads(l) for l in Path(args.candidates).read_text(encoding='utf-8').splitlines() if l.strip()]
    metadata = json.loads(Path(args.metadata).read_text(encoding='utf-8'))
    reviews = json.loads(Path(args.reviews).read_text(encoding='utf-8')) if args.reviews else []
    results = prepare(records, metadata, reviews)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in results), encoding='utf-8')
    os.replace(temporary, target)
    print(json.dumps({'records': len(results), 'approved': sum(r['eligible_for_import'] for r in results)}))


if __name__ == '__main__':
    main()
