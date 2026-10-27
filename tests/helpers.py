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

# 'LLM 이 없는 상태' 를 보는 테스트용 — 개발 PC 나 샌드박스의 .env 에 Claude 키·로컬 LLM(LLM_BASE_URL) 이 있어도 가린다
# (kstock.env.env() 는 빈 문자열을 없는 것으로 본다). mock.patch.dict(os.environ, NO_LLM_ENV) 로 쓴다.
NO_LLM_ENV = {"ANTHROPIC_API_KEY": "", "LLM_BASE_URL": "", "LLM_MODEL": "", "LLM_API_KEY": ""}

__all__ = ["D0", "D1", "NO_LLM_ENV", "build_all", "build_new_map"]
