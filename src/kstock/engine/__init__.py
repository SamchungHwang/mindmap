"""엔진: 계산식 · 규칙 DSL · 상태등 · 결정 매트릭스 · 검증 · 라이프사이클 · 포트폴리오 롤업."""

from . import calc
from .attractiveness import compute_attractiveness, compute_confidence, compute_valuation_all
from .lifecycle import (
    HoldingEval,
    NewMapEval,
    apply_event,
    close_holding,
    evaluate_holding,
    evaluate_new_map,
    reconstruct_holding,
    record_decision,
    set_thesis_state,
    snapshot_to_holding,
    update_driver,
    update_market,
)
from .matrix import MatrixResult, decide
from .rollup import HoldingRow, rollup
from .rules import (
    ConditionError,
    FiredRule,
    RuleContext,
    evaluate_condition,
    evaluate_rules,
    validate_condition,
)
from .status import LightResult, compute_light, mark_stale
from .validate import (
    Issue,
    has_errors,
    is_complete,
    snapshot_hash,
    validate,
    validate_holding_map,
    validate_new_map,
)

__all__ = [
    "calc", "compute_attractiveness", "compute_confidence", "compute_valuation_all",
    "HoldingEval", "NewMapEval", "apply_event", "close_holding", "evaluate_holding", "evaluate_new_map",
    "reconstruct_holding", "record_decision", "set_thesis_state", "snapshot_to_holding", "update_driver", "update_market",
    "MatrixResult", "decide",
    "HoldingRow", "rollup",
    "ConditionError", "FiredRule", "RuleContext", "evaluate_condition", "evaluate_rules", "validate_condition",
    "LightResult", "compute_light", "mark_stale",
    "Issue", "has_errors", "is_complete", "snapshot_hash", "validate", "validate_holding_map", "validate_new_map",
]
