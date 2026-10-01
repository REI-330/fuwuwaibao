# 中国职业导航知识库

以用户提供的《职业路径导航 Agent 数据来源与获取设计文档 V1.0》为需求依据。
当前处于重建阶段，旧 O*NET/ESCO 文件不在来源白名单内。

## 已取得官方快照

- 教育部 2026 本科专业目录：538,898 bytes，SHA-256 `51026248004546171620678895e991a6f0ada1ebf0de6498fe8c563873b43f11`。
- 国家统计局 2025 工资发布：490,106 bytes，SHA-256 `4ce7e6bf8fe69d52babb53f26ae55359b100f47c977c523ccf9ec630e41cf20a`。
- 职业分类大典转载附件（旧快照）：29,078,833 bytes，SHA-256 `43b86f85a730627ad0941354ccf4e32cca6140a343c1bdb5dc0b4629386ebf8d`，已降级为待核验附件。
- 职业分类大典社会公示稿：9,450,957 bytes，SHA-256 `4b3af986925b9abc2fe048d50c56b3fc7b0781e6de3fbb21f8a6b3feffebd3ce`，570 页；仅作 `DRAFT_STANDARD` 候选，不计入正式职业事实。

所有快照均通过 Clash `http://127.0.0.1:7897` 获取并记录 `health.json`、`latest.json`、内容哈希和获取时间。

## 当前可执行流程

```powershell
python -m acquisition.collect --source moe-majors-2026 --proxy http://127.0.0.1:7897
python -m acquisition.collect --source nbs-wages-2025 --proxy http://127.0.0.1:7897
python -m acquisition.collect --source occupation-2022-replacement --proxy http://127.0.0.1:7897
```

工作目录为本目录。依赖见 requirements.txt。代理仅对当前采集命令生效，不修改系统设置。教育部 PDF 的同域 HTTP 重定向会被记录为 `transport_warning`。

WeKnora 模型配置与验收顺序见 [docs/WEKNORA_MODEL_SETUP.md](docs/WEKNORA_MODEL_SETUP.md)。

## 数据边界

- 来源登记不等于已接入。`discovery_required` 来源不能启动采集。
- 企业公示系统仅作按需验证；实时岗位页面须先核验访问规则。
- 原始文件保存在 `data/sources/<id>/<sha256>.*`，元数据和健康状态单独保存。
- 原始快照、抽取候选、已审核事实和检索块分别计数，关系边不计作独立知识文档。
- FACT、STATISTIC、JOB_POSTING、MARKET_SIGNAL、REPORT_CLAIM 必须保持类型，统计口径不能混用。
- 薪资保存年度、地区、单位、样本范围；不将统计平均工资视作具体招聘薪资。
- 公开可访问不代表可以公开再分发，当前快照仅作内部研究证据。

## 已验收：导入与检索（2026-10-01）

1519 条已审核候选（业主豁免闸门 `WAIVED`）**已导入 WeKnora Lite 并通过核验**：

```powershell
# 1) 起 WeKnora Lite（必须显式加载 .env.lite，否则 DB_DRIVER 为空会 panic）
cd knowledge-v1/weknora-src
set -a; source .env.lite; set +a; ./WeKnora-lite.exe     # 监听 0.0.0.0:8080

# 2) 门禁（自带登录，不需要预先给 token）
python knowledge-cn/acquisition/check_weknora.py

# 3) 导入：**上传真实 .md 文件**，不要用 knowledge/manual
python knowledge-cn/acquisition/import_weknora.py --input data/reviewed/<x>.jsonl --kb-id <kb-id>

# 4) 核验：逐条比对记录 ID / 向量完整性 / 活检索探针
python knowledge-cn/verify_import.py --db ../knowledge-v1/weknora-src/data/weknora-cn.db --kb <kb-id>
```

核验结果（`evidence/import-verify.json`）：6 份文档 / **2167 chunk** / 记录 ID **1519 全命中、0 缺失** /
向量 2167 条（1024 维）/ 活检索有命中。

两个**踩过的坑**，照抄可省一轮：

- **不要用 `knowledge/manual`**：它建的 knowledge `file_type=manual`，不在 docparser 的
  `simpleFormats`（md/txt/csv/json）里 → 被路由到 Python **docreader**；本机 docreader 没跑
  （50051 无监听），解析会停在 processing。上传真实 `.md` 才走 Go 原生解析。
- **`/api/v1/models` 要带 token**：它挂在 `Viewer()` 后面，**无 token 返回 200 + 空数组**，
  看起来像「没配模型」。门禁脚本必须真登录。

## 尚未验收

全量中文职业/专业抽取、招聘源接入、去重合并、岗位失效策略、**本批语料的检索质量评测（需为其重新出题）**、
人工标注评估集均需后续验证。不得引用旧版万条计数或单关键词测试作为本版本验收结果。
