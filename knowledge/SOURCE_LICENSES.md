# 来源许可矩阵（26 份来源）

**这份文件回答一个问题：仓库里这 26 份来源的内容，各自允许做什么。**
它不是法律意见，是**按各来源声明的许可做的分类**，每条都能从 `knowledge/sources/sources.json`
的 `license` 字段复算出来；凡是我们自己判断的地方（例如「快照是否随仓库分发」），都注明依据。

复算：

```bash
python -c "import json;print(*[(s['sourceId'],s['license']) for s in json.load(open('knowledge/sources/sources.json',encoding='utf-8'))['sources']],sep='\n')"
```

## 1. 事实基线

| 项 | 值 | 依据 |
|---|---|---|
| 登记来源 | **26** | `knowledge/sources/sources.json` |
| 原始快照 | **26 份 × 3 文件（`.html` / `.txt` / `.blocks.json`）+ `manifest.json` = 79 个文件，约 12 MB** | `ls knowledge/raw/` 79 项；`git ls-files knowledge/raw` 有跟踪 |
| 快照是否随仓库分发 | **是**（`knowledge/raw/` 已被 git 跟踪，不在 `.gitignore` 内） | `.gitignore` 只有 `knowledge/import/raw/`、`knowledge-v1/data/`、`knowledge-cn/data/sources/` |
| 抽取结果是否随仓库分发 | **是**（`knowledge/chunks/`、`knowledge/graph/`、`knowledge/wiki/`、`knowledge/exports/`） | 同上 |
| `publishedAt` | **26 份全为 `null`** | 公开文档站多无明确定版日期；按设计留 `null` 不猜 |

## 2. 矩阵

「再分发口径」一列写的是**按上表许可推出的结论**，不是我们的选择。

| 来源 | 发布方 | 许可 | 再分发口径 |
|---|---|---|---|
| S01 | Amazon Web Services | CC-BY-SA-4.0 | 可再分发；须署名，且**衍生作品须以同一许可（SA）提供** |
| S02 | Espressif Systems | Apache-2.0 | 可再分发；须保留版权与许可声明 |
| S03 | Espressif Systems | Apache-2.0 | 同上 |
| S04 | Espressif Systems | Apache-2.0 | 同上 |
| S05 | MindSpore（华为） | Apache-2.0 | 同上 |
| S06 | 百度飞桨 | Apache-2.0 | 同上 |
| S07 | 百度飞桨 | Apache-2.0 | 同上 |
| **S08** | **Ultralytics** | **AGPL-3.0** | **见 §3，须单独处理** |
| **S09** | **Ultralytics** | **AGPL-3.0** | **见 §3** |
| **S10** | **Ultralytics** | **AGPL-3.0** | **见 §3** |
| S11 | Software Freedom Conservancy / Selenium 项目 | Apache-2.0 | 可再分发；须保留声明 |
| S12 | Software Freedom Conservancy / Selenium 项目 | Apache-2.0 | 同上 |
| S13 | pytest（pytest-dev） | MIT | 可再分发；须保留版权与许可声明 |
| S14 | OSGeo 中国中心（pytest 中文镜像） | community-mirror | **无明示再分发许可**；见 §4 |
| S15 | TesterHome 社区 | community-forum | **无明示再分发许可**；见 §4 |
| S16 | U.S. Department of Labor / O*NET | CC-BY-4.0 | 可再分发；须署名 |
| S17 | U.S. Department of Labor / O*NET | CC-BY-4.0 | 同上 |
| S18 | 人力资源和社会保障部职业技能鉴定中心 | public-policy | 公开政策文件，可引用；详见 §5 |
| S19 | 中华人民共和国工业和信息化部 | public-policy | 同上 |
| S20 | 国家市场监督管理总局 / 国家标准化管理委员会 | public-standard | 检索入口可引用；**标准全文再分发受出版方约束**，见 §5 |
| S21 | 与非网 eefocus | vendor-media | **商业媒体内容**，无明示许可；见 §4 |
| S22 | 阿里云 | vendor-docs | **厂商文档**，无明示许可；见 §4 |
| S23 | pytest（pytest-dev）/ 中文镜像：OSGeo 中国中心 | community-mirror | **无明示再分发许可**；见 §4 |
| S24 | pytest（pytest-dev）/ 中文镜像：OSGeo 中国中心 | community-mirror | 同上 |
| S25 | pytest（pytest-dev）/ 中文镜像：OSGeo 中国中心 | community-mirror | 同上 |
| S26 | 人力资源和社会保障部 职业技能鉴定中心 | public-policy | 公开政策文件，可引用；见 §5 |

