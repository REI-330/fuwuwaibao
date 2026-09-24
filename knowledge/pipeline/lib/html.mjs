/**
 * 零依赖 HTML → 结构化 block 抽取。
 *
 * 为什么不用 cheerio/jsdom：知识库流水线要能在评委机器上 `node 02-fetch-sources.mjs` 直接跑起来，
 * 不能引入前端工程的 node_modules。所以这里是一个有明确取舍的流式扫描器：
 *
 * - 只认块级标签（p/li/td/h1-h6/pre/...），行内标签只做文本拼接；
 * - 注释与 `<!doctype>` 直接抹掉，不进正文；
 * - script/style/textarea/title 按 HTML 规范当「raw text」整段跳过：只认真实结束标签，
 *   内部出现的 `<` 不再当标签（否则内联 JS 里的 `<o.length` 这类片段会吞掉 `</script>`，
 *   导致跳过栈永不弹出、整页正文被丢光——opencv.org.cn 实测就死在这里）；
 * - 遇到导航/页脚/侧边栏等样板容器整段丢弃（按标签名 + class/id 关键词判断）；
 * - 产出 `{kind, level, text}` 序列，`kind` 取 heading | text | list | code；
 * - 不做 JS 渲染，页面若是单页应用会得到很少的 block，第 02 步据此把该来源标为 insufficient-text，
 *   这是一个显式的失败信号，不是静默通过。
 */
import { collapseLine, decodeEntities, normalizeText } from "./text.mjs";

/** 整段丢弃的容器标签。 */
const SKIP_TAGS = new Set([
  "script",
  "style",
  "noscript",
  "template",
  "svg",
  "math",
  "iframe",
  "form",
  "button",
  "select",
  "option",
  "textarea",
  "title",
  "nav",
  "aside",
  "footer",
  "applet",
  "canvas",
  "audio",
  "video",
]);

/**
 * 「raw text」标签：按 HTML 规范，其内容不是标记而是纯文本，只有配对的结束标签能终止它。
 * 这类标签必须靠文本查找整体跳过，不能让通用标签正则进去扫。
 */
const RAW_TEXT_TAGS = new Set(["script", "style", "textarea", "title"]);

const COMMENT_RE = /<!--[\s\S]*?-->/g;
const DECLARATION_RE = /<![^>]*>/g;

/**
 * 样板容器的 class/id 关键词。
 *
 * 这里刻意**不**包含 `nav` / `menu` / `toc` / `pagination` 这类过于通用的词：
 * 文档站常把整个正文包在 `wy-grid-for-nav`、`page-with-menu` 这类布局容器里，
 * 一旦按子串命中就会把整页正文丢掉（实测 espressif 文档站 250KB 页面只剩 1 个 block）。
 * 真正的导航由 `<nav>` 标签覆盖；剩余噪声由第 02 步的"无剪枝回退"兜底。
 */
const BOILERPLATE_PATTERN =
  /(^|[-_ ])(sidebar|sidenav|side-nav|table-of-contents|breadcrumb|breadcrumbs|masthead|skip-link|cookie|consent|advert|advertisement|site-header|site-footer|topbar|searchbox|search-bar|version-switcher|prev-next|related-posts|dropdown-menu)([-_ ]|$)/i;

const VOID_TAGS = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);

const BLOCK_TAGS = new Set([
  "p",
  "div",
  "li",
  "tr",
  "td",
  "th",
  "section",
  "article",
  "main",
  "ul",
  "ol",
  "dl",
  "dt",
  "dd",
  "figure",
  "figcaption",
  "blockquote",
  "pre",
  "br",
  "hr",
  "details",
  "summary",
  "table",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
]);

const HEADING_RE = /^h([1-6])$/;

