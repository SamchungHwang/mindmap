"""시나리오 = 드라이버 가정 묶음 → 통합 1차 모델 (설계 v6 §4.2~4.3).

    사업부 i:  매출 R_i, 영업이익 O_i, 원가 C_i = R_i − O_i
      가격·물량 가정 (asp a_i, volume v_i):  R_i' = R_i (1 + a_i)(1 + v_i) · k ,  C_i' = C_i (1 + v_i)      → O_i' = R_i' − C_i'
            → 같은 사업부의 가격과 물량이 함께 움직이면 교차항 a·v 가 들어간다 (민감도를 합산하면 빠지는 항 — §6)
      원가율 가정 (cost c_i, %p):            O_i' −= R_i' × c_i / 100
      손익 직접 가정 (op, %):                O_i' += |O_i| × Δ% / 100          (파운드리 적자 축소 등)
      손익 절대 가정 (op, value 억원):       O_i' = value
    전사:  OP' = Σ O_i' + (OP − Σ O_i)  — 사업부에 없는 나머지는 고정 ;  NP' = OP' (1 − t) ;  EPS' = NP' / N ;  가격 = EPS'(기준) × PER_s
    EPS 기준이 '정상화' 인 시나리오는 OP 경로를 기준 EPS 에 비례로 옮긴다 (EPS' = EPS_basis × NP'/NP).

민감도(1변수, `sensitivity.compute_sensitivity`) 와 같은 입력 스냅숏(meta.draft.financials / segments) 과 같은 꼬리(`valuation.common_tail`) 를 쓴다.
가정이 하나이고 가격·물량 템플릿이면 결과는 민감도와 같다 (회귀 테스트). 확률·가정·PER 은 사람 몫 — 자동 초안은 가정 묶음을 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from ..models import DriverAssumption, MindMap, NewMap, Scenario, ScenarioResult, SensitivityInput
from .valuation import (
    Criteria,
    common_tail,
    drop_contained,
    ensure_valuation_nodes,
    eps_for_basis,
    segment_values,
)

SCENARIO_IDS = {"Bear": "A4.2", "Base": "A4.3", "Bull": "A4.4"}
# KR-SEMI Bear 기본 템플릿 — 라벨만, 숫자는 공란 (사용자 결정 ⑥). 사이클 꺾임을 담을 수 있게 세 축을 비워 둔다
BEAR_TEMPLATES: dict[str, list[str]] = {
    "KR-SEMI": ["메모리 ASP 경로 (컨센 대비 −%)", "멀티플 압축 (적용 PER)", "기준 연도 (2027E 고점 vs 2028E 하강)"],
}


def _inp(name: str, value: float | None, unit: str | None, source: str, period: str | None = None, note: str | None = None) -> SensitivityInput:
    return SensitivityInput(name=name, value=value, unit=unit, source=source if value is not None else "missing", period=period, note=note)


@dataclass
class SegState:
    name: str
    rev: float | None
    op: float | None
    rev_n: int = 0
    op_n: int = 0
    a: float = 0.0      # 가격 Δ (비율)
    v: float = 0.0      # 물량 Δ (비율)
    c: float = 0.0      # 원가율 Δ (%p)
    op_pct: float = 0.0 # 손익 직접 Δ (%)
    op_abs: float | None = None
    op_add: float = 0.0   # 억원 직접 가감
    k: float = 1.0

    def delta(self) -> float | None:
        """가정 적용 뒤 영업이익 변화 (억원). 필요한 입력이 없으면 None."""
        if self.op_abs is not None:
            if self.op is None:
                return None
            return (self.op_abs - self.op) + self.op_add
        d = 0.0
        if self.a or self.v or self.c:
            if self.rev is None:
                return None
            rev2 = self.rev * (1.0 + self.a) * (1.0 + self.v)
            d += (rev2 - self.rev) * self.k                 # 가격·물량 → 매출 변화 중 이익으로 가는 몫 (교차항 a·v 포함)
            if self.v:
                if self.op is None:
                    return None
                d -= (self.rev - self.op) * self.v          # 물량이 늘면 원가도 비례해 는다
            d -= rev2 * self.c / 100.0                      # 원가율 %p
        if self.op_pct:
            if self.op is None:
                return None
            d += abs(self.op) * self.op_pct / 100.0
        return d + self.op_add


def _driver_meta(m: MindMap, node_id: str) -> dict[str, Any]:
    return ((m.meta.get("draft") or {}).get("drivers") or {}).get(node_id) or {}


def _variable_of(m: MindMap, da: DriverAssumption) -> str | None:
    if da.variable:
        return da.variable
    meta = _driver_meta(m, da.node_id)
    if meta.get("variable"):
        return str(meta["variable"])
    n = m.get(da.node_id)
    if n is not None and n.driver is not None and n.driver.metric and ":" in n.driver.metric:
        return n.driver.metric.split(":")[1]
    tpl = meta.get("template")
    return {"price_volume": "asp", "cost_ratio": "cost", "direct_op": "op", "payout": "fcf", "orders": "orders"}.get(tpl or "", None)


def _segment_of(m: MindMap, da: DriverAssumption) -> str | None:
    meta = _driver_meta(m, da.node_id)
    return meta.get("segment")


def evaluate_scenario(m: MindMap, sc: Scenario, *, today: date | None = None) -> ScenarioResult:
    """가정 묶음 하나를 돌린다. 맵의 meta.draft.financials / segments 와 A3.2 평가 기준만 쓴다 (digest 불필요)."""
    today = today or date.today()
    crit = Criteria.from_map(m)
    year = sc.eps_year or crit.eps_year or ((m.meta.get("draft") or {}).get("financials") or {}).get("year")
    tail = common_tail(m, year=year, per_override=sc.per)
    r = ScenarioResult(eps_basis=sc.eps_basis or crit.eps_basis, eps_year=year, computed_at=today, per=tail.per)
    inputs: list[SensitivityInput] = list(tail.inputs)
    missing = list(tail.missing)
    warnings: list[str] = []
    p0 = getattr(m, "current_price", None)
    rev = segment_values(m, year, "revenue")
    op = segment_values(m, year, "op")
    # 기준 OP (전사) — 결과 지표 표
    r.op_base = tail.op
    if tail.op is None:
        missing.append(f"전사 영업이익({year}) — 추정치 표")
    # 사업부 상태
    states: dict[str, SegState] = {}
    deltas: dict[str, float] = {}
    for da in sc.driver_assumptions:
        var = _variable_of(m, da)
        seg = _segment_of(m, da)
        n = m.get(da.node_id)
        label = n.label.split(" (")[0] if n is not None else da.node_id
        if da.delta_pct is None and da.value is None:
            continue   # 라벨만 있는 템플릿 줄 — 숫자는 사람이 넣는다
        if var in ("fcf", "orders", "backlog", None):
            warnings.append(f"{da.node_id} {label}: 변수 '{var}' 는 가격 경로에 넣지 않는다 (주주환원·수주는 별도)")
            continue
        if not seg:
            missing.append(f"{da.node_id} {label}: 사업부가 없어 가정을 적용할 수 없다 (meta.draft.drivers.segment)")
            continue
        st = states.get(seg)
        if st is None:
            rv, ov = rev.get(seg), op.get(seg)
            st = states[seg] = SegState(seg, rv[0] if rv else None, ov[0] if ov else None, rv[1] if rv else 0, ov[1] if ov else 0)
            user = {k: v for k, v in ((n.driver.sensitivity_inputs if n is not None and n.driver is not None else {}) or {}).items() if v is not None}
            if "pass_through" in user:
                st.k = float(user["pass_through"])
            if st.rev is None and "segment_revenue" in user:
                st.rev = float(user["segment_revenue"])
            if st.op is None and "segment_op" in user:
                st.op = float(user["segment_op"])
        before = st.delta()
        d = (da.delta_pct or 0.0) / 100.0
        if var == "asp":
            if st.rev is None:
                missing.append(f"{seg} 매출({year}) — 사업부 표 (A1 sensitivity_inputs.segment_revenue)")
            st.a += d
            inputs.append(_inp(f"{label} ASP Δ", da.delta_pct, "%", "user", year, f"{seg} 가격 경로"))
        elif var == "volume":
            if st.rev is None:
                missing.append(f"{seg} 매출({year}) — 사업부 표")
            st.v += d
            inputs.append(_inp(f"{label} 물량 Δ", da.delta_pct, "%", "user", year, f"{seg} 물량 경로 (원가도 비례)"))
        elif var in ("cost", "margin"):
            if st.rev is None:
                missing.append(f"{seg} 매출({year}) — 사업부 표")
            st.c += (da.delta_pct or 0.0)
            inputs.append(_inp(f"{label} 원가율 Δ", da.delta_pct, "%p", "user", year, f"{seg} 원가율"))
        elif var in ("op", "revenue", "margin_abs"):
            if da.value is not None:
                st.op_abs = float(da.value)
                inputs.append(_inp(f"{label} 영업이익", da.value, "억원", "user", year, f"{seg} 손익 절대값"))
            else:
                if st.op is None:
                    missing.append(f"{seg} 영업이익({year}) — 사업부 표 (A1 sensitivity_inputs.segment_op)")
                if da.unit == "억원":
                    st.op_add += (da.delta_pct or 0.0)
                    inputs.append(_inp(f"{label} 영업이익 Δ", da.delta_pct, "억원", "user", year, f"{seg} 손익 가감"))
                else:
                    st.op_pct += (da.delta_pct or 0.0)
                    inputs.append(_inp(f"{label} 손익 Δ", da.delta_pct, "%", "user", year, f"|{seg} 영업이익| × Δ%"))
        after = st.delta()
        if after is not None:
            deltas[da.node_id] = deltas.get(da.node_id, 0.0) + (after - (before or 0.0))
    for seg, st in states.items():
        if st.rev is not None:
            inputs.append(_inp(f"{seg} 매출", st.rev, "억원", "segment" if st.rev_n else "user", year, f"사업부 표 {st.rev_n}곳" if st.rev_n else None))
        if st.op is not None:
            inputs.append(_inp(f"{seg} 영업이익", st.op, "억원", "segment" if st.op_n else "user", year, f"사업부 표 {st.op_n}곳" if st.op_n else None))
        if st.k != 1.0:
            inputs.append(_inp(f"{seg} 전가율 k", st.k, "", "user"))
    # 겹치는 사업부 (DRAM 과 HBM 둘 다 가정) — 둘 다 적용되면 HBM 몫이 두 번 들어간다 → 경고만 (물량 가정은 HBM 만 따로 두는 쓰임이 있다)
    keep, dropped = drop_contained(list(states))
    for child, parent in dropped:
        warnings.append(f"{child} ⊂ {parent}: 두 사업부에 가정이 함께 있다 — {child} 가정은 {parent} 안의 몫으로 겹칠 수 있다")
    r.op_delta_by_node = {k: round(v, 2) for k, v in deltas.items()}
    # 전사 OP'
    if tail.op is not None:
        ds = {seg: st.delta() for seg, st in states.items()}
        if all(v is not None for v in ds.values()):
            r.op = tail.op + sum(v or 0.0 for v in ds.values())
        else:
            missing.append("사업부 손익 계산 불가 — " + ", ".join(seg for seg, v in ds.items() if v is None) + " 의 매출·영업이익 입력")
    # 꼬리
    if r.op is not None and tail.t is not None:
        r.np = r.op * (1.0 - tail.t)
        if tail.shares:
            basis = r.eps_basis or "consensus"
            eps_b, src, label, warn = eps_for_basis(m, basis, tail, crit)
            if warn:
                warnings.append(warn)
            if eps_b is not None and tail.np and tail.np > 0:
                # 기준 EPS 에 NP 변화율을 곱한다: EPS' = EPS_basis × NP'/NP — 가정이 없으면 Base = 기준 EPS × PER (평가 모델과 같다),
                # 가정 하나면 ΔEPS = EPS_E × ΔNP/NP_E = ΔNP/N(NP_E/EPS_E) 로 민감도와 같다
                r.eps = eps_b * (r.np / tail.np)
                inputs.append(_inp("기준 EPS", eps_b, "원", src, year, label + " — NP 변화율을 곱한다"))
            else:
                r.eps = r.np * 1e8 / tail.shares
                inputs.append(_inp("기준 EPS", None, "원", "missing", year, "기준 EPS 없음 — NP'/N 로 직접"))
            if tail.per is not None:
                r.price = r.eps * tail.per
                if p0:
                    r.price_vs_p0_pct = (r.price / p0 - 1.0) * 100.0
    if sc.name == "Bear" and r.price is not None and p0 and r.price >= p0:
        warnings.append(f"Bear 가격 {r.price:,.0f} 이 현재가 {p0:,.0f} 위 — 하락 시나리오가 아니다 (사이클 꺾임·멀티플 압축·기준 연도를 볼 것)")
    if sc.name == "Bull" and r.price is not None and p0 and r.price <= p0:
        warnings.append(f"Bull 가격 {r.price:,.0f} 이 현재가 아래")
    if not sc.driver_assumptions or all(da.delta_pct is None and da.value is None for da in sc.driver_assumptions):
        if sc.name != "Base":
            warnings.append("가정이 비어 있다 — 기준 경로(컨센) 그대로")
    r.basis_status = tail.basis_status
    if tail.basis_status == "not_comparable":
        warnings.insert(0, "EPS 기준 불일치 — 가격은 참고값")
    r.inputs, r.missing, r.warnings = inputs, missing, warnings
    r.status = "계산됨" if r.price is not None else "미계산"
    r.method = "OP' = Σ[R_i(1+a_i)(1+v_i)·k − C_i(1+v_i) − R_i'·c_i] + 기타 ; NP' = OP'(1−t) ; EPS' = NP'/N (기준 EPS 비례) ; P = EPS' × PER_s"
    return r


def scenario_line(sc: Scenario) -> str:
    r = sc.model
    if r is None:
        return f"{sc.name}: " + (f"{sc.target_price:,.0f}원" if sc.target_price else "미계산") + " (모델 없음)"
    if r.status != "계산됨":
        return f"{sc.name}: 미계산 — 필요: " + "; ".join(r.missing)[:160]
    parts = [("⚠ 참고값 " if r.basis_status == "not_comparable" else "") + (f"{sc.name}: OP {r.op / 1e4:,.1f}조" if r.op is not None else sc.name)]
    if r.eps is not None:
        parts.append(f"EPS {r.eps:,.0f}")
    parts.append(f"× PER {r.per:.2f}" if r.per else "")
    parts.append(f"→ {r.price:,.0f}원" + (f" ({r.price_vs_p0_pct:+.0f}%)" if r.price_vs_p0_pct is not None else ""))
    if sc.name == "Base" and not sc.driver_assumptions and sc.per is None and (sc.eps_basis or "consensus") == "consensus":
        parts.append("(= 목표가 중앙값 재현 — 순환, 내 PER 입력 전 참고)")       # 설계 v6 §11.2 (v7 T2)
    return " ".join(p for p in parts if p) + (" ⚠ " + r.warnings[0] if r.warnings else "")


SCENARIO_MEANING = {"Bear": "무엇이 무너지면 어디까지 떨어지나", "Base": "논제가 그대로 실현되면", "Bull": "무엇이 추가로 잘되면"}


def _assumption_text(m: MindMap, sc: Scenario) -> str:
    """가정 묶음을 사람 말로 — 'DRAM ASP −30% · 파운드리 손익 −20,000억원'. 없으면 Base 는 컨센 경로, 그 밖은 미입력."""
    parts: list[str] = []
    for da in sc.driver_assumptions:
        lab = m[da.node_id].label.split(" (")[0] if da.node_id in m else da.node_id
        var = f" {da.variable}" if da.variable else ""
        if da.delta_pct is not None:
            parts.append(f"{lab}{var} {da.delta_pct:+g}{da.unit or '%'}")
        elif da.value is not None:
            parts.append(f"{lab}{var} = {da.value:,g}{da.unit or ''}")
    if parts:
        return " · ".join(parts)
    if sc.assumptions:
        return "; ".join(a[:40] for a in sc.assumptions[:2]) + (" (문장 가정 — 모델에 안 들어감)" if sc.price_source != "computed" else "")
    return "가정 없음 = 컨센 경로" if sc.name == "Base" else "미입력"


def scenario_overview(m: MindMap) -> str:
    """A4.1 「시나리오 한눈에」 — 갈래마다 한 줄(뜻 · 가정 · PER · 확률 → 목표가 · 현재가 대비), 확률가중, 증권사 후보, 빠진 것.
    숫자는 통합 모델 결과(코드), 가정·확률은 사람이 넣는다."""
    if not isinstance(m, NewMap):
        return ""
    p0 = m.current_price
    lines = ["시나리오 = 드라이버 가정 묶음 + 적용 PER + 확률 → 통합 모델이 목표가 (가정·확률은 사람, 숫자는 코드)"]
    todo: list[str] = []
    probs: list[float] = []
    starred = False
    for name, nid in SCENARIO_IDS.items():
        sc = m[nid].scenario if nid in m else None
        head = f"  {name} ({SCENARIO_MEANING[name]}): "
        if sc is None:
            lines.append(head + "미입력")
            todo.append(f"{name} 가정")
            continue
        r = sc.model
        per = sc.per if sc.per else (r.per if r is not None and r.per else None)
        per_txt = (f"PER {per:.2f}" + ("" if sc.per else "*")) if per else "PER —"
        starred = starred or bool(per and not sc.per)
        p_txt = f"p {sc.probability:.0%}" if sc.probability is not None else "p —"
        tp = sc.target_price
        if tp:
            tp_txt = f"{tp:,.0f}원" + (f" ({tp / p0 - 1:+.0%})" if p0 else "")
        else:
            tp_txt = "미계산" + (" (필요: " + "; ".join(r.missing)[:60] + ")" if r is not None and r.missing else "")
        lines.append(head + f"{_assumption_text(m, sc)} · {per_txt} · {p_txt} → {tp_txt}")
        if sc.probability is None:
            todo.append(f"{name} 확률")
        else:
            probs.append(sc.probability)
        if not tp:
            todo.append(f"{name} 목표가")
        if name == "Base" and not sc.driver_assumptions and sc.per is None:
            lines.append("        (Base 가정 없음 = 컨센 EPS × 목표가 유도 PER → 목표가 중앙값 재현 — 순환, 내 PER 입력 전 참고)")
    if starred:
        lines.append("  (* = 적용 PER 기본값 — 목표가 중앙값 ÷ 컨센 EPS)")
    if probs and abs(sum(probs) - 1.0) > 0.011:
        todo.append(f"확률 합 {sum(probs):.0%} ≠ 100%")
    att = (m.meta.get("draft") or {}).get("attractiveness") or {}
    if att.get("status") == "계산됨":
        lines.append(f"  확률가중 V = Σ p·TP = {att['value']:,.0f}원 · 기대수익률 {att['er_price_pct']:+.1f}% · 하방(Bear) {att.get('downside_pct', 0):+.0f}% → A4.7")
    cands = (m.meta.get("draft") or {}).get("scenario_candidates") or []
    if cands:
        lines.append(f"  증권사 후보 {len(cands)}개 (리포트 제시 = 그대로 · 산식 재계산/목표가 = 사용자 재해석으로 기록) — 콘솔 「C」:")
        for i, c in enumerate(cands[:6]):
            tp = c.get("target_price")
            m_ = (f" {c.get('method')} {c.get('multiple'):g}배 × {c.get('base_year') or ''} {c.get('base_value'):,.0f}" if c.get("multiple") and c.get("base_value") else "")
            lines.append(f"    {i + 1}) {c.get('broker', '?')} {c.get('kind', '')}{(' ' + str(c.get('name'))) if c.get('name') else ''}{m_} → {tp:,.0f}원" + (f" ({tp / p0 - 1:+.0%})" if tp and p0 else ""))
    if todo:
        lines.append("  빠진 것: " + " · ".join(todo[:6]) + " — 콘솔 「C」 후보 넣기 · 「P」 예시 확률(미검증) · 「V」 가정·PER · HTML 계산기 ∑ · `mindmap scenario --set Bear.A1.1=-30 --probs 30/45/25`")
    else:
        lines.append("  세 갈래 모두 가정·확률·목표가가 있다 — 바꾸려면 「V」 또는 HTML 계산기 ∑")
    return "\n".join(lines)


def write_scenario_overview(m: MindMap, *, today: date | None = None) -> str:
    """A4.1 값에 쓴다 (compute_valuation_all 끝에서)."""
    txt = scenario_overview(m)
    if isinstance(m, NewMap) and "A4.1" in m and txt:
        m["A4.1"].value = txt
        m["A4.1"].as_of = today or date.today()
    return txt


def apply_bear_template(m: MindMap, sector: str | None, *, today: date) -> bool:
    """Bear 노드가 비어 있으면 섹터 템플릿 라벨을 질문에 적는다 — 숫자는 넣지 않는다 (사용자 결정 ⑥)."""
    labels = BEAR_TEMPLATES.get(sector or "")
    if not labels or "A4.2" not in m:
        return False
    n = m["A4.2"]
    if n.scenario is not None:
        return False
    tag = "Bear 템플릿: " + " · ".join(labels)
    if tag not in n.question:
        n.question = (n.question + f" — {tag} (숫자는 사람이 넣는다; 현재가가 이미 '이익 지속성 의심' 을 내재하면 완만한 Bear 는 현재가 위에 뜬다)").strip(" —")
    draft = m.meta.setdefault("draft", {})
    draft["scenario_template"] = {"Bear": labels, "sector": sector}
    return True


def compute_scenarios(m: MindMap, *, today: date | None = None) -> list[str]:
    """A4.2~4 중 price_source=computed 인 시나리오를 다시 계산해 model·target_price 를 쓴다. 미계산이면 기존 가격은 두고 경고."""
    if not isinstance(m, NewMap):
        return []
    today = today or date.today()
    ensure_valuation_nodes(m)
    notes: list[str] = []
    for name, nid in SCENARIO_IDS.items():
        n = m.get(nid)
        if n is None or n.scenario is None:
            continue
        sc = n.scenario
        if sc.price_source != "computed":
            continue
        r = evaluate_scenario(m, sc, today=today)
        sc.model = r
        if r.price is not None:
            sc.target_price = round(r.price, -2) if r.price >= 10000 else round(r.price)
            sc.valuation_method = f"통합 모델 — {r.eps_basis or 'consensus'} EPS × PER {r.per:.2f}" if r.per else "통합 모델"
            n.as_of = today
        else:
            notes.append(f"{name}: 미계산 — " + "; ".join(r.missing)[:120] + (" (기존 목표가 유지)" if sc.target_price else ""))
        notes += [f"{name}: ⚠ {w}" for w in r.warnings]
    return notes


__all__ = ["evaluate_scenario", "compute_scenarios", "scenario_line", "scenario_overview", "write_scenario_overview", "apply_bear_template", "SCENARIO_IDS", "SCENARIO_MEANING", "BEAR_TEMPLATES"]
