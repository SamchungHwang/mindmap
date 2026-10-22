"""자동 생성: 리포트 → A 맵 초안 (규칙 채움 → 선택적 LLM 그룹 채움 → 교차검증 → 엔진)."""

from .check import cross_check
from .draft import (
    PRICE_STALE_DAYS,
    DraftError,
    DraftResult,
    PricePick,
    draft_from_reports,
    latest_price,
    resolve_price,
)
from .kpi_catalog import SECTOR_KPIS, Kpi, guess_sector, kpi_candidates, sector_name
from .llm_fill import (
    ClaudeGroupFiller,
    GroupFiller,
    LlmCheck,
    MockGroupFiller,
    OpenAICompatFiller,
    complete_json,
    fill_groups,
    llm_available,
    llm_check,
    llm_status,
    local_llm_available,
    make_filler,
)
from .rules import DRAFT, DRAFT_TAG, RuleFillResult, fill_from_digest, scenario_anchors

__all__ = [
    "DraftError", "DraftResult", "draft_from_reports", "latest_price", "resolve_price", "PricePick", "PRICE_STALE_DAYS", "cross_check", "Kpi", "SECTOR_KPIS", "guess_sector", "kpi_candidates",
    "sector_name", "GroupFiller", "MockGroupFiller", "ClaudeGroupFiller", "fill_groups", "llm_available", "llm_status", "llm_check", "LlmCheck", "OpenAICompatFiller", "make_filler", "complete_json", "local_llm_available", "DRAFT", "DRAFT_TAG",
    "RuleFillResult", "fill_from_digest", "scenario_anchors",
]
