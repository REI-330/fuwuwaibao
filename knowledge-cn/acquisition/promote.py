"""Prepare evidence with provenance. Review decisions are per record and snapshot.

三种记录状态，语义严格区分（**不要把三者混为一谈**）：

- ``PENDING``  —— 还没人审。``eligible_for_import=False``。默认状态。
- ``APPROVED`` —— 有人审过并通过：必须有 reviewer / reviewed_at / evidence_locator，
                 且审核时的 raw_content_hash 与当前快照一致（否则 ``Review snapshot mismatch`` 直接报错）。
- ``WAIVED``   —— **由业主显式豁免闸门**：没有人工审核记录，但把豁免本身写进数据
                 （``review_waiver`` 记下豁免人 / 时间 / 理由 / 政策），因此它可审计、可追责，
                 且永远不会被误认成「有人审过」。

为什么要有 ``WAIVED`` 而不是直接写 ``APPROVED``：把 1519 条没人看过的记录标成 APPROVED，
等于伪造审核记录 —— 那比闸门卡着更糟。``WAIVED`` 说的是实话：「这道门禁被授权跳过了」。

用法::

    # 正常审核路径（需要人填好的 reviews 文件）
    python -m acquisition.promote --candidates ... --metadata ... --output ... --reviews ...

    # 业主豁免闸门（必须写清是谁豁免的）
    python -m acquisition.promote --candidates ... --metadata ... --output ... \
        --waive-review --waived-by "业主" --waive-reason "交付前由业主拍板跳过人工逐条审核"
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def prepare(records, metadata, reviews, waiver=None):
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
        elif waiver:
            # 真审核优先：只有没人审过的记录才会被豁免覆盖
            record['review_status'] = 'WAIVED'
            record['eligible_for_import'] = True
            record['review_waiver'] = waiver
        result.append(record)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidates', 'metadata', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--reviews')
    parser.add_argument('--waive-review', action='store_true', help='业主豁免审核闸门；必须在 --waived-by 里写清是谁拍板的')
    parser.add_argument('--waived-by', help='豁免人（--waive-review 时必填）')
    parser.add_argument('--waive-reason', default='交付前由业主拍板跳过人工逐条审核', help='豁免理由')
    args = parser.parse_args()

    waiver = None
    if args.waive_review:
        if not args.waived_by:
            raise SystemExit('--waive-review 必须同时给 --waived-by（豁免要有人负责）')
        waiver = {
            'by': args.waived_by,
            'at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'reason': args.waive_reason,
            'policy': 'review-gate-waived',
            'note': '本条没有人工审核记录，eligible_for_import=true 来自业主显式豁免，不是 APPROVED。',
        }

    records = [json.loads(l) for l in Path(args.candidates).read_text(encoding='utf-8').splitlines() if l.strip()]
    metadata = json.loads(Path(args.metadata).read_text(encoding='utf-8'))
    reviews = json.loads(Path(args.reviews).read_text(encoding='utf-8')) if args.reviews else []
    results = prepare(records, metadata, reviews, waiver=waiver)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in results), encoding='utf-8')
    os.replace(temporary, target)
    print(json.dumps({
        'records': len(results),
        'approved': sum(r['review_status'] == 'APPROVED' for r in results),
        'waived': sum(r['review_status'] == 'WAIVED' for r in results),
        'pending': sum(r['review_status'] == 'PENDING' for r in results),
        'eligible_for_import': sum(r['eligible_for_import'] for r in results),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
