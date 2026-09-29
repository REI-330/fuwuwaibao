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
python -m pytest backend/tests -q      # 后端：16 项，验证导出→目录/推荐投影
npm test                                # 前端：12 项，验证图谱导出→视图模型
npm run mcp:verify                      # MCP ：stdio 链路，断言与网页同源
npm run mcp:verify-http                 # MCP ：HTTP 链路，含「两条链路 tools/list 逐字相同」
```

后端与 MCP 都不需要额外数据准备——导出文件已在仓库里。

## 文档索引（哪份是现行）

| 文档 | 用途 |
|---|---|
| **`IMPLEMENTATION_PLAN.md`** | ★ 现行开发计划（任务 + 验收标准） |
| **`项目架构与技术文档.md`** | ★ 架构与实测事实总览（含与概要介绍的差异清单） |
| **`knowledge/README.md`** | ★ 知识库怎么跑、哪些跑得了、数据边界 |
| `PAGE_FUNCTION_MAP.md` | 每个页面当前真实行为与缺口 |
| `frontend/frotent/frontend-backend-page-contract.md` | 逐页前后端接口契约 |
| `knowledge/evaluations/系统知识库构建方法与效果评估报告.md` | 知识库方法与效果（最全的一份） |
| `knowledge/evaluations/实验总表.md` | 所有实验的一页索引（含不可复算的历史结论） |
| `DEMO_PROTOTYPE_PLAN.md`、`FRONTEND_MVP_FEATURE_LIST.md` | 早期范围规划，**仅供参考** |

## 当前已知缺口（摘要）

- **数据不持久化**：画像在后端进程内存、成长记录在前端内存，刷新即丢
- **路径引擎未实现**：`POST /api/v1/career-path/generate` 返回 501
- **行动与训练空白**：`/actions` 只有"筹备中"；`/work-map`、`/catalog` 仍是 redirect
- **未实现接口一律 501**（不假装可用）：auth / 简历解析 / chat / career-matches / growth-records
- **知识库**：`publishedAt` 0/26；中文占比 46.4%（中文字符口径）；60% 图谱标注未人工复核（174/289）
- **素材可复现性**：raw 快照 sha256 实测 **20/25 与登记值一致**，5 份不一致（`S15`/`S18`/`S19`/`S20`/`S22`，成因未定，详见 `knowledge/README.md`）

## 数据与密钥

- **`knowledge/eval/.env` 含 API key，不入库**（模板见 `.env.example`）。
- 大体积原始数据不入库：`knowledge/import/raw/`（111MB）、`knowledge-v1/data/`（403MB）、`knowledge-cn/data/sources/`（38MB）、向量缓存。
- 第三方源码克隆（WeKnora、Tabiya 数据集）不入库，用官方仓库还原。
