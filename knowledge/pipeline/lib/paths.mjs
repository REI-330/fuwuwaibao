/**
 * 流水线路径解析。
 *
 * 所有步骤脚本都从这里取路径，避免每个脚本各自拼一串 ../../。
 * 目录布局与 KNOWLEDGE_BASE_DESIGN.md 第 6 节一一对应：
 *
 *   knowledge/
 *     sources/       01 来源登记
 *     raw/           02 抓取与去重（html + 抽取出的 blocks/txt + manifest）
 *     chunks/        03 主题分块
 *     extract/       04 候选实体/关系、05 别名归一
 *     review/        06/08 人工裁定与审核
 *     wiki/          07/08 Wiki 页
 *     graph/         10 布局与索引
 *     exports/       11 版本化导出（前端与 MCP 消费）
 *     evaluations/   11 评测集与评测结果
 *     evidence/      每一步的机器可读执行日志
 */
import { dirname, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

/** knowledge/pipeline/lib */
const HERE = dirname(fileURLToPath(import.meta.url));

/** knowledge/pipeline */
export const PIPELINE_DIR = resolve(HERE, "..");

/** 仓库根 */
export const WORKSPACE_ROOT = resolve(PIPELINE_DIR, "..", "..");

export const KNOWLEDGE_DIR = resolve(WORKSPACE_ROOT, "knowledge");

export const SOURCES_DIR = resolve(KNOWLEDGE_DIR, "sources");
export const RAW_DIR = resolve(KNOWLEDGE_DIR, "raw");
export const CHUNKS_DIR = resolve(KNOWLEDGE_DIR, "chunks");
export const EXTRACT_DIR = resolve(KNOWLEDGE_DIR, "extract");
export const REVIEW_DIR = resolve(KNOWLEDGE_DIR, "review");
export const WIKI_DIR = resolve(KNOWLEDGE_DIR, "wiki");
export const GRAPH_DIR = resolve(KNOWLEDGE_DIR, "graph");
export const EXPORTS_DIR = resolve(KNOWLEDGE_DIR, "exports");
export const EVAL_DIR = resolve(KNOWLEDGE_DIR, "evaluations");
export const EVIDENCE_DIR = resolve(KNOWLEDGE_DIR, "evidence");

export const SOURCES_FILE = resolve(SOURCES_DIR, "sources.json");
export const SOURCES_REGISTERED_FILE = resolve(SOURCES_DIR, "sources.registered.json");
export const RAWSET_FILE = resolve(SOURCES_DIR, "sourceset.json");
export const TAXONOMY_FILE = resolve(PIPELINE_DIR, "taxonomy.json");
export const SOURCE_CANDIDATES_FILE = resolve(PIPELINE_DIR, "sources.candidates.json");
export const MANIFEST_FILE = resolve(RAW_DIR, "manifest.json");
export const CHUNKS_FILE = resolve(CHUNKS_DIR, "chunks.jsonl");
export const GRAPH_RAW_FILE = resolve(GRAPH_DIR, "graph.raw.json");
export const GRAPH_FILE = resolve(GRAPH_DIR, "graph.json");
export const EXPORT_FILE = resolve(EXPORTS_DIR, "career-graph.json");
export const PIPELINE_LOG_FILE = resolve(EVIDENCE_DIR, "pipeline-log.json");

/** 步骤 04：抽取输入（人/LLM 按 evidence 契约写的种子）与输出。 */
export const EXTRACT_SEED_FILE = resolve(EXTRACT_DIR, "seed.json");
export const CANDIDATES_FILE = resolve(EXTRACT_DIR, "candidates.jsonl");
/** 步骤 05：归一化 + 别名消解产物。 */
export const ALIASES_FILE = resolve(EXTRACT_DIR, "aliases.json");
export const NORMALIZED_FILE = resolve(EXTRACT_DIR, "normalized.jsonl");
/** 步骤 06：人工裁定。输入是 review/ 下的人工文件，输出是 accepted.jsonl。 */
export const ADJUDICATION_FILE = resolve(REVIEW_DIR, "adjudication.json");
export const REVIEW_QUEUE_FILE = resolve(REVIEW_DIR, "review-queue.md");
export const ACCEPTED_FILE = resolve(EXTRACT_DIR, "accepted.jsonl");
/** 步骤 08：Wiki 审核结论（人工文件，与 06 同构）。 */
export const WIKI_REVIEW_FILE = resolve(REVIEW_DIR, "wiki-review.json");
/** 步骤 07/08：Wiki 页目录清单（frontmatter + 内链，机器可读，免去下游再解析 Markdown）。 */
export const WIKI_INDEX_FILE = resolve(WIKI_DIR, "index.json");
/** 步骤 09：完整性校验报告。 */
export const INTEGRITY_FILE = resolve(EVIDENCE_DIR, "integrity-report.json");
/** 步骤 10：检索索引（BM25 倒排 + 别名倒排）。 */
export const SEARCH_INDEX_FILE = resolve(GRAPH_DIR, "search-index.json");
/** 步骤 11：评测集（人/LLM 编写）与评测结果。 */
export const EVAL_QUESTIONS_FILE = resolve(EVAL_DIR, "questions.json");
export const EVAL_RESULTS_FILE = resolve(EVAL_DIR, "results.json");
export const EVAL_REPORT_FILE = resolve(EVAL_DIR, "report.md");

/** 输出路径时统一用仓库相对路径 + 正斜杠，便于写进证据与报告。 */
export function relPath(target) {
  const rel = relative(WORKSPACE_ROOT, target);
  return rel.split(sep).join("/");
}

export const RAW_HTML = (sourceId) => resolve(RAW_DIR, `${sourceId}.html`);
export const RAW_BLOCKS = (sourceId) => resolve(RAW_DIR, `${sourceId}.blocks.json`);
export const RAW_TEXT = (sourceId) => resolve(RAW_DIR, `${sourceId}.txt`);
