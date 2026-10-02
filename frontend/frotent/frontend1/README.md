# frontend1 —— 唯一的正式构建源

本目录是项目的**唯一正式源码**（前端 + 后端 + MCP），构建、运行与测试都在这里执行
（`npm run dev` / `python backend/run.py` / `npm test` / `npm run e2e:all`）。
安装依赖、运行方式与验证口径以根目录 [`README.md`](../../../README.md) 为准。

- `app/`：页面与路由（`(entry)` 登录/画像、`(product)` 产品主体）
- `components/`：界面组件（chat / profile / work-map / layout）
- `lib/client/`：请求层、状态规则、图谱视图纯函数
- `types/`：前后端数据契约
- `backend/`：后端服务（Python 标准库，零三方依赖）
- `mcp/`：MCP 服务（3 个只读工具，stdio + Streamable HTTP）
- `tests/`：契约测试（node:test）；`backend/tests/`：后端 pytest
- `evidence/`：端到端与 MCP 验证证据
