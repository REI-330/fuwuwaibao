# fuwuwaibao 页面功能、联动与前端入口

本文依据当前正式前端源码 `frontend/frotent/frontend1` 整理。`frontend2` 和 `pages/*/code` 是阅读镜像，不能替代正式构建源。

## 先看完整用户链路

```text
/ → /auth
/auth → /onboarding
/onboarding → /chat → /growth?chat=open
/growth → /path?occupation=<id>
/growth-records ↔ /actions ↔ /growth
```

另外两条入口目前处于“组件已写、页面未挂载”状态：

```text
/work-map                （2026-09-30 M1-5：已挂真组件，不再 redirect）
/catalog                 （2026-09-30 M1-5：已挂真组件，不再 redirect）
```

产品页共同包裹关系是：`app/(product)/layout.tsx` → `ProductShell` → `ProfileProvider` + `ChatProvider` → `AppShell`。因此聊天抽屉、动态画像内存状态、侧边导航会跨 `/growth`、`/path`、`/actions`、`/growth-records` 共享。

## 页面总览

| Page | 路由 | 当前真实行为 | 主要联动 |
|---|---|---|---|
| 根入口 | `/` | 服务端跳转到 `/auth` | 进入认证 |
| 认证 | `/auth` | 登录、注册按钮当前都创建 guest | 跳 `/onboarding` |
| 初始画像 | `/onboarding` | 手填画像可保存；简历解析走真接口（文本/DOCX，PDF 明确拒绝） | 跳 `/work-map`、确认后跳 `/chat` |
| 动态画像 | `/growth` | 读取画像和岗位推荐，支持画像/市场两个 tab | 跳 `/path`，可打开聊天抽屉 |
| 聊天兼容入口 | `/chat` | 跳 `/growth?chat=open` | 全局聊天抽屉 |
| 工作地图 | `/work-map` | 已挂真组件：`WorkMapExplorer` 读职业/技能真数据 + `CareerMatchPanel`（对 501 显示「尚未上线」） | 可跳 `/path`、`/catalog` |
| 职业目录 | `/catalog` | 已挂真组件：`CatalogBrowser` 读 `/api/v1/occupations`、`/skills`、`/catalog/stats` | 可跳 `/path` |
| 成长路径 | `/path` | 按 occupation 参数请求路径生成 | 读取职业目录；任务计划应进入 `/actions` |
| 职场模拟 | `/actions` | 模拟场景入口：两条真链路（`/mock-interview` 模拟面试、`/scenarios/cross-role` 跨岗位沟通训练） | 提交任务并写入记忆候选 |
| 成长记录 | `/growth-records` | 使用 Provider 内存状态展示记录 | 跳 `/actions`、`/growth` |

## 1. 根入口 `/`

### 功能

- 不展示业务界面。
- 服务端直接重定向到 `/auth`。

### 联动

- `/` → `/auth`。

### 前端代码入口

- `frontend/frotent/frontend1/app/page.tsx`
- 入口函数：`Home()`，调用 `redirect("/auth")`。

### 开发要点

- 通常无需扩展；如果增加登录态恢复，可在这里根据会话决定进入 `/growth` 或 `/auth`。

## 2. 认证 `/auth`

### 功能

- 展示品牌、登录表单、注册表单和体验账号入口。
- 当前登录和注册的表单提交没有使用邮箱/密码认证逻辑，均调用 `createGuest(displayName)` 创建体验会话。
- 成功后进入 onboarding。

### 联动

- 登录成功：`router.push("/onboarding")`。
- 注册成功：`router.push("/onboarding")`。
- 体验账号成功：`router.push("/onboarding")`。
- 页面失败提示由页面本地状态显示。

### 前端代码入口

- 页面：[app/(entry)/auth/page.tsx](frontend/frotent/frontend1/app/(entry)/auth/page.tsx)
  - `AuthPage()`：页面状态、表单提交和跳转。
- 组件：[components/entry/entry-brand.tsx](frontend/frotent/frontend1/components/entry/entry-brand.tsx)
  - `EntryBrand`：入口页品牌区。
- API：[lib/client/profile-api.ts](frontend/frotent/frontend1/lib/client/profile-api.ts)
  - `createGuest()`：调用 `/api/auth/guest`。
