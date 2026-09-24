# 工作地图与职业匹配

- 路由：/work-map
- 正式源码目录：frontend/frotent/frontend1
- 本目录代码：code/（按原项目相对路径归档）
- 当前状态：主要为前端展示；职业图谱、匹配和场景数据待接真实服务

## 使用说明

进入前端项目根目录后运行 npm run dev，访问 /work-map。页面依赖项目根目录的 React、Vinext、全局样式和公共资源；code/ 是用于逐页阅读和开发的镜像，不单独运行。

## 分阶段开发计划

1. 建立职业、技能和关系数据模型
2. 实现职业图谱与目录查询接口
3. 实现画像到职业的匹配评分
4. 接入前端筛选、节点详情和职业路径跳转

## 开发记录

- 需求依据：frontend/frotent/frontend-backend-page-contract.md、frontend/frotent/前端界面设计文档(1).md
- 后端接口、数据库和联调结果在本文件持续补充。
