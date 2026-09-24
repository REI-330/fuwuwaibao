#!/usr/bin/env node
/**
 * 图谱数据层（步骤 05–11 共用）
 *
 * 只做三件事，不含任何业务结论：
 *   1) 读 04/05/06 的产物（candidates / normalized / accepted）并拆成 nodes + edges；
 *   2) 术语归一的规范化函数（别名词典的 key 必须唯一口径，否则消歧无从谈起）；
 *   3) 确定性的排序与零依赖 BM25 倒排 —— 第 10 步建索引、第 11 步评测都靠它。
 *
 * 画布常量刻意与前端 `lib/client/graph-view.ts` 对齐（NODE_HALF_WIDTH / NODE_HALF_HEIGHT /
 * NODE_GAP / DEFAULT_CANVAS）。第 10 步算出的 x/y 要直接喂给那份视图模型，两边各写一套常量
 * 迟早会漂移，所以这里只是把前端已有的数值抄成可 import 的常量，并在注释里点明出处。
 */
import { existsSync } from "node:fs";

import { readJson, readJsonl } from "./log.mjs";
import { ACCEPTED_FILE, ALIASES_FILE, CANDIDATES_FILE, NORMALIZED_FILE, TAXONOMY_FILE, relPath } from "./paths.mjs";

/** 节点类型顺序 = taxonomy.nodeKinds 的顺序（v2 起由词表派生，不再手抄）。 */
export const TAXONOMY = (() => {
  if (!existsSync(TAXONOMY_FILE)) throw new Error(`缺少 ${relPath(TAXONOMY_FILE)}：它是 kind / 边类型 / 边附加字段的唯一来源，步骤 05–11 都要用，不能省`);
  return readJson(TAXONOMY_FILE);
})();

export const KIND_ORDER = TAXONOMY.nodeKinds.map((spec) => spec.id);

/** 边类型顺序 = taxonomy.edgeTypes 的顺序。 */
export const EDGE_ORDER = TAXONOMY.edgeTypes.map((spec) => spec.id);

/** §3.2 L131-140：各边类型自己的附加字段。v1 时这里与词表各写一份，加类型必抄错一处。 */
export const EDGE_TYPE_FIELDS = Object.fromEntries(TAXONOMY.edgeTypes.map((spec) => [spec.id, spec.fields ?? []]));

/** 出处：frontend/frotent/frontend1/lib/client/graph-view.ts L24–L36。 */
export const NODE_HALF_WIDTH = 105;
export const NODE_HALF_HEIGHT = 29;
export const NODE_GAP = 8;
export const DEFAULT_CANVAS = { width: 1050, height: 820, padding: 24 };

/** 节点槽位尺寸：与 graph-view.ts 的 NODE_HALF_* 同源，第 10 步的网格布局直接用它。 */
export const COLUMN_PITCH = 2 * NODE_HALF_WIDTH + NODE_GAP;
export const ROW_PITCH = 2 * NODE_HALF_HEIGHT + NODE_GAP;

/** `chunk:` 前缀。设计文档 §3.2 L144：`evidenced_by` 的 to 是 chunk 引用，不是节点。 */
export const CHUNK_PREFIX = "chunk:";

/**
 * `weight` 的取值规则（不是拍脑袋定的数字）。
 *
 * 设计文档 §3.2 把 `weight` 列在边结构里，§9.4 又明令「`weight` 缺失时不要用线宽表示重要度」。
 * 抽取层只在 `requires` 边上给出了量化字段 `importance`（0–1）——那本来就是「这项技能对这个岗位
 * 有多重要」的同一个数字。所以：
 *
 *   requires → weight = importance（同源，不是新编的）
 *   其余边类型 → 不写 weight（宁缺不造）
 *
 * 覆盖率写进第 11 步的导出说明与效果报告，前端对 `weight === undefined` 的边不做线宽映射。
 */
export const WEIGHT_RULE = "requires.weight = importance（同一份数字）；其余边类型没有量化来源，不写 weight。";

export function edgeWeight(edge) {
  if (!edge) return null;
  if (edge.type === "requires" && typeof edge.importance === "number") {
    return Math.round(edge.importance * 1000) / 1000;
  }
  return null;
}

