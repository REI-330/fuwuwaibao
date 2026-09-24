/**
 * 每一步的执行日志。
 *
 * 设计文档第 6 节要求「每一步的命令、输入、输出、条数都记录下来，直接构成材料的
 * 构建方法部分」。所以日志不是调试输出，而是交付物：`knowledge/evidence/pipeline-log.json`。
 *
 * 同一步重复执行会覆盖该步的旧记录（保留 `runCount`），避免日志无限膨胀。
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

import { PIPELINE_LOG_FILE, WORKSPACE_ROOT, relPath } from "./paths.mjs";

export function nowIso() {
  return new Date().toISOString();
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readLogFile() {
  if (!existsSync(PIPELINE_LOG_FILE)) {
    return { schema: "career-graph-pipeline-log/v1", updatedAt: null, steps: [] };
  }
  try {
    const parsed = JSON.parse(readFileSync(PIPELINE_LOG_FILE, "utf8"));
    if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.steps)) throw new Error("bad shape");
    return parsed;
  } catch (error) {
    return { schema: "career-graph-pipeline-log/v1", updatedAt: null, steps: [], recoveredFrom: String(error) };
  }
}

export function writeJson(file, value) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

export function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

export function readJsonl(file) {
  if (!existsSync(file)) return [];
  return readFileSync(file, "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

export function writeJsonl(file, rows) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, rows.map((row) => JSON.stringify(row)).join("\n") + (rows.length ? "\n" : ""), "utf8");
}

/**
 * 记录一步的执行结果。
 * @param {{step:string,title:string,command:string,inputs?:string[],outputs?:string[],counts?:Record<string,number>,notes?:string[],startedAt:string}} entry
 */
export function recordStep(entry) {
  const log = readLogFile();
  const finishedAt = nowIso();
  const startedAt = entry.startedAt ?? finishedAt;
  const record = {
    step: entry.step,
    title: entry.title,
    command: entry.command,
    startedAt,
    finishedAt,
    durationMs: Math.max(0, new Date(finishedAt).getTime() - new Date(startedAt).getTime()),
    inputs: (entry.inputs ?? []).map((item) => (item.startsWith(WORKSPACE_ROOT) ? relPath(item) : item)),
    outputs: (entry.outputs ?? []).map((item) => (item.startsWith(WORKSPACE_ROOT) ? relPath(item) : item)),
    counts: entry.counts ?? {},
    notes: entry.notes ?? [],
    node: process.version,
  };
  const previous = log.steps.find((step) => step.step === entry.step);
  if (previous) record.runCount = (previous.runCount ?? 1) + 1;
  log.steps = log.steps.filter((step) => step.step !== entry.step).concat(record).sort((a, b) => a.step.localeCompare(b.step));
  log.updatedAt = finishedAt;
  writeJson(PIPELINE_LOG_FILE, log);
  return record;
}

/** 统一的中文控制台行，便于人工把 stdout 直接贴进报告。 */
export function logLine(message) {
  process.stdout.write(`[kb] ${message}\n`);
}

export function fail(message) {
  process.stderr.write(`[kb:error] ${message}\n`);
  process.exitCode = 1;
}