const TAG_RE = /<(\/?)([a-zA-Z][a-zA-Z0-9:_-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/g;

function attributesOf(rawAttributes) {
  const attributes = {};
  const attributeRe = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/g;
  let match;
  while ((match = attributeRe.exec(rawAttributes ?? "")) !== null) {
    attributes[match[1].toLowerCase()] = decodeEntities(match[2] ?? match[3] ?? match[4] ?? "");
  }
  return attributes;
}

function shouldSkip(tag, attributes, skipBoilerplate) {
  // SKIP_TAGS 永远生效：脚本/样式/表单等即使不做剪枝也不能变成正文。
  if (SKIP_TAGS.has(tag)) return true;
  if (!skipBoilerplate) return false;
  const marker = `${attributes.class ?? ""} ${attributes.id ?? ""} ${attributes.role ?? ""}`.trim();
  if (!marker) return false;
  return BOILERPLATE_PATTERN.test(marker);
}

function classify(text, inPre) {
  if (inPre) return "code";
  if (/^[-*·•]\s/.test(text) || /^\d+[.)、]\s/.test(text)) return "list";
  return "text";
}

/**
 * 在 raw text 标签（script/style/textarea/title）里找到配对结束标签之后的偏移。
 * 找不到返回 -1，调用方按「该标签一直没关，后面内容全部丢弃」处理。
 *
 * @param {string} lowerHtml 预先算好的 html.toLowerCase()
 * @param {string} html
 * @param {number} from TAG_RE.lastIndex，即刚匹配完的那个开始标签之后
 * @param {string} tag
 */
function findRawTextEnd(lowerHtml, html, from, tag) {
  const needle = `</${tag}`;
  let at = lowerHtml.indexOf(needle, from);
  while (at !== -1) {
    const next = html[at + needle.length];
    if (next === undefined || next === ">" || next === "/" || /\s/.test(next)) {
      const gt = lowerHtml.indexOf(">", at);
      return gt === -1 ? html.length : gt + 1;
    }
    at = lowerHtml.indexOf(needle, at + needle.length);
  }
  return -1;
}

/** 抽取 `<title>`，抽取不到返回空串。 */
export function extractTitle(html) {
  const match = /<title[^>]*>([\s\S]{0,300}?)<\/title>/i.exec(html ?? "");
  return match ? collapseLine(decodeEntities(match[1])) : "";
}

/**
 * 把 HTML 转成有序 block 列表。
 *
 * @param {string} html
 * @param {{ skipBoilerplate?: boolean }} [options]
 *   skipBoilerplate=false 时只按标签丢弃（script/style/nav/…），不做 class/id 剪枝。
 *   第 02 步会先跑剪枝版，正文过薄时再用无剪枝版重跑一次并取较长的结果，
 *   避免「剪枝把整页正文剪掉」被误判成 insufficient-text。
 * @returns {{ blocks: {kind: "heading"|"text"|"list"|"code", level: number, text: string}[] }}
 */
export function htmlToBlocks(html, options = {}) {
  const skipBoilerplate = options.skipBoilerplate !== false;
  const blocks = [];
  const skipStack = [];
  let buffer = "";
  let pendingHeadingLevel = 0;
  let preDepth = 0;

  // 注释与 <!doctype>/<![CDATA[…]> 不是正文，先抹掉，省得它们和文本一起被 flush 成 block。
  const source = String(html ?? "")
    .replace(COMMENT_RE, "")
    .replace(DECLARATION_RE, "");
  const lowerSource = source.toLowerCase();

  const flush = () => {
    const text = normalizeText(buffer);
    buffer = "";
    if (pendingHeadingLevel) {
      const headingText = collapseLine(text);
      if (headingText) blocks.push({ kind: "heading", level: pendingHeadingLevel, text: headingText });
      pendingHeadingLevel = 0;
      return;
    }
    if (text.length < 2) return;
    blocks.push({ kind: classify(text, preDepth > 0), level: 0, text });
  };

  /** 进入 raw text 标签后整体跳到配对结束标签之后；返回是否成功跳到。 */
  const skipRawText = (tag) => {
    const end = findRawTextEnd(lowerSource, source, TAG_RE.lastIndex, tag);
    if (end === -1) return false;
    TAG_RE.lastIndex = end;
    cursor = end;
    return true;
  };

  TAG_RE.lastIndex = 0;
  let cursor = 0;
  let match;
  while ((match = TAG_RE.exec(source)) !== null) {
    const rawText = source.slice(cursor, match.index);
    cursor = TAG_RE.lastIndex;
    if (rawText) {
      const decoded = decodeEntities(rawText);
      if (skipStack.length === 0) buffer += decoded;
    }

    const isClosing = match[1] === "/";
    const tag = match[2].toLowerCase();
    const attributes = isClosing ? {} : attributesOf(match[3] ?? "");

    if (skipStack.length > 0) {
      if (isClosing && skipStack[skipStack.length - 1] === tag) skipStack.pop();
      else if (!isClosing && shouldSkip(tag, attributes, skipBoilerplate)) {
        if (RAW_TEXT_TAGS.has(tag) && skipRawText(tag)) continue;
        skipStack.push(tag);
      }
      continue;
    }

    if (isClosing) {
      const heading = HEADING_RE.exec(tag);
      if (heading) {
        if (pendingHeadingLevel === Number(heading[1])) flush();
        else if (pendingHeadingLevel) pendingHeadingLevel = 0;
        continue;
      }
      if (tag === "pre" && preDepth > 0) preDepth -= 1;
      if (BLOCK_TAGS.has(tag)) flush();
      continue;
    }

    if (shouldSkip(tag, attributes, skipBoilerplate)) {
      if (!VOID_TAGS.has(tag)) {
        if (RAW_TEXT_TAGS.has(tag) && skipRawText(tag)) continue;
        skipStack.push(tag);
      }
      continue;
    }

    if (tag === "br") {
      flush();
      continue;
    }
    if (tag === "pre") {
      flush();
      preDepth += 1;
      continue;
    }
    const heading = HEADING_RE.exec(tag);
    if (heading) {
      flush();
      pendingHeadingLevel = Number(heading[1]);
      continue;
    }
    if (BLOCK_TAGS.has(tag)) flush();
  }

  if (pendingHeadingLevel || buffer) flush();
  return { blocks };
}

/** 计算从 block 列表拼出的纯文本与每个 block 的 charRange。 */
export function blocksToDocument(blocks) {
  const pieces = [];
  const ranges = [];
  let offset = 0;
  for (const block of blocks) {
    const text = block.kind === "heading" ? block.text : block.text;
    if (offset > 0) offset += 2; // "\n\n"
    ranges.push([offset, offset + text.length]);
    pieces.push(text);
    offset += text.length;
  }
  return { text: pieces.join("\n\n"), ranges };
}
