# 向新 · AI 职业导航与成长伙伴

面向大学生与职场新人的职业成长系统：**职业知识图谱 + 动态画像 + 可执行的成长路径**。
不替用户决定职业，而是把"我该学什么、先学什么、凭什么这么说"变成能算、能溯源、能验证的东西。

## 目录导航（先看这一节）

```
fuwuwaibao/
├── frontend/frotent/frontend1/   ← ★ 唯一的正式构建源，改这里才生效
│   ├── app/          页面与路由（(entry) 登录/画像、(product) 产品主体）
│   ├── components/   界面组件（chat / profile / work-map / layout）
│   ├── lib/client/   请求层、状态规则、图谱视图纯函数
│   ├── types/        前后端数据契约
│   ├── backend/      后端服务（Python 标准库，零三方依赖）
│   ├── mcp/          MCP 服务（3 个只读工具，stdio + Streamable HTTP）
│   ├── tests/        契约测试（node:test）
│   └── evidence/     MCP 验证证据
├── knowledge/        ★ 知识库（26 来源 / 1757 段 / 63 节点 226 边 / 38 页 Wiki）
│   └── SOURCE_LICENSES.md  26 份来源的许可矩阵与再分发口径
├── DATA_BOUNDARY.md  仓库里有什么 / 要重下什么 / 根本没拿到什么
├── knowledge-cn/     第二代语料（中国官方来源，采集完成、审核 0 通过）
├── knowledge-v1/     第一代语料（O*NET + ESCO，已停用）
├── pages/            按页面拆分的阅读镜像（不是构建源）
└── 赛题要求/          赛题原始材料
```

> ⚠️ `frontend/frotent/frontend2/` 和 `pages/*/code/` 是**阅读镜像**，改了不生效。

## 快速开始

需要：**Node ≥ 22.13**、**Python 3.10+**（后端只用标准库，测试用 pytest）。

