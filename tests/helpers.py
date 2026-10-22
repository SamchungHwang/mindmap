"""테스트 공용 — 데모 맵을 빌드한다 (unittest 와 pytest 모두에서 import 가능)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 테스트는 네트워크를 쓰지 않는다 — 작성 시점 현재가 조회(네이버)는 KSTOCK_OFFLINE 로 끈다
os.environ.setdefault("KSTOCK_OFFLINE", "1")

from kstock.demo import D0, D1, build_all, build_new_map  # noqa: E402

__all__ = ["D0", "D1", "build_all", "build_new_map"]