/** `type|from|to`：跨步骤稳定的边主键（第 05 步写进归一化行，09/10/11 直接复用）。 */
export function edgeKey(edge) {
  return edge.edgeKey ?? `${edge.type}|${edge.from}|${edge.to}`;
}

export function indexById(rows) {
  const map = new Map();
  for (const row of rows) map.set(row.id, row);
  return map;
}

/**
 * 节点 id → 落在它身上的边（双向都算，`chunk:` 目标不算端点）。
 * Wiki 编译与完整性校验都要「这个实体在图谱里有哪些边」，两边必须是同一口径。
 */
export function incidentEdges(edges) {
  const map = new Map();
  const push = (id, edge) => {
    if (!map.has(id)) map.set(id, []);
    map.get(id).push(edge);
  };
  for (const edge of edges) {
    if (isChunkTarget(edge.to)) {
      push(edge.from, edge);
      continue;
    }
    push(edge.from, edge);
    push(edge.to, edge);
  }
  for (const list of map.values()) list.sort((a, b) => edgeKey(a).localeCompare(edgeKey(b)));
  return map;
}

/** 节点 id → out 边 / in 边，两个方向分开（Wiki 的「先修提示」要看方向）。 */
export function directedEdges(edges) {
  const out = new Map();
  const inbound = new Map();
  const push = (map, id, edge) => {
    if (!map.has(id)) map.set(id, []);
    map.get(id).push(edge);
  };
  for (const edge of edges) {
    push(out, edge.from, edge);
    if (!isChunkTarget(edge.to)) push(inbound, edge.to, edge);
  }
  for (const map of [out, inbound]) {
    for (const list of map.values()) list.sort((a, b) => edgeKey(a).localeCompare(edgeKey(b)));
  }
  return { out, in: inbound };
}

export function groupBy(rows, keyOf) {
  const map = new Map();
  for (const row of rows) {
    const key = keyOf(row);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(row);
  }
  return map;
}

/** `skill:SK101` → `SK101`；`knowledge:AI001:stage1` → `AI001:stage1`。 */
export function shortRef(id) {
  const at = String(id).indexOf(":");
  return at >= 0 ? String(id).slice(at + 1) : String(id);
}

export function isChunkTarget(id) {
  return String(id).startsWith("chunk:");
}

export function chunkIdOf(id) {
  return isChunkTarget(id) ? String(id).slice("chunk:".length) : null;
}

export function splitRows(rows) {
  return {
    nodes: rows.filter((row) => row.recordType === "node"),
    edges: rows.filter((row) => row.recordType === "edge"),
  };
}

export function readRows(file) {
  if (!existsSync(file)) return null;
  return splitRows(readJsonl(file));
}

export function loadCandidates() {
  if (!existsSync(CANDIDATES_FILE)) {
    throw new Error(`缺少 ${relPath(CANDIDATES_FILE)}，请先跑 04-extract-candidates.mjs`);
  }
  return splitRows(readJsonl(CANDIDATES_FILE));
}

export function loadNormalized() {
  if (!existsSync(NORMALIZED_FILE)) {
    throw new Error(`缺少 ${relPath(NORMALIZED_FILE)}，请先跑 05-normalize.mjs`);
  }
  return splitRows(readJsonl(NORMALIZED_FILE));
}

/**
 * 读 06 的产物 `extract/accepted.jsonl`。
 * 允许用环境变量 `CAREER_GRAPH_ACCEPTED` 覆盖路径：第 09 步的硬约束失败路径要有办法被真实演练
 * （塞一份故意造错的图谱进去，脚本必须退出码 1），否则「会拦」只是口头承诺。
 */
export function loadAccepted() {
  const file = process.env.CAREER_GRAPH_ACCEPTED || ACCEPTED_FILE;
  if (!existsSync(file)) {
    throw new Error(`缺少 ${relPath(file)}，请先跑 06-adjudicate.mjs`);
  }
  return splitRows(readJsonl(file));
}

export function loadAliases() {
  if (!existsSync(ALIASES_FILE)) {
    throw new Error(`缺少 ${relPath(ALIASES_FILE)}，请先跑 05-normalize.mjs`);
  }
  return readJson(ALIASES_FILE);
}

/**
 * 别名归一 key。中文下标点符号不参与比对：`MCU 开发` / `MCU开发` / `ＭＣＵ开发` 必须命中同一条。
 * 之所以不做繁简转换或拼音，是因为那会引入外部数据；这里只保证「大小写 + 空白 + 全角」三件事确定。
 */
