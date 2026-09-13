"""pytest 引导：sys.path + 测试环境变量。

这里必须做三件事：

1. 把 backend/ 放进 sys.path —— backend 下的模块是平铺的（server.py /
   health_graph.py / safety.py …），既没打成包也没走 editable install，
   测试得自己把目录加进来才能 `import safety`。

2. 在 health_graph 被导入之前准备好 DEEPSEEK_API_KEY —— 该模块在
   **导入期**就校验 Key，缺失时直接 `sys.exit(1)`（见 README「已知限制」）。
   测试不会真的调模型，给个占位值让模块可导入即可。

3. 把 trace 日志重定向到临时目录 —— 否则跑一次测试就在仓库里
   留下一堆 logs/trace.jsonl。

pytest.ini 里已经有 `pythonpath = .`（pytest >= 7 的内置支持）；
这里再兜一层，是为了让「从仓库根目录跑 pytest backend」和
「在 backend 目录里跑 pytest」两种姿势都能工作。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_BACKEND_DIR = str(Path(__file__).resolve().parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# health_graph 导入期校验 API Key，缺失会 sys.exit(1)
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-placeholder")

# 测试产生的 trace 日志不污染仓库
os.environ.setdefault("TRACE_DIR", tempfile.mkdtemp(prefix="hp-traces-"))
