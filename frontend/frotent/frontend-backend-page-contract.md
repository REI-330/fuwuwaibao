## 1.页面总览

| 前端路由 | 页面用途 | 页面代码 | 主要实现组件 | 当前数据状态 |
| --- | --- | --- | --- | --- |
| `/` | 根入口，跳转 `/auth` | [`app/page.tsx`](../app/page.tsx) | — | 无数据 |
| `/auth` | 登录、注册、体验账号入口 | [`app/(entry)/auth/page.tsx`](<../app/(entry)/auth/page.tsx>) | `EntryBrand` | 仅体验账号接口已接入 |
| `/onboarding` | 建立、预览并确认初始画像 | [`app/(entry)/onboarding/page.tsx`](<../app/(entry)/onboarding/page.tsx>) | — | 手填画像已接入；简历解析为前端演示 |
|                   |                                      |                                                              |                         |                                           |
| `/path` | 个性化成长路径、技能差距、路径评估 | [`app/(product)/path/page.tsx`](<../app/(product)/path/page.tsx>) | — | 路径生成已接入，用户能力输入仍为硬编码 |
| `/actions` | 职场模拟（模拟场景入口） | [`app/(product)/actions/page.tsx`](<../app/(product)/actions/page.tsx>) | `/api/v1/interview-skills`、`/api/v1/interviews*`、`/api/v1/cross-role/*`、`/api/tasks*`（在子页面调用） | 入口卡片；三条真链路见 §6 |
| `/growth` | 用户画像总览、岗位推荐和市场信息 | [`app/(product)/growth/page.tsx`](<../app/(product)/growth/page.tsx>) | — | 画像和推荐已接入；部分个人/市场数据为占位 |
| `/growth-records` | 成长变化、行动结果和证据档案 | [`app/(product)/growth-records/page.tsx`](<../app/(product)/growth-records/page.tsx>) | `ProfileProvider` | 仅前端会话内存，刷新丢失 |
| `/chat` | 兼容入口，跳转到 `/growth?chat=open` | [`app/(product)/chat/page.tsx`](<../app/(product)/chat/page.tsx>) | 全局 `ChatConversation` | 对话接口已接入；候选画像仅前端内存 |
|                   |                                      |                                                              |                         |                                           |

## 2. `/auth` 登录、注册和体验账号

代码位置：

- 页面：[`app/(entry)/auth/page.tsx`](<../app/(entry)/auth/page.tsx>)
- 请求：[`lib/client/profile-api.ts`](../lib/client/profile-api.ts)
- 后端：[`backend/app/api/auth.py`](../backend/app/api/auth.py)

### 页面需要的数据

当前页面首屏不请求后端数据。

### 用户输入

- 登录：`email`、`password`、保持登录勾选。
- 注册：`name`、`email`、`password`、协议勾选。
- 体验账号：无输入，默认显示名为“体验用户”。

### 当前实际提交

登录和注册按钮当前都没有提交邮箱和密码，只把姓名或邮箱前缀作为 `displayName` 调用体验账号接口：

`POST /api/auth/guest`（**已接入，且后端已于 2026-09-30 实现**）

```json
{
  "displayName": "周同学"
}
```

后端返回 `201`，并设置会话 Cookie（`career_session=user_<12hex>`；`HttpOnly; SameSite=Lax; Max-Age=2592000`）：

```json
{
  "requestId": "uuid",
  "data": {
    "user": {
      "userId": "user_xxx",
      "displayName": "周同学",
      "isGuest": true,
      "createdAt": "2026-09-11T08:00:00Z"
    }
  },
  "error": null
}
```

### 后端待补

**`/api/auth/guest` 已实现**（2026-09-30，M1-2）：返回 201 + `HttpOnly` 会话 Cookie，带 Cookie 的请求按 `user_id` 隔离画像/记忆/成长记录；无 Cookie 或 Cookie 值非法一律回落 `user_local`（不返回 401）。`register` / `login` **仍是 501** —— 本项目不存账号密码。

正式登录/注册若要落地，至少需要：

- `POST /api/auth/register`：接收 `displayName`、`email`、`password`、`agreementAccepted`，返回用户和会话。
- `POST /api/auth/login`：接收 `email`、`password`、`rememberMe`，返回用户和会话。
- 找回密码接口。

新增后端接口后，前端也必须修改，当前表单不会自动使用它们。

## 3. `/onboarding` 初始画像

代码位置：