```bash
# 1) 前端
cd frontend/frotent/frontend1
npm install
npm run dev                 # 默认 http://localhost:3000

# 2) 后端（另开一个终端，同样在 frontend/frotent/frontend1 下）
python backend/run.py       # 默认 http://127.0.0.1:8000
                            # 可用 --port / --host / BACKEND_PORT 覆盖

# 3) 前端请求的 API 基地址（缺省已指向本机 8000）
#    如需改动：NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

MCP 服务（可选）：

```bash
npm run mcp                 # stdio
npm run mcp:http            # Streamable HTTP，默认 127.0.0.1:8787
```

## 一条命令验证「三端联动」

知识库不是放着看的，三端都读**同一份导出** `knowledge/exports/career-graph.json`：

```bash
cd frontend/frotent/frontend1
npm run e2e                             # ★ 端到端：一次跑完整条链，169 条断言，约 10 s（全离线）
python -m pytest backend/tests -q      # 后端：130 项（17 图谱/契约 + 19 记忆库 + 20 LLM/触发器 + 14 对话 + 12 成长记录 + 18 简历 + 5 画像落库 + 5 访客会话 + 6 成长确认 + 14 路径引擎）
npm test                                # 前端：12 项，验证图谱导出→视图模型
npm run mcp:verify                      # MCP ：stdio 链路，断言与网页同源
npm run mcp:verify-http                 # MCP ：HTTP 链路，含「两条链路 tools/list 逐字相同」
npm run e2e:llm                         # 可选：真调模型端点（模型版触发器 + 真实对话），约 32 s
```

`npm run e2e` 是**唯一一条覆盖整条链**的命令：原始快照 sha256 → 分块/图谱/索引自洽 →
重跑 09/10/11 构建闸门（语义投影必须逐字不变）→ BM25 检索基线不退化 →
真起 **python 后端进程**跑契约接口、记忆库全生命周期、真实对话注入与成长记录→候选 →
真起 **MCP HTTP 进程**把三个工具各调一次 →
断言后端与 MCP 报的 `generatedAt`/counts 与导出**同一份字节**、每条 citation 都能指回真实 chunk →
最后拉起前端 dev server 验证 SSR 页面。默认档**显式关掉模型**（`CAREER_LLM_DISABLED=1`）以保证
「5–10 秒全绿、不依赖外部端点」；要看模型那一档用 `npm run e2e:llm`。
证据落 `frontend/frotent/frontend1/evidence/knowledge-e2e.json`。

```bash
npm run e2e:quick    # 跳过前端 dev server（CI/无 GUI 环境）
npm run e2e:all      # 再叠上 pytest / npm test / MCP verify / 题集硬规则校验
npm run e2e:llm      # 再真调一次模型生成记忆触发器（约 30 s，默认不跑）
npm run demo         # ★ 交付演示：9 步走完「画像→推荐→依据→路径→行动→记录」，真起后端、可复算
```

后端与 MCP 都不需要额外数据准备——导出文件已在仓库里。

### 模型端点：产品与评测共用一份配置

记忆库的写时触发器、以及评测的重排/裁判档，**读的是同一组变量**：

```
CAREER_LLM_BASE_URL / CAREER_LLM_API_KEY / CAREER_LLM_MODEL   ← 优先
DEEPEVAL_BASE_URL   / DEEPEVAL_API_KEY   / DEEPEVAL_MODEL     ← 其次（含 knowledge/eval/.env）
CAREER_LLM_DISABLED=1                                          ← 显式关闭（离线/测试）
```

统一实现在 `frontend/frotent/frontend1/backend/llm.py`，评测脚本经 `knowledge/eval/_shared_llm.py` 复用。
配不上时：记忆触发器自动降级为规则版并把原因写进 `generated_by` 与 `GET /health` 的 `llm` 块，
评测脚本直接报错——两条路都不会静默给错数。

## 文档索引（哪份是现行）

| 文档 | 用途 |
|---|---|
| **`交付验收对照表.md`** | ★ 交付时逐条核验：三条验收标准 → 实测结果 → 证据文件 → 复算命令 → 留白项 |
| **`IMPLEMENTATION_PLAN.md`** | ★ 现行开发计划（任务 + 验收标准） |
| **`项目架构与技术文档.md`** | ★ 架构与实测事实总览（含与概要介绍的差异清单） |
| **`knowledge/README.md`** | ★ 知识库怎么跑、哪些跑得了、数据边界 |
| **`knowledge/SOURCE_LICENSES.md`** | ★ 26 份来源的许可矩阵与再分发口径（含 3 份 AGPL-3.0 单独说明） |
| **`DATA_BOUNDARY.md`** | ★ 仓库里有什么、什么要重下、什么根本没拿到 |
| **`记忆系统整合方案.md`** | ★ 记忆库整合了什么、没整合什么、原文材料哪些说法不可引用 |
| `PAGE_FUNCTION_MAP.md` | 每个页面当前真实行为与缺口 |
| `frontend/frotent/frontend-backend-page-contract.md` | 逐页前后端接口契约 |
| `knowledge/evaluations/系统知识库构建方法与效果评估报告.md` | 知识库方法与效果（最全的一份） |
| `knowledge/evaluations/实验总表.md` | 所有实验的一页索引（含不可复算的历史结论） |
| `knowledge/evaluations/语言自适应融合.md` | M2-5：单路向量 vs 融合 vs 查询自适应门控（dev 选型 / 新 holdout 报数，零 LLM 可复算） |
| `DEMO_PROTOTYPE_PLAN.md`、`FRONTEND_MVP_FEATURE_LIST.md` | 早期范围规划，**仅供参考** |

## 跑得通 / 跑不通（先看这个，少走弯路）

**开箱就能跑通**（克隆后只需 `npm install`）：

| 命令 | 结果 |
|---|---|
| `npm run e2e` | **169 条断言全绿、约 10 s、全离线**（阶段 A–G） |
| `npm run e2e:all` | 169 + 既有套件（pytest 130 / node:test 12 / MCP 双传输 / 题集硬规则） |
| `python -m pytest backend/tests -q` | **130 项**（含记忆库、对话、成长记录、简历、画像落库、会话、成长确认、路径引擎） |
| `npm test` | 12 项（导出 → 视图模型） |
| `npm run mcp:verify` / `mcp:verify-http` | 三个工具真实往返，两条传输 `tools/list` 逐字一致 |
| `node knowledge/pipeline/09..11*.mjs` | 构建闸门 **14 项校验，硬约束 13/13**；图谱类产物重跑只差时间戳 |
| `node knowledge/pipeline/import/bridge-coverage.mjs` | 桥接覆盖率 + 「按名字机械对齐」实测（M2-6 的证据，只读、全离线） |

**需要额外条件**（不满足也能跑，只是走降级档）：

| 能力 | 需要什么 | 没有会怎样 |
|---|---|---|
| 记忆触发器「模型版」 | `knowledge/eval/.env` 里的端点与 key | 自动降级规则版，`generated_by=rule-based-fallback` 并写明原因 |
| 重排 / 裁判档评测 | 同上 | 脚本直接报错，不落缓存、**不输出假数** |
| 稠密向量档 | 本机 TEI 兼容服务 `127.0.0.1:8090` | 只剩 BM25 档 |
| 原始快照自洽（阶段 A） | `knowledge/raw/`（已入库） | 若把该目录移出分发物，阶段 A 会失败 —— 这是设计，不是 bug |
| **简历 PDF 解析** | 本机装任一 PDF 库（`pypdf` / `PyMuPDF` / `pdfminer`） | PDF 解析是**可选能力**，不是硬依赖；装了就能解析，一个都没装才回 415 + 安装建议 |
| O*NET / 大典外部层重建 | 外网（`knowledge/import/raw/` 不入库，116 MB） | 外部层用现成 `import/build/*.jsonl` 即可，不必重下 |

**跑不通的**（做不了就是做不了）：

- **扫描件 / 图片简历、`.doc` 老格式** → `415` + 可执行建议（无 OCR，不假装识别）。
  （**PDF 不再属于「跑不通」**：本机装了 pypdf / PyMuPDF / pdfminer 任一就能解析，见上表；一个都没装才回 415 并给安装命令。）
- **`/api/auth/register`、`/login`** → `501`。本项目**不存账号密码**，访客会话足以支撑单用户使用。
- **WeKnora 标准版对照** → 5 个镜像 2.72 GB，本机 Docker 拉不动。**不影响功能**：对照已用 Lite 版完成。
- **中国官方语料已进检索链路（2026-10-01）** → 采集与闸门都完成后，1519 条已导入 WeKnora Lite 并**逐条核验**：
  6 份文档 → **2167 个 chunk**，记录 ID **1519/1519 全部命中、0 缺失**，每个 chunk 都有 1024 维向量，活检索有命中。
  审核状态仍是**业主豁免**（`WAIVED`，**不是人工审核通过**）。核验可复现：
  `python knowledge-cn/verify_import.py --db knowledge-v1/weknora-src/data/weknora-cn.db --kb <kb-id>`。
  ⚠️ 检索**质量**尚未评测 —— 旧题集（24 题）是围绕职业图谱出的，不适用于这批官方语料，需要重新出题。
- **「在招岗位」（契约外的未来能力）** → 目前没有数据源，且**「用猎聘」这条路已查清、不通**，别再重复找：
  - `Viy1204/liepin-cli`（`@viyzhu/liepin-cli`）是**猎聘招聘者端（lpt.liepin.com）**工具 —— `search` 搜的是
    **候选人**、`joblist` 是招聘方自己发的职位，不是求职者端的岗位搜索；且要 Node ≥20 + 本机 Chrome +
    **招聘者账号扫码登录**，猎聘**封境外 IP**（须关代理直连），无头模式会被判「账号行为异常」。
  - 队友 `mcp-server` 包里的 `providers/liepin.py` 假设的命令 `liepin-cli job search --job-name … --output json`
    **在真实 CLI 里不存在**（真身是 `liepin search <关键词> --city …`），且该包默认 `MCP_ALLOW_DEMO=true`，
    拿不到就返回 `DEMO-1 / Demo company` 的**假岗位** —— 与本项目纪律冲突，未采用。
  - 注意：**这件事不影响职业匹配**（见下），职业匹配根本不需要岗位数据。
- **大典 / O*NET 外部职业库「接回」自建图谱** → **定不出机械规则**（实测：自建 127 个词条 vs 大典 1,676 个职业命中 **0**、vs 18,552 个职业功能/技能命中 1、vs O*NET 1,253 条命中 1）；
  `knowledge/import/README.md` §8 要求 `aligned_with` 必须由人写下 `alignment` 与理由。机器只能给证据
  （`node knowledge/pipeline/import/bridge-coverage.mjs`）：覆盖率 4/1676 = **0.24%**，人力上限 27.5%。**不做假的跨库合并。**

## 已知坑

1. **`frontend/frotent/frontend2/`、`pages/*/code/` 是阅读镜像**，改了不生效 —— 唯一构建源是
   `frontend/frotent/frontend1/`。
2. **Windows + Git Bash 下没有 `pkill`**：清理残留进程用
   `netstat -ano | grep LISTENING` 找 PID 后 `taskkill //PID <pid> //T //F`。
