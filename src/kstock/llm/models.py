"""모델 이름 고르기 — 하드코딩한 기본값이 목록에서 사라져도(모델 세대 교체) 쓸 수 있는 Sonnet 을 고른다.

KSTOCK_LLM_MODEL 을 .env 에 적으면 그것을 쓴다(목록에 없으면 오류로 알린다). 비워 두면 /v1/models 목록에서 선호 순서대로 고른다.
"""

from __future__ import annotations

from collections.abc import Iterable

# 선호 순서: 최신 Sonnet 부터. 목록에 하나도 없으면 'sonnet' 이 들어간 가장 뒤 이름 → 그래도 없으면 목록 마지막.
MODEL_PREFERENCE = ("claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5-20250929", "claude-sonnet-4-5")
FALLBACK_MODEL = "claude-sonnet-4-5-20250929"   # 목록을 못 받았을 때 (네트워크 없음 등)


def pick_model(available: Iterable[str], preferred: str | None = None) -> str | None:
    """available 에서 쓸 모델 하나. preferred 가 있고 목록에 있으면 그것, 없으면 None(명시한 모델이 없다 — 호출자가 오류로 알림).
    preferred 가 없으면 MODEL_PREFERENCE → 'sonnet' 포함 → 마지막 순."""
    ids = sorted({m for m in available if m})
    if preferred:
        return preferred if preferred in ids else None
    for m in MODEL_PREFERENCE:
        if m in ids:
            return m
    sonnets = [m for m in ids if "sonnet" in m]
    if sonnets:
        return sonnets[-1]
    return ids[-1] if ids else None


__all__ = ["MODEL_PREFERENCE", "FALLBACK_MODEL", "pick_model"]