- 页面：[`app/(entry)/onboarding/page.tsx`](<../app/(entry)/onboarding/page.tsx>)
- 请求：[`lib/client/profile-api.ts`](../lib/client/profile-api.ts)
- 类型：[`types/contracts/profile.ts`](../types/contracts/profile.ts)
- 后端：[`backend/app/api/profile.py`](../backend/app/api/profile.py)

### 页面需要的数据

当前页面不读取已有画像，初始表单数据来自页面内部。若要支持用户返回继续编辑，建议进入页面时调用 `GET /api/profile` 并回填。

页面维护以下字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `identity` | string | `在校生`、`应届生`、`职场新人`、`转型探索中` |
| `school` | string | 学校/毕业院校，选填 |
| `major` | string | 专业或行业背景，选填 |
| `grade` | string | 年级、毕业届别或工作年限，选填 |
| `careerStage` | string | 当前岗位或状态，选填 |
| `skills` | string | 逗号、顿号、分号或换行分隔的技能，选填 |
| `experience` | string | 经历文本，允许用分隔符拆成多条，选填 |
| `directions` | string | 兴趣/职业方向，选填 |
| `location` | string | 当前或意向城市，选填 |
| `question` | string | 当前最想解决的问题，选填 |
| `source` | string | `manual` 或 `resume` |

### 用户提交与后端返回

用户点击“确认并开始探索”时，前端连续发两个请求。

第一步：`PUT /api/profile`（**已接入**）

```json
{
  "identity": "在校生",
  "school": "浙江大学",
  "major": "自动化",
  "grade": "大三",
  "careerStage": "在校学习",
  "skills": "C语言、STM32、Python",
  "experience": "传感器采集项目；视觉识别小车",
  "directions": "嵌入式开发、边缘AI",
  "location": "杭州",
  "question": "希望确认更适合哪个方向",
  "source": "manual"
}
```

后端将身份映射为英文枚举，将技能、经历、方向标准化，并返回：

```json
{
  "requestId": "uuid",
  "data": {
    "profile": {
      "userId": "user_xxx",
      "profileVersion": 1,
      "status": "draft",
      "identity": "student",
      "school": "浙江大学",
      "major": "自动化",
      "grade": "大三",
      "graduationYear": null,
      "location": "杭州",
      "careerStage": "在校学习",
      "currentGoal": "希望确认更适合哪个方向",
      "interests": ["嵌入式开发", "边缘AI"],
      "skills": [
        { "name": "C语言", "level": "unknown" },
        { "name": "STM32", "level": "unknown" }
      ],
      "experiences": [
        {
          "experienceId": "experience_1_1",
          "type": "project",
          "title": "传感器采集项目",
          "description": "传感器采集项目"
        }
      ],
      "candidateOccupationIds": ["embedded", "edge-ai"],
      "source": "manual",
      "updatedAt": "2026-09-11T08:00:00Z"
    }
  },
  "error": null
}
```

第二步：`POST /api/profile/confirm`（**已接入，无请求体**）

返回同一个 `profile` 结构，其中：

- `status` 变为 `confirmed`；
- `updatedAt` 更新；
- `profileVersion` 当前不会因确认再次递增。

补充读取：`GET /api/profile`（**已接入，`/growth` 正在使用**）返回 `{ data: { profile } }`。

### 简历解析（`POST /api/resumes/extract`，**已实现 2026-09-29**）

`app/(entry)/onboarding/page.tsx` 的「开始提取」现在**真的**把文件交给后端（不再有 760ms 演示数据）：

- 入口一：`multipart/form-data`，字段 `file`，上限 10MB；
- 入口二：JSON `{"text": "..."}`（粘贴文本）；两条都可加 `?generator=rule-based` 强制不调模型。

后端只产出**画像草稿 + 待确认记忆候选**，不写正式画像；页面拿到草稿后再走 `PUT /api/profile`（`source: "resume"`），用户复核后才 `POST /api/profile/confirm`。

能力边界（写清楚是设计纪律，不是临时限制）：

| 形态 | 行为 |
| --- | --- |
| `.docx` | Python 标准库 `zipfile` + `xml.etree` 读 `word/document.xml`，**不需要第三方依赖** |
| `.txt` / `.md` | 依次按 UTF-8 → GB18030 解码 |
| `.pdf` | **解析**（后端运行时探测 pypdf / PyMuPDF / pdfminer，装了哪个用哪个；都不是硬依赖）。抽不出文字（扫描件）→ `415 RESUME_EMPTY_TEXT`；读出来是乱码（字体缺 ToUnicode 映射）→ `415 RESUME_PDF_TEXT_UNREADABLE`。**两条都不是「假装解析成功」** |
| 图片 / `.doc` | **415 `RESUME_FORMAT_UNSUPPORTED`** + 可执行建议（「请粘贴文本，或另存为 DOCX」）；不假装解析、不退回演示数据（改名的 PDF 也会被魔数嗅探拦进 PDF 路径，而不是当成 DOCX 读） |