许可分布：Apache-2.0 **8**、community-mirror **4**、AGPL-3.0 **3**、public-policy **3**、
CC-BY-4.0 **2**、CC-BY-SA-4.0 **1**、MIT **1**、community-forum **1**、public-standard **1**、
vendor-media **1**、vendor-docs **1**。

## 3. AGPL-3.0 单独说明（S08 / S09 / S10，均为 Ultralytics YOLO 文档）

- **现状**：这三份来源的原文快照、分块、以及由它们支撑的技能/知识节点（如 `SK110 目标检测模型训练`、
  `SK113 模型导出与格式转换`、`knowledge:AI002:stage1`）都在仓库里，图谱边上的 `sourceRefs`
  也直接指向这三份来源的 chunk（例如 `S09#s154`）。
- **我们要说清楚的三件事**：
  1. 本项目**没有复制、修改或链接 Ultralytics 的代码**，只引用了它的**文档文本片段**作为事实依据；
  2. AGPL-3.0 的「衍生作品须以 AGPL 提供源码」约束针对的是**软件**；把文档片段作为引用保留、
     并保留其许可与署名，与把它的代码并入本项目是两件事 —— 我们做的是前者；
  3. 但**「本项目整体以什么许可发布」不能因此被默认成 AGPL**，也不能把这些文本片段
     说成本项目自己的内容。对外分发时这三份来源必须能单独识别出来（本文件 + 每条边上的
     `sourceRefs` 就是这个标识）。
- **仍然存在的现实风险**：仓库目前把这三份来源的快照**原样随 git 分发**。如果分发对象不接受
  AGPL-3.0 内容，正确做法是从分发物里**移除 `knowledge/raw/S08*`、`S09*`、`S10*`**，
  并按 `knowledge/README.md` 的方式重新抓取；抽取结果（chunks / 图谱 / wiki）里对这些来源的**引用**
  保留，但**原文段落**需一并替换为重新抓取的版本。

## 4. 无明示再分发许可的来源（community-* / vendor-*，共 **8** 份）

S14、S15、S21、S22、S23、S24、S25（+ S14 同源的镜像）。这些站点**没有给出可再分发的许可**，
版权默认归原站。

口径：

- 它们在仓库里的用途是**内部评测与检索基线**（`scopeZh` 字段写明了各自支撑什么），
- 对外分发（提交评审、公开仓库、演示材料）时应当**移除这些来源的原文快照**，
  或先取得授权；抽取结果里可以保留「引用了这条来源的哪个 chunkId」这个事实，
  但不应把大段原文直接搬进对外材料。
- `knowledge/raw/` 一旦被移出分发物，`npm run e2e` 的阶段 A（原始快照 sha256 自洽）会失败 ——
  这是**设计如此**：它在提醒你「这一份分发物里缺了原文」，而不是静默放过。

## 5. 政策与标准类来源（S18 / S19 / S20 / S26，共 **4** 份）

- 政策文件与职业分类信息属**公开可查**的行政信息，引用其条目（职业编号、技能要求）没有问题；
- **国家标准（GB/GB-T）全文**的复制与再分发受标准出版方约束，本项目只引用
  `openstd.samr.gov.cn` 的**检索入口与条目级信息**，未分发标准全文；
- 这四份来源是中文语料的主体，也是「国内岗位名称与岗位要求」这条线唯一的合法落点 ——
  语料过审（`knowledge-cn/`）仍为 0/1519，**未过审的内容不进本仓库的检索链路**。

## 6. 本项目自身代码的许可：**尚未确定**

`LICENSE` 文件**故意没有创建**。原因：给本项目选哪一个许可（MIT / Apache-2.0 / 私有 /
竞赛作品按赛题要求处理）是**作者的权利与责任**，不是实现细节 —— 替他选一个许可、
或在没有授权的情况下写一份「本项目采用 X」的声明，都属于**代签**。

本文件只做一件事：把 26 份第三方来源的许可事实与再分发口径摆清楚，让作者在选择本项目许可时，
能一眼看出哪些来源会与候选许可冲突（例如：若要选 MIT，则 §3 的三份 AGPL-3.0 与 §4 的八份
无许可来源必须先处理）。

## 7. 复算与自查

```bash
cd <仓库根>
# 26 份来源与许可
python -c "import json;print(len(json.load(open('knowledge/sources/sources.json',encoding='utf-8'))['sources']))"
# 快照是否在分发物里（阶段 A 会因此失败/通过）
npm --prefix frontend/frotent/frontend1 run e2e
```
