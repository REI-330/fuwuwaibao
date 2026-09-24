#!/usr/bin/env node
/**
 * 步骤 08：人工审核 Wiki（生成/应用 review/wiki-review.json）
 *
 * 与第 06 步同构：**脚本不代签**。
 *   - 第一次运行：算出「全审 / 抽审」范围，写出 review/wiki-review.json 骨架，然后失败退出；
 *   - 人工填好 reviewer / reviewedAt / 两组 verdict / 驳回理由后再运行，才回写 wiki/*.md 与 wiki/index.json。
 *
 * 审核分级直接抄设计文档 §4.2 的表，不另立标准：
 *   全审（会被推荐理由、技能差距、任务直接引用的实体：职业页 + 被 requires 指到的技能页）→ review=reviewed
 *   抽审（其余实体：本图谱里是 9 个 knowledge 学习单元页）→ 抽查 ≥20%，通过也保持 review=llm-draft
 *   被驳回的页面保持 llm-draft，并把人工理由写进页面正文的「审核意见（人工）」小节 —— 驳回记录本身就是材料 ②。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import { ADJUDICATION_FILE, WIKI_DIR, WIKI_INDEX_FILE, WIKI_REVIEW_FILE, relPath } from "./lib/paths.mjs";
import { fail, logLine, nowIso, readJson, recordStep, writeJson } from "./lib/log.mjs";
import { describeCounts, loadAccepted } from "./lib/graph.mjs";

const STEP = "08";
const TITLE = "人工审核 Wiki";
const COMMAND = "node knowledge/pipeline/08-review-wiki.mjs";
const SCHEMA = "career-graph-wiki-review/v1";
const DRAFT = "llm-draft";
const REVIEWED = "reviewed";
const REJECTION_HEADING = "## 审核意见（人工）";

/** §4.2：抽查比例 ≥20%，下限 6 条（样本量太小的话抽查本身就失去意义）。 */
const SPOT_RATE = 0.25;
const SPOT_MIN = 6;

const startedAt = nowIso();
const { nodes, edges } = loadAccepted();

if (!existsSync(WIKI_INDEX_FILE)) {
  fail(`缺少 ${relPath(WIKI_INDEX_FILE)}，请先跑 07-compile-wiki.mjs`);
  process.exit(1);
}
const index = readJson(WIKI_INDEX_FILE);
const pages = index.pages ?? [];
if (pages.length === 0) {
  fail(`${relPath(WIKI_INDEX_FILE)} 里没有任何页面，请先跑 07-compile-wiki.mjs`);
  process.exit(1);
}

// ---------- 1) 机器算范围（与 06 同一口径，避免两边各算一套） ----------

const requiredSkillIds = new Set(edges.filter((edge) => edge.type === "requires").map((edge) => edge.to));
const fullTier = pages.filter(
  (page) => page.entityType === "occupation" || (page.entityType === "skill" && requiredSkillIds.has(page.entityId)),
);
const fullIds = new Set(fullTier.map((page) => page.entityId));
const restPages = pages.filter((page) => !fullIds.has(page.entityId)).sort((a, b) => a.entityId.localeCompare(b.entityId));

/** 确定性等距抽样：同输入同样本，报告里可以复算。 */
const spotSize = Math.max(SPOT_MIN, Math.ceil(restPages.length * SPOT_RATE));
const spotSample = [];
for (let i = 0; i < Math.min(spotSize, restPages.length); i += 1) {
  spotSample.push(restPages[Math.floor((i * restPages.length) / Math.min(spotSize, restPages.length))]);
}

// ---------- 2) 人工文件骨架 ----------

function skeleton() {
  return {
    schema: SCHEMA,
    note: "人工审核文件。reviewer / reviewedAt / 两组 verdict / 驳回理由必须由人填写；填完重跑 08-review-wiki.mjs 才会回写 wiki/*.md。",
    reviewer: null,
    reviewedAt: null,
    howToFill: {
      reviewer: "审核人标识（与 06-adjudicate.mjs 用同一个人的话更好追溯）",
      reviewedAt: "审核日期 YYYY-MM-DD",
      "fullReview.verdict": `accept 全部通过 / reject 全部不通过；范围 ${fullTier.length} 页（occupation + requires 指到的 skill）`,
      "spotCheck.reviewed": `必须等于抽样条数 ${spotSample.length}`,
      "spotCheck.findings": "抽查发现的问题，没有问题写 []；有就写清楚哪页错在哪",
      rejections: "被驳回的页面：{ entityId, reason }，驳回理由会写进该页正文的「审核意见（人工）」小节",
    },
    fullReview: { verdict: null, reason: null, pages: fullTier.length },
    spotCheck: { verdict: null, reason: null, sampleSize: spotSample.length, reviewed: null, findings: null },
    rejections: [],
    operationLog: [],
  };
}

