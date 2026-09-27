/**
 * 边级「依据强度」契约。
 *
 * ## 为什么需要它
 *
 * 图谱原先只保证「每条边都有 sourceRefs 且指向真实存在的 chunkId」（09 步硬约束之一）。
 * 但那只能证明**引用了一个真实存在的段落**，不能证明**那段落真的支持这条边**。
 * 实测：117 条内容边里只有 17 条两端实体都能在依据段里查到；16 条两端都查不到。
 * 更典型的例子是 `S03#s46` 一段同时被 3 条不同类型的边当作依据 —— 那是「有一条引用」，
 * 不是「逐条核验过」。
 *
 * ## 设计取舍：为什么不一刀切要求「两端都出现」
 *
 * 节点 label 有两类来源：
 *   · **原文用词**：skill（「堆内存属性与 DMA 缓冲」）、tool（「ESP-IDF」）—— 原文里能找到；
 *   · **编者构造的标签**：occupation（「嵌入式开发工程师」）、domain（「嵌入式与实时系统」）、
 *     trend（「计算机硬件工程岗位稳定需求」）、credential、knowledge（学习单元名）——
 *     原文不会这么写，要求它们出现等于自欺。
 * 所以按类型定义「哪一端必须出现」，而不是对所有边提同一个要求。
 *
 * ## 豁免
 *
 * 硬约束若无法满足，必须走**显式豁免**（knowledge/review/evidence-waivers.json），
 * 每条豁免写清理由与批准人；豁免清单本身进版本控制，不得静默跳过。
 */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const HERE = new URL(".", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
export const EVIDENCE_WAIVERS_FILE = resolve(HERE, "..", "..", "review", "evidence-waivers.json");

/** 边类型 → 依据要求。level=hard 的违规会导致构建失败；warn 只记警告；skip 不检查。 */
export const EDGE_EVIDENCE_RULES = {
  requires: { mustAppear: ["to"], level: "hard", why: "职业名是编者标签（豁免），技能名应出自原文" },
  prerequisite: { mustAppear: ["from", "to"], level: "hard", why: "技能先后关系需要原文同时提到两项技能" },
  trains: { mustAppear: ["to"], level: "hard", why: "任务名是编者构造，技能名应出自原文" },
  uses: { mustAppear: ["to"], level: "hard", why: "工具名应出自原文" },
  covers: { mustAppear: ["to"], level: "hard", why: "学习单元覆盖的技能名应出自原文" },
  certifies: { mustAppear: ["to"], level: "warn", why: "证书名可能出自原文，先只警告" },
  belongs_to: { mustAppear: ["from"], level: "warn", why: "能力域是编者标签（豁免），技能名应出自原文" },
  emerging_in: { mustAppear: ["to"], level: "warn", why: "趋势名是编者标签，技能名应出自原文" },
  learning_unit: { mustAppear: [], level: "warn", why: "两端都是编者标签；该关系靠 evidenced_by 回到原文" },
  transitions_to: { mustAppear: [], level: "warn", why: "职业间转换是结构性判断，两端皆为编者标签" },
  evidenced_by: { mustAppear: [], level: "skip", why: "它本身就是引用边" },
};

// 注意：JS 的 \W 只按 ASCII 定义，`[\s\W_]` 会把**中文全部当成分隔符剥掉**，
// 导致「C 语言与内存模型」归一化成空串、任何中文标签都匹配不上。必须用 Unicode 属性类。
const SEPARATOR_RE = /[^\p{L}\p{N}_]+/gu;
const normalize = (value) => String(value ?? "").replace(SEPARATOR_RE, "").toLowerCase();

function bigrams(value) {
  const text = normalize(value);
  if (text.length < 2) return text ? [text] : [];
  const out = new Set();
  for (let i = 0; i + 2 <= text.length; i += 1) out.add(text.slice(i, i + 2));
  return [...out];
}

/**
 * 判断一个实体的名称（或其别名）是否出现在依据文本里。
 * 判定口径：归一化后整名包含 → 命中；否则二元组覆盖度 ≥ 0.6 → 命中。
 * 阈值偏高是刻意的：宁可漏判（进豁免清单），也不要误判成「有依据」。
 */
export function appearsIn(label, aliases, text) {
  const haystack = normalize(text);
  if (!haystack) return false;
  for (const candidate of [label, ...(aliases ?? [])]) {
    const needle = normalize(candidate);
    if (!needle) continue;
    if (haystack.includes(needle)) return true;
    const grams = bigrams(candidate);
    if (grams.length === 0) continue;
    const hit = grams.filter((gram) => haystack.includes(gram)).length;
    if (hit / grams.length >= 0.6) return true;
  }
  return false;
}

/** 返回该边在 mustAppear 指定的那些端里、名称没出现在依据段上的端。 */
export function missingEntities(edge, mustAppear, nodeById, chunkById) {
  if (!mustAppear || mustAppear.length === 0) return [];
  const refs = (edge.sourceRefs ?? []).filter((ref) => chunkById.has(ref));
  if (refs.length === 0) return mustAppear.map((side) => `${side}:${edge[side]}(无有效依据段)`);
  const text = refs.map((ref) => chunkById.get(ref).text ?? "").join("\n");
  const missing = [];
  for (const side of mustAppear) {
    const node = nodeById.get(edge[side]);
    if (!node) {
      missing.push(`${side}:${edge[side]}(节点不存在)`);
      continue;
    }
    if (!appearsIn(node.label, node.aliases, text)) missing.push(`${side}:${node.label}`);
  }
  return missing;
}

/** 读取显式豁免清单；读不到就返回空（不是「全部豁免」）。 */
export function loadWaivers() {
  if (!existsSync(EVIDENCE_WAIVERS_FILE)) return new Map();
  try {
    const raw = JSON.parse(readFileSync(EVIDENCE_WAIVERS_FILE, "utf8"));
    return new Map((raw.waivers ?? []).map((item) => [item.edgeId, item]));
  } catch {
    return new Map();
  }
}

/** 边的稳定标识：优先用 id，缺 id 时按 type+两端合成，保证豁免清单可引用。 */
export const edgeKey = (edge) => edge.id ?? `${edge.type}:${edge.from}->${edge.to}`;
