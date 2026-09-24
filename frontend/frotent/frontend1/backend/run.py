"""后端入口：``python backend/run.py``（对应 npm run dev:backend）。

同时支持 ``python -m backend.run`` 与直接脚本执行两种调用方式。
"""

from __future__ import annotations

import os
import sys

# 允许 `python backend/run.py`（以文件路径执行）时也能按包导入 backend。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
