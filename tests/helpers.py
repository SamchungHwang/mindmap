"""테스트 공용 — 데모 맵을 빌드한다 (unittest 와 pytest 모두에서 import 가능)."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kstock.demo import D0, D1, build_all, build_new_map  # noqa: E402

__all__ = ["D0", "D1", "build_all", "build_new_map"]