- 类型：[types/contracts/auth.ts](frontend/frotent/frontend1/types/contracts/auth.ts)
  - `DemoUser` 等认证响应类型。
- 全局入口布局：[app/(entry)/layout.tsx](frontend/frotent/frontend1/app/(entry)/layout.tsx)

### 当前缺口

- 缺少正式 `register/login/logout/forgot-password` 前端请求和后端实现。
- 页面 API 使用 `lib/client/profile-api.ts` 的 `NEXT_PUBLIC_API_BASE_URL`，而 onboarding 页面部分请求直接写死相对 `/api`，需要统一。

## 3. 初始画像 `/onboarding`

### 功能

- 首屏提供“上传简历”和“回答基础问题”两种方式。
- 手动填写画像字段：身份、学校、专业、年级/工作年限、当前状态、技能、经历、方向、城市、当前问题。
- 简历解析**真的**走后端（`POST /api/resumes/extract`）：支持 DOCX / 纯文本 / **PDF**（PDF 走本机已装的 pypdf / PyMuPDF / pdfminer，运行时探测）；`.doc` 老格式与图片会被明确拒绝（415 + 可执行建议），图片仍可先走 `ImageEditor`，但**不做 OCR**。
- 解析后进入画像预览确认，不直接确认写入。
- 支持跳过画像进入工作地图入口。

### 联动

- `/auth` → `/onboarding`。
- 选择“上传简历” → 页面内部 `choices → resume`。
- 选择手动填写 → `choices → form`。
- 图片文件 → 打开 `ImageEditor` → 处理后的 Blob 替换原文件。
- 手动提交或简历解析 → `review`。
- 画像确认成功 → `/chat`。
- “进入未来工作地图”和“暂时跳过” → `/work-map`；M1-5 之后该页已是真页面（不再重定向 `/growth`）。

### 实际数据行为

- `ensureGuest()` 调 `/api/auth/guest`；**M1-2 之后该接口返回 201 并下发 `HttpOnly` 会话 Cookie**（按 `user_id` 隔离画像/记忆）。`ensureGuest()` 仍保留对 **501 的容忍**（当成「这个构建没有会话系统」继续，回落本机单用户 `user_local`）—— 两条路都不该让 onboarding 卡住。
- 手动提交调用 `PUT /api/profile`。
- 画像确认调用 `POST /api/profile/confirm`。
- `parseResume()` 真上传文件到 `POST /api/resumes/extract`（multipart），拿 `profileDraft` 再走 `PUT /api/profile`；后端同时生成**待确认**记忆候选，页面展示 `warnings` 与候选条数。
- `?mode=demo` 会自动创建 guest、写入一份固定演示画像并打开 review。

### 前端代码入口

- 页面：[app/(entry)/onboarding/page.tsx](frontend/frotent/frontend1/app/(entry)/onboarding/page.tsx)
  - `OnboardingPage()`：四步视图、文件选择、guest、保存和确认。
- 表单：[components/entry/profile-form.tsx](frontend/frotent/frontend1/components/entry/profile-form.tsx)
  - `ProfileForm`：手动画像字段及字段映射。
- 图片编辑：[components/entry/image-editor.tsx](frontend/frotent/frontend1/components/entry/image-editor.tsx)
  - `ImageEditor`：图片裁剪/删除区域处理。
- 预览：[components/entry/profile-review-card.tsx](frontend/frotent/frontend1/components/entry/profile-review-card.tsx)
  - `ProfileReviewCard`：确认前展示画像。
- 类型：[types/contracts/profile.ts](frontend/frotent/frontend1/types/contracts/profile.ts)
  - `UserProfile`、技能、经历等契约。

### 当前缺口

- 应改为 `multipart/form-data POST /api/resumes/extract`，并保留解析警告、文件大小和类型校验。
- 页面内的相对 `/api` 请求应统一走 `apiUrl()`，以支持独立后端端口。

## 4. 动态画像 `/growth`

### 功能

- “用户画像总览” tab：展示身份、专业、城市、兴趣技能、学校经历、项目经历。
- “岗位市场动态” tab：展示所选岗位的匹配度、薪资区间、行业前景、核心技能和技能差距。
- 岗位卡片支持查看详情。
- 没有画像或请求失败时，提供回到 onboarding 的操作。

### 联动

