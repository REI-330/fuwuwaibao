#!/usr/bin/env node
/**
 * 13 来源日期标注（离线、幂等）
 *
 * 为什么单独一步，而不是塞回 01/02：
 *   - 01 只做登记，明确「不猜 publishedAt」；它的产物里 sha256/chunkCount 是 null 占位，
 *     重跑 01 会把 02/03 回填的值冲掉 —— 所以「只补日期」不能靠重跑 01。
 *   - 02 手上有网络抓取，但重跑 02 要重新访问 26 个站点；而快照早已入库（knowledge/raw/）。
 *   - 因此本步只读**已入库的原始快照**，把可核验的日期回填进 sources.json。全离线、可反复跑。
 *
 * 它补哪两个字段（刻意分开，不混为一谈）：
 *   - publishedAt / publishedAtSource：**页面发布时间**。只有 schema.org 的
 *     Article / TechArticle / NewsArticle / BlogPosting 这类"文章节点"上的 datePublished 才算数。
 *   - sourceUpdatedAt / sourceUpdatedAtSource：**页面最后更新时间**，取自同类节点的 dateModified。
 *     它与发布时间是两件事：拿修改时间冒充发布时间就是编。
 *   - 两者都没有 → 保持 null，不拿 collectedAt（我们什么时候抓的）顶上。
 *
 * 为什么不用正则直接抓 `"datePublished"`：
 *   快照里同一段字符串可能出现在别的结构里（例如页面嵌入的视频对象、FAQ 结构），
 *   正则抓到的日期不一定属于页面本体。所以这里**先解析 JSON-LD、再按 @type 收窄**。
 *   （本步开发时实测：S08 的 datePublished 一度被怀疑是内嵌视频的日期，正式解析后确认
 *     它挂在 @type=Article 节点上，才敢写进数据。）
 *
 * 已知事实（2026-09-30 跑出来的，供复核）：
 *   26 份快照里只有 5 份带日期信号 —— S08/S09/S10 有 datePublished（三份**完全相同**，
 *   疑似站点模板常量，故 publishedAtSource 里如实标注来源字段，便于人工复核）；
 *   S11/S12 只有 dateModified。其余 21 份快照没有任何页面级日期。
 *
 * 输入：knowledge/sources/sources.json（步骤 01/02 产出）+ knowledge/raw/<sourceId>.html
 * 输出：knowledge/sources/sources.json（原地回填，其余字段一字不动）
 * 日志：knowledge/evidence/pipeline-log.json（本步一条记录）
 *
 * 跑法（仓库根目录）：
 *   node knowledge/pipeline/13-annotate-source-dates.mjs           # 回填
 *   node knowledge/pipeline/13-annotate-source-dates.mjs --dry-run # 只看会改什么
 *
 * 冷重建顺序：01 → 02 → 03 … → **本步** → 09 → 10 → 11（本步必须在 11 之前，
 * 因为 11 会把 sources.json 投影进导出）。
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..", "..");
const SOURCES = resolve(ROOT, "knowledge", "sources", "sources.json");
const LOG = resolve(ROOT, "knowledge", "evidence", "pipeline-log.json");
const RAW = (sourceId) => resolve(ROOT, "knowledge", "raw", `${sourceId}.html`);
const STEP = "13";
const DRY_RUN = process.argv.includes("--dry-run");

/** 只有这些 schema.org 类型上的 datePublished 才算"页面发布时间"。 */
const ARTICLE_TYPES = new Set([
  "Article",
  "TechArticle",
  "NewsArticle",
  "BlogPosting",
  "ScholarlyArticle",
  "WebPage",
  "Report",
  "CreativeWork",
]);

/** 收窄到 YYYY-MM-DD：与 01 步的 DATE_RE 保持一致，不引入时区语义。 */
function toDateOnly(value) {
  if (typeof value !== "string") return null;
  const match = value.trim().match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : null;
}

function collectNodes(node, types, out) {
  if (Array.isArray(node)) {
    for (const item of node) collectNodes(item, types, out);
    return;
  }
  if (!node || typeof node !== "object") return;
  const type = node["@type"];
  const typeList = Array.isArray(type) ? type : type === undefined ? [] : [type];
  if (typeList.some((item) => types.has(String(item)))) out.push({ type: typeList.join("/"), node });
  if (Array.isArray(node["@graph"])) collectNodes(node["@graph"], types, out);
  for (const value of Object.values(node)) {
    if (value && typeof value === "object") collectNodes(value, types, out);
  }
}

