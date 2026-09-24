# fuwuwaibao 知识库第一版

## 目标
从 O*NET、ESCO 和中文官方公开资料构建万条级职业导航知识库，并输出可导入 WeKnora 的 JSONL。

## 目录
- `schema/knowledge.schema.json`：统一知识条目模型
- `scripts/normalize.py`：将 CSV/JSON/JSONL 标准化为知识条目
- `scripts/evaluate.py`：基于标注问题集计算命中率、来源覆盖率
- `docs/DESIGN.md`：设计报告
- `docs/EVALUATION.md`：评估报告与验收标准

## 快速运行
```powershell
python scripts/normalize.py --input .\sample --output .\out\knowledge.jsonl
python scripts/evaluate.py --knowledge .\out\knowledge.jsonl --questions .\sample\questions.jsonl
```

生产环境中，下载 O*NET/ESCO 后按脚本参数导入；中文官方资料要求保留来源 URL、许可证和更新时间。
