# 初始画像与简历解析

- 路由：/onboarding
- 正式源码目录：frontend/frotent/frontend1
- 本目录代码：code/（按原项目相对路径归档）
- 当前状态：手填画像已接入；简历上传目前为前端演示

## 使用说明

进入前端项目根目录后运行 npm run dev，访问 /onboarding。页面依赖项目根目录的 React、Vinext、全局样式和公共资源；code/ 是用于逐页阅读和开发的镜像，不单独运行。

## 分阶段开发计划

1. 实现画像读取、保存与确认
2. 实现简历上传、文件校验与解析接口
3. 接入 PDF/DOC/DOCX/OCR/LLM 解析流水线
4. 完成草稿回填、确认和异常恢复

## 开发记录

- 需求依据：frontend/frotent/frontend-backend-page-contract.md、frontend/frotent/前端界面设计文档(1).md
- 后端接口、数据库和联调结果在本文件持续补充。
