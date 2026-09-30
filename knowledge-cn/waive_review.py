"""业主豁免审核闸门：把 5 份候选一次过成 `WAIVED`（不伪造人工审核）。

背景（2026-09-30）：中国官方语料采集完成后，`data/reviewed/*.jsonl` 的
`eligible_for_import` 一直是 0 —— 因为 `promote.py` 要求每条记录都有
reviewer / reviewed_at / evidence_locator 齐备的人工审核记录，且审核时的
`raw_content_hash` 必须与快照一致。

业主拍板：**跳过人工逐条审核**。做法不是把 1519 条写成 `APPROVED`（那是伪造审核记录），
而是写成 `review_status='WAIVED'` + `review_waiver{by, at, reason, policy}`：
数据里明明白白写着「这条没有人工审核，是业主授权跳过的」，可审计、可追责。

为什么用脚本而不是命令行参数：Windows + Git Bash 会用 GBK 发送非 ASCII 参数
（本项目已有此坑的记录），豁免人/理由里的中文会在传到 Python 之前就被破坏。
脚本文件本身是 UTF-8，中文安全。

跑法（仓库根目录）：
    python knowledge-cn/waive_review.py --waived-by 业主 --dry-run
    python knowledge-cn/waive_review.py --waived-by 业主
"""
import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from acquisition.promote import prepare  # noqa: E402

# 候选文件 → 来源 id（metadata 目录名）
PAIRS = [
    ("moe-majors-2026-structured.jsonl", "moe-majors-2026"),
    ("moe-majors-2026.jsonl", "moe-majors-2026"),
    ("nbs-wages-2025.jsonl", "nbs-wages-2025"),
    ("occupation-2022.jsonl", "occupation-2022"),
    ("occupation-2022-replacement.jsonl", "occupation-2022-replacement"),
]


def metadata_for(source_id):
    matches = sorted(glob.glob(str(ROOT / "data" / "sources" / source_id / "*.metadata.json")))
    if not matches:
        raise SystemExit(f"找不到 {source_id} 的 metadata（需要在 data/sources/{source_id}/ 下有 *.metadata.json）")
    return json.loads(Path(matches[0]).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--waived-by", required=True, help="豁免人（写清是谁拍板的）")
    parser.add_argument("--waive-reason", default="交付前由业主拍板跳过人工逐条审核")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from datetime import datetime, timezone

    waiver = {
        "by": args.waived_by,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": args.waive_reason,
        "policy": "review-gate-waived",
        "note": "本条没有人工审核记录，eligible_for_import=true 来自业主显式豁免，不是 APPROVED。",
    }

    out_dir = ROOT / "data" / "reviewed"
    out_dir.mkdir(parents=True, exist_ok=True)
    totals = {"records": 0, "waived": 0, "approved": 0, "eligible_for_import": 0}

    for name, source_id in PAIRS:
        candidates = ROOT / "data" / "candidates" / name
        if not candidates.exists():
            raise SystemExit(f"找不到候选文件：{candidates}")
        records = [json.loads(line) for line in candidates.read_text(encoding="utf-8").splitlines() if line.strip()]
        metadata = metadata_for(source_id)
        results = prepare(records, metadata, [], waiver=waiver)

        waived = sum(r["review_status"] == "WAIVED" for r in results)
        approved = sum(r["review_status"] == "APPROVED" for r in results)
        eligible = sum(r["eligible_for_import"] for r in results)
        totals["records"] += len(results)
        totals["waived"] += waived
        totals["approved"] += approved
        totals["eligible_for_import"] += eligible
        print(f"  {name}: {len(results)} 条 → WAIVED {waived} / APPROVED {approved} / eligible {eligible}")

        if not args.dry_run:
            target = out_dir / name
            target.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results),
                encoding="utf-8",
            )

    print(json.dumps({**totals, "dry_run": args.dry_run}, ensure_ascii=False))
    if args.dry_run:
        print("（dry-run：没有写盘）")


if __name__ == "__main__":
    main()