export function normalizeAlias(text) {
  return String(text ?? "")
    .replace(/\u3000/g, " ")
    .replace(/[\s]+/g, "")
    .replace(/[‐‑‒–—―]/g, "-")
    .replace(/[（(]/g, "(")
    .replace(/[）)]/g, ")")
    .replace(/[·・]/g, "")
    .trim()
    .toLowerCase();
}

export function sortNodes(nodes) {
  return [...nodes].sort((a, b) => {
    const ka = KIND_ORDER.indexOf(a.kind);
    const kb = KIND_ORDER.indexOf(b.kind);
    if (ka !== kb) return (ka < 0 ? 99 : ka) - (kb < 0 ? 99 : kb);
    return a.id.localeCompare(b.id);
  });
}

export function sortEdges(edges) {
  return [...edges].sort((a, b) => {
    const ta = EDGE_ORDER.indexOf(a.type);
    const tb = EDGE_ORDER.indexOf(b.type);
    if (ta !== tb) return (ta < 0 ? 99 : ta) - (tb < 0 ? 99 : tb);
    if (a.from !== b.from) return a.from.localeCompare(b.from);
    return a.to.localeCompare(b.to);
  });
}

/**
 * 零依赖分词。
 *
 * 三种模式，用环境变量 `CAREER_GRAPH_TOKENIZER` 切换，默认 `hybrid`：
 *
 *   bigram     拉丁词整词 + 中文连续片段切 bigram（最早的实现，召回宽、噪声大）
 *   segmenter  拉丁词整词 + `Intl.Segmenter` 的 ICU 中文分词（词粒度准、召回窄）
 *   hybrid     两者并用：ICU 词 + bigram（默认）
 *
 * 为什么要做成可切换的：中文没有空格，切词方式直接决定检索召回，
 * 而「哪个更好」不该靠争论 —— 评测集就是拿来干这个的。三种模式跑同一套 18 题，
 * 谁高谁低是实测出来的（见 `knowledge/evaluations/report.md` 的口径说明）。
 *
 * `Intl.Segmenter` 是 Node 内置的（ICU 数据），不需要任何依赖 ——
 * 这是「中文没有词典」这个问题在零依赖约束下的正解之一。
 * 但它的词典对「工程师」这类词会切成「工程 | 师」，所以默认不单独用它，而是与 bigram 并用。
 */
const SEGMENTER = typeof Intl?.Segmenter === "function" ? new Intl.Segmenter("zh-Hans", { granularity: "word" }) : null;

export const TOKENIZER_MODE = process.env.CAREER_GRAPH_TOKENIZER ?? "segmenter";

/**
 * 分词器的自描述。第 10 步把它写进检索索引、第 11 步写进评测结果 ——
 * 都在描述「这份产物是用哪套分词算出来的」。之前这两处是硬编码的字符串，
 * 换了分词实现却忘了改，元数据立刻说谎；现在从实现派生，漂不了。
 */
export function describeTokenizer() {
  const cjk =
    TOKENIZER_MODE === "bigram"
      ? "连续汉字片段的 bigram（单字片段保留 unigram）"
      : TOKENIZER_MODE === "segmenter"
        ? "Intl.Segmenter（ICU 词典）中文分词"
        : "ICU 中文分词 + 汉字 bigram 并用";
  return { mode: TOKENIZER_MODE, latin: "整词 [a-z0-9][a-z0-9+._#-]*", cjk };
}

