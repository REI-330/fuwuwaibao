# 全部前端代码

此目录是项目的前端源码镜像，保留 Next/vinext 的原始目录结构，便于整体阅读或迁移。

- `app/`：页面、布局和样式（已排除 `app/api/` 服务端接口）
- `components/`：可复用 React 组件
- `lib/client/`：浏览器端 API 与状态逻辑
- `lib/fixtures/`：前端展示所需的示例数据
- `types/`：前端使用的 TypeScript 类型契约
- `public/`：图片、图标等静态资源
- 根目录配置文件：`package.json`、`tsconfig.json`、Vite/Next/PostCSS 配置

该目录用于整理和交付，项目实际构建仍从仓库根目录执行（例如 `npm run dev`）。