3. **端口上可能叠着多个残留 listener**：`ManagedProcess` 之类只管住父进程，python 子进程会活下来。
   表现是「改了源码、重启了服务，行为却没变」（请求被旧代码进程接走）。验证前先确认端口上只剩一个。
4. **单元测试必须设 `CAREER_LLM_DISABLED=1`**（`backend/tests/conftest.py` 已默认设）：
   否则 `knowledge/eval/.env` 里的真 key 会让测试真的联网，慢且不可复现。
5. **`CAREER_MEMORY_DB` 决定落地文件**，缺省 `backend/career.db`，测试用 `:memory:`。
   跑后端前若想保持工作树干净，把它指到临时路径。
6. **CJK 输出**：Windows 控制台需要 `PYTHONIOENCODING=utf-8` + `python -X utf8`，否则中文乱码/报错。
7. **`git` 在 Windows 上会提示 LF→CRLF**，属正常，不影响断言（e2e 按语义投影比对）。
8. **`publishedAt` 只有 3/26 有值**（S08/S09/S10，取自页面 `schema.org Article.datePublished`）：不是漏填 ——
   另外 21 份快照里确实没有页面级日期，**不拿采集日期顶上**。另新增 `sourceUpdatedAt`（页面最后更新，5/26：
   S08–S12），两者是两件事，引用输出会分别说明。补日期用
   `node knowledge/pipeline/13-annotate-source-dates.mjs`（离线、幂等；正式解析 JSON-LD 而不是正则抓裸日期）。
