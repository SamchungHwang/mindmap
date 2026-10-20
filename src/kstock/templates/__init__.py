"""템플릿 레이어: 노드 정의 테이블(questions) 과 스켈레톤 생성기(skeleton)."""

from .questions import A_SPEC, B_SPEC, C_SPEC, M_SPEC, S_SPEC
from .skeleton import (
    hold_map_skeleton,
    macro_skeleton,
    new_map_skeleton,
    portfolio_skeleton,
    sector_skeleton,
)

__all__ = [
    "A_SPEC", "B_SPEC", "C_SPEC", "M_SPEC", "S_SPEC",
    "new_map_skeleton", "hold_map_skeleton", "portfolio_skeleton", "macro_skeleton", "sector_skeleton",
]