if (!existsSync(WIKI_REVIEW_FILE)) {
  mkdirSync(dirname(WIKI_REVIEW_FILE), { recursive: true });
  writeJson(WIKI_REVIEW_FILE, skeleton());
  recordStep({
    step: STEP,
    title: TITLE,
    command: COMMAND,
    inputs: [WIKI_INDEX_FILE],
    outputs: [WIKI_REVIEW_FILE],
    counts: { pages: pages.length, fullReviewPages: fullTier.length, spotCheckSample: spotSample.length },
    notes: [`生成了人工审核骨架，尚未回写任何 wiki 页（文件刚生成，还没人填）。`],
    startedAt,
  });
  logLine(`${TITLE}：已生成 ${relPath(WIKI_REVIEW_FILE)} 骨架（全审 ${fullTier.length} 页 / 抽审样本 ${spotSample.length} 条）`);
  logLine("  文件刚生成，还没人填 —— 填完 reviewer / reviewedAt / verdict 后重跑本步才会回写 wiki/*.md。");
  process.exitCode = 1;
} else {
  const review = readJson(WIKI_REVIEW_FILE);
  const problems = [];

  if (typeof review.reviewer !== "string" || review.reviewer.trim() === "") problems.push("reviewer 为空");
  if (typeof review.reviewedAt !== "string" || !/^\d{4}-\d{2}-\d{2}/.test(review.reviewedAt)) {
    problems.push("reviewedAt 为空或不是 YYYY-MM-DD");
  }
  const full = review.fullReview ?? {};
  if (!["accept", "reject"].includes(full.verdict)) problems.push("fullReview.verdict 必须是 accept 或 reject");
  if (typeof full.reason !== "string" || full.reason.trim() === "") problems.push("fullReview.reason 为空");
  const spot = review.spotCheck ?? {};
  if (!["accept", "reject"].includes(spot.verdict)) problems.push("spotCheck.verdict 必须是 accept 或 reject");
  if (typeof spot.reason !== "string" || spot.reason.trim() === "") problems.push("spotCheck.reason 为空");
  if (spot.reviewed !== spotSample.length) {
    problems.push(`spotCheck.reviewed=${JSON.stringify(spot.reviewed)}，必须等于抽样条数 ${spotSample.length}`);
  }
  if (!Array.isArray(spot.findings)) problems.push("spotCheck.findings 必须是数组（没有发现就写 []）");
  if (!Array.isArray(review.rejections)) problems.push("rejections 必须是数组（没有驳回就写 []）");

  const pageIds = new Set(pages.map((page) => page.entityId));
  const rejections = [];
  for (const item of Array.isArray(review.rejections) ? review.rejections : []) {
    if (!pageIds.has(item?.entityId)) {
      problems.push(`rejections 里的 entityId「${item?.entityId}」不在 Wiki 页目录中`);
      continue;
    }
    if (typeof item.reason !== "string" || item.reason.trim() === "") {
      problems.push(`rejections「${item.entityId}」缺理由 —— 驳回理由必须写清楚哪一句违反了 §4.3`);
      continue;
    }
    rejections.push({ entityId: item.entityId, reason: item.reason.trim() });
  }

  if (problems.length > 0) {
    fail(`${relPath(WIKI_REVIEW_FILE)} 还不能用来审核（${problems.length} 项）：`);
    for (const problem of problems) process.stderr.write(`  - ${problem}\n`);
    recordStep({
      step: STEP,
      title: TITLE,
      command: COMMAND,
      inputs: [WIKI_INDEX_FILE, WIKI_REVIEW_FILE],
      outputs: [],
      counts: { pages: pages.length, fullReviewPages: fullTier.length, spotCheckSample: spotSample.length, problems: problems.length },
      notes: [`人工文件未填全（${problems.length} 项），按要求**没有**回写 wiki/*.md —— 08 不许自己签字。`],
      startedAt,
    });
    process.exit(1);
  }

  const rejectedIds = new Set(rejections.map((item) => item.entityId));
  const updated = [];
  for (const page of pages) {
    const isFull = fullIds.has(page.entityId);
    const isSpotSample = spotSample.some((item) => item.entityId === page.entityId);
    const rejected = rejectedIds.has(page.entityId);
    /** 只有「全审 且 人工全审通过 且 没被单独驳回」的页面才升 reviewed；抽审页按 §4.2 保持 llm-draft。 */
    const promoted = isFull && full.verdict === "accept" && !rejected;
    const next = {
      ...page,
      review: promoted ? REVIEWED : DRAFT,
      reviewedBy: promoted ? review.reviewer : null,
      version: promoted ? review.reviewedAt : page.version,
      tier: isFull ? "full" : "spot",
      spotChecked: isSpotSample,
      rejection: rejected ? rejections.find((item) => item.entityId === page.entityId).reason : null,
    };
    updated.push(next);

    const file = resolve(WIKI_DIR, page.path.slice("wiki/".length));
    const raw = readFileSync(file, "utf8");
    const body = stripRejection(raw.slice(raw.indexOf("\n---", 4) + 4)).replace(/^\n+/, "");
    const front = [
      "---",
      `entityId: ${next.entityId}`,
      `entityType: ${next.entityType}`,
      `title: ${scalar(next.title)}`,
      `aliases: [${(next.aliases ?? []).map((alias) => JSON.stringify(alias)).join(", ")}]`,
      `version: ${next.version}`,
      `review: ${next.review}`,
      `reviewedBy: ${next.reviewedBy ?? "null"}`,
      `sources: [${(next.sources ?? []).map((ref) => JSON.stringify(ref)).join(", ")}]`,
      "---",
      "",
    ].join("\n");
    const rejectionBlock = rejected ? `\n\n${REJECTION_HEADING}\n\n${next.rejection}\n` : "";
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, `${front}${body.replace(/\s*$/, "")}${rejectionBlock}\n`, "utf8");
  }

  writeJson(WIKI_INDEX_FILE, {
    ...index,
    generatedAt: startedAt,
    reviewedAt: review.reviewedAt,
    reviewer: review.reviewer,
    note: `Wiki 页目录，已经过第 08 步人工审核（${review.reviewedAt}，${review.reviewer}）。`,
    counts: { ...index.counts, reviewed: updated.filter((page) => page.review === REVIEWED).length },
    pages: updated,
  });

  const byReview = {};
  for (const page of updated) byReview[page.review] = (byReview[page.review] ?? 0) + 1;
  const byTier = {};
  for (const page of updated) byTier[page.tier] = (byTier[page.tier] ?? 0) + 1;

  recordStep({
    step: STEP,
    title: TITLE,
    command: COMMAND,
    inputs: [WIKI_INDEX_FILE, WIKI_REVIEW_FILE],
    outputs: [WIKI_DIR, WIKI_INDEX_FILE],
    counts: {
      pages: updated.length,
      reviewed: updated.filter((page) => page.review === REVIEWED).length,
      draft: updated.filter((page) => page.review === DRAFT).length,
      rejected: rejections.length,
      spotChecked: spotSample.length,
      byTier,
    },
    notes: [
      `${review.reviewer} 于 ${review.reviewedAt} 完成 Wiki 审核：全审 ${full.verdict}，抽审 ${spot.verdict}（${spot.reviewed}/${spotSample.length}）。`,
      `review 分布：${describeCounts(byReview, [REVIEWED, DRAFT, "runtime"])}；分级：${describeCounts(byTier, ["full", "spot"])}。`,
      `驳回 ${rejections.length} 页${rejections.length > 0 ? `：${rejections.map((item) => item.entityId).join("、")}` : ""}（驳回理由已写进页面正文）。`,
      `已审核覆盖率 ${(updated.filter((page) => page.review === REVIEWED).length / updated.length * 100).toFixed(1)}%，其余保持 ${DRAFT} 并按 §5 检索降权。`,
    ],
    startedAt,
  });

  logLine(`${TITLE}：${updated.length} 页（${describeCounts(byReview, [REVIEWED, DRAFT])}）→ ${relPath(WIKI_DIR)}`);
  logLine(`  审核人 ${review.reviewer}（${review.reviewedAt}）；升为 ${REVIEWED} 的页面 ${updated.filter((page) => page.review === REVIEWED).length} 页`);
  logLine(`  抽审 ${spot.reviewed}/${spotSample.length}，驳回 ${rejections.length} 页`);
}

/** 与 07 同一套标量规则：含特殊符号的标题加双引号，保证 front-matter 仍是合法 YAML。 */
function scalar(value) {
  const text = String(value ?? "");
  return /^[A-Za-z0-9\u4e00-\u9fff._\- :]+$/.test(text) && !/:\s/.test(text) ? text : JSON.stringify(text);
}

/** 重跑时先把上一轮的「审核意见」小节摘掉，避免同一段理由被叠加多次。 */
function stripRejection(body) {
  const at = body.indexOf(REJECTION_HEADING);
  if (at < 0) return body;
  return body.slice(0, at).replace(/\s*$/, "");
}
