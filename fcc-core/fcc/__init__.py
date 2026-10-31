"""启动 shim。

真实代码在 `src/fcc/`（派单包 §11 要求的目录布局）。
这个文件只做一件事：把 `src/` 加进 `sys.path`，并把包路径指向 `src/fcc`，
好让 `python -m fcc.server --port 8099` 在**不安装**的情况下也能直接跑起来。

不加 shim 的话，要么必须 `pip install -e .`，要么必须 `PYTHONPATH=src`，
两者都违反"下载下来一条命令就能跑"。
"""

from __future__ import annotations

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SRC = _os.path.join(_os.path.dirname(_HERE), "src")
if _SRC not in _sys.path:
    _sys.path.insert(0, _SRC)

__path__ = [_os.path.join(_SRC, "fcc")]  # type: ignore[assignment]
__version__ = "1.5.0"

from fcc.client import Client  # noqa: E402
from fcc.engine import Engine, LoopNotFound  # noqa: E402
from fcc.store import Store  # noqa: E402

__all__ = ["Client", "Engine", "Store", "LoopNotFound", "__version__"]
