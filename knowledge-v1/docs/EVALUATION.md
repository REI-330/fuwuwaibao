# 知识库评估报告

## 验收指标
- 数据量：>= 10000 条有效知识；重复率 < 5%
- 来源覆盖：>= 95% 条目有 URL、许可证和更新时间
- 检索：Top-5 命中率 >= 80%，Top-10 召回率 >= 90%
- 图谱：职业—技能关系有效率 >= 95%，孤立职业 < 10%
- 引用：回答引用来源覆盖率 >= 95%

## 测试集
建立至少 100 条人工标注问题，覆盖职业搜索、技能差距、学习资源、路径规划和 FAQ；每题标注期望实体、来源和可接受答案。

## 当前状态
已完成第一版目录、统一条目模型、标准化脚本、WeKnora 接入设计和验收指标。实际数据下载、导入和在线检索需在部署 WeKnora 后执行，并记录版本、命令和结果。

## 已验证
- O*NET 31.0 下载脚本成功，文件已保存于 data/onet.zip，ZIP 格式校验通过。
- normalize.py 与 evaluate.py 已用样例执行，keyword_hit_rate=1.0。

## O*NET 实测
- 已从 O*NET 31.0 Occupation Data 与 Essential Skills 转换 19,216 条记录，输出 data/onet-knowledge.jsonl。
- 数据规模指标达标（>=10,000）。
- 中文问题对英文 O*NET 数据的关键词命中率为 0，需在后续步骤加入 ESCO 中文包与中文官方资料/术语映射。

## ESCO 实测
- 从 Tabiya ESCO v1.1.1 开源 CSV 转换 16,903 条职业/技能记录。
- 与 O*NET 合并后 knowledge-v1.jsonl 共 36,119 条记录，超过万条目标。
- 当前评估问题为中文关键词，英文/多语言术语映射尚未加入，因此命中率不能代表最终效果。

## 关系图谱实测
- ESCO occupation_skill_relations.csv 转换 123,788 条 requires_skill 关系记录。
- 合并后 knowledge-v1-with-relations.jsonl 共 159,907 条记录，可用于 WeKnora GraphRAG 导入。

## 导入适配实测
- 159,907 条记录已切分为 32 个 WeKnora 批次，manifest.json 已生成。
- 每批不超过 5,000 条，适合批量上传与失败重试。