- 产品壳侧边导航默认进入 `/growth`。
- `/chat` 重定向到 `/growth?chat=open`，聊天是否自动打开由 `ChatProvider/AppShell` 读取 URL 的实现决定。
- 岗位详情的“查看个性化路径”使用推荐响应中的 `career_path`，通常跳 `/path?occupation=<occupation_id>`。
- 画像或推荐加载失败 → `/onboarding`。
- 顶部用户头像 → `/growth`；通知弹层从 `ProfileProvider.records` 读取本次会话记录。

### 实际请求与数据

- `GET /api/profile` → `getProfile()`。
- `GET /api/career/recommendations` → `getCareerRecommendations()`。
- 推荐接口响应直接读取 `recommendations`，不是通用 `data` 包。
- 人才需求柱状图 `chartHeights` 是固定数组，当前只是布局占位。
- 姓名、性别、年龄、职务、获奖、课程等部分字段是固定“待补充”。

### 前端代码入口

- 页面：[app/(product)/growth/page.tsx](frontend/frotent/frontend1/app/(product)/growth/page.tsx)
  - `GrowthPage()`：请求、tab、岗位选择和主要展示。
- 客户端：[lib/client/profile-api.ts](frontend/frotent/frontend1/lib/client/profile-api.ts)
  - `getProfile()`。
- 客户端：[lib/client/career-recommendation-api.ts](frontend/frotent/frontend1/lib/client/career-recommendation-api.ts)
  - `getCareerRecommendations()`。
- 类型：[types/contracts/career-recommendation.ts](frontend/frotent/frontend1/types/contracts/career-recommendation.ts)

### 当前缺口

- 市场图表需要真实趋势序列、地区、经验、币种、周期、来源和更新时间。
- 页面没有消费 `?view=成长动态` 这类旧组件链接参数，后续需要统一 URL 状态。

## 5. 聊天 `/chat` 与全局聊天抽屉

### 功能

- `/chat` 本身不是独立聊天页面，只是兼容入口。
- 真实聊天由产品壳中的 `ChatProvider` 和 `ChatConversation` 负责，显示为全局抽屉/区域。
- 支持消息列表、输入、发送、思考状态、候选画像卡片和建议问题。

### 联动

- `/chat` → `/growth?chat=open`。
- 聊天消息可以生成 `ProfileCandidate`，确认后写入 `ProfileProvider` 内存中的 records/events/evidence。
- 聊天候选确认后，成长记录页可看到本次会话记录；刷新后丢失。
- 聊天建议问题和工作地图场景可复用同一聊天上下文。

### 实际请求与数据

- `POST /api/chat`，请求 `{ message, conversationId? }`（可选 `?generator=auto|llm|rule-based` 强制生成器）。
- 收到 401 时，客户端先调用 `/api/auth/guest`，再重试聊天请求。
- `conversationId` 保存在聊天 Provider 的客户端状态中；后端也会沿用同一个 id（进程内最近 6 轮）。
- **后端已实现**（`frontend1/backend/chat.py`）：每轮注入已确认记忆 + 图谱事实，配了模型就用模型、否则降级为规则版；响应带 `provider` / `injected` / `llm`，界面在输入框上方如实显示「本轮由谁回答、注入了几条记忆」。
- 附件按钮目前只有界面行为，没有真实上传。

### 前端代码入口

- 兼容页：[app/(product)/chat/page.tsx](frontend/frotent/frontend1/app/(product)/chat/page.tsx)
  - `ChatPage()`：`redirect("/growth?chat=open")`。
- 容器：[components/chat/chat-provider.tsx](frontend/frotent/frontend1/components/chat/chat-provider.tsx)
  - `ChatProvider`、抽屉开关和消息状态。
- 对话：[components/chat/chat-conversation.tsx](frontend/frotent/frontend1/components/chat/chat-conversation.tsx)
  - `ChatConversation`：消息渲染、发送和候选卡片。
- 上下文：[components/chat/chat-context.ts](frontend/frotent/frontend1/components/chat/chat-context.ts)
  - `useChat()`。
- API：[lib/client/chat-api.ts](frontend/frotent/frontend1/lib/client/chat-api.ts)
  - `sendChatMessage()`。

### 当前缺口

- 会话、消息、候选画像和附件都未持久化到后端。
- 候选确认需要后端事务：候选、画像记录、证据、成长事件一次写入且幂等。

