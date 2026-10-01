# 职场模拟与行动任务

- 路由：/actions
- 正式源码目录：frontend/frotent/frontend1
- 本目录代码：code/（按原项目相对路径归档）
- 当前状态：**已落地（2026-10-01）**。`/actions` 是模拟场景入口，挂三条真链路：模拟面试 `/mock-interview`、跨岗位沟通训练 `/scenarios/cross-role`、任务实践 `/actions/tasks`（清单 + 详情 + 提交 + 评估 + **附件真上传**）。口径见根 `PAGE_FUNCTION_MAP.md` §9 与 `frontend/frotent/frontend-backend-page-contract.md` §6。

## 使用说明

进入前端项目根目录后运行 npm run dev，访问 /actions。页面依赖项目根目录的 React、Vinext、全局样式和公共资源；code/ 是用于逐页阅读和开发的镜像，不单独运行。

## 分阶段开发计划（均已落地）

1. 设计任务、提交、评估和证据模型 —— `backend/tasks.py`
2. 实现任务目录与任务详情接口 —— `GET /api/tasks`、`GET /api/tasks/<id>`
3. 实现行动提交、附件和评估流程 —— `POST /api/tasks/<id>/runs`、`POST /api/tasks/<id>/attachments`、`POST /api/task-runs/<id>/evaluate`
4. 将观察结果接入候选画像与成长记录 —— 提交写成长记录 + **待确认**候选，评估不落已确认能力

**仍未做**：任务的编辑/删除/排序；附件的删除与原始字节下载。

## 开发记录

- 需求依据：frontend/frotent/frontend-backend-page-contract.md、frontend/frotent/前端界面设计文档(1).md
- 后端接口、数据库和联调结果在本文件持续补充。
