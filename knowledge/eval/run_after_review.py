#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
审核通过之后的完整链路编排：门禁 → 导入 → 从 WeKnora 取上下文 → deepeval → 汇总。

设计原则：**每一道门禁都要真的能拦住**。当前中国官方语料是 0/1519 已审核，
所以这个脚本现在就应该拒绝执行导入，而不是"悄悄跑一遍什么都不做还报成功"。

链路（对应仓库已有资产）：
  1. 审核门禁   knowledge-cn/data/reviewed/*.jsonl 里的 eligible_for_import 计数
                依据 acquisition/promote.py：必须 reviewer + reviewed_at + evidence_locator 齐备，
                且 raw_content_hash 与快照一致，才置 eligible_for_import=true
  2. 运行态门禁 WeKnora /health 200 + /api/v1/models 非空（acquisition/check_weknora.py 同款判据）
  3. 导入       acquisition/import_weknora.py（只导入 APPROVED 且 eligible 的记录）
  4. 取上下文   knowledge/eval/weknora_retrieval.py（按题从 WeKnora hybrid-search 取 top-k）
  5. 评测       knowledge/eval/deepeval_rag_eval.py --levels WEKNORA
  6. 汇总       knowledge/eval/summarize_deepeval.py

用法：
  python knowledge/eval/run_after_review.py                 # 只做门禁检查（默认 dry-run）
  python knowledge/eval/run_after_review.py --apply         # 真正执行导入与评测
  python knowledge/eval/run_after_review.py --kb <kb-id>    # 指定知识库
  python knowledge/eval/run_after_review.py --questions <path>  # 指定题集（新语料要重新出题）
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REVIEWED = os.path.join(ROOT, "knowledge-cn", "data", "reviewed", "*.jsonl")
RUNTIME = os.path.join(ROOT, "knowledge-cn", "data", "reviewed")  # 仅用于报错提示
EVAL = os.path.join(ROOT, "knowledge", "eval")
PY = sys.executable


def step(n, title):
    print(f"\n{'='*66}\n[{n}] {title}\n{'='*66}")


def gate_review():
    """审核门禁：统计已批准且可导入的记录数。

    分开报 APPROVED 与 WAIVED，**不把两者合并成一个「已审」数字**：
    APPROVED 是有人审过，WAIVED 是业主豁免了闸门，混报会让人以为数据经过人工审核。
    """
    files = sorted(glob.glob(REVIEWED))
    if not files:
        print(f"  未找到审核产物：{REVIEWED}")
        return 0, 0
    total = approved = eligible = waived = 0
    for p in files:
        n = 0
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                total += 1
                n += 1
                if d.get("review_status") == "APPROVED":
                    approved += 1
                if d.get("review_status") == "WAIVED":
                    waived += 1
                if d.get("eligible_for_import"):
                    eligible += 1
        print(f"  {os.path.basename(p)}: {n} 条")
    print(f"  → 总计 {total} 条候选，APPROVED {approved}，WAIVED（业主豁免） {waived}，eligible_for_import {eligible}")
    return total, eligible


def gate_runtime(base):
    """运行态门禁：服务存活 + 至少有一个模型（无模型则解析不会产生 chunk）。"""
    try:
        with urllib.request.urlopen(base + "/health", timeout=10) as r:
            ok = r.status == 200
            status_code = r.status
        print(f"  /health: HTTP {status_code}")
    except Exception as e:
        print(f"  /health 失败：{type(e).__name__}: {e}")
        return False
    # /api/v1/models 挂在 Viewer() 后面：**不带 token 会返回 200 + 空数组**，
    # 看起来像「一个模型都没配」。所以这里必须真的登录，否则门禁会把
    # 「已配好向量模型」误判成「没配」。
    try:
        token = _login(base)
        request = urllib.request.Request(base + "/api/v1/models")
        request.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(request, timeout=10) as r:
            body = json.loads(r.read().decode() or "{}")
        models = body.get("data") or []
        embeddings = [m for m in models if str(m.get("type", "")).lower() == "embedding"]
        print(f"  /api/v1/models: {len(models)} 个模型，其中向量模型 {len(embeddings)} 个"
              + (f"（{embeddings[0].get('id')}）" if embeddings else ""))
        if not embeddings:
            print("  ✗ 没有向量模型：导入后解析会停在 processing，门禁不通过")
            return False
        return ok
    except Exception as e:
        print(f"  /api/v1/models 不可用：{type(e).__name__}: {e}")
        return False


def _login(base, email="agent.verify@local.test", password="verify12345"):
    """真实登录换 token（先试注册，已存在则忽略）。"""
    for payload in ({"email": email, "password": password, "username": email.split("@")[0]}, None):
        if payload is None:
            break
        try:
            request = urllib.request.Request(base + "/api/v1/auth/register",
                                             data=json.dumps(payload).encode("utf-8"),
                                             headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(request, timeout=10).read()
        except Exception:
            pass
    request = urllib.request.Request(base + "/api/v1/auth/login",
                                     data=json.dumps({"email": email, "password": password}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as r:
        return json.loads(r.read().decode() or "{}")["token"]


def run(cmd, cwd=None, dry=False):
    print("  $ " + " ".join(str(c) for c in cmd))
    if dry:
        print("    (dry-run，未执行)")
        return 0
    return subprocess.call(cmd, cwd=cwd or ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行导入与评测")
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--kb", help="知识库 ID")
    ap.add_argument("--api-key", help="WeKnora API Key（导入用；也可用 --email/--password）")
    ap.add_argument("--email", default="agent.verify@local.test")
    ap.add_argument("--password", default="verify12345")
    ap.add_argument("--questions", default=os.path.join(ROOT, "knowledge", "evaluations", "questions.json"))
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()
    dry = not args.apply

    step(1, "审核门禁（中国官方语料）")
    total, eligible = gate_review()
    if eligible == 0:
        print("\n  ✗ 门禁未通过：0 条记录获批，无可导入内容。")
        print("    这是预期状态——审核是人的判断，需按 knowledge-cn/data/reviews/*.template.json")
        print("    逐条填写 decision=APPROVED 且补齐 reviewer / reviewed_at / evidence_locator，")
        print("    再跑 acquisition/promote.py 生成 eligible_for_import=true 的记录。")
        print("\n  → 链路在此终止（未做任何写入）。")
        return 3
    print(f"\n  ✓ 门禁通过：{eligible} 条可导入")

    step(2, "运行态门禁（WeKnora）")
    if not gate_runtime(args.base_url):
        print("\n  ✗ WeKnora 未就绪（health 不通或无模型），终止。")
        return 4

    step(3, f"导入 WeKnora{'（dry-run）' if dry else ''}")
    for p in sorted(glob.glob(REVIEWED)):
        # import_weknora 走「上传真实 .md 文件」，不走 manual 接口：
        # manual 建的 knowledge file_type=manual，不在 docparser 的
        # simpleFormats（md/txt/csv/json）里 → 会被路由到 Python docreader；
        # 本机 docreader 没跑（50051 无监听），解析会永久停在 processing。
        cmd = [PY, os.path.join(ROOT, "knowledge-cn", "acquisition", "import_weknora.py"),
               "--input", p, "--base-url", args.base_url, "--kb-id", args.kb or "",
               "--email", args.email, "--password", args.password]
        if dry:
            cmd.append("--dry-run")
        run(cmd, cwd=os.path.join(ROOT, "knowledge-cn"), dry=False)

    step(4, "从 WeKnora 取检索上下文")
    run([PY, os.path.join(EVAL, "weknora_retrieval.py"), "--base-url", args.base_url,
         "--kb", args.kb or "", "--questions", args.questions,
         "--out", os.path.join(EVAL, "runs", "weknora-topk.json"),
         "--email", args.email, "--password", args.password], dry=dry)

    step(5, f"DeepEval 评测{'（跳过，dry-run）' if dry else ''}")
    run([PY, os.path.join(EVAL, "deepeval_rag_eval.py"), "--levels", "WEKNORA",
         "--workers", str(args.workers)], dry=dry)

    step(6, "汇总")
    run([PY, os.path.join(EVAL, "summarize_deepeval.py"),
         "--out", os.path.join(EVAL, "results", "summary.md")], dry=dry)

    print("\n完成。" + ("（dry-run，未产生写入）" if dry else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