/** 从 HTML 里抽页面级日期。先解析 JSON-LD，再按 @type 收窄，绝不正则抓裸日期。 */
export function extractPageDates(html) {
  const blocks = [...html.matchAll(/<script[^>]+application\/ld\+json[^>]*>([\s\S]*?)<\/script>/gi)];
  const found = [];
  for (const block of blocks) {
    let payload;
    try {
      payload = JSON.parse(block[1].trim());
    } catch {
      continue; // 站点的 JSON-LD 语法错误不该让整步失败，跳过即可
    }
    const nodes = [];
    collectNodes(payload, ARTICLE_TYPES, nodes);
    found.push(...nodes);
  }
  let publishedAt = null;
  let publishedAtSource = null;
  let sourceUpdatedAt = null;
  let sourceUpdatedAtSource = null;
  for (const { type, node } of found) {
    if (!publishedAt) {
      const value = toDateOnly(node.datePublished);
      if (value) {
        publishedAt = value;
        publishedAtSource = `schema.org ${type}.datePublished`;
      }
    }
    if (!sourceUpdatedAt) {
      const value = toDateOnly(node.dateModified);
      if (value) {
        sourceUpdatedAt = value;
        sourceUpdatedAtSource = `schema.org ${type}.dateModified`;
      }
    }
  }
  const articleNodes = found.length;
  return { publishedAt, publishedAtSource, sourceUpdatedAt, sourceUpdatedAtSource, articleNodes };
}

function main() {
  const registry = JSON.parse(readFileSync(SOURCES, "utf8"));
  const sources = registry.sources ?? [];
  const changed = [];
  const noSignal = [];
  const publishedGroups = new Map();

  for (const source of sources) {
    const file = RAW(source.sourceId);
    if (!existsSync(file)) {
      noSignal.push(`${source.sourceId}(无快照)`);
      continue;
    }
    const dates = extractPageDates(readFileSync(file, "utf8"));
    const before = JSON.stringify([
      source.publishedAt ?? null,
      source.publishedAtSource ?? null,
      source.sourceUpdatedAt ?? null,
      source.sourceUpdatedAtSource ?? null,
    ]);
    // 只在解析出真实值时写入；解析不到就保持上游的值（可能是 12 步登记稿里人工填的）
    if (dates.publishedAt) {
      source.publishedAt = dates.publishedAt;
      source.publishedAtSource = dates.publishedAtSource;
    }
    if (dates.sourceUpdatedAt) {
      source.sourceUpdatedAt = dates.sourceUpdatedAt;
      source.sourceUpdatedAtSource = dates.sourceUpdatedAtSource;
    }
    // 解析不到的字段显式写 null，保证 26 条**形状一致**（否则导出里只有少数几条多出键，
    // 消费方要么漏判 undefined、要么以为字段不存在）。
    source.publishedAt ??= null;
    source.publishedAtSource ??= null;
    source.sourceUpdatedAt ??= null;
    source.sourceUpdatedAtSource ??= null;
    const after = JSON.stringify([
      source.publishedAt ?? null,
      source.publishedAtSource ?? null,
      source.sourceUpdatedAt ?? null,
      source.sourceUpdatedAtSource ?? null,
    ]);
    if (before !== after) changed.push(source.sourceId);
    if (!dates.publishedAt && !dates.sourceUpdatedAt) noSignal.push(source.sourceId);
    if (dates.publishedAt) {
      const key = dates.publishedAt;
      publishedGroups.set(key, [...(publishedGroups.get(key) ?? []), source.sourceId]);
    }
  }

  const withPublished = sources.filter((source) => source.publishedAt).length;
  const withUpdated = sources.filter((source) => source.sourceUpdatedAt).length;
  // 多份来源共享同一个 publishedAt 时点名，提示可能来自站点模板而非逐页真实发布日
  const shared = [...publishedGroups.entries()].filter(([, ids]) => ids.length > 1);

  const counts = {
    sources: sources.length,
    annotated: changed.length,
    withPublishedAt: withPublished,
    withSourceUpdatedAt: withUpdated,
    withoutAnyDate: noSignal.length,
  };
  const notes = [
    `publishedAt 补齐 ${withPublished}/${sources.length}，sourceUpdatedAt 补齐 ${withUpdated}/${sources.length}；两者都没有的 ${noSignal.length} 条保持 null，不拿 collectedAt 顶上。`,
    `改写 ${changed.length} 条：${changed.join("、") || "（无）"}。`,
  ];
  if (shared.length > 0) {
    notes.push(
      `注意：以下来源共享同一 publishedAt，可能是站点模板常量而非逐页真实发布日，` +
        shared.map(([date, ids]) => `${date} → ${ids.join("/")}`).join("；") +
        `（数据里已用 publishedAtSource 标出取值字段，供人工复核）。`,
    );
  }
  notes.push(`无日期信号的来源 ${noSignal.length} 条：${noSignal.join("、") || "（无）"}。`);

  console.log(`[kb] 13 来源日期标注${DRY_RUN ? "（dry-run）" : ""}`);
  console.log(`[kb]   publishedAt ${withPublished}/${sources.length} · sourceUpdatedAt ${withUpdated}/${sources.length}`);
  for (const note of notes) console.log(`[kb]   ${note}`);
  if (DRY_RUN) return;

  writeFileSync(SOURCES, `${JSON.stringify(registry, null, 2)}\n`);

  const log = existsSync(LOG) ? JSON.parse(readFileSync(LOG, "utf8")) : { steps: [] };
  const steps = (log.steps ?? []).filter((entry) => entry.step !== STEP);
  steps.push({
    step: STEP,
    name: "annotate-source-dates",
    ranAt: new Date().toISOString(),
    counts,
    notes,
  });
  writeFileSync(LOG, `${JSON.stringify({ ...log, steps }, null, 2)}\n`);
}

main();
