"""Claim 품질 (설계 v7 T3 / v6 §11.3): 단계별 통계 · 두 ClaimSet 묶음 비교 · 품질 게이트.

- stats: ① 인용(quote) ② 숫자(numbers) ③ 의미(support) 의 교차표, 상태(verified/needs_review/rejected), 검토 사유(review_reason) 집계,
  추출 모델·프롬프트 버전별. "검증률 19%" 가 어느 단계에서 떨어지는지 보인다.
- compare: 기준(예: Claude 캐시) 대비 비교(예: 로컬 Nano) — 같은 리포트끼리 (a) 원인 KPI 셀 재현율 (b) 증권사별 방향 일치율 (c) 핵심 테마(driver_id) 누락률 (d) 인용 위치 겹침.
- gate: 핵심 Claim 검증률 < 50% ∨ (기준이 있을 때) 재현율 < 70% 면 `needs_claim_review` — 초안을 완료로 표시하지 않는다. 문턱은 첫 값.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..generate.kpi_catalog import RESULT_DRIVER_IDS
from ..models import ClaimSet, ClaimStatus, normalized_value

GATE_VERIFIED = 0.50
GATE_RECALL = 0.70


def is_core(c: Any) -> bool:
    """핵심 Claim — 원인 드라이버 셀(driver_id 가 결과 지표가 아님) · 수치 참조가 있음 · 조건부(리스크) · 목표가/추정치 변경."""
    did = c.driver_id or ""
    if did and did not in RESULT_DRIVER_IDS:
        return True
    if c.quant_refs:
        return True
    return str(c.certainty) in ("conditional", "Certainty.conditional") or did in ("estimate_revision", "valuation_multiple")


@dataclass
class ClaimStats:
    n: int = 0
    status: Counter = field(default_factory=Counter)
    quote: Counter = field(default_factory=Counter)
    numbers: Counter = field(default_factory=Counter)
    support: Counter = field(default_factory=Counter)
    stage_fail: Counter = field(default_factory=Counter)          # 어느 단계가 통과를 막았나 (첫 실패 단계)
    reasons: Counter = field(default_factory=Counter)
    by_extractor: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    core_n: int = 0
    core_verified: int = 0
    per_report: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def verified_ratio(self) -> float:
        return self.status.get("verified", 0) / self.n if self.n else 0.0

    @property
    def core_verified_ratio(self) -> float:
        return self.core_verified / self.core_n if self.core_n else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "status": dict(self.status), "quote": dict(self.quote), "numbers": dict(self.numbers), "support": dict(self.support),
                "stage_fail": dict(self.stage_fail), "reasons": dict(self.reasons), "by_extractor": {k: dict(v) for k, v in self.by_extractor.items()},
                "core_n": self.core_n, "core_verified": self.core_verified, "verified_ratio": round(self.verified_ratio, 3),
                "core_verified_ratio": round(self.core_verified_ratio, 3), "per_report": self.per_report}


def claim_stats(claimsets: list[ClaimSet]) -> ClaimStats:
    st = ClaimStats()
    for cs in claimsets:
        rep = {"n": len(cs.claims), "verified": 0, "needs_review": 0, "rejected": 0, "quality": cs.quality, "stage_fail": Counter()}
        for c in cs.claims:
            st.n += 1
            v = c.validation
            s = c.status.value if isinstance(c.status, ClaimStatus) else str(c.status)
            st.status[s] += 1
            rep[s] = rep.get(s, 0) + 1
            st.quote[v.quote] += 1
            st.numbers[v.numbers] += 1
            sup = v.support.value if hasattr(v.support, "value") else str(v.support)
            st.support[sup] += 1
            key = f"{c.extractor.model or '?'} / {c.extractor.prompt_version or '?'}"
            st.by_extractor[key][s] += 1
            if s != "verified":
                if v.quote != "ok":
                    stage = "① 인용 " + v.quote
                elif v.numbers not in ("ok", "none"):
                    stage = "② 숫자 " + v.numbers
                elif sup != "supported":
                    stage = "③ 의미 " + sup
                else:
                    stage = "④ 사람 반려/보류"
                st.stage_fail[stage] += 1
                rep["stage_fail"][stage] += 1
            for r in (c.review_reason or "").split(" · "):
                if r.strip():
                    st.reasons[r.strip().split(" (")[0].split(" >")[0]] += 1
            if is_core(c):
                st.core_n += 1
                if s == "verified":
                    st.core_verified += 1
        rep["stage_fail"] = dict(rep["stage_fail"])
        st.per_report[cs.report_id] = rep
    return st


def stats_text(st: ClaimStats) -> str:
    if not st.n:
        return "Claim 없음"
    lines = [f"Claim 품질 통계 — {st.n}건: verified {st.status.get('verified', 0)} ({st.verified_ratio:.0%}) · 검토 대기 {st.status.get('needs_review', 0)} · 반려 {st.status.get('rejected', 0)}"
             f" · 핵심 Claim {st.core_n}건 중 verified {st.core_verified} ({st.core_verified_ratio:.0%})"]
    lines.append("  단계별: ① 인용 " + ", ".join(f"{k} {v}" for k, v in st.quote.most_common()) + " | ② 숫자 " + ", ".join(f"{k} {v}" for k, v in st.numbers.most_common())
                 + " | ③ 의미 " + ", ".join(f"{k} {v}" for k, v in st.support.most_common()))
    if st.stage_fail:
        lines.append("  통과를 막은 첫 단계: " + " · ".join(f"{k} {v}건" for k, v in st.stage_fail.most_common()))
    if st.reasons:
        lines.append("  검토 사유: " + " · ".join(f"{k} {v}" for k, v in st.reasons.most_common(6)))
    for k, v in st.by_extractor.items():
        tot = sum(v.values())
        lines.append(f"  추출기 {k}: {tot}건 · verified {v.get('verified', 0)} ({(v.get('verified', 0) / tot if tot else 0):.0%})")
    for rid, rep in st.per_report.items():
        sf = " · ".join(f"{k} {v}" for k, v in sorted(rep["stage_fail"].items(), key=lambda x: -x[1])[:3])
        lines.append(f"  {rid}: {rep['n']}건 · verified {rep.get('verified', 0)} · 대기 {rep.get('needs_review', 0)} · 반려 {rep.get('rejected', 0)} ({rep['quality']})" + (f" — {sf}" if sf else ""))
    lines.append("  (검증률은 정확도가 아니라 검증기 통과율 — ③ 의미 판정을 같은 작은 모델이 하면 판정기가 병목일 수 있다)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 비교
def _cells(cs: ClaimSet) -> set[tuple[str, str, str, float]]:
    out: set[tuple[str, str, str, float]] = set()
    for c in cs.claims:
        did = c.driver_id or ""
        if not did or did in RESULT_DRIVER_IDS:
            continue
        for q in c.quant_refs:
            v, u = normalized_value(q.value, q.unit)
            out.add((did, q.metric or "", q.col or "", round(v, 1)))
    return out


def _cells_loose(cells: set[tuple[str, str, str, float]]) -> set[tuple[str, str, float]]:
    return {(d, col, round(v / (abs(v) if abs(v) >= 1 else 1.0) * (abs(v) if abs(v) >= 1 else 1.0), 1)) for d, _m, col, v in cells}


def _direction_by_driver(cs: ClaimSet) -> dict[str, str]:
    cnt: dict[str, Counter] = defaultdict(Counter)
    for c in cs.claims:
        if c.driver_id and c.driver_id not in RESULT_DRIVER_IDS:
            cnt[c.driver_id][c.direction.value if hasattr(c.direction, "value") else str(c.direction)] += 1
    return {k: v.most_common(1)[0][0] for k, v in cnt.items()}


def _spans(cs: ClaimSet) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for c in cs.claims:
        for sid in c.span_ids:
            sp = cs.span(sid)
            if sp is not None:
                out.append((sp.start, sp.end))
    return out


def _overlap_ratio(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> float:
    if not a:
        return 0.0
    hit = 0
    for s, e in a:
        if any(min(e, e2) - max(s, s2) > 0 for s2, e2 in b):
            hit += 1
    return hit / len(a)


def claim_compare(ref: list[ClaimSet], cmp: list[ClaimSet]) -> dict[str, Any]:
    """기준(ref) 대비 비교(cmp). 같은 report_id 끼리. 돌려주는 dict: per_report · totals(cell_recall, direction_agreement, theme_loss, span_overlap)."""
    ref_by = {cs.report_id: cs for cs in ref}
    cmp_by = {cs.report_id: cs for cs in cmp}
    common = sorted(set(ref_by) & set(cmp_by))
    per: dict[str, dict[str, Any]] = {}
    tot_ref_cells = tot_hit_cells = 0
    tot_dir = tot_dir_hit = 0
    themes_ref: Counter = Counter()
    themes_cmp: set[str] = set()
    spans_sum = 0.0
    for rid in common:
        a, b = ref_by[rid], cmp_by[rid]
        ca, cb = _cells(a), _cells(b)
        cb_loose = {(d, col, round(v, 1)) for d, _m, col, v in cb}
        hit = sum(1 for d, _m, col, v in ca if (d, col, round(v, 1)) in cb_loose or any(d == d2 and col == col2 and abs(v - v2) <= abs(v) * 0.01 + 1e-9 for d2, col2, v2 in cb_loose))
        da, db = _direction_by_driver(a), _direction_by_driver(b)
        dk = [k for k in da if k in db]
        dh = sum(1 for k in dk if da[k] == db[k])
        ov = _overlap_ratio(_spans(a), _spans(b))
        per[rid] = {"ref_claims": len(a.claims), "cmp_claims": len(b.claims), "ref_cells": len(ca), "cell_hits": hit,
                    "cell_recall": (hit / len(ca)) if ca else None, "drivers_both": len(dk), "direction_hits": dh,
                    "direction_agreement": (dh / len(dk)) if dk else None, "span_overlap": ov,
                    "missing_drivers": sorted(set(da) - set(db)), "extra_drivers": sorted(set(db) - set(da))}
        tot_ref_cells += len(ca)
        tot_hit_cells += hit
        tot_dir += len(dk)
        tot_dir_hit += dh
        spans_sum += ov
        for c in a.claims:
            if c.driver_id and c.driver_id not in RESULT_DRIVER_IDS:
                themes_ref[c.driver_id] += 1
        themes_cmp |= {c.driver_id for c in b.claims if c.driver_id}
    core_themes = [t for t, n in themes_ref.items() if n >= 2]
    lost = [t for t in core_themes if t not in themes_cmp]
    totals = {"reports": len(common), "cell_recall": (tot_hit_cells / tot_ref_cells) if tot_ref_cells else None, "ref_cells": tot_ref_cells, "cell_hits": tot_hit_cells,
              "direction_agreement": (tot_dir_hit / tot_dir) if tot_dir else None, "core_themes": len(core_themes), "lost_themes": lost,
              "theme_loss": (len(lost) / len(core_themes)) if core_themes else None, "span_overlap": (spans_sum / len(common)) if common else None,
              "ref_only": sorted(set(ref_by) - set(cmp_by)), "cmp_only": sorted(set(cmp_by) - set(ref_by))}
    return {"per_report": per, "totals": totals}


def compare_text(c: dict[str, Any]) -> str:
    t = c["totals"]
    if not t["reports"]:
        return "비교할 같은 리포트가 없다 (report_id 가 같아야 한다)" + (f" — 기준에만 {t['ref_only']}, 비교에만 {t['cmp_only']}" if t["ref_only"] or t["cmp_only"] else "")

    def pct(x: float | None) -> str:
        return f"{x:.0%}" if x is not None else "—"

    lines = [f"Claim 비교 — 같은 리포트 {t['reports']}건: 원인 KPI 셀 재현율 {pct(t['cell_recall'])} ({t['cell_hits']}/{t['ref_cells']}) · 증권사별 방향 일치 {pct(t['direction_agreement'])}"
             f" · 핵심 테마 누락 {pct(t['theme_loss'])} ({len(t['lost_themes'])}/{t['core_themes']}: {', '.join(t['lost_themes'][:5]) or '없음'}) · 인용 위치 겹침 {pct(t['span_overlap'])}"]
    for rid, r in c["per_report"].items():
        lines.append(f"  {rid}: 기준 {r['ref_claims']}건 vs 비교 {r['cmp_claims']}건 · 셀 {r['cell_hits']}/{r['ref_cells']} ({pct(r['cell_recall'])}) · 방향 {r['direction_hits']}/{r['drivers_both']}"
                     f" · 겹침 {pct(r['span_overlap'])}" + (f" · 빠진 드라이버 {', '.join(r['missing_drivers'][:4])}" if r["missing_drivers"] else ""))
    if t["ref_only"] or t["cmp_only"]:
        lines.append(f"  (기준에만 {len(t['ref_only'])}건 · 비교에만 {len(t['cmp_only'])}건 — 비교에서 제외)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 게이트
def claim_gate(claimsets: list[ClaimSet], *, reference: list[ClaimSet] | None = None, verified_min: float = GATE_VERIFIED,
               recall_min: float = GATE_RECALL) -> dict[str, Any]:
    """품질 게이트 — needs_claim_review 면 초안을 '완료' 로 표시하지 않는다."""
    st = claim_stats(claimsets)
    out: dict[str, Any] = {"n": st.n, "verified_ratio": round(st.verified_ratio, 3), "core_n": st.core_n, "core_verified_ratio": round(st.core_verified_ratio, 3),
                           "recall_vs_reference": None, "thresholds": {"verified": verified_min, "recall": recall_min}, "status": "ok", "reasons": []}
    if not claimsets or all(cs.quality == "rules" for cs in claimsets):
        out["status"] = "n/a"
        out["reasons"].append("LLM Claim 없음 (규칙 분류) — 게이트 대상 아님")
        return out
    if st.core_n and st.core_verified_ratio < verified_min:
        out["reasons"].append(f"핵심 Claim 검증률 {st.core_verified_ratio:.0%} < {verified_min:.0%}")
    if reference:
        c = claim_compare(reference, claimsets)
        rec = c["totals"].get("cell_recall")
        out["recall_vs_reference"] = round(rec, 3) if rec is not None else None
        if rec is not None and rec < recall_min:
            out["reasons"].append(f"기준 대비 원인 KPI 셀 재현율 {rec:.0%} < {recall_min:.0%}")
    if out["reasons"]:
        out["status"] = "needs_claim_review"
    out["stage_fail"] = dict(st.stage_fail)
    return out


def gate_line(g: dict[str, Any]) -> str:
    if g.get("status") == "n/a":
        return "Claim 품질 게이트: 해당 없음 (" + "; ".join(g.get("reasons") or []) + ")"
    base = f"Claim 품질 게이트: {'⚠ 미달 — needs_claim_review' if g.get('status') == 'needs_claim_review' else '통과'} (핵심 {g.get('core_n', 0)}건 검증률 {g.get('core_verified_ratio', 0):.0%}"
    if g.get("recall_vs_reference") is not None:
        base += f" · 기준 대비 재현율 {g['recall_vs_reference']:.0%}"
    base += f" · 문턱 {g['thresholds']['verified']:.0%}/{g['thresholds']['recall']:.0%})"
    if g.get("reasons") and g.get("status") == "needs_claim_review":
        base += " — " + "; ".join(g["reasons"])
    return base


__all__ = ["claim_stats", "stats_text", "claim_compare", "compare_text", "claim_gate", "gate_line", "ClaimStats", "is_core", "GATE_VERIFIED", "GATE_RECALL"]
