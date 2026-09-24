# 登录与注册

- 路由：/auth
- 正式源码目录：frontend/frotent/frontend1
- 本目录代码：code/（按原项目相对路径归档）
- 当前状态：体验账号已接入；正式注册、登录、找回密码待开发

## 使用说明

进入前端项目根目录后运行 npm run dev，访问 /auth。页面依赖项目根目录的 React、Vinext、全局样式和公共资源；code/ 是用于逐页阅读和开发的镜像，不单独运行。

## 分阶段开发计划

1. 实现注册接口与密码安全存储
2. 实现登录、登出、会话续期与记住我
3. 接入前端表单真实提交和错误提示
4. 补充找回密码与限流策略

## 开发记录

- 需求依据：frontend/frotent/frontend-backend-page-contract.md、frontend/frotent/前端界面设计文档(1).md
- 后端接口、数据库和联调结果在本文件持续补充。
