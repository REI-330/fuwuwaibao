#!/usr/bin/env node
/**
 * 抓取人社部「职业分类大典系统」全量职业清单 → `knowledge/import/raw/osta-dadian/`
 *
 *   node knowledge/pipeline/import/fetch-osta-dadian.mjs
 *
 * 数据来源：技能人才评价工作网（人力资源和社会保障部）https://www.osta.org.cn/career
 * 页面标题是「职业分类大典系统」，底层是这三条公开接口（无需登录，带 UA + Referer 即可）：
 *
 *   GET /api/client/get/tree                         全量分类树（大类 → 中类 → 小类）
 *   GET /api/client/subordinate/data?careerCode=X    某节点的直接下级
 *   GET /api/client/find/hot/data                    首页热门职业（本脚本不用）
 *
 * 层级实测是四级，职业在第四级：
 *   大类 `1`  →  中类 `1-01`  →  小类 `1-01-00`  →  职业 `1-01-00-01`
 * 树接口只给到小类，职业要逐个（450 个小类）再问一次 —— 这是唯一能拿到全量的办法，
 * 没有批量端点。所以本脚本要发 1 + 450 次请求，故意压到并发 2、每次间隔 200ms：
 * 这是政府站的公开接口，没必要为了快几十秒把它打出一串 429。
 *
 * 产出的两份 JSON 原样落盘（不做任何加工），后续的导入脚本只读这两份文件，
 * 保证「抓取」与「换算」分开：抓取只发生一次，换算可以反复重跑。
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = resolve(HERE, "..", "..", "..");
const OUT_DIR = resolve(WORKSPACE_ROOT, "knowledge", "import", "raw", "osta-dadian");

const BASE = "https://www.osta.org.cn";
const VERSION_ID = 2;
/** 版本号来自接口返回的 `versionId`。写死是为了让「拿的是哪一版」可追溯，换版本要显式改这里。 */
const HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
  Referer: `${BASE}/career`,
  Accept: "application/json, text/plain, */*",
};

const CONCURRENCY = 2;
const DELAY_MS = 200;

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function getJson(path, { retries = 2 } = {}) {
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const response = await fetch(`${BASE}${path}`, { headers: HEADERS });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (payload.code !== 200) throw new Error(`code ${payload.code} ${payload.msg}`);
      return payload.body;
    } catch (error) {
      if (attempt === retries) throw new Error(`${path} 失败：${error.message}`);
      await sleep(800 * (attempt + 1));
    }
  }
  return null;
}

/** 并发受限的 map。并发数写死在下面，不引入依赖。 */
async function mapLimited(items, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  const runners = Array.from({ length: Math.min(CONCURRENCY, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await worker(items[index], index);
      await sleep(DELAY_MS);
    }
  });
  await Promise.all(runners);
  return results;
}

mkdirSync(OUT_DIR, { recursive: true });

// ---------- 1. 分类树 ----------

process.stdout.write("拉取分类树 … ");
const tree = await getJson("/api/client/get/tree");
console.log(`${tree.length} 个大类`);

/** 只有小类（叶子）才有下级职业，非叶子直接跳过，省掉无谓请求。 */
const smallCategories = [];
const walk = (node, depth) => {
  if (!node.children || node.children.length === 0) {
    smallCategories.push({ code: node.careerCode, name: node.careerName, depth });
    return;
  }
  for (const child of node.children) walk(child, depth + 1);
};
for (const node of tree) walk(node, 0);

writeFileSync(resolve(OUT_DIR, "tree.json"), `${JSON.stringify({ versionId: VERSION_ID, fetchedFrom: `${BASE}/api/client/get/tree`, body: tree }, null, 2)}\n`, "utf8");
console.log(`小类 ${smallCategories.length} 个 → 逐个小类取职业（这一步要发 ${smallCategories.length} 次请求）`);

// ---------- 2. 逐个类取职业 ----------

let done = 0;
const bySmall = await mapLimited(smallCategories, async (small) => {
  const body = await getJson(`/api/client/subordinate/data?careerCode=${encodeURIComponent(small.code)}&versionId=${VERSION_ID}`);
  const careers = (body ?? []).filter((item) => item.careerCode && item.careerCode.split("-").length === 4);
  done += 1;
  if (done % 50 === 0) process.stdout.write(`  已取 ${done}/${smallCategories.length}（累计职业 ${careers.length}）\r`);
  return {
    smallCode: small.code,
    smallName: small.name,
    careers: careers.map((item) => ({
      code: item.careerCode,
      name: item.name,
      /** 工种数：接口里叫 workNum，指的是这个职业下的具体工种（如「灌区管理工（灌区供水工）」）。 */
      workNum: item.workNum ?? 0,
    })),
  };
});
process.stdout.write("\n");

writeFileSync(resolve(OUT_DIR, "careers.json"), `${JSON.stringify({ versionId: VERSION_ID, fetchedFrom: `${BASE}/api/client/subordinate/data`, bySmallCategory: bySmall }, null, 2)}\n`, "utf8");

// ---------- 3. 汇总 ----------

const all = bySmall.flatMap((group) => group.careers.map((career) => ({ ...career, smallCode: group.smallCode, smallName: group.smallName })));
const codes = new Set(all.map((career) => career.code));
const byLarge = {};
for (const node of tree) {
  const large = node.careerCode;
  const codesInLarge = new Set(
    bySmall.filter((group) => group.smallCode.startsWith(`${large}-`)).flatMap((group) => group.careers.map((career) => career.code)),
  );
  byLarge[`${large} ${node.careerName}`] = codesInLarge.size;
}

const summary = {
  schema: "career-graph-osta-dadian/v1",
  source: "人力资源和社会保障部 技能人才评价工作网 · 职业分类大典系统",
  page: `${BASE}/career`,
  versionId: VERSION_ID,
  fetchedAt: new Date().toISOString(),
  counts: {
    largeCategories: tree.length,
    smallCategories: smallCategories.length,
    careers: codes.size,
    /** 同一个 code 出现在多个小类下时会小于上面那个数，分开记，免得以后以为抓重了。 */
    careerRows: all.length,
    works: all.reduce((sum, career) => sum + (career.workNum || 0), 0),
  },
  careersByLargeCategory: byLarge,
  note: "职业在第四级（code 形如 1-01-00-01）。本文件的数字全部来自接口返回，未经人工增删。",
};
writeFileSync(resolve(OUT_DIR, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`, "utf8");

console.log("================ 结果 ================");
console.log(`大类 ${summary.counts.largeCategories} ｜ 小类 ${summary.counts.smallCategories} ｜ 职业 ${summary.counts.careers} ｜ 工种 ${summary.counts.works}`);
for (const [name, count] of Object.entries(byLarge)) console.log(`  ${String(count).padStart(5)}  ${name}`);
console.log(`产物：knowledge/import/raw/osta-dadian/{tree,careers,summary}.json`);