## 6. 工作地图 `/work-map`

### 当前真实行为

- 页面入口 `WorkMapPage()` 渲染 `WorkMapExplorer` + `CareerMatchPanel`（M1-5 挂载），不再 `redirect("/growth")`。
- 职业匹配**已实现**（2026-09-30）：`/api/career-matches/*` 不再回 501。排序依据来自图谱
  （`requires` 边带 `importance`/`targetLevel`）与已确认画像，四维打分 + 逐条依据 + 差距/待验证问题；
  未生成回 404 `CAREER_MATCH_NOT_FOUND`、画像或图谱变过回 409 `CAREER_MATCH_STALE`、
  画像太空回 409 `INSUFFICIENT_PROFILE`。**不需要任何外部岗位数据源**（契约里没有岗位/公司/薪资字段）。
- `AppShell` 侧边导航也没有 `/work-map` 链接。

### 已存在但未挂载的功能

- `WorkMapExplorer`：加载职业、技能、目录统计，构建知识图谱和职业详情。
- `KnowledgeGraph`：图谱节点选择。
- `CareerMatchPanel`：生成匹配、查看四维评分与逐条依据、查看技能差距与待验证问题、选择目标职业（**已接真后端**）。
- `CareerScenarios`：职业情境选择并生成路径或进入职场模拟。
- `CatalogBrowser`：职业/技能搜索、详情和路径跳转。

### 预期联动

- 工作地图 → `/path?occupation=<id>`。
- 工作地图 → `/actions`。
- 职业匹配要求已确认画像；未确认时 → `/onboarding`。
- 选择职业时可把职业方向写成聊天/画像候选，但当前确认写入仍是 Provider 内存。

### 前端代码入口

- 页面：[app/(product)/work-map/page.tsx](frontend/frotent/frontend1/app/(product)/work-map/page.tsx)
- 主组件：[components/work-map/work-map-explorer.tsx](frontend/frotent/frontend1/components/work-map/work-map-explorer.tsx)
- 图谱：[components/work-map/knowledge-graph.tsx](frontend/frotent/frontend1/components/work-map/knowledge-graph.tsx)
- 匹配：[components/work-map/career-match-panel.tsx](frontend/frotent/frontend1/components/work-map/career-match-panel.tsx)
- 场景：[components/work-map/career-scenarios.tsx](frontend/frotent/frontend1/components/work-map/career-scenarios.tsx)
- 目录：[components/work-map/catalog-browser.tsx](frontend/frotent/frontend1/components/work-map/catalog-browser.tsx)
- 图谱构建：[lib/client/career-graph.ts](frontend/frotent/frontend1/lib/client/career-graph.ts)
- 匹配 API：[lib/client/career-match-api.ts](frontend/frotent/frontend1/lib/client/career-match-api.ts)

### 预期接口

- `GET /api/v1/occupations`
- `GET /api/v1/occupations/{id}`
- `GET /api/v1/skills`
- `GET /api/v1/catalog/stats`
- `GET/POST /api/career-matches/current|generate`
- `POST /api/career-matches/select`

## 7. 职业目录 `/catalog`

### 当前真实行为

- 页面入口当前只有 `redirect("/growth")`。
- 目录浏览能力由 `CatalogBrowser` 提供，但当前没有从页面挂载。

### 已存在功能

- 职业关键词搜索。
- 技能关键词搜索。
- 目录统计。
- 职业详情、任务、发展信号。
- 从职业详情跳转个性化路径。

### 前端代码入口

- 页面：[app/(product)/catalog/page.tsx](frontend/frotent/frontend1/app/(product)/catalog/page.tsx)
- 组件：[components/work-map/catalog-browser.tsx](frontend/frotent/frontend1/components/work-map/catalog-browser.tsx)
- 客户端：[lib/client/http.ts](frontend/frotent/frontend1/lib/client/http.ts)
- 类型：[types/contracts/catalog.ts](frontend/frotent/frontend1/types/contracts/catalog.ts)

### 预期联动

- 职业详情 → `/path?occupation=<id>`。
- 可作为 `/work-map` 的目录子视图，避免两个入口维护重复逻辑。

## 8. 个性化路径 `/path`

### 功能

