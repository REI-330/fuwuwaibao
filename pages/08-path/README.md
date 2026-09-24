# 个性化成长路径

- 路由：/path
- 正式源码目录：frontend/frotent/frontend1
- 本目录代码：code/（按原项目相对路径归档）
- 当前状态：路径生成已接入；输入仍有硬编码，任务写操作待接入

## 使用说明

进入前端项目根目录后运行 npm run dev，访问 /path。页面依赖项目根目录的 React、Vinext、全局样式和公共资源；code/ 是用于逐页阅读和开发的镜像，不单独运行。

## 分阶段开发计划

1. 导入职业技能图和先修关系
2. 根据确认画像生成路径输入
3. 实现路径生成、评估和快照保存
4. 接入任务安排、重新生成和导出

## 开发记录

- 需求依据：frontend/frotent/frontend-backend-page-contract.md、frontend/frotent/前端界面设计文档(1).md
- 后端接口、数据库和联调结果在本文件持续补充。