响应（实测样例）：

```json
{
  "requestId": "uuid",
  "data": {
    "resumeId": "resume_7bca384005a4",
    "profileDraft": {
      "identity": "在校生",
      "school": "浙江大学",
      "major": "自动化",
      "grade": "大三",
      "skills": ["轻量级推理引擎集成", "模型量化与部署"],
      "experience": ["模型量化部署实践"],
      "directions": "边缘 AI 工程师",
      "question": "边缘 AI 工程师",
      "source": "resume"
    },
    "skills": [
      {
        "skillId": "SK...", "nodeId": "skill:SK...", "kind": "skill", "name": "轻量级推理引擎集成",
        "evidence": { "field": "skills", "value": "轻量级推理引擎集成", "snippet": "轻量级推理引擎集成",
                      "charRange": [113, 122], "located": true }
      }
    ],
    "unrecognizedSkills": [{ "token": "Python", "reason": "图谱里没有同名词条（未写入画像技能表）" }],
    "evidence": [
      { "field": "school", "value": "浙江大学", "snippet": "浙江大学", "charRange": [59, 63], "located": true }
    ],
    "memoryCandidates": [{ "id": "memory_...", "category": "skill", "content": "具备或正在学习：轻量级推理引擎集成",
                           "status": "candidate", "sourceType": "resume", "sourceId": "resume_...:skill:0" }],
    "candidateCount": 5,
    "llmHints": { "directions": [], "highlights": [] },
    "extraction": { "module": "resume-extract/v1", "source": "text", "charCount": 241,
                    "sections": ["education", "skills", "projects", "objective"],
                    "llm": { "requested": true, "used": false, "model": null,
                             "error": { "code": "LLM_NOT_CONFIGURED", "message": "…" } } },
    "warnings": ["2 个技能词没有对应图谱词条，已列在 unrecognizedSkills 里（未写入画像）"],
    "originalFileRetained": false
  },
  "error": null
}
```

比原设计多出来的字段都有明确用途，不是装饰：

- `skills[]` 每条带 `nodeId`（指向导出的 skill 节点）与 `evidence`（`charRange` 可在原文里切出该值）；
- `unrecognizedSkills[]` 是「图谱不认识」的词（实测：`Python`、`ROS`）——**只列出、绝不写进画像技能表**，
  否则它们会进匹配算式却无依据；
- `memoryCandidates[]` 是落进记忆库的**待确认**候选（`sourceType: "resume"`，按内容指纹幂等，重复上传不重复生成）；
- `extraction.llm{requested,used,model,error}`：这次到底走了规则还是模型、失败原因是什么（模型只处理规则认不出的残差）。



## 4. 全局聊天抽屉与 `/chat`

代码位置：

- 兼容页：[`app/(product)/chat/page.tsx`](<../app/(product)/chat/page.tsx>)
- 抽屉状态：[`components/chat/chat-provider.tsx`](../components/chat/chat-provider.tsx)
- 对话界面：[`components/chat/chat-conversation.tsx`](../components/chat/chat-conversation.tsx)
- 请求：[`lib/client/chat-api.ts`](../lib/client/chat-api.ts)
- 后端：[`frontend1/backend/chat.py`](../frontend1/backend/chat.py)（队友那版 `backend/app/api/chat.py` 未移植，见根目录 `记忆系统整合方案.md` §5）

聊天不是独立产品页，而是挂在所有产品页右侧的全局抽屉。`/chat` 会跳转到 `/growth?chat=open` 并打开抽屉。

用户发送消息时调用 `POST /api/chat`（**已实现**，2026-09-29）：

```json
{
  "message": "我应该优先补哪些技能？",
  "conversationId": "conversation_xxx"
}
```

第一轮可以不传 `conversationId`（后端造一个并沿用）。如果后端返回 `401`，前端会自动创建体验账号后重试一次。
查询串可加 `?generator=auto|llm|rule-based` 强制选择生成器（与 `/api/memories/{id}/triggers` 同一套取值）。

后端返回：

