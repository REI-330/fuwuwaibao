# 中国职业路径导航知识库第一版评估

## 已验证

- Clash 代理 `127.0.0.1:7897` 可访问 GitHub（HTTP 200）。
- WeKnora 源码已获取，commit `6f98ff80c3185679cc16f834faf42fc662770e68`。
- Docker `29.4.3`、Compose `v5.1.3` 可用。
- 使用 Go `1.26.0` 尝试构建 Lite 后端，编译推进到 sqlite-vec CGO 阶段后失败：本机缺少 `sqlite3.h`；因此不能把本地编译二进制当作可运行服务证据。
- 后续补充 SQLite amalgamation 头文件后，Lite 二进制编译成功（`WeKnora-lite.exe` 440,828,158 bytes）。实际启动后 `GET /health` 返回 HTTP 200 `{"status":"ok"}`。
- 通过真实 `/api/v1/auth/register`、`/auth/login` 创建账号，并创建知识库 `fuwuwaibao中国职业路径知识库`（ID `b99e3d3e-01f8-4d32-9f2f-fd8d75449538`）。
- 通过真实 `/knowledge-bases/:id/knowledge/manual` 导入 1 条国家统计局官方工资知识；提交 `/knowledge/:id/reparse` 成功，但因未配置 embedding 模型，`parse_status` 仍为 `processing`，尚未宣称检索完成。
- 教育部 2026 本科目录、国家统计局 2025 工资发布、人社职业分类大典已通过代理取得并记录 SHA-256。
- 两份 PDF 已抽取 31 与 78 个页级候选，均标记 `FACT_CANDIDATE/PENDING`，未冒充审核事实。
- `python -m acquisition.quality` 实测：4 个来源、4 个 ACTIVE，1519 个待审核候选，来源元数据完整率 100%。
- 候选提升命令已验证 31 条元数据完整的转换结果；这些结果仍属于 `PENDING_HUMAN_REVIEW`，不计入生产事实或专业条目数。

## 验收门槛

P0 来源成功率 >= 95%；来源元数据完整率 >= 95%；重复率 < 5%；动态岗位具有 `published_at/last_seen_at/valid_until`；人工 100 题 Top-5 命中率 >= 80%；引用覆盖率 >= 95%；WeKnora 导入成功率 >= 99%。

## 未完成项

人社新职业、职业技能标准、公共招聘、大学生就业平台和国资委页面仍需逐源授权/接口核验；PDF 表格字段解析、职业—专业—岗位实体对齐、WeKnora Docker 启动、真实导入和人工检索集尚未完成。当前不得宣称中国万条知识已达标。

- Lite 二进制已用 SQLite amalgamation 头文件编译成功（440,828,158 bytes）；后续启动时已监听 8080，健康端点返回 HTTP 200。解析任务仍需配置 embedding/worker。


- 关键词检索接口已真实调用（知识库 ea589a6f-d173-48da-8fab-cd1dca7bba9e，查询‘2025年平均工资’），返回 HTTP 成功但 data 为空；原因是知识仍处于 processing，尚无 chunk。


- 新增 python -m acquisition.verify_release --root . 发布验收汇总：4 个来源、1519 条候选、0 条 APPROVED、0 条 eligible_for_import；只有审核清单提供快照哈希、证据定位、审核人和时间后才可导入。

- 2026-09-16 实测 GET /api/v1/models 返回空数组；因此 WeKnora 当前没有 embedding 模型，解析任务无法生成 chunks。该结论由真实运行实例接口验证，不以 mock 代替。

## 职业来源纠错（2026-09-16）
旧 occupation-2022 抽取第一页标题为附件7岗位类别编码表，完整大典与正式版本身份未证实；停止将其计入职业标准覆盖率。新 occupation-2022-replacement 经 Clash 下载 9,450,957 字节，SHA256 4b3af986925b9abc2fe048d50c56b3fc7b0781e6de3fbb21f8a6b3feffebd3ce；实测570页、559个非空候选页，封面明确为社会公示稿。记录类型 DRAFT_STANDARD，不计入正式职业事实覆盖率。此前将三来源健康和字段非空等同于内容正确的结论撤回；仍需正式版核对。

- 教育部目录新增布局解析：从官方 PDF 生成 850 条带专业代码、名称、页码和来源定位的结构化候选（data/candidates/moe-majors-2026-structured.jsonl），仍待人工审核。

- 已生成 850 条专业候选的审核模板 data/reviews/moe-majors-2026.template.json，绑定快照 SHA-256 51026248004546171620678895e991a6f0ada1ebf0de6498fe8c563873b43f11；未填写审核决定前不可导入。

- 当前运行态复核（2026-09-16）：WeKnora-lite 进程曾以 PID 32004 运行；`curl.exe --noproxy '*' http://127.0.0.1:8080/health` 返回 HTTP 200、`{"status":"ok"}`。这只证明服务存活；`GET /api/v1/models` 仍为空，故导入解析与检索验收仍未完成。
- 新增 `python acquisition/check_weknora.py` 运行态门禁：只读取真实 `/health` 和鉴权后的 `/api/v1/models`，不保存账号或 token；无模型时以非零状态退出，避免误报“已接入”。


- 质检/发布脚本现按来源登记的 mode=public_document 白名单计数，自动排除 discovery_required 的旧附件；当前发布候选为 1441 条（4 个公开快照来源，approved=0，eligible=0）。

- 2026-09-16 本机运行态探测未发现 Ollama（11434）、LM Studio/兼容服务（1234）或常用 OpenAI-compatible endpoint（8000）；因此当前没有可直接接入的本地 embedding 服务。

- 本机 Python 环境仅发现 onnxruntime，未安装 sentence-transformers/	ransformers/	orch；Hugging Face 缓存只有语音模型 aster-whisper-large-v3，没有可用 embedding 模型。故无法离线伪造或替代 WeKnora embedding。

- 2026-09-16 对用户提供的临时 OpenAI-compatible 服务进行真实验证：GET /v1/models 可访问，但返回模型均为对话模型；使用其中一个模型调用 POST /v1/embeddings 返回 MODEL_CAPABILITY_NOT_SUPPORTED。该服务不能作为 WeKnora embedding 后端。API key 未写入仓库或文档。

- 结论：需要提供真正支持 embeddings 能力的模型/服务（模型列表中应明确支持 embedding，且 /v1/embeddings 返回向量及维度）。