9. **向量档要让语料缓存与端点同源**：`run_adopted.py` / `vector_retrieval.py` / `refusal_eval.py`
   的缓存名由 `TEI_MODEL` 决定，未设时取 **float32** 那份，而现役服务是 **int8/onnx** ——
   「float32 语料向量 × int8 查询向量」会让同一题集差 1–3 题。复算前
   `export TEI_MODEL=Qwen3-Embedding-0.6B-onnx-int8`（未设时脚本会打印警告）。

## 当前已知缺口（摘要）

- **数据持久化已补齐（M1-1）**：**记忆库 / 成长记录 / 画像 / 证据 / 成长事件全部落同一个 SQLite**（`backend/memories.py`，默认 `backend/career.db`，可用 `CAREER_MEMORY_DB` 覆盖：`memory_items` / `memory_triggers` / `growth_records` / `profiles` / `profile_evidence` / `growth_events`）。后端重启后画像读得到（单测 `test_profile_survives_new_api_instance` 钉住）；**成长档案页的时间线仍在前端内存**，刷新即丢
- **记忆已真正注入对话**：`POST /api/chat` 把已确认记忆（persona 常驻 + 联想召回）与图谱事实拼成**固定可审计前缀**再回答；配了模型就用模型（本机实测 6–10 s），没配或调用失败**降级为规则版**并回传 `provider` / `llm.error`（不报 5xx、不假装有模型）。`GET /api/memories/context` 仍是同一份注入的**预览**。触发器也有两条路：写路径走**规则版**（快、确定），点「重建触发器」走**模型版**（实测约 24 s，失败自动降级且标明原因），详见 `记忆系统整合方案.md`
- **成长记录可写入记忆库（M1-3 已落地）**：`POST /api/growth-records` 把一条记录投影成**待确认**记忆候选（只取图谱已知的职业/技能名，认不出就不写）；`POST /api/growth-records/confirm` 把候选确认为正式记忆，**候选 → 记录 → 证据 → 事件一个事务写入**，重复 confirm 幂等、越权（拿别的记录的候选）直接 400
- **路径引擎已实现（M1-4）**：`POST /api/v1/career-path/generate` 由图谱 `requires` / `prerequisite` 边做拓扑排序算出阶段与缺口，模型不参与；同输入两次输出完全相同（时钟可注入）；未知职业 404。`/path` 页不再硬编码 `AI001` / `SK215=3`，改读已确认画像且每周小时数可调（M1-6）
- **五个入口全通（M1-5）**：`/work-map`、`/catalog` 由 redirect 改挂真组件（都是 200 + SSR 出内容）；`/actions` 仍只有"筹备中"（职场模拟，尚未排期）
- **简历解析已实现（文本 / DOCX / PDF）**：`POST /api/resumes/extract` 真解析文件（DOCX 用 stdlib `zipfile`；PDF 走运行时探测的 pypdf / PyMuPDF / pdfminer），产出**画像草稿 + 待确认记忆候选 + 画像证据**，每条抽取都带原文 `charRange`；**图片与 `.doc` 明确 415**（无 OCR），扫描件 PDF 与乱码 PDF 也分别明确报错，不假装解析。`GET /health` 的 `resume.pdfBackend` 会报当前用的是哪个后端
- **访客会话已实现（M1-2）**：`POST /api/auth/guest` 返回 201 + `HttpOnly` 会话 Cookie，带 Cookie 的请求按 `user_id` 隔离画像/记忆/成长记录；无 Cookie 或 Cookie 非法一律回落 `user_local`（本机单用户形态照常可用）。`register` / `login` **仍是 501**：本项目不存账号密码
- **未实现接口一律 501**（不假装可用）：**只剩** `/api/auth/{register,login}`（本项目不存账号密码）。
  职业匹配 `/api/career-matches/*` **已实现**（2026-09-30）：排序依据全在图谱（requires 边带
  importance/targetLevel）与已确认画像里，四维打分 + 逐条依据 + 差距/待验证问题，见 `backend/career_match.py`。
- **知识库**：`publishedAt` 0/26；中文占比 46.4%（中文字符口径）；60% 图谱标注未人工复核（174/289）。
  **检索**：跨语言层已解（M2-5 查询自适应门控，dev 29→31/34、新 holdout 26→27/35，见 `knowledge/evaluations/语言自适应融合.md`）；
  **外部库接不回自建层**（M2-6：桥接覆盖 4/1676 = 0.24%，定不出机械规则、只能人工裁定，与 M2-1 同类门禁）
- **素材可复现性**：raw 快照 sha256 实测 **20/25 与登记值一致**，5 份不一致（`S15`/`S18`/`S19`/`S20`/`S22`，成因未定，详见 `knowledge/README.md`）

## 数据与密钥

- **`knowledge/eval/.env` 含 API key，不入库**（模板见 `.env.example`）。
- 大体积原始数据不入库：`knowledge/import/raw/`（111MB）、`knowledge-v1/data/`（403MB）、`knowledge-cn/data/sources/`（38MB）、向量缓存。
- 第三方源码克隆（WeKnora、Tabiya 数据集）不入库，用官方仓库还原。