```json
{
  "requestId": "uuid",
  "data": {
    "message": "...",
    "conversationId": "conversation_xxx",
    "provider": "llm",
    "injected": {
      "query": "我应该优先补哪些技能？",
      "count": 2,
      "memoryIds": ["memory_xxx"],
      "memoryHash": "sha1…",
      "summaryText": "【常驻】\n- 目标职业：边缘 AI 工程师\n【本次想起】\n- 技能：…",
      "prefixBytes": 1204,
      "memoryBlockBytes": 210,
      "promptTemplate": "career-chat/v1"
    },
    "llm": { "requested": "auto", "used": "llm", "model": "deepseek-v4.1-flash", "error": null, "elapsedMs": 6444 }
  },
  "error": null
}
```

实现口径（与本文其他小节一致：**只写真实发生的事**）：

- **注入**：已确认记忆（persona 常驻 + 联想召回，来自 `GET /api/memories/context` 的同一份 `build_context`）+ 用户画像 + 图谱事实（沿既有边算出的要求技能/缺口），拼成**固定可审计前缀**；`injected` 逐轮回传，能复核这次到底喂了什么。
- **无记忆时不改写提问**：检索用的 query 就是用户原话（只去首尾空白）；没有记忆时记忆块整块缺席，不插空标题占位。
- **降级不报错**：没配端点或调用失败时，`provider=rule-based`，回答只由图谱事实与已确认记忆拼出，错误码与说明放在 `llm.error` 里（前端在输入框上方显示"本轮未接模型"）。
- **不是 TBox**：走的是本项目自己的 OpenAI 兼容客户端（`frontend1/backend/llm.py`，与评测侧共用一份配置）；会话表在进程内保留最近 6 轮，**不落库**（跨重启的历史要等画像持久化那批）。

每条用户消息还会在前端生成一条“候选画像”，用户可修改内容、选择画像模块和信息类别，再确认写入；这些候选目前**不会提交后端**，刷新后丢失（后端的记忆候选走 `/api/memories` 与 `/api/growth-records` 两条写入通道）。聊天附件按钮也只展示说明，不上传文件。

## 5. `/path` 个性化成长路径

代码位置：

- 页面：[`app/(product)/path/page.tsx`](<../app/(product)/path/page.tsx>)
- 类型：[`types/domain/career-path.ts`](../types/domain/career-path.ts)
- 后端：[`backend/app/api/career_path.py`](../backend/app/api/career_path.py)

### 页面需要的数据

页面先调用 `GET /api/v1/occupations`，用于确认 URL 中的 `occupation` 是否有效。随后调用路径生成接口。

### 当前实际提交

`POST /api/v1/career-path/generate`（**已接入**）：

```json
{
  "target_job": "AI001",
  "current_skills": [
    { "skill_id": "SK215", "current_level": 3 }
  ],
  "weekly_hours": 10
}
```

完整可接收字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `target_job` | string | 是 | 职业 ID 或后端支持的职业名称 |
| `current_skills` | array | 否 | `{ skill_id/skill_name, current_level }[]` |
| `experience_years` | number | 否 | 工作年限 |
| `education` | string | 否 | 学历/阶段 |
| `major` | string | 否 | 专业 |
| `weekly_hours` | number | 否 | 每周可投入小时数 |
| `career_goal` | string | 否 | 职业目标 |

> **2026-09-30 更新（M1-4/M1-6）**：`POST /api/v1/career-path/generate` **已实现**。前端不再硬编码 `weekly_hours=10` 与 `AI001` 的 `SK215=3`：目标职业按 `?occupation=` → 画像 `candidateOccupationIds[0]` → 目录第一个回落，当前技能由**后端读已确认画像**补（画像里未标等级的按 1 级计入，并在 `warnings` 里明说），每周小时数可在页面上改并影响各阶段周数。生成结果还带 `warnings`、`rules_version`、`metrics_version`。

### 后端返回

接口返回 `201`，`data` 直接是 `GeneratedCareerPath`：

