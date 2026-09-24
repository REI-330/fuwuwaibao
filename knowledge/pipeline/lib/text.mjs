/**
 * 文本工具：实体解码、空白归一、切分、charRange 计算。
 *
 * charRange 的基准必须唯一，否则第 09 步的完整性校验没有意义。
 * 本项目约定：**charRange 是相对该来源 `raw/<sourceId>.txt` 的 UTF-16 下标区间**，
 * 左闭右开。`raw/<sourceId>.txt` 由第 02 步抽出，等于所有 block 文本用 `\n\n` 连接。
 */

const NAMED_ENTITIES = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  ensp: " ",
  emsp: " ",
  thinsp: " ",
  shy: "",
  zwnj: "",
  zwj: "",
  hellip: "…",
  mdash: "—",
  ndash: "–",
  minus: "−",
  lsquo: "‘",
  rsquo: "’",
  ldquo: "“",
  rdquo: "”",
  sbquo: "‚",
  bdquo: "„",
  laquo: "«",
  raquo: "»",
  dagger: "†",
  bull: "•",
  middot: "·",
  para: "¶",
  sect: "§",
  copy: "©",
  reg: "®",
  trade: "™",
  deg: "°",
  plusmn: "±",
  times: "×",
  divide: "÷",
  frac12: "½",
  larr: "←",
  rarr: "→",
  uarr: "↑",
  darr: "↓",
  harr: "↔",
  le: "≤",
  ge: "≥",
  ne: "≠",
  infin: "∞",
  alpha: "α",
  beta: "β",
  gamma: "γ",
  mu: "μ",
  pi: "π",
  sigma: "σ",
  omega: "ω",
  euro: "€",
  pound: "£",
  yen: "¥",
};

/** 解码 HTML 实体（命名 + 十进制 + 十六进制）。未知实体原样保留。 */
export function decodeEntities(input) {
  if (!input || input.indexOf("&") === -1) return input;
  return input.replace(/&(#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[a-zA-Z][a-zA-Z0-9]{1,31});/g, (match, body) => {
    if (body[0] === "#") {
      const hex = body[1] === "x" || body[1] === "X";
      const code = Number.parseInt(hex ? body.slice(2) : body.slice(1), hex ? 16 : 10);
      if (!Number.isFinite(code) || code <= 0 || code > 0x10ffff) return match;
      try {
        return String.fromCodePoint(code);
      } catch {
        return match;
      }
    }
    const key = body.toLowerCase();
    return Object.prototype.hasOwnProperty.call(NAMED_ENTITIES, key) ? NAMED_ENTITIES[key] : match;
  });
}

/** 归一空白：全角空格、制表符、连续空行都压掉，但保留换行以维持段落。 */
export function normalizeText(input) {
  if (!input) return "";
  return input
    .replace(/\r\n?/g, "\n")
    .replace(/\u00a0|\u3000/g, " ")
    .replace(/[ \t\f\v]+/g, " ")
    .split("\n")
    .map((line) => line.trim())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** 单行折叠：用于标题、标签这类不该含换行的字段。 */
export function collapseLine(input) {
  return normalizeText(input).replace(/\s*\n\s*/g, " ").trim();
}

/** UTF-16 长度，与 charRange 口径一致。 */
export function charLength(input) {
  return input ? input.length : 0;
}

/** 取标题里的编号前缀，例如 “2.1 岗位职责” → “2.1”。 */
export function numericPrefix(heading) {
  const match = /^\s*(\d+(?:\.\d+)*)(?=[\s.、:：]|$)/.exec(heading ?? "");
  return match ? match[1] : null;
}

/** 生成 chunkId 里可用的短键：优先编号，否则用 s<序号>。 */
export function sectionKey(heading, ordinal) {
  const numeric = numericPrefix(heading);
  if (numeric) return numeric;
  return `s${ordinal}`;
}

/** 按段落边界切分长文本，保证每段不超过 limit，且不切断段落。 */
export function splitByParagraph(text, limit) {
  if (text.length <= limit) return [text];
  const parts = [];
  let buffer = "";
  const pushBuffer = () => {
    const trimmed = buffer.trim();
    if (trimmed) parts.push(trimmed);
    buffer = "";
  };
  for (const paragraph of text.split(/\n{2,}/)) {
    if (paragraph.length > limit) {
      pushBuffer();
      let slice = paragraph;
      while (slice.length > limit) {
        // 优先在句末断句，退化为硬切。
        const window = slice.slice(0, limit);
        const cut = Math.max(window.lastIndexOf("。"), window.lastIndexOf("."), window.lastIndexOf("\n"));
        const end = cut > limit * 0.5 ? cut + 1 : limit;
        parts.push(slice.slice(0, end).trim());
        slice = slice.slice(end);
      }
      buffer = slice;
      continue;
    }
    if (buffer.length + paragraph.length + 2 > limit) pushBuffer();
    buffer = buffer ? `${buffer}\n\n${paragraph}` : paragraph;
  }
  pushBuffer();
  return parts.filter(Boolean);
}

/** 归一标题用于比对（去编号、去空白、转小写）。 */
export function normalizeHeadingKey(heading) {
  return collapseLine(heading)
    .replace(/^\s*\d+(?:\.\d+)*[\s.、:：]*/, "")
    .replace(/[\s\u3000]+/g, "")
    .toLowerCase();
}
