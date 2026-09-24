# AI 职业对话

- 路由：/chat
- 正式源码目录：frontend/frotent/frontend1
- 本目录代码：code/（按原项目相对路径归档）
- 当前状态：聊天接口已预留；候选画像和附件仍为前端内存/占位

## 使用说明

进入前端项目根目录后运行 npm run dev，访问 /chat。页面依赖项目根目录的 React、Vinext、全局样式和公共资源；code/ 是用于逐页阅读和开发的镜像，不单独运行。

## 分阶段开发计划

1. 实现会话与消息持久化
2. 接入 TBox/OpenAI 兼容模型和降级回复
3. 实现候选画像保存、修改、确认和撤销
4. 实现附件上传、引用和安全限制

## 开发记录

- 需求依据：frontend/frotent/frontend-backend-page-contract.md、frontend/frotent/前端界面设计文档(1).md
- 后端接口、数据库和联调结果在本文件持续补充。