```json
{
  "occupation_id": "AI001",
  "target_job": "机器视觉应用工程师",
  "target_job_en": "Machine Vision Application Engineer",
  "profile_summary": "...",
  "match_score": 0.86,
  "match_type": "...",
  "weekly_hours": 10,
  "skill_gap_summary": {
    "total_target_skills": 12,
    "satisfied_count": 2,
    "improve_count": 3,
    "learning_count": 4,
    "priority_learning_count": 3
  },
  "path": [
    {
      "stage": "junior",
      "period": "0—1年",
      "goal": "...",
      "skills": [
        {
          "skill_id": "SK215",
          "name_zh": "Python 编程",
          "current_level": 1,
          "target_level": 3,
          "gap": 2,
          "importance": 5,
          "status": "priority_learning",
          "prerequisite_ids": [],
          "priority_score": 9.5,
          "prerequisite_depth": 0,
          "default_stage": "junior",
          "prerequisite_only": false,
          "primary_learning": true
        }
      ],
      "tasks": [
        {
          "task": "完成一个视觉项目",
          "required_skill_ids": ["SK215"],
          "tools": ["Python"],
          "deliverable": "代码仓库与演示",
          "evidence": [{ "type": "git_repository", "description": "代码仓库" }],
          "subtasks": ["..."],
          "source_refs": ["..."]
        }
      ],
      "estimated_hours": 40,
      "estimated_weeks": 4,
      "satisfied_ratio": 0.2,
      "compressed": false,
      "stage_skipped": false
    }
  ],
  "evaluation": {
    "path_valid": true,
    "overall_score": 88,
    "grade": "优秀",
    "metrics": {
      "prerequisite_reasonableness": 100,
      "gap_coverage": 90,
      "stage_alignment": 85,
      "personalization": 80,
      "task_skill_alignment": 90,
      "executability": 85
    },
    "hard_checks": {
      "prerequisite_cycle": false,
      "missing_skill_id": false,
      "invalid_level": false,
      "invalid_stage": false,
      "invalid_gap": false,
      "prerequisite_order": false
    },
    "workload": {
      "status": "normal",
      "stages": {
        "junior": {
          "required_weeks": 4,
          "recommended_max_weeks": 12,
          "status": "normal"
        }
      }
    },
    "warnings": [],
    "suggestions": []
  },
  "generated_at": "2026-09-11T08:00:00Z",
  "rules_version": "...",
  "metrics_version": "..."
}
```

注意：`hard_checks` 的布尔值表示“是否发现问题”，因此 `false` 才会在前端显示为通过。

页面没有路径编辑、确认、重新生成表单或安排任务的写操作。页面中的任务只展示，尚未连接 `/actions`。

## 6. `/actions` 职场模拟（模拟场景入口，2026-10-01 起已接通）

代码位置：[`app/(product)/actions/page.tsx`](<../app/(product)/actions/page.tsx>)。页面本身只渲染两张入口卡片，真实数据在子页面调用：

| 页面 | 路由 | 用到的接口 |
|---|---|---|
| 模拟面试配置 | `/mock-interview` | `GET /api/v1/interview-skills`、`GET /api/v1/interviews`、`GET /api/resumes`、`POST /api/v1/interviews` |
| 模拟面试作答 | `/mock-interview/<sessionId>` | `GET /api/v1/interviews/<id>`、`POST .../answers`、`POST .../complete` |
| 面试报告 / 历史 | `/mock-interview/<sessionId>/report`、`/mock-interview/history` | `GET .../report`、`DELETE /api/v1/interviews/<id>` |
| 跨岗位选岗 | `/scenarios/cross-role` | `GET /api/v1/cross-role/roles`、`GET/POST /api/v1/cross-role/sessions` |
| 跨岗位作答 / 报告 / 历史 | `/scenarios/cross-role/<sessionId>[/report]`、`/scenarios/cross-role/history` | `GET .../sessions/<id>`、`POST .../answers`、`POST .../complete`、`GET .../report`、`DELETE .../sessions/<id>` |
| 任务实践清单 | `/actions/tasks` | `GET /api/tasks`（支持 `?occupation=` / `?stage=`） |
| 任务实践详情 | `/actions/tasks/<taskId>` | `GET /api/tasks/<id>`、`POST /api/tasks/<id>/attachments`（multipart）、`POST /api/tasks/<id>/runs`、`POST /api/task-runs/<id>/evaluate`、`GET /api/attachments/<id>` |
| 任务深链 | `/actions?task=<taskId>` | 前端直接跳到 `/actions/tasks/<taskId>` |

口径要点（由 `backend/tests/test_interviews.py`、`backend/tests/test_cross_role.py` 与 `npm run e2e` 阶段 D/G 钉住）：

- 响应一律走本项目的 `{requestId, data, error}` 通用包。错误码：`ROLE_NOT_FOUND` 404、`ROLE_NAME_REQUIRED`/`INVALID_BODY`/`INVALID_DIFFICULTY`/`INVALID_MODE`/`OPTION_NOT_FOUND` 422、`INTERVIEW_FINISHED`/`SESSION_FINISHED`/`REPORT_NOT_READY` 409、`QUESTION_NOT_FOUND` 404。
- **本项目没有 401 分支**：用户来自会话 Cookie，无 Cookie 回落 `user_local`。队友那版有真账号体系，前端因此带「401 → 建访客会话」的重试，移植时已去掉。
- `questionSource` 取值 `llm` / `fallback`（队友写死 `tbox`）：出题走 `backend/llm.py`，端点没配或返回不是 JSON 时自动用本地备用题并在 `questionSource` 里如实标注。
- 跨岗位题库来自队友项目（32 职业 / 320 题），来源与免责声明随 `/api/v1/cross-role/roles` 一起返回，前端原样展示。

