# 采集流水线

1. `collect.py` 只下载登记为 `public_document` 的官方文件，生成 SHA-256 快照。
2. `extract_pdf.py` 输出 `FACT_CANDIDATE/PENDING`，不会自动进入知识库。
3. 人工核对后，使用 `promote.py --approve` 生成可导入条目。
4. `quality.py` 检查来源健康和元数据完整性。

`discovery_required` 数据源不会被自动抓取。商业招聘源必须先登记 API/合作授权。
# 第一版复现顺序

在项目根目录执行：

```powershell
python -m acquisition.extract_pdf --pdf <snapshot.pdf> --source-id <id> --output data/candidates/<id>.jsonl
python -m acquisition.promote --candidates data/candidates/<id>.jsonl --metadata data/sources/<id>/latest.json --output data/reviewed/<id>.jsonl --reviews data/reviews/<id>.json
python -m acquisition.quality --root data
python -m acquisition.verify_release --root .
```

`--reviews` 省略时所有记录保持 `PENDING`；只有快照哈希匹配且审核字段完整的 `APPROVED` 记录才允许导入 WeKnora。