- 读取 URL 中的 `occupation` 参数。
- 先请求职业目录，确认目标职业是否有效。
- 提交路径生成请求并展示阶段、技能差距、任务、工作量和评估结果。
- 展示加载、失败、无目标职业等状态。

### 联动

- 来源包括 `/growth` 岗位卡、未挂载的工作地图/职业目录组件。
- `occupation` 来自 URL；无参数时使用页面默认目标或提示选择，具体以页面状态为准。
- 路径中的任务目前只展示，后续应跳 `/actions` 并带任务 ID。
- 顶部导航可回到 `/growth` 或 `/growth-records`。

### 实际请求与数据

- `GET /api/v1/occupations`。
- `POST /api/v1/career-path/generate`。
- 当前请求中 `weekly_hours=10`。
- `AI001` 当前技能 `SK215`、等级 3 是硬编码示例；没有自动读取确认画像。
- 使用通用 `requestJson()`，API 基础地址默认 `http://localhost:8000`。

### 前端代码入口

- 页面：[app/(product)/path/page.tsx](frontend/frotent/frontend1/app/(product)/path/page.tsx)
  - `PathPage()`：参数读取、目录校验、生成和路径展示。
- 请求工具：[lib/client/http.ts](frontend/frotent/frontend1/lib/client/http.ts)
  - `requestJson()`、`apiUrl()`。
- 路径类型：[types/domain/career-path.ts](frontend/frotent/frontend1/types/domain/career-path.ts)
- 目录类型：[types/contracts/catalog.ts](frontend/frotent/frontend1/types/contracts/catalog.ts)

### 当前缺口

- 需要把已确认画像映射为 `current_skills`、学历、专业、工作年限和目标。
- 需要保存路径快照、重新生成、编辑和任务安排。

## 9. 职场模拟 `/actions`

### 当前真实行为（2026-10-01 起不再是占位页）

- `/actions` 是**模拟场景入口**，三张卡片分别指向三条真链路：
  - **模拟面试** `/mock-interview`（列表 `/mock-interview/history`、作答 `/mock-interview/<id>`、报告 `/mock-interview/<id>/report`）；
  - **跨岗位沟通训练** `/scenarios/cross-role`（列表 `/scenarios/cross-role/history`、作答与报告同构）；
  - **任务实践** `/actions/tasks`（清单）/ `/actions/tasks/<taskId>`（详情 + 提交 + 反馈）。
- 三者都请求真接口：`/api/v1/interview-skills`、`/api/v1/interviews*`、`/api/v1/cross-role/*`、
  `/api/tasks`、`/api/tasks/<id>/runs`、`/api/task-runs/<id>/evaluate`（路由与语义见 `frontend-backend-page-contract.md` §6）。
- 前两条移植自队友项目 `career-ai-system`（差异清单见 `模拟面试与跨岗位训练移植说明.md`）；
  **任务实践是本项目自己的设计**，任务全部由路径引擎从图谱 `task --trains--> skill` 边派生。
- 支持 `?task=<taskId>` 深链：带它进 `/actions` 会直接跳到该任务详情（`/growth-records` 与旧文档都按这个写）。

### 任务实践的口径（三条，都有用例钉住）

1. **任务不臆造**：标题/交付要求/执行步骤（图谱考核点）/证据要求/出处都来自图谱；
   难度与**任务级**学时图谱没有 → 返回 `null` 并列出 `unavailableFields`；
   `estimatedHours` 只给**阶段级**投入，并在响应里说明它不是这一条任务的耗时。
2. **状态由结构决定**：`completed` = 提交过；`available` = 第一个还没做完的阶段；
   `planned` = 更后面的阶段。职业来源（`?occupation=` / 画像候选 / 目录第一个）回传 `occupationSource`，不静默回落。
3. **候选不落已确认**：提交只写「成长记录 + 待确认候选」（复用记忆库那条通道，`requestId` 幂等）；
   评估只产出反馈、观察到的能力与「仍需验证」，**不写任何已确认的能力结论**。
   模型自称的观察还要过两道闸（名字必须逐字属于任务要求能力、evidence 必须逐字出现在用户原文里），
   过不了就降级进 `needsVerification`；规则版的观察按名字去重并入，**不被模型替换掉**。

### 上一版遗留的准备代码（仍保留，未被这三条链路使用）