上一版遗留的 [`lib/fixtures/product-data.ts`](../lib/fixtures/product-data.ts)、[`lib/client/training-api.ts`](../lib/client/training-api.ts)（演示任务）仍保留，但**未被这三条链路使用**。

### 任务实践（2026-10-01 实现，不再是「建议」）

- `GET /api/tasks?status=planned|available|completed&occupation=<id>`：任务来自路径引擎（图谱 `task --trains--> skill` 边），
  返回 `taskId`、`title`、`sourcePath`（职业 / 阶段 / 阶段目标 / 周期）、`requiredSkills`、`tools`、
  `steps`（图谱考核点）、`deliverable`、`evidenceTargets`、`sourceRefs`、`status`；
  **难度与任务级学时图谱没有** → `null`，并出现在 `unavailableFields` 里。响应另带 `occupation`、
  `occupationSource`（`query` / `profile` / `catalog`）、`counts`、`notes`、`disclaimer`。
  未知 `status` → 400 `INVALID_STATUS`；未知职业 → 404 `OCCUPATION_NOT_FOUND`。
- `GET /api/tasks/{taskId}`：任务详情 + `runs`（历史提交，各自带 `feedback`）+ `latestFeedback`
  + `attachments` / `attachmentCount` / `attachmentNotes`（这条任务下已上传的附件，见下）；
  非法/不存在的 id → 404 `TASK_NOT_FOUND`。
- `POST /api/tasks/{taskId}/runs`：提交行动。**只写成长记录（`kind='任务行动'`）与待确认候选**，
  与记忆库那条通道同源；`requestId` 幂等（重复提交不重复写记录与候选）。返回 `run`、`growthRecord`、
  `candidates`、`candidateNote`、`created`。空 `submission` / 超长 / `attachmentIds` 不是数组或超过 20 项
  → 422 `INVALID_BODY`；`attachmentIds` 里出现**不在这条任务下、属于自己**的 id
  → 422 `UNKNOWN_ATTACHMENT` 并回 `unknownAttachmentIds`（未知 / 别人的 / 挂在别的任务下的都算，不静默忽略）。
- `POST /api/tasks/{taskId}/attachments`（**真文件上传**，`multipart/form-data`，字段名 `file`）：
  字节真的存进 `task_attachments.content`（5MB 上限、每条任务 20 个），返回 `attachment`
  （`attachmentId` / `filename` / `kind` / `byteSize` / `sha256` / `textExtracted` / `preview` /
  `previewTruncated` / `note`）、`attachmentCount`、`created`、`limits`、`attachmentNotes`。
  同一文件重复上传按幂等复用（`created:false`，不产生第二条）。
  错误：不是 multipart / 没有 `file` 字段 / 空体 → 400 `ATTACHMENT_BAD_UPLOAD`；
  超过 5MB → 413 `ATTACHMENT_TOO_LARGE`；超过每条任务 20 个 → 422 `ATTACHMENT_LIMIT`；
  任务不存在 → 404 `TASK_NOT_FOUND`。
- `GET /api/attachments/{attachmentId}`：附件元数据 + 预览（**不回原始字节**）。
  别人的 id 一律 404 `ATTACHMENT_NOT_FOUND`。
- `POST /api/task-runs/{runId}/evaluate`：返回 `report` 与 `run`。**不写任何已确认的能力结论**；
  重复调用返回同一份（幂等）。不是自己的运行记录 → 404 `TASK_RUN_NOT_FOUND`。

附件的口径（三条，都有用例钉住）：

1. **收得下就存，读不出就说读不出**：与简历解析（读不出就 415）刻意不同 —— 附件是交付物存档，
   图片 / 压缩包 / xlsx 一样收，只是 `textExtracted:false` + `note` 如实说明（图片那句明写「不做 OCR」）。
2. **附件不算能力证据**：能力闸门只认用户手写的「行动说明 / 文本成果」，附件里的文字不进评估输入 ——
   否则拿一份别人写的文档就能刷出「已具备能力」的观察。
