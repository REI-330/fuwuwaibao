# 页面拆分开发目录

正式构建入口仍是 frontend/frotent/frontend1。pages/ 用于按页面独立阅读、规划和实现：每个目录都有 code/（页面相关源码镜像）和 README.md（使用说明、当前状态、分阶段计划）。

## 页面目录

| 目录 | 路由 | 页面 |
|---|---|---|
| 01-auth | /auth | 登录与注册 |
| 02-onboarding | /onboarding | 初始画像与简历解析 |
| 03-work-map | /work-map | 工作地图与职业匹配 |
| 04-catalog | /catalog | 职业目录 |
| 05-chat | /chat | AI 职业对话 |
| 06-growth | /growth | 动态画像与岗位推荐 |
| 07-growth-records | /growth-records | 成长记录档案 |
| 08-path | /path | 个性化成长路径 |
| 09-actions | /actions | 职场模拟与行动任务 |

## 开发约定

1. 先在对应页面的 README.md 更新接口和数据设计。
2. 修改正式源码 frontend/frotent/frontend1，再同步到对应 pages/*/code/ 镜像。
3. 每完成一个页面，启动前端和后端做真实页面链路验收，再进入下一个页面。
4. 跨页面共享代码统一归档在 pages/shared/，避免各页面复制出互相漂移的公共实现。
