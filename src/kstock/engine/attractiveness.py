"""A4.7 확률가중 투자 매력도 · A3.4 모델 신뢰도 (설계 v6 §4.1·§4.4) 와 밸류 묶음 오케스트레이션.

    V         = Σ p_i · TP_i                       (확률은 사람 — 없으면 미계산)
    ER_price  = V / P0 − 1
    ER_total  = ER_price + DPS_E / P0              (사용자 결정 ⑤: 둘 다 표시 — 배당은 가격에 넣지 않고 수익률에만)
    downside  = TP_bear / P0 − 1
    MoS       = 1 − P0 / V  (그리고 1 − P0 / TP_base)
    RR        = (TP_base − P0) / (P0 − TP_bear)    (기존 calc.risk_reward 그대로 — A4.6)

신뢰도는 규칙 기반 H/M/L: 입력 커버리지 · 증권사 수 · EPS 기준 혼합 · 사업부 합 vs 전사 · 데이터 나이 · PER/SOTP 차이 · Bear ≥ 현재가 · 확률 미입력 · 1곳 셀.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..models import MindMap, NewMap
from .valuation import Criteria, ensure_valuation_nodes, segment_values, write_valuation, year_cell


# --------------------------------------------------------------------------- A4.7
def dps_estimate(m: MindMap, crit: Criteria | None = None) -> tuple[float | None, str, str | None]:
    """(DPS_E, 출처, 메모) — 사용자 > 표 중앙값(연도 = 모델 연도). 증권사 간 흩어짐이 크면 메모."""
    crit = crit or Criteria.from_map(m)
    if crit.dps is not None:
        return crit.dps, "criteria", "A3.2 평가 기준"
    fin = (m.meta.get("draft") or {}).get("financials") or {}
    year = crit.eps_year or fin.get("year")
    c = year_cell(fin, year, "dps")
    if not c:
        return None, "missing", None
    xs = sorted(float(x) for x in (c.get("by_broker") or {}).values() if x is not None)
    if not xs:
        return (float(c["mean"]) if c.get("mean") is not None else None), "table", None
    med = xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2
    note = f"표 DPS 중앙값 ({len(xs)}곳, {year})"
    if len(xs) >= 2 and med and (max(xs) - min(xs)) / med > 0.5:
        note += f" — 증권사 간 흩어짐 {min(xs):,.0f}~{max(xs):,.0f}"
    return med, "table", note


def compute_attractiveness(m: MindMap, *, today: date | None = None) -> dict[str, Any]:
    """A4.7 값(dict) 을 만들고 노드와 meta.draft.attractiveness 에 쓴다."""
    today = today or date.today()
    out: dict[str, Any] = {"status": "미계산", "missing": [], "warnings": []}
    if not isinstance(m, NewMap):
        return out
    ensure_valuation_nodes(m)
    p0 = m.current_price
    sc = m.scenarios()
    crit = Criteria.from_map(m)
    dps, dsrc, dnote = dps_estimate(m, crit)
    out["dividend_mode"] = crit.dividend_in_return
    if dps is not None:
        out["dps"] = dps
        out["dps_source"] = dsrc
        out["dps_note"] = dnote
    if not p0:
        out["missing"].append("현재가 (A0.4)")
    missing_sc = {"Bear", "Base", "Bull"} - set(sc)
    if missing_sc:
        out["missing"].append("시나리오 목표가: " + ", ".join(sorted(missing_sc)) + " (미계산 또는 공란)")
    if sc and any(s.probability is None for s in sc.values()):
        out["missing"].append("시나리오 확률 — 사람이 넣는다 (콘솔 「예시 확률 입력」 은 미검증 가정)")
    if p0 and not missing_sc:
        bear, base, bull = sc["Bear"], sc["Base"], sc["Bull"]
        out["downside_pct"] = (bear.target_price / p0 - 1.0) * 100.0
        out["mos_base_pct"] = (1.0 - p0 / base.target_price) * 100.0
        out["upside_base_pct"] = (base.target_price / p0 - 1.0) * 100.0
        out["upside_bull_pct"] = (bull.target_price / p0 - 1.0) * 100.0
        if bear.target_price >= p0:
            out["warnings"].append(f"Bear 목표가 {bear.target_price:,.0f} 이 현재가 {p0:,.0f} 위 — 하락 시나리오가 아니다 (손익비 ∞)")
        else:
            out["rr_base"] = (base.target_price - p0) / (p0 - bear.target_price)
        if not out["missing"]:
            psum = sum(s.probability or 0.0 for s in sc.values())
            if abs(psum - 1.0) > 0.01:
                out["missing"].append(f"확률 합 {psum:.2f} ≠ 1")
            else:
                v = sum((s.probability or 0.0) * s.target_price for s in sc.values())
                out["value"] = v
                out["er_price_pct"] = (v / p0 - 1.0) * 100.0
                out["mos_value_pct"] = (1.0 - p0 / v) * 100.0
                if dps is not None:
                    out["dividend_yield_pct"] = dps / p0 * 100.0
                    out["er_total_pct"] = out["er_price_pct"] + out["dividend_yield_pct"]
                out["probabilities"] = {k: s.probability for k, s in sc.items()}
                out["status"] = "계산됨"
    if dps is None and out.get("status") == "계산됨":
        out["warnings"].append("DPS 전망이 없어 배당 포함 기대수익률은 비움")
    out["computed_at"] = today.isoformat()
    m.meta.setdefault("draft", {})["attractiveness"] = out
    if "A4.7" in m:
        n = m["A4.7"]
        n.value = attractiveness_line(out)
        n.as_of = today
    return out


def attractiveness_line(a: dict[str, Any] | None) -> str:
    if not a:
        return "확률가중 매력도 없음"
    parts: list[str] = []
    if a.get("status") == "계산됨":
        parts.append(f"확률가중 가치 {a['value']:,.0f}원 · 기대수익률 {a['er_price_pct']:+.1f}%")
        mode = a.get("dividend_mode", "separate")
        if a.get("er_total_pct") is not None and mode != "exclude":
            parts.append(f"배당 포함 {a['er_total_pct']:+.1f}% (DPS {a['dps']:,.0f} = {a['dividend_yield_pct']:.1f}%)")
        parts.append(f"안전마진 {a['mos_value_pct']:.0f}% (vs 가중) · {a['mos_base_pct']:.0f}% (vs Base)")
    else:
        parts.append("확률가중 미계산 — 필요: " + "; ".join(a.get("missing") or [])[:160])
    if a.get("downside_pct") is not None:
        parts.append(f"하방 {a['downside_pct']:+.0f}% (Bear)")
    if a.get("rr_base") is not None:
        parts.append(f"손익비 {a['rr_base']:.1f}")
    if a.get("warnings"):
        parts.append("⚠ " + a["warnings"][0])
    return " · ".join(parts)


# --------------------------------------------------------------------------- A3.4
def compute_confidence(m: MindMap, *, today: date | None = None) -> dict[str, Any]:
    """규칙 기반 신뢰도 H/M/L + 경고 목록. 평가 모델·시나리오·매력도가 먼저 계산돼 있어야 한다."""
    today = today or date.today()
    out: dict[str, Any] = {"grade": "L", "warnings": [], "checks": {}}
    if not isinstance(m, NewMap):
        return out
    ensure_valuation_nodes(m)
    draft = m.meta.get("draft") or {}
    fin = draft.get("financials") or {}
    vm = draft.get("valuation") or {}
    att = draft.get("attractiveness") or {}
    warns: list[str] = []
    checks: dict[str, Any] = {}
    # 1) 입력 커버리지 — 평가 모델 입력 중 missing·default·derived 가 아닌 것
    inputs = vm.get("inputs") or []
    good = sum(1 for i in inputs if i.get("source") not in ("default", "missing"))   # 유도(derived) 는 표에서 나온 것 — 커버리지에 든다
    cov = good / len(inputs) if inputs else 0.0
    checks["coverage"] = round(cov, 2)
    if inputs and cov < 0.6:
        warns.append(f"입력 커버리지 {cov:.0%} — 기본값·유도·누락이 많다")
    for i in inputs:
        if i.get("source") == "default":
            warns.append(f"{i.get('name')} 은 기본값 ({(i.get('note') or '')[:40]})")
    # 2) 증권사 수
    n_eps = int(((fin.get("eps") or {}).get("n")) or 0)
    checks["brokers_eps"] = n_eps
    if n_eps and n_eps < 3:
        warns.append(f"EPS 컨센 증권사 {n_eps}곳 — 3곳 미만")
    # 3) 회계 기준 — 설계 v6 §11.1: 정규화가 됐으면 상태(comparable/mixed/not_comparable) 를 읽고, 옛 맵(검사 없음) 은 예전 혼합 경고
    bc = fin.get("basis") or {}
    eps_f = fin.get("eps") or {}
    checks["basis_status"] = bc.get("status") or "unchecked"
    if bc:
        st = bc.get("status")
        if st == "not_comparable":
            warns.append("EPS 기준 불일치 — 정규화된 증권사 0곳 (가격은 참고값): " + "; ".join(bc.get("notes") or [])[:120])
        elif st == "mixed":
            warns.append("EPS 정규화 1곳뿐 — 컨센이 아니라 한 증권사 값")
        elif st == "unavailable":
            warns.append("발행주식수 없음 — EPS 기준 미정규화 (증권사 EPS 평균)")
        if eps_f.get("approx"):
            warns.append("EPS 정규화에 측정 지배 비율 환산 " + f"{len(eps_f['approx'])}곳 (전체 순이익만 적은 증권사)")
        if eps_f.get("excluded"):
            warns.append("EPS 평균에서 제외: " + ", ".join(f"{k} ({v})" for k, v in eps_f["excluded"].items())[:120])
        for note in bc.get("notes") or []:
            if note.startswith("주식 수 기준 혼합"):
                warns.append(note + " — 공시 보통주로 통일")
    else:
        basis = fin.get("np_basis") or {}
        if len(set(basis.values())) > 1:
            warns.append("순이익 기준 혼합: " + ", ".join(f"{b} {v}" for b, v in basis.items()))
        so = (fin.get("shares_out") or {})
        np_m, eps_m = (fin.get("np") or {}).get("mean"), (fin.get("eps") or {}).get("mean")
        if so.get("median") and np_m and eps_m:
            derived = np_m * 1e8 / eps_m
            diff = derived / so["median"] - 1.0
            checks["shares_derived_vs_header_pct"] = round(diff * 100, 1)
            if abs(diff) > 0.05:
                warns.append(f"EPS 기준 혼합 의심 — NP/EPS 유도 주식 수가 발행주식수와 {diff:+.0%} 다르다 (우선주 포함·지배/전체)")
        by = so.get("by_broker") or {}
        if len(by) >= 2 and (max(by.values()) / min(by.values()) - 1.0) > 0.05:
            warns.append("증권사별 발행주식수가 다르다 (우선주 포함 여부) — 중앙값 사용")
    # 4) 사업부 합 vs 전사 OP
    year = fin.get("year")
    seg_op = segment_values(m, year, "op")
    op_m = (fin.get("op") or {}).get("mean")
    tops = [s for s in ("DS", "DX", "SDC", "Harman", "하만") if s in seg_op]
    if op_m and tops:
        tot = sum(seg_op[s][0] for s in tops)
        gap = (tot - op_m) / abs(op_m)
        checks["segment_sum_gap_pct"] = round(gap * 100, 1)
        if abs(gap) > 0.03:
            warns.append(f"사업부 합({'+'.join(tops)}) 이 전사 영업이익과 {gap:+.0%} 차이")
    # 5) 데이터 나이
    dates = fin.get("report_dates") or []
    if dates:
        try:
            newest = max(date.fromisoformat(d) for d in dates)
            age = (today - newest).days
            checks["data_age_days"] = age
            if age > 45:
                warns.append(f"리포트 최신일 {newest} — {age}일 전")
        except ValueError:
            pass
    # 6) PER/SOTP 차이 · 정상화 1곳 · 역산
    if vm.get("gap_pct") is not None and abs(vm["gap_pct"]) > 15:
        warns.append(f"PER 가격과 SOTP 차이 {vm['gap_pct']:+.0f}%")
    if vm.get("price_sotp") is None:
        warns.append("SOTP 미계산 (멀티플 미입력) — 검증 없이 PER 단일")
    for w in vm.get("warnings") or []:
        if "1곳" in w:
            warns.append(w)
    if vm.get("price_per") is not None and vm.get("price_normalized") is not None and vm["price_normalized"] > 0:
        d = vm["price_per"] / vm["price_normalized"] - 1.0
        checks["peak_vs_normalized_pct"] = round(d * 100, 1)
        if d > 0.25:
            warns.append(f"기준 EPS 가격이 정상화 가격보다 {d:+.0%} — 고점 EPS × 목표 PER 가능성")
    # 7) 시나리오·확률
    for w in att.get("warnings") or []:
        warns.append(w)
    if att.get("status") != "계산됨":
        warns.append("확률가중 미계산 (확률 또는 시나리오 가격 없음)")
    for nm, n in m.scenario_nodes().items():
        sc = n.scenario
        if sc is not None and sc.price_source == "computed" and (sc.model is None or sc.model.status != "계산됨"):
            warns.append(f"{nm} 시나리오 미계산")
        if sc is not None and sc.price_source != "computed" and sc.target_price:
            warns.append(f"{nm} 목표가는 가정 묶음이 아니라 입력값 ({sc.price_source})")
    # 등급 — 기준 불일치(not_comparable) 면 L 고정
    hard = [w for w in warns if any(k in w for k in ("혼합", "미계산", "차이", "커버리지", "하락 시나리오", "불일치", "1곳뿐"))]
    if checks.get("basis_status") == "not_comparable":
        grade = "L"
    elif not warns and cov >= 0.8:
        grade = "H"
    elif len(hard) <= 2 and cov >= 0.6:
        grade = "M"
    else:
        grade = "L"
    seen: list[str] = []
    for w in warns:
        if w not in seen:
            seen.append(w)
    out.update({"grade": grade, "warnings": seen, "checks": checks, "computed_at": today.isoformat()})
    draft_meta = m.meta.setdefault("draft", {})
    draft_meta["confidence"] = out
    log = draft_meta.get("valuation_log") or []
    if "A3.4" in m:
        n = m["A3.4"]
        n.value = confidence_line(out, len(log))
        n.as_of = today
    return out


def confidence_line(c: dict[str, Any] | None, n_log: int = 0) -> str:
    if not c:
        return "신뢰도 없음"
    ws = c.get("warnings") or []
    head = f"신뢰도 {c.get('grade', '?')} · 경고 {len(ws)}" + (f" · 갱신 {n_log}회" if n_log else "")
    return head + (" — " + " / ".join(ws[:3]) if ws else "")


# --------------------------------------------------------------------------- 묶음
def compute_valuation_all(m: MindMap, *, today: date | None = None) -> list[str]:
    """시나리오(가정 묶음) → 평가 모델 → 확률가중 매력도 → 신뢰도. draft·편집 저장·CLI 가 같이 부른다. 돌려주는 것은 메모."""
    if not isinstance(m, NewMap):
        return []
    today = today or date.today()
    from .scenario_model import compute_scenarios

    notes: list[str] = []
    ensure_valuation_nodes(m)
    notes += compute_scenarios(m, today=today)
    vm = write_valuation(m, today=today)
    notes.append("평가 모델: " + (m["A3.3"].value if "A3.3" in m and isinstance(m["A3.3"].value, str) else vm.status))
    att = compute_attractiveness(m, today=today)
    if att.get("status") == "계산됨":
        notes.append("매력도: " + attractiveness_line(att))
    conf = compute_confidence(m, today=today)
    notes.append("신뢰도: " + confidence_line(conf))
    from .scenario_model import write_scenario_overview

    write_scenario_overview(m, today=today)          # A4.1 시나리오 한눈에 — 세 갈래 요약 (매력도까지 계산된 뒤)
    return notes


__all__ = ["compute_attractiveness", "attractiveness_line", "compute_confidence", "confidence_line", "compute_valuation_all", "dps_estimate"]