3. **引用必须是自己的、且挂在这条任务下**：`attachmentIds` 不再是自由字符串。

提交结构：

```json
{
  "action": "先核对硬件约束，再按优先级逐项验证",
  "submission": "定位结论与验证记录……",
  "attachmentIds": [],
  "requestId": "客户端生成的 uuid（幂等键）"
}
```

提交返回（实际形状）：

```json
{
  "requestId": "uuid",
  "data": {
    "run": {
      "runId": "run_e1a2c6624b9f",
      "taskId": "task_AI001_intermediate_0",
      "title": "写 SPI 总线驱动做一次从机读写",
      "status": "SUBMITTED",
      "growthRecordId": "run_e1a2c6624b9f",
      "candidateIds": ["memory_xxx"],
      "submittedAt": "2026-10-01T12:00:00Z"
    },
    "growthRecord": { "id": "run_e1a2c6624b9f", "kind": "任务行动" },
    "candidates": [{ "id": "memory_xxx", "status": "candidate", "content": "具备或正在学习：…" }],
    "candidateNote": { "reason": "ok", "message": "…等你确认后才会进入对话与推荐" }
  },
  "error": null
}
```

> 注意：`runId` 与成长记录的 `id` 是**同一个值** —— 两边都拿它当幂等键与互指依据。

评估返回（实际形状）：

```json
{
  "requestId": "uuid",
  "data": {
    "report": {
      "runId": "run_xxx",
      "provider": "llm",
      "score": 58,
      "coverage": 1.0,
      "summary": "提交说明了…",
      "strengths": ["…"],
      "improvements": ["…"],
      "observedAbilities": [
        { "skillId": "SK090", "name": "微控制器外设驱动", "evidence": "先读手册确认 微控制器外设驱动 的寄存器布局，…" }
      ],
      "needsVerification": [{ "kind": "evidence", "name": "…", "why": "需要可核验的交付物" }],
      "disclaimer": "单次表现不构成已掌握能力；候选观察需你在记忆面板确认后才会进入对话与推荐。"
    },
    "run": { "runId": "run_xxx", "status": "EVALUATED" },
    "taskAvailable": true
  },
  "error": null
}
```

口径：`observedAbilities[].evidence` 必须是**用户原文（行动说明 + 文本成果）里逐字存在**的片段 ——
模型给的也要过这道闸，过不了就降级进 `needsVerification`（`kind` ∈ `skill|evidence|detail|steps`）；
规则版自己找到的观察按名字去重并入，**不会被模型替换掉**。

## 7. `/growth` 用户画像与岗位市场动态

代码位置：

- 页面：[`app/(product)/growth/page.tsx`](<../app/(product)/growth/page.tsx>)
- 画像请求：[`lib/client/profile-api.ts`](../lib/client/profile-api.ts)
- 推荐请求：[`lib/client/career-recommendation-api.ts`](../lib/client/career-recommendation-api.ts)
- 后端：[`backend/app/api/profile.py`](../backend/app/api/profile.py)、[`backend/app/api/career_recommendations.py`](../backend/app/api/career_recommendations.py)

页面进入时并行读取：

1. `GET /api/profile`：返回第 4 节的 `UserProfile`。
2. `GET /api/career/recommendations`：返回岗位推荐。

岗位推荐接口当前**不使用通用响应包**，应直接返回：

```json
{
  "recommendations": [
    {
      "occupation_id": "AI001",
      "occupation_name": "机器视觉应用工程师",
      "match_score": 86,
      "reason": "已有视觉项目经历；方向兴趣一致",
      "core_skills": ["Python", "OpenCV"],
      "skill_gaps": ["模型部署"],
      "salary_range": "15k-30k",
      "future_signal": "...",
      "career_path": "/path?occupation=AI001"
    }
  ]
}
```

页面展示：

- 基本信息：身份、专业、城市、兴趣；
- 学校经历；
- 项目/实习/竞赛/工作经历；
- 推荐岗位、匹配分、核心技能和推荐原因；
- 选中岗位的薪资区间、行业前景、核心能力和技能差距。

用户只会切换标签和选择查看哪个岗位，不产生写请求。

当前占位/缺失数据：

- 用户姓名固定显示“周同学”，头像固定显示“周”；性别、年龄、在校职务、奖项和核心课程没有后端字段。
- 人才需求趋势图是固定高度的布局占位，不是市场数据。
- 只有 `AI001` 有硬编码薪资 `15k-30k`，其他职业返回“暂未提供”。
- 薪资没有城市、经验范围、币种、周期、样本和更新时间，不能作为可信市场数据。

