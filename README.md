# 向新 · AI 职业导航与成长伙伴

面向大学生与职场新人的职业成长系统：**职业知识图谱 + 动态画像 + 可执行的成长路径**。
不替用户决定职业，而是把「我该学什么、先学什么、凭什么这么说」变成能算、能溯源、能验证的东西。

> 本文件是**唯一的使用说明**（安装 / 运行 / 验证）。
> 架构、实测事实与已知差距见 [`项目架构与技术文档.md`](项目架构与技术文档.md)。

---

## 目录导航

```text
fuwuwaibao/
├── frontend/frotent/frontend1/   ← ★ 唯一的正式构建源，改这里才生效
│   ├── app/          页面与路由（(entry) 登录/画像、(product) 产品主体）
│   ├── components/   界面组件（chat / profile / work-map / layout）
│   ├── lib/client/   请求层、状态规则、图谱视图纯函数
│   ├── types/        前后端数据契约
│   ├── backend/      后端服务（Python 标准库，零三方依赖）
│   ├── mcp/          MCP 服务（3 个只读工具，stdio + Streamable HTTP）
│   ├── tests/        契约测试（node:test）
│   └── evidence/     端到端与 MCP 验证证据
├── knowledge/        ★ 知识库（26 来源 / 1757 段 / 63 节点 226 边 / 38 页 Wiki）
│   ├── README.md           知识库怎么跑、哪些跑得了、数据边界
│   └── SOURCE_LICENSES.md  26 份来源的许可矩阵与再分发口径（含 3 份 AGPL-3.0 说明）
├── knowledge-cn/     第二代语料（中国官方来源，已进检索链路）
├── knowledge-v1/     第一代语料（O*NET + ESCO，已停用）
├── 项目架构与技术文档.md  ★ 技术文档：架构、实测事实、已知差距
└── 赛题要求/          赛题原始材料
```

---

## 1. 环境与依赖（要装什么）

| 用途 | 需要什么 | 必需？ | 说明 |
|---|---|---|---|
| 运行前端 | **Node ≥ 22.13**（本机实测 v22.22.2） | ✅ | 在 `frontend/frotent/frontend1/` 下 `npm install` |
| 运行后端 | **Python 3.10+**（本机实测 3.12.4） | ✅ | 后端**只用标准库、零三方依赖**，无需 pip 安装即可跑 |
| 前端依赖 | `npm install`（一次） | ✅ | Next/vinext、MCP SDK、zod 等，见 `package.json` |
| 跑后端单测 | `pytest` | 仅开发 | `python -m pip install -r backend/requirements-dev.txt` |
| **模型能力**（可选） | `knowledge/eval/.env` 里的端点 + key（模板见 `.env.example`） | 可选 | 没配 → 记忆触发器/对话自动降级为规则版并注明原因 |
| **PDF 简历解析**（可选） | 任一：`pypdf` / `PyMuPDF` / `pdfminer` | 可选 | 一个都没装时 PDF 返回 415 + 安装建议 |
| 稠密向量档评测（可选） | 本机 TEI 兼容服务 `127.0.0.1:8090` | 可选 | 没起就只剩 BM25 档 |
| 重建外部职业层（可选） | 外网（`knowledge/import/raw/` 116 MB 不入库） | 可选 | 用现成 `knowledge/import/build/*.jsonl` 即可，不必重下 |

> **一句话**：克隆后 `cd frontend/frotent/frontend1 && npm install` 就能跑起来；
> 后端与 MCP 不需要任何第三方依赖，也不需要额外数据准备（导出文件已在仓库里）。

---

## 2. 快速开始

```bash
cd frontend/frotent/frontend1

# 1) 前端（默认 http://localhost:3000）
npm install
npm run dev

# 2) 后端（另开一个终端，同样在这个目录下；默认 http://127.0.0.1:8000）
python backend/run.py            # 可用 --port / --host / BACKEND_PORT 覆盖
                                 # 落地库默认 backend/career.db，可用 CAREER_MEMORY_DB 覆盖

# 3) 前端请求的 API 基地址（缺省已指向本机 8000，需改时）：
#    NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

MCP 服务（可选）：

```bash
npm run mcp          # stdio
npm run mcp:http     # Streamable HTTP，默认 127.0.0.1:8787
```

---

## 3. 验证「三端联动」（含期望数字）

知识库不是放着看的：网页、后端、MCP **三端读同一份导出** `knowledge/exports/career-graph.json`。

```bash
cd frontend/frotent/frontend1
export PYTHONIOENCODING=utf-8                 # Windows 控制台中文

