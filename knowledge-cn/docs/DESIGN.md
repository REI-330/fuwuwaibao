# 中国职业路径导航知识采集设计

本版本严格按《职业路径导航 Agent 数据来源与获取设计文档 V1.0》执行。采集层只负责取得可追溯原始证据；抽取候选、审核事实、检索文档和 WeKnora 图谱分别存放。

## 来源优先级

P0 为人社部职业标准/新职业、教育部专业目录、国家统计局、中国公共招聘网、国家大学生就业服务平台和企业真实性核验。P1 为国资委、地方公共就业、企业官网；P2 为授权商业数据和研究报告。

## 证据元数据

每份快照保存来源组织、原始 URL、落地页、来源等级、发布时间、统计期、地区、获取方法、抓取时间、最后核验时间、内容类型、SHA-256、许可说明和健康状态。HTTP 同域重定向会保留告警。

## 数据类型边界

FACT、STATISTIC、JOB_POSTING、MARKET_SIGNAL、REPORT_CLAIM 不可相互升级。公开页面不得推断可再分发；招聘数据不得绕过登录、验证码或风控；企业登记系统默认只按需核验。

## WeKnora 适配

审核后的 Markdown/JSON 条目再进入 WeKnora；关系边使用 `occupation requires_skill skill`、`major leads_to occupation`、`task validates_skill skill`。原始快照和待审核候选禁止直接导入生产知识库。
# WeKnora 运行前置条件

Lite 服务健康检查不等于知识可检索。启用向量或关键词索引的知识库前，必须在真实 WeKnora 实例中配置至少一个 embedding 模型，并通过 `GET /api/v1/models` 验证返回非空；随后导入一条已审核记录，等待 `parse_status=completed` 且 `chunks` 数量大于 0，再执行检索验收。若模型列表为空，导入只能记录为 `processing`，不得计入导入成功率或 Top-5 命中率。