若要完成市场页，建议在推荐项或独立市场接口中返回：`region`、`experienceRange`、`salaryMin`、`salaryMax`、`currency`、`salaryPeriod`、`demandTrend[]`、`skillTrend[]`、`source`、`sourceUpdatedAt`。

## 8. `/growth-records` 成长记录档案

代码位置：

- 页面：[`app/(product)/growth-records/page.tsx`](<../app/(product)/growth-records/page.tsx>)
- 状态容器：[`components/profile/profile-provider.tsx`](../components/profile/profile-provider.tsx)
- 状态规则：[`lib/client/profile-state.ts`](../lib/client/profile-state.ts)
- 类型：[`types/view-models/dynamic-profile.ts`](../types/view-models/dynamic-profile.ts)
- 候选确认：[`components/profile/candidate-profile-card.tsx`](../components/profile/candidate-profile-card.tsx)

### 当前数据来源

本页没有后端请求。所有数据都来自 `ProfileProvider` 的 React 内存（**前端本地**），包括：

- `records`：用户已确认的画像记录；
- `taskRuns`：任务提交；
- `evidence`：画像记录与任务提交之间的证据关联；
- `events`：新增画像证据、职业方向变化、成长路径调整；
- `plannedTasks`：用户从路径安排的任务。

用户的分类、时间范围、确认状态和关键词筛选都是本地操作，不需要后端写请求。但刷新页面后全部记录会丢失。

> **已实现部分（2026-09-29）**：`POST /api/growth-records`（写一条记录，并在同一事务里把它投影成**待确认**记忆候选）、
> `GET /api/growth-records?kind=&limit=`（返回已落库的记录，每条带它派生出的候选）、
> `DELETE /api/growth-records/{id}`（删记录只清**未确认**候选，已确认的记忆归用户）。
> 档案页因此多了一个「写入记忆候选」按钮，写入后到「用户画像 → 记忆库」的待确认栏里决定是否保留。
> 请求/响应类型见 [`types/contracts/growth.ts`](../frontend1/types/contracts/growth.ts)。
> **本小节下面的 `summary` / `cursor` / 服务端分类筛选仍属【设计方案】**：页面时间线依旧来自 `ProfileProvider` 内存，
> 后端只存"被显式写入"的那些记录；`/api/growth-records/confirm`（候选→记录→证据→事件一个事务，重复 confirm 幂等、越权 400）已于 **2026-09-30 实现（M1-3）**。

### 后端应返回的数据

建议提供 `GET /api/growth-records`，支持查询参数：

- `category=ability|action|career|path`；
- `status=confirmed|recorded|pending`；
- `from`、`to`；
- `keyword`；
- `cursor`、`limit`。

返回：

```json
{
  "requestId": "uuid",
  "data": {
    "summary": {
      "eventCount": 8,
      "completedActionCount": 3,
      "confirmedEvidenceCount": 5,
      "pendingConfirmationCount": 1
    },
    "items": [
      {
        "id": "event_xxx",
        "category": "ability",
        "title": "能力基础新增一条已确认信息",
        "before": null,
        "after": "具备 Python 项目经验",
        "explanation": "用户确认了候选画像",
        "occurredAt": "2026-09-11T08:00:00Z",
        "source": "用户聊天原文",
        "status": "confirmed",
        "taskRunId": null,
        "evidence": {
          "id": "evidence_xxx",
          "level": "用户自述",
          "profileRecordId": "profile_record_xxx"
        },
        "taskRun": null,
        "impact": "该记录已进入动态画像的证据链"
      }
    ],
    "nextCursor": null
  },
  "error": null
}
```

### 候选画像确认待补

聊天、图谱和职业情境页都会生成候选画像卡。建议统一后端流程：

1. `POST /api/profile-candidates`：保存 AI/前端生成的候选信息；
2. `PATCH /api/profile-candidates/{id}`：用户修改内容和分类；
3. `POST /api/profile-candidates/{id}/confirm`：用户明确确认后，原子地创建画像记录、证据和成长事件；
4. `POST /api/profile-candidates/{id}/dismiss`：记录暂不写入。

确认请求至少包含：

```json
{
  "module": "能力基础",
  "field": "已有技能",
  "content": "具备 Python 项目经验"
}
```

后端应保证：

- 未分类候选不能确认；
- 同一候选重复确认必须幂等；
- 关联 `taskRunId` 时必须校验任务提交属于当前用户；
- 单次任务观察不能自动升级为“已掌握”；
- 画像记录、证据关联和成长事件应在同一事务中写入。

## 