npm run e2e:all        # ★ 一条命令 = 端到端 237 条 + pytest 204 + node:test 12 + MCP 双传输 + 题集硬规则
npm run demo           # ★ 交付演示：9 步走完「画像→推荐→依据→路径→行动→记录」，真起后端、可复算
```

**期望数字（最近一次实机复跑）**：

| 命令 | 期望 |
|---|---|
| `npm run e2e` | **237 条断言全绿**，约 20 s，全离线（阶段 A–H：A17 B5 C4 **D157** E23 F10 **G16** H5） |
| `python -m pytest backend/tests -q` | **204 passed** |
| `npm test` | 12 pass / 0 fail |
| `npm run mcp:verify` / `mcp:verify-http` | stdio 14/14 · HTTP 25/25（两条传输 `tools/list` 逐字相同） |
| `node knowledge/pipeline/09-integrity-check.mjs` | 14 项校验，硬约束 13/13，`verdict=pass` |
| `npm run demo` | 9 步全通过，exit 0 |

`npm run e2e` 是**唯一一条覆盖整条链**的命令：原始快照 sha256 → 分块/图谱/索引自洽 →
重跑 09/10/11 构建闸门（语义投影必须逐字不变）→ BM25 检索基线不退化 →
真起 **python 后端进程**跑契约接口、记忆库全生命周期、对话注入、成长记录→候选、画像历史快照 →
真起 **MCP HTTP 进程**把三个工具各调一次 →
断言后端与 MCP 报的 `generatedAt`/counts 与导出**同一份字节**、每条 citation 都能指回真实 chunk →
最后拉起前端 dev server 验证 SSR 页面。默认档**显式关掉模型**（`CAREER_LLM_DISABLED=1`），
保证「20 秒全绿、不依赖外部端点」；要看模型那一档用 `npm run e2e:llm`。
证据落 `frontend/frotent/frontend1/evidence/knowledge-e2e.json`。

```bash
npm run e2e:quick    # 跳过前端 dev server（CI / 无 GUI 环境）
npm run e2e:llm      # 再真调一次模型端点（模型版触发器 + 真实对话），约 30 s，默认不跑
```

### 模型端点：产品与评测共用一份配置

记忆库的写时触发器、对话、以及评测的重排/裁判档，**读的是同一组变量**：

```text
CAREER_LLM_BASE_URL / CAREER_LLM_API_KEY / CAREER_LLM_MODEL   ← 优先
DEEPEVAL_BASE_URL   / DEEPEVAL_API_KEY   / DEEPEVAL_MODEL     ← 其次（含 knowledge/eval/.env）
CAREER_LLM_DISABLED=1                                          ← 显式关闭（离线/测试）
```

统一实现在 `frontend/frotent/frontend1/backend/llm.py`，评测脚本经 `knowledge/eval/_shared_llm.py` 复用。
配不上时：记忆触发器自动降级为规则版并把原因写进 `generated_by` 与 `GET /health` 的 `llm` 块，
评测脚本直接报错 —— 两条路都不会静默给错数。

---

## 4. 跑得通 / 跑不通

**开箱就能跑通**（克隆后只需 `npm install`）：

| 命令 | 结果 |
|---|---|
| `npm run e2e` / `e2e:all` | 237 条断言全绿（`e2e:all` 再叠 pytest 204 / node:test 12 / MCP 双传输 / 题集硬规则） |
| `python -m pytest backend/tests -q` | 204 项（图谱契约、记忆库、对话、成长记录、简历、画像落库与画像历史快照、会话、路径引擎、职业匹配、模拟面试、跨岗位训练、任务实践/附件） |
| `npm test` | 12 项（导出 → 视图模型） |
| `npm run mcp:verify` / `mcp:verify-http` | 三个工具真实往返，两条传输 `tools/list` 逐字一致 |
| `node knowledge/pipeline/09-integrity-check.mjs` | 14 项校验，硬约束 13/13 |

**需要额外条件**（不满足也能跑，只是走降级档）：

| 能力 | 需要什么 | 没有会怎样 |
|---|---|---|
| 记忆触发器「模型版」/ 对话走模型 | `knowledge/eval/.env` 里的端点与 key | 自动降级规则版，`generated_by=rule-based-fallback` 并写明原因 |
| 重排 / 裁判档评测 | 同上 | 脚本直接报错，不落缓存、**不输出假数** |
| 稠密向量档 | 本机 TEI 兼容服务 `127.0.0.1:8090` | 只剩 BM25 档 |
| 简历 PDF 解析 | 本机装任一 PDF 库（`pypdf` / `PyMuPDF` / `pdfminer`） | 一个都没装才回 415 + 安装建议 |
| 原始快照自洽（e2e 阶段 A） | `knowledge/raw/`（已入库） | 若把该目录移出分发物，阶段 A 会失败 —— 这是设计，不是 bug |
| O*NET / 大典外部层重建 | 外网（`knowledge/import/raw/` 不入库） | 用现成 `import/build/*.jsonl` 即可，不必重下 |

**跑不通的**（做不了就是做不了）：

- **扫描件 / 图片简历、`.doc` 老格式** → `415` + 可执行建议（无 OCR，不假装识别）。
  PDF 不在其中：装了任一 PDF 库即可解析。
- **`/api/auth/register`、`/login`** → `501`。本项目**不存账号密码**，访客会话足以支撑单用户使用。
- **WeKnora 标准版对照** → 5 个镜像 2.72 GB，本机 Docker 拉不动。**不影响功能**：对照已用 Lite 版完成。
- **中国官方语料已进检索链路** → 1519 条导入 WeKnora Lite 并逐条核验（5 份文档 / 2612 chunk，记录 ID 1519/1519 全命中、0 缺失）。
  审核状态是**业主豁免**（`WAIVED`，不是人工审核通过）。检索质量已跑三版：现役混合检索命中@1 73.1% / @5 92.3% / @10 100% / MRR 0.819。
  仍是**作者自出题**、非业主人工判定；且超范围题分数不可分，**检索层无法拒答**。证据在 `knowledge-cn/evidence/`。
- **「在招真实岗位」（契约外的未来能力）** → 没有数据源，且**「用猎聘」这条路已查清、不通**（`liepin-cli` 是招聘者端工具，搜的是候选人、要招聘者账号扫码登录）。
  这件事**不影响职业匹配** —— 职业匹配不需要岗位数据。

---

## 5. 结构性做不了 / 留白（不要当未完成去补）

| 事项 | 为什么留白 |
|---|---|
| 学习单元 `covers` 边（图谱 knowledge→skill） | **不代签**：代码路径已实现并跑通到 63 节点/246 边，但第 06 步要求新增边进入**人工裁定抽样**（22→27），`adjudication.json` 写明「只校验与应用，不代签」→ 已回退，图谱维持 226 边 |
| 大典 / O*NET 接回自建图谱 | **定不出机械规则**：自建 127 词条 vs 大典 1,676 职业命中 0、vs 18,552 职业功能命中 1、vs O*NET 1,253 命中 1；`aligned_with` 的 `alignment` 必须由人写下。机器只能给证据（覆盖率 4/1676 = 0.24%） |
| 在招真实岗位 | 无求职者视角的真实岗位数据源；**不做假岗位** |
| 图片简历 / `.doc` 老格式 | 无 OCR，`415` + 可执行建议，不假装识别 |
| `/api/auth/{register,login}` | `501`：本项目不存账号密码，访客会话足够单用户形态 |
| 演示视频 | **按用户决定不做**：`npm run demo` 脚本能证明「你现在也能跑出同样的东西」，且随代码被回归覆盖 |
| 任务**内容**编辑 | **设计上不做**：任务由图谱 `task --trains--> skill` 边派生；用户能改的只是个人视图（备注/隐藏/顺序，走 `task_overrides` 覆盖层） |
| 聊天附件 | 明确**不解析、不上传**（设计选择） |
| 业主人工复核 gold / 大典 1665 chunk 切块扫描 | 未做（人力/成本；当前是作者自出题 + 机器复核） |

> 口径：外部数据只解决「内容从哪来」，不解决「能力有没有」。机器能算范围、能出证据，
> 但**下结论的那一笔只能是人** —— 在「不依赖人工审核」的前提下，这些就是做不到，如实留白，不替人签。

---

## 6. 已知坑

1. **唯一构建源是 `frontend/frotent/frontend1/`**（注意 `frotent` 是拼写），改别的目录不生效。
2. **Windows + Git Bash 下没有 `pkill`**：清理残留进程用
   `netstat -ano | grep LISTENING` 找 PID 后 `taskkill //PID <pid> //T //F`。
3. **端口上可能叠着多个残留 listener**：`ManagedProcess` 之类只管住父进程，python 子进程会活下来。
   表现是「改了源码、重启了服务，行为却没变」（请求被旧代码进程接走）。验证前先确认端口上只剩一个。
4. **单元测试必须设 `CAREER_LLM_DISABLED=1`**（`backend/tests/conftest.py` 已默认设）：
   否则 `knowledge/eval/.env` 里的真 key 会让测试真的联网，慢且不可复现。
5. **`CAREER_MEMORY_DB` 决定落地文件**，缺省 `backend/career.db`，测试用 `:memory:`。
6. **CJK 输出**：Windows 控制台需要 `PYTHONIOENCODING=utf-8` + `python -X utf8`，否则中文乱码/报错。
7. **`git` 在 Windows 上会提示 LF→CRLF**，属正常，不影响断言（e2e 按语义投影比对）。
8. **`publishedAt` 只有 3/26 有值**（S08/S09/S10，取自页面 `schema.org Article.datePublished`）：不是漏填 ——
   另外 21 份快照里确实没有页面级日期，**不拿采集日期顶上**。另有 `sourceUpdatedAt`（5/26：S08–S12）。
   补日期用 `node knowledge/pipeline/13-annotate-source-dates.mjs`（离线、幂等、正式解析 JSON-LD）。
9. **向量档要让语料缓存与端点同源**：缓存名由 `TEI_MODEL` 决定，未设时取 float32 那份，而现役服务是 int8/onnx
   —— 复算前 `export TEI_MODEL=Qwen3-Embedding-0.6B-onnx-int8`（未设时脚本会打印警告）。
10. **全仓 `npm run lint` 不是全绿**：`components/entry/{image-editor,profile-form}.tsx` 上有 9 个
    `no-explicit-any` / react-hooks 报错，属基线遗留、与运行无关；lint 不在 e2e 门禁里。
    改过的文件单独跑 eslint 无报错。

---

## 7. 文档索引（现行）

| 文档 | 用途 |
|---|---|
| **`README.md`** | ★ 本文件：安装依赖、运行、验证口径、跑不通与留白清单 |
| **`项目架构与技术文档.md`** | ★ 技术文档：架构、端到端实测事实、与概要介绍的差异、已知差距 |
| **`knowledge/README.md`** | ★ 知识库怎么跑、哪些跑得了、数据边界 |
| **`knowledge/SOURCE_LICENSES.md`** | ★ 26 份来源的许可矩阵与再分发口径 |
| `knowledge/evaluations/` | 知识库评测与实验记录（含可复算与不可复算的结论索引） |
| `knowledge-cn/README.md`、`knowledge-cn/docs/` | 中国官方语料的采集/导入/评测说明 |
| `frontend/frotent/frontend1/README.md` | 前端子项目说明 |
| `赛题要求/` | 赛题原始材料 |

---

## 8. 数据与密钥

- **`knowledge/eval/.env` 含 API key，不入库**（模板见 `.env.example`）。
- 大体积原始数据不入库：`knowledge/import/raw/`（111 MB）、`knowledge-v1/data/`（403 MB）、
  `knowledge-cn/data/sources/`（38 MB）、向量缓存。
- 第三方源码克隆（WeKnora、Tabiya 数据集）不入库，用官方仓库还原。
- 后端本地库 `backend/career.db` 不入库（可用 `CAREER_MEMORY_DB` 指到别处）。