export function tokenize(text, mode = TOKENIZER_MODE) {
  const src = String(text ?? "").toLowerCase();
  const out = [];
  for (const word of src.match(/[a-z0-9][a-z0-9+._#-]*/g) ?? []) out.push(word);

  const runs = src.replace(/[^\u4e00-\u9fff]+/g, " ").split(" ").filter(Boolean);
  const useSegmenter = (mode === "segmenter" || mode === "hybrid") && SEGMENTER !== null;
  const useBigram = mode === "bigram" || mode === "hybrid";

  if (useSegmenter) {
    for (const piece of SEGMENTER.segment(src)) {
      if (!piece.isWordLike) continue;
      const word = piece.segment.toLowerCase();
      // 只收中文词；拉丁词上面那一趟已经收过了，再收一遍会重复计频
      if (/[\u4e00-\u9fff]/.test(word) && word.length >= 2) out.push(word);
    }
  }
  if (useBigram) {
    for (const run of runs) {
      if (run.length === 1) out.push(run);
      for (let i = 0; i + 1 < run.length; i += 1) out.push(run.slice(i, i + 2));
    }
  }
  return out;
}

/**
 * BM25 倒排。零依赖、确定性：同输入同输出，评测才可复算。
 * @param {{id:string, text:string, meta?:Record<string, unknown>}[]} docs
 */
export function buildBm25(docs, { k1 = 1.2, b = 0.75 } = {}) {
  const df = new Map();
  const entries = docs.map((doc) => {
    const tf = new Map();
    for (const term of tokenize(doc.text)) tf.set(term, (tf.get(term) ?? 0) + 1);
    for (const term of tf.keys()) df.set(term, (df.get(term) ?? 0) + 1);
    let length = 0;
    for (const count of tf.values()) length += count;
    return { id: doc.id, meta: doc.meta ?? {}, tf, length };
  });
  const total = entries.reduce((sum, entry) => sum + entry.length, 0);
  const avgLen = entries.length > 0 ? total / entries.length : 0;
  return { entries, df, N: entries.length, avgLen, k1, b };
}

export function bm25Search(index, query, limit = 5) {
  const terms = tokenize(query);
  if (terms.length === 0) return [];
  const { entries, df, N, avgLen, k1, b } = index;
  const scored = [];
  for (const entry of entries) {
    let score = 0;
    for (const term of terms) {
      const f = entry.tf.get(term);
      if (!f) continue;
      const n = df.get(term) ?? 0;
      const idf = Math.log(1 + (N - n + 0.5) / (n + 0.5));
      score += (idf * (f * (k1 + 1))) / (f + k1 * (1 - b + b * (entry.length / (avgLen || 1))));
    }
    if (score > 0) scored.push({ id: entry.id, score: Math.round(score * 1000) / 1000, meta: entry.meta });
  }
  scored.sort((a, b2) => (b2.score - a.score) || a.id.localeCompare(b2.id));
  return scored.slice(0, limit);
}

/** 把 map[type]=count 这类统计稳定排序成 `type 12，type 3` 形式（文档与日志共用）。 */
export function describeCounts(counts, order = null) {
  const keys = order ? order.filter((key) => counts[key]) : Object.keys(counts).sort();
  return keys.map((key) => `${key} ${counts[key]}`).join("，");
}

/** 文件名安全化：Windows 不允许文件名里出现 `:`，而 knowledge 节点的 ref 形如 `AI001:stage1`。 */
export function safeFileSegment(id) {
  return shortRef(id).replace(/[:*?"<>|\\/]+/g, "-");
}

/** 交付物版本号：第 10/11 步共用一份，避免 graph.json 与 exports/ 各写一个数。 */
export const KB_VERSION = "2026.09.15";
export const GRAPH_VERSION = "0.1.0";

/**
 * 交付物的字段白名单。第 04–06 步为了让下游能复算，随行带了 `labelKey` / `aliasKeys` /
 * `normalize` / `adjudication` / `edgeKey` 这些中间态；交付物里不留 —— 它们不是契约字段
 * （adjudication.reason 一份 289 行）。
 *
 * 节点保留 §3.1 的公共字段 + `annotatedBy`（§3.3 L151 要求标注可见，§9.4 的降权视觉也靠它）；
 * 边保留 §3.2 的统一字段 + 该类型的附加字段，字段顺序刻意与 sample 一致。
 */
export function exportNode(node) {
  const out = {};
  for (const key of ["id", "kind", "label", "aliases", "description", "x", "y", "sourceRefs", "annotatedBy"]) {
    if (node[key] !== undefined) out[key] = node[key];
  }
  return out;
}

export function exportEdge(edge) {
  const out = {};
  for (const key of ["id", "type", "from", "to", "weight"]) if (edge[key] !== undefined) out[key] = edge[key];
  for (const key of EDGE_TYPE_FIELDS[edge.type] ?? []) if (edge[key] !== undefined) out[key] = edge[key];
  for (const key of ["sourceRefs", "annotatedBy", "updatedAt"]) if (edge[key] !== undefined) out[key] = edge[key];
  return out;
}