- `training-api.ts` 中的 `evaluateTraining(choice, taskId)`、`product-data.ts` 中的演示任务：仍是客户端演示函数，
  与上面这条真链路无关（真链路走 `/api/tasks*`）。

### 尚未做

- 任务的**编辑/删除/排序**：任务由路径派生，没有「把某条任务改成自定义」的写接口。
- 附件只接受 `attachmentIds`（引用），**没有文件上传**：文本成果才是这条链路的输入。
- `/growth-records` 空状态 → `/actions` 的引导仍是原样。

## 10. 成长记录 `/growth-records`

### 功能

- 展示画像、行动、职业、路径等成长记录。
- 支持分类、状态、时间范围和关键词筛选。
- 点击记录查看详情、前后变化、来源、证据和关联任务。
- 无结果时引导前往职场模拟。

### 联动

- 从关联任务详情 → `/actions?task=<taskId>`。
- 查看当前画像 → `/growth`。
- `AppShell` 通知读取 `ProfileProvider.records`，并链接回 `/growth`。
- 当前记录由聊天候选确认和任务状态操作写入 Provider 内存，刷新即丢失。
- **详情面板有「写入记忆候选」**（2026-09-29 新增）：把选中的这条记录 `POST /api/growth-records`，
  后端写记录并派生**待确认**记忆候选（只取图谱已知的职业/技能名，认不出就不写），
  随后到 `/growth` 的「记忆库 → 待确认」栏里决定是否保留。写同一记录两次是幂等的。

### 前端代码入口

- 页面：[app/(product)/growth-records/page.tsx](frontend/frotent/frontend1/app/(product)/growth-records/page.tsx)
- 状态容器：[components/profile/profile-provider.tsx](frontend/frotent/frontend1/components/profile/profile-provider.tsx)
- 状态规则：[lib/client/profile-state.ts](frontend/frotent/frontend1/lib/client/profile-state.ts)
- 动态画像类型：[types/view-models/dynamic-profile.ts](frontend/frotent/frontend1/types/view-models/dynamic-profile.ts)
- 记录/候选组件：[components/profile/activity-panels.tsx](frontend/frotent/frontend1/components/profile/activity-panels.tsx)、[components/profile/candidate-profile-card.tsx](frontend/frotent/frontend1/components/profile/candidate-profile-card.tsx)

### 当前缺口

- ~~应增加 `GET /api/growth-records`，支持筛选、分页和详情。~~ **写入与列表已实现**（`POST/GET/DELETE /api/growth-records`）；服务端分类筛选、分页与 `/confirm`（候选→记录→证据→事件一个事务）仍未做。
- 候选确认应由后端事务化写入画像记录、证据和成长事件（M1-3，依赖 `ProfileProvider` 先改成服务端数据）。
- `ProfileProvider` 需要改为服务端数据 + 本地乐观更新，而不是只依赖 `useState`。

## 11. 跨页面共享入口

### 产品壳和导航

- [app/(product)/layout.tsx](frontend/frotent/frontend1/app/(product)/layout.tsx)
- [components/layout/product-shell.tsx](frontend/frotent/frontend1/components/layout/product-shell.tsx)
- [components/layout/app-shell.tsx](frontend/frotent/frontend1/components/layout/app-shell.tsx)

当前侧边导航只有 `/growth`、`/path`、`/actions`、`/growth-records`，没有 `/work-map`、`/catalog`、`/chat`。

### 全局样式

- [app/globals.css](frontend/frotent/frontend1/app/globals.css)
- `app/catalog-path.css`
- `app/phase1-profile.css`
- `app/work-map-chat.css`
- `app/career-match.css`
- `app/profile-showcase.css`

### 基础请求

- [lib/client/http.ts](frontend/frotent/frontend1/lib/client/http.ts)
- 默认后端地址：`http://localhost:8000`。
- 当前部分页面直接使用相对 `/api`，需要统一请求层和环境变量。

## 建议开发顺序

1. **认证与 onboarding**：先建立会话、画像和简历解析，形成可用用户基础。
2. **职业目录与工作地图**：先把已存在但未挂载的组件接回真实路由。
3. **growth 与 path**：让推荐和路径真正读取确认画像。
4. **chat**：持久化会话和候选画像确认。
5. **actions**：实现任务执行和评估闭环。
6. **growth-records**：接入持久化记录、证据和筛选分页。

