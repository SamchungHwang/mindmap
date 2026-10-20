"""계산식 (템플릿 §3.4, §4.4, §5.2). 순수 함수만 — 맵 객체에 의존하지 않는다."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from ..models import Confidence, Scenario, Settings, ValuationState, WeightState


class CalcError(ValueError):
    pass


# --------------------------------------------------------------------------- A3.4 / A3.5
def expected_return(scenarios: Mapping[str, Scenario], p0: float) -> float:
    """ER = Σ p_i × (TP_i / P0 − 1). 확률 합이 1 이 아니면 CalcError."""
    if p0 <= 0:
        raise CalcError("P0 must be positive")
    if not scenarios:
        raise CalcError("no scenarios")
    psum = sum(s.probability for s in scenarios.values())
    if abs(psum - 1.0) > 0.01:
        raise CalcError(f"scenario probabilities sum to {psum:.3f}, not 1.0")
    return sum(s.probability * (s.target_price / p0 - 1.0) for s in scenarios.values())


def risk_reward(scenarios: Mapping[str, Scenario], p0: float, upside: str = "base") -> float | None:
    """RR = 상승여력 / 하락위험. upside 기준은 base(보수적) 또는 bull. 하락위험 ≤ 0 이면 None (무한대)."""
    if p0 <= 0:
        raise CalcError("P0 must be positive")
    bear = scenarios.get("Bear")
    up = scenarios.get("Bull" if upside == "bull" else "Base")
    if bear is None or up is None:
        raise CalcError("Bear and Base/Bull scenarios are required")
    downside = (p0 - bear.target_price) / p0
    upside_v = (up.target_price - p0) / p0
    if downside <= 0:
        return None
    return upside_v / downside


# --------------------------------------------------------------------------- A6.1
def suggested_weight(confidence: Confidence | str | None, rr: float | None, settings: Settings | None = None) -> float:
    """w = clamp(w_base × f_conf × f_rr, 0, w_max). rr 가 None(하락위험 0)이면 f_rr 상한."""
    s = settings or Settings()
    lo, hi = s.f_rr_clamp
    f_rr = hi if rr is None else min(max(rr / s.f_rr_divisor, lo), hi)
    w = s.w_base * s.f_conf_of(confidence) * f_rr
    return max(0.0, min(w, s.w_max))


# --------------------------------------------------------------------------- B2.2
@dataclass(frozen=True)
class Decomposition:
    r_total: float
    d_metric: float  # ΔEPS (또는 매출/EBITDA)
    d_multiple: float  # ΔPE
    metric: str
    interpretation: str
    consistent: bool  # (1+ΔEPS)(1+ΔPE)−1 ≈ r_total

    def as_dict(self) -> dict:
        return {"r_total": round(self.r_total, 4), "d_metric": round(self.d_metric, 4),
                "d_multiple": round(self.d_multiple, 4), "metric": self.metric,
                "interpretation": self.interpretation, "consistent": self.consistent}


def decompose_return(p_entry: float, p_now: float, metric_entry: float | None, metric_now: float | None,
                     metric: str = "eps") -> Decomposition:
    """주가 변화 = 이익(또는 대체 지표) 변화 × 멀티플 변화. 적자 등으로 지표가 없으면 멀티플 변화만 계산 불가."""
    if p_entry <= 0 or p_now <= 0:
        raise CalcError("prices must be positive")
    r_total = p_now / p_entry - 1.0
    if not metric_entry or not metric_now or metric_entry <= 0 or metric_now <= 0:
        return Decomposition(r_total, float("nan"), float("nan"), metric,
                             "지표(EPS 등)가 양수가 아니어서 분해 불가 — decomp_metric 을 sales/ebitda 로 바꾸라", False)
    d_m = metric_now / metric_entry - 1.0
    d_pe = (p_now / metric_now) / (p_entry / metric_entry) - 1.0
    consistent = abs((1 + d_m) * (1 + d_pe) - 1 - r_total) < 1e-6
    if r_total > 0 and abs(d_pe) > abs(d_m):
        interp = "기대감 주도 상승 — 멀티플 확장이 대부분 (고평가 경계)"
    elif r_total > 0:
        interp = "이익 주도 상승 — 펀더멘털이 따라옴"
    elif d_m > 0 and r_total < 0:
        interp = "멀티플 압축 — 이익은 늘었는데 주가는 하락 (논제 유효 시 add 후보)"
    elif d_m < 0 and r_total < 0:
        interp = "이익 훼손 하락 — 논제 상태 재점검 필요"
    else:
        interp = "변화 미미"
    return Decomposition(r_total, d_m, d_pe, metric, interp, consistent)


# --------------------------------------------------------------------------- B3.6 / B4.1
def valuation_state(band_percentile: float | None, settings: Settings | None = None) -> ValuationState | None:
    s = settings or Settings()
    if band_percentile is None:
        return None
    if band_percentile < s.band_low:
        return ValuationState.저평가
    if band_percentile > s.band_high:
        return ValuationState.고평가
    return ValuationState.적정


def weight_band(w_target: float, settings: Settings | None = None) -> float:
    s = settings or Settings()
    return min(s.dev_abs, s.dev_rel * w_target)


def weight_state(w_now: float | None, w_target: float | None, settings: Settings | None = None) -> WeightState | None:
    """|w − w_target| > min(dev_abs, dev_rel × w_target) → 과소/과다 (둘 중 먼저 걸리는 쪽)."""
    if w_now is None or w_target is None:
        return None
    band = weight_band(w_target, settings)
    dev = w_now - w_target
    if dev > band:
        return WeightState.과다
    if dev < -band:
        return WeightState.과소
    return WeightState.적정


# --------------------------------------------------------------------------- misc
def drawdown_from_peak(peak: float | None, now: float | None) -> float | None:
    if not peak or not now or peak <= 0:
        return None
    return max(0.0, 1.0 - now / peak)


def relative_edge(er_this: float, er_alternatives: Iterable[float]) -> float | None:
    alts = [a for a in er_alternatives if a is not None]
    if not alts:
        return None
    return er_this - max(alts)


def gain_driven_weight_share(w_entry: float, w_now: float, price_return: float) -> float | None:
    """비중 증가분 중 가격 상승이 기여한 몫. 비중이 늘지 않았으면 None."""
    inc = w_now - w_entry
    if inc <= 0:
        return None
    price_part = w_entry * price_return
    return max(0.0, min(1.0, price_part / inc))


def liquidation_days(quantity: float, adv_shares: float | None, participation: float = 0.10) -> float | None:
    if not adv_shares or adv_shares <= 0:
        return None
    return quantity / (adv_shares * participation)


def hhi(weights: Iterable[float]) -> float:
    return sum(w * w for w in weights)


def checkpoints(dates: Iterable[date | None]) -> list[date]:
    return sorted({d for d in dates if d is not None})


__all__ = [
    "CalcError", "Decomposition", "expected_return", "risk_reward", "suggested_weight", "decompose_return",
    "valuation_state", "weight_band", "weight_state", "drawdown_from_peak", "relative_edge",
    "gain_driven_weight_share", "liquidation_days", "hhi", "checkpoints",
]
