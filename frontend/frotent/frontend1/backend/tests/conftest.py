"""backend/tests/conftest.py

把记忆库的默认落地文件换成**内存库**，让 pytest 不碰仓库、也不跨测试串状态。

为什么必须这么做：`CareerApi` 缺省会打开 `backend/career.db`（本地运行态）。
单元测试若共用这个文件，上一轮跑出来的记忆会污染下一轮 —— 而记忆恰恰会改变
`/api/career/recommendations` 的输入（这是它存在的意义），于是测试变成不可复现。
"""

from __future__ import annotations

import os

# 在导入 backend 之前设好：MemoryStore 读的是环境变量，`:memory:` 只在单连接内有效，
# 而 MemoryStore 恰好只持有一个连接，所以测试之间互不可见。
os.environ.setdefault("CAREER_MEMORY_DB", ":memory:")

# 关掉真实 LLM：本机 `knowledge/eval/.env` 里有可用的 key，若不关，
# 「重建触发器」这类测试会真的联网（单次 5–20 s，且结果不可复现）。
# 需要验证模型路径的测试自己注入假 transport（见 test_llm.py / test_memories.py）。
os.environ.setdefault("CAREER_LLM_DISABLED", "1")
