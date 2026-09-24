#!/usr/bin/env node
/**
 * 可达性 / 可解析性探测（不写任何产物，只打印）
 *
 * 用途：挑选或复核来源时，先确认「HTTP 200 且真能解析出正文」，再决定要不要写进
 * sources.candidates.json。设计文档要求「只登记真实可达且可解析的来源」，
 * 这个脚本就是那条规矩的执行器 —— 它和步骤 02 用同一个 UA、同一套解码、同一个
 * htmlToBlocks，所以探测结果与流水线实际抓到的结果一致。
 *
 * 跑法（仓库根目录）：
 *   node knowledge/pipeline/probe-url.mjs <url> [<url> ...]
 *   node knowledge/pipeline/probe-url.mjs --peek 300 <url>
 *
 * 输出每行：
 *   status  HTTP 码（0 = 网络层失败）
 *   bytes   响应字节数
 *   prune   剪枝版 blocks / 字符数
 *   full    无剪枝版 blocks / 字符数
 *   title   页面标题
 *   peek    无剪枝版正文开头（判断是「正文」还是「导航墙」用）
 */
import { createHash } from "node:crypto";

import { htmlToBlocks, extractTitle } from "./lib/html.mjs";

/** 与 02-fetch-sources.mjs 完全一致的 UA，避免「探测能过、抓取被抓」的假象。 */
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

const REQUEST_TIMEOUT_MS = 25000;

function detectCharset(buffer, contentType) {
  const fromHeader = /charset\s*=\s*"?([\w-]+)"?/i.exec(contentType ?? "");
  if (fromHeader) return fromHeader[1].toLowerCase();
  const head = buffer.subarray(0, 4096).toString("latin1");
  const fromMeta = /<meta[^>]+charset\s*=\s*["']?([\w-]+)/i.exec(head);
  if (fromMeta) return fromMeta[1].toLowerCase();
  return "utf-8";
}

function decodeBuffer(buffer, charset) {
  const label = charset === "gb2312" || charset === "gbk" ? "gb18030" : charset;
  try {
    return new TextDecoder(label, { fatal: false }).decode(buffer);
  } catch {
    return new TextDecoder("utf-8", { fatal: false }).decode(buffer);
  }
}

function textOf(blocks) {
  return blocks.map((block) => block.text).join("\n");
}

async function probe(url, peekLength) {
  const row = { url, status: 0, bytes: 0, pruned: null, full: null, title: "", peek: "" };
  let response;
  try {
    response = await fetch(url, {
      redirect: "follow",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      headers: {
        "user-agent": USER_AGENT,
        accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
      },
    });
  } catch (error) {
    row.error = error?.name ?? String(error);
    return row;
  }

  const buffer = Buffer.from(await response.arrayBuffer());
  const contentType = response.headers.get("content-type") ?? "";
  const html = decodeBuffer(buffer, detectCharset(buffer, contentType));

  row.status = response.status;
  row.bytes = buffer.length;
  row.contentType = contentType.split(";")[0];
  row.sha256 = createHash("sha256").update(buffer).digest("hex").slice(0, 12);
  row.finalUrl = response.url;
  row.title = extractTitle(html) ?? "";

  const pruned = htmlToBlocks(html, { skipBoilerplate: true }).blocks;
  const full = htmlToBlocks(html, { skipBoilerplate: false }).blocks;
  const prunedText = textOf(pruned);
  const fullText = textOf(full);
  row.pruned = `${pruned.length}/${prunedText.length}`;
  row.full = `${full.length}/${fullText.length}`;
  row.peek = fullText.replace(/\s+/g, " ").slice(0, peekLength);
  return row;
}

const argv = process.argv.slice(2);
let peekLength = 200;
const urls = [];
for (let i = 0; i < argv.length; i += 1) {
  if (argv[i] === "--peek") {
    peekLength = Number(argv[i + 1]) || 200;
    i += 1;
    continue;
  }
  urls.push(argv[i]);
}

if (urls.length === 0) {
  console.error("用法：node knowledge/pipeline/probe-url.mjs [--peek N] <url> [<url> ...]");
  process.exit(2);
}

for (const url of urls) {
  const row = await probe(url, peekLength);
  const head = [
    String(row.status).padStart(3),
    `${String(row.bytes).padStart(8)}B`,
    `prune ${row.pruned ?? "-"}`,
    `full ${row.full ?? "-"}`,
  ].join("  ");
  console.log(`${head}  ${row.sha256 ?? "-"}  ${row.title.slice(0, 48) || "(无标题)"}`);
  console.log(`     ${url}`);
  if (row.finalUrl && row.finalUrl !== url) console.log(`  -> ${row.finalUrl}`);
  if (row.error) console.log(`     error=${row.error}`);
  if (row.peek) console.log(`     peek: ${row.peek}`);
  console.log("");
}
