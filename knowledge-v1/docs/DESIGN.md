# 知识库设计报告

## 范围
第一版覆盖职业、技能、任务、学习资源、职业路径、FAQ 和业务规则，目标规模 10000 条以上。

## 数据源
O*NET（CC BY 4.0，职业与技能基础）、ESCO（开放下载的职业技能分类）、中国官方职业/教育资料（逐条保留来源与授权）。

## 模型
每条知识包含唯一 ID、类型、正文、职业/技能关联、关系边、阶段、来源、更新时间和置信度。关系包括 requires_skill、has_task、learn_by、next_stage、similar_to。

## WeKnora 接入
将标准 JSONL 转为 WeKnora 文档导入；启用混合检索和 GraphRAG。业务后端通过检索 API 组装职业匹配、成长路径和聊天上下文。

## 合规
只使用允许再分发或明确开放的数据；禁止未经授权复制招聘网站正文。保留许可证、原始 URL、抓取时间和变更记录。

## 导入适配
make_weknora_batches.py 将统一 JSONL 切分为批量 payload（默认 5000 条/批），输出 manifest.json 与 batch-*.json，便于调用 WeKnora REST 导入接口并支持失败重试。
