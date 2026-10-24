"""드라이버 민감도 (설계 v5 §3 / R3) — 원인 변수의 변화 → 영업이익 → 순이익 → EPS → 적정주가. 1차 선형, 다른 변수는 고정.

    price_volume  ΔOP = 사업부 매출 × Δ% × k          (DRAM ASP · HBM 출하: 가격/물량이 그대로 이익으로 — k 는 전가율, 기본 1.0)
    cost_ratio    ΔOP = −사업부 매출 × Δ%p            (원가율이 Δ%p 오르면)
    direct_op     ΔOP = |사업부 영업이익| × Δ%         (파운드리 적자가 Δ% 축소되면)
    payout        Δ주당 환원 = FCF × Δ%p / N           (FCF 의 환원율이 Δ%p 오르면 — 주가 환산은 하지 않는다)
    orders        ΔOP = 수주 × Δ% × 매출 전환율 × 마진   (수주 산업 — 전환율·마진은 사용자 입력)
    그 뒤 공통:   ΔNP = ΔOP × (1 − t),  t = 1 − NP_E/OP_E ;  ΔEPS = ΔNP / N,  N = NP_E/EPS_E ;  ΔP = ΔEPS × PER_target,  PER_target = TP_median / EPS_E

입력마다 출처를 적는다 — table(추정치 표) · segment(사업부 표) · claim · user(편집 폼 sensitivity_inputs) · default(k=1.0) · derived.
빠진 입력이 있으면 status=미계산 + missing 목록. 코드만 계산한다 — LLM 은 민감도를 만들지 않는다. 금액은 억원, EPS·주가는 원.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..models import NewMap, Sensitivity, SensitivityInput

DEFAULT_PASS_THROUGH = 1.0
DEFAULT_DELTA = {"price_volume": 10.0, "cost_ratio": 1.0, "direct_op": 10.0, "payout": 10.0, "orders": 10.0}   # % 또는 %p (템플릿 설명 참조)
TEMPLATE_BY_VARIABLE = {"asp": "price_volume", "volume": "price_volume", "cost": "cost_ratio", "margin": "cost_ratio", "op": "direct_op",
                        "fcf": "payout", "orders": "orders", "backlog": "orders"}
TEMPLATE_LABEL = {"price_volume": "가격·물량", "cost_ratio": "원가율", "direct_op": "손익 직접", "payout": "주주환원", "orders": "수주"}


def _year_of(period: str | None) -> str | None:
    if not period:
        return None
    p = period.rstrip("EPAF")
    if "Q" in p and len(p) == 4:
        return "20" + p[2:4] + "E"
    return p + "E" if len(p) == 4 and p.isdigit() else period


def financial_snapshot(m: NewMap, digest: Any, *, year: str | None = None) -> dict[str, Any]:
    """추정치 표에서 민감도 공통 입력을 맵(meta.draft.financials) 에 남긴다 — 편집·재계산 때 digest 없이 쓰기 위해."""
    draft = m.meta.setdefault("draft", {})
    if digest is None:
        return draft.get("financials") or {}
    year = year or digest.next_estimate_year("op") or digest.next_estimate_year("np")
    out: dict[str, Any] = {"year": year}
    if year:
        for met in ("np", "op", "eps", "revenue"):
            c = digest.cell(met, year)
            if c is not None:
                out[met] = {"mean": c.mean, "n": c.n, "by_broker": dict(c.by_broker)}
    if digest.n_tp:
        out["tp_median"] = digest.tp_median
        out["tp_n"] = digest.n_tp
    # 설계 v6: 연도별 표 (EPS 정상화 · DPS · 순차입금 · FCF …) · 발행주식수 · 순이익 기준 · 리포트 날짜 — 평가 모델·신뢰도가 digest 없이 쓴다
    from .valuation import SNAP_METRICS

    years: dict[str, dict[str, Any]] = {}
    for met in SNAP_METRICS:
        for yr in digest.years(met):
            c = digest.cell(met, yr)
            if c is not None and c.n:
                years.setdefault(yr, {})[met] = {"mean": c.mean, "median": c.median, "n": c.n, "by_broker": dict(c.by_broker)}
    if years:
        out["years"] = years
    la = digest.latest_actual_year("op") or digest.latest_actual_year("np") or digest.latest_actual_year("revenue")
    if la:
        out["latest_actual_year"] = la
    if getattr(digest, "shares_out", None):
        out["shares_out"] = {"median": digest.shares_out, "by_broker": dict(digest.shares_by_broker)}
    basis = {(f.broker or f.path.name): f.basis.get("np") for f in digest.aggregated if f.basis.get("np")}
    if basis:
        out["np_basis"] = basis
    out["report_dates"] = sorted(str(f.date) for f in digest.aggregated if f.date)
    out["as_of"] = str(digest.as_of)
    draft["financials"] = out
    return out


def segment_snapshot(m: NewMap, digest: Any, segments: list[str], cols: list[str]) -> dict[str, Any]:
    """사업부 표 셀(평균·증권사별) 을 맵(meta.draft.segments) 에 남긴다."""
    draft = m.meta.setdefault("draft", {})
    if digest is None or not getattr(digest, "segments", None):
        return draft.get("segments") or {}
    out = draft.get("segments") or {}
    for seg in segments:
        for met in ("revenue", "op"):
            for col in cols:
                c = digest.segment_cell(seg, met, col)
                if c is None:
                    continue
                out.setdefault(seg, {}).setdefault(met, {})[col] = {"mean": c.mean, "n": c.n, "by_broker": dict(c.by_broker)}
    draft["segments"] = out
    return out


def _inp(name: str, value: float | None, unit: str | None, source: str, period: str | None = None, note: str | None = None) -> SensitivityInput:
    return SensitivityInput(name=name, value=value, unit=unit, source=source if value is not None else "missing", period=period, note=note)


def compute_sensitivity(m: NewMap, node_id: str, *, delta_pct: float | None = None, today: date | None = None) -> Sensitivity:
    """A1.x 드라이버 하나의 민감도. 맵의 meta.draft.financials / segments 와 Driver.sensitivity_inputs 만 쓴다."""
    today = today or date.today()
    n = m[node_id]
    d = n.driver
    assert d is not None, f"{node_id} is not a driver"
    draft = m.meta.get("draft") or {}
    info = (draft.get("drivers") or {}).get(node_id) or {}
    variable = info.get("variable") or (d.metric.split(":")[1] if d.metric and ":" in d.metric else None)
    template = info.get("template") or TEMPLATE_BY_VARIABLE.get(variable or "", "price_volume")
    user = {k: v for k, v in (d.sensitivity_inputs or {}).items() if v is not None}
    fin = draft.get("financials") or {}
    year = fin.get("year")
    segs = draft.get("segments") or {}
    segment = info.get("segment")
    delta = float(user.get("delta_pct", delta_pct if delta_pct is not None else DEFAULT_DELTA.get(template, 10.0)))
    inputs: list[SensitivityInput] = []
    missing: list[str] = []
    s = Sensitivity(template=template, input_delta_pct=delta, computed_at=today)

    def seg_val(metric: str) -> tuple[float | None, str]:
        key = {"revenue": "segment_revenue", "op": "segment_op"}[metric]
        if key in user:
            return float(user[key]), "user"
        if segment and year and segs.get(segment, {}).get(metric, {}).get(year):
            return float(segs[segment][metric][year]["mean"]), "segment"
        return None, "missing"

    # ---- 템플릿별 ΔOP (억원)
    op_delta: float | None = None
    if template == "price_volume":
        rev, src = seg_val("revenue")
        inputs.append(_inp(f"{segment or '사업부'} 매출", rev, "억원", src, year, "세그먼트 표 평균" if src == "segment" else None))
        k = float(user.get("pass_through", DEFAULT_PASS_THROUGH))
        inputs.append(_inp("전가율 k", k, "", "user" if "pass_through" in user else "default", None, "기본값 1.0 = 가격·물량 변화가 전부 이익으로" if "pass_through" not in user else None))
        inputs.append(_inp(f"{variable or '변수'} 변화", delta, "%", "user" if "delta_pct" in user else "default", d.period))
        if rev is None:
            missing.append(f"{segment or '사업부'} 매출({year or '연간'}) — 사업부 표 없음: 편집 폼 sensitivity_inputs.segment_revenue (억원)")
        else:
            op_delta = rev * delta / 100.0 * k
        s.method = f"ΔOP = {segment or '사업부'} 매출 × Δ{variable or ''}% × k"
    elif template == "cost_ratio":
        rev, src = seg_val("revenue")
        inputs.append(_inp(f"{segment or '사업부'} 매출", rev, "억원", src, year))
        inputs.append(_inp("원가율 변화", delta, "%p", "user" if "delta_pct" in user else "default", d.period))
        if rev is None:
            missing.append(f"{segment or '사업부'} 매출({year or '연간'}) — sensitivity_inputs.segment_revenue")
        else:
            op_delta = -rev * delta / 100.0
        s.method = f"ΔOP = −{segment or '사업부'} 매출 × Δ원가율%p"
    elif template == "direct_op":
        op, src = seg_val("op")
        inputs.append(_inp(f"{segment or '사업부'} 영업이익", op, "억원", src, year))
        inputs.append(_inp("손익 개선", delta, "%", "user" if "delta_pct" in user else "default", d.period, "적자면 적자 축소, 흑자면 이익 증가"))
        if op is None:
            missing.append(f"{segment or '사업부'} 영업이익({year or '연간'}) — sensitivity_inputs.segment_op")
        else:
            op_delta = abs(op) * delta / 100.0
        s.method = f"ΔOP = |{segment or '사업부'} 영업이익| × Δ%"
    elif template == "payout":
        fcf = user.get("fcf")
        fsrc, fnote = "user", None
        if fcf is None:
            # 설계 v6 S1: 표의 FCF 행 (또는 영업활동현금흐름 − CAPEX) 을 먼저 쓴다
            from .valuation import year_cell

            c = year_cell(fin, year, "fcf")
            if c and c.get("mean") is not None:
                fcf, fsrc, fnote = float(c["mean"]), "table", f"표 FCF 행 ({c.get('n', 0)}곳)"
            else:
                o, cx = year_cell(fin, year, "ocf"), year_cell(fin, year, "capex")
                if o and cx and o.get("mean") is not None and cx.get("mean") is not None:
                    fcf, fsrc, fnote = float(o["mean"]) - abs(float(cx["mean"])), "derived", "영업활동현금흐름 − CAPEX (표)"
        inputs.append(_inp("FCF", float(fcf) if fcf is not None else None, "억원", fsrc if fcf is not None else "missing", year,
                           fnote or ("영업활동현금흐름 − CAPEX (표에 없음 — 사용자 입력)" if fcf is None else None)))
        inputs.append(_inp("환원율 변화", delta, "%p", "user" if "delta_pct" in user else "default", d.period))
        if fcf is None:
            missing.append("FCF(억원) — sensitivity_inputs.fcf (리포트 표에 현금흐름 행이 없다)")
        else:
            user = {**user, "fcf": fcf}
        s.method = "Δ주당 환원 = FCF × Δ환원율%p / N (주가 환산 없음)"
    elif template == "orders":
        orders = user.get("orders")
        conv, margin = user.get("conversion_rate"), user.get("margin")
        inputs.append(_inp("수주", float(orders) if orders is not None else None, "억원", "user" if orders is not None else "missing", year))
        inputs.append(_inp("매출 전환율", float(conv) if conv is not None else None, "", "user" if conv is not None else "missing"))
        inputs.append(_inp("마진", float(margin) if margin is not None else None, "", "user" if margin is not None else "missing"))
        inputs.append(_inp("수주 변화", delta, "%", "user" if "delta_pct" in user else "default", d.period))
        if None in (orders, conv, margin):
            missing.append("수주·매출 전환율·마진 — sensitivity_inputs.orders / conversion_rate / margin")
        else:
            op_delta = float(orders) * delta / 100.0 * float(conv) * float(margin)
        s.method = "ΔOP = 수주 × Δ% × 전환율 × 마진"
    # ---- 공통: OP → NP → EPS → 주가 — 평가 모델·시나리오와 같은 꼬리 (설계 v6 `valuation.common_tail`)
    from .valuation import common_tail

    tail = common_tail(m, user, year=year)
    inputs += tail.inputs
    tax, shares, per = tail.t, tail.shares, tail.per
    if template == "payout":
        n_out = tail.shares_out or shares   # 주당 환원은 공시 발행주식수로 (설계 v6 — 사용자 결정 ⑦)
        if "FCF" in " ".join(missing):
            pass
        elif n_out:
            per_share = float(user["fcf"]) * delta / 100.0 * 1e8 / n_out
            s.price_delta = per_share
            s.price_delta_pct = (per_share / m.current_price * 100.0) if m.current_price else None
        else:
            missing.append("주식 수")
    elif op_delta is not None:
        s.op_delta = op_delta
        if tax is None:
            missing.append("유효세율 (표의 순이익·영업이익)")
        if shares is None:
            missing.append("주식 수 (표의 순이익·EPS)")
        if per is None:
            missing.append("목표 PER (목표가·EPS)")
        if tax is not None:
            s.np_delta = op_delta * (1.0 - float(tax))
            if shares:
                s.eps_delta = s.np_delta * 1e8 / float(shares)
                if per is not None:
                    s.price_delta = s.eps_delta * float(per)
                    s.price_delta_pct = (s.price_delta / m.current_price * 100.0) if m.current_price else None
    s.inputs = inputs
    s.missing = missing
    s.status = "계산됨" if (s.price_delta is not None or (template == "payout" and s.price_delta is not None)) else "미계산"
    return s


def compute_all(m: NewMap, *, today: date | None = None) -> list[str]:
    """A1 원인 드라이버 전부 — role=cause 이고 템플릿을 알 수 있는 것만. 돌려주는 것은 한 줄 요약들."""
    out: list[str] = []
    for n in m.drivers():
        d = n.driver
        if d is None or d.role != "cause":
            continue
        s = compute_sensitivity(m, n.id, today=today)
        d.sensitivity = s
        out.append(f"{n.id} {n.label.split(' (')[0]}: {sensitivity_line(s)}")
    return out


def sensitivity_line(s: Sensitivity | None) -> str:
    if s is None:
        return "민감도 없음"
    if s.status != "계산됨":
        return "민감도 미계산 — 필요: " + "; ".join(s.missing)[:160]
    srcs = {"segment": "사업부 표", "table": "표", "user": "사용자", "default": "기본값", "derived": "유도", "claim": "Claim"}
    cnt = {}
    for i in s.inputs:
        cnt[i.source] = cnt.get(i.source, 0) + 1
    tail = " · ".join(f"{srcs.get(k, k)} {v}" for k, v in cnt.items() if k != "missing")
    if s.template == "payout":
        return f"환원율 +{s.input_delta_pct:g}%p → 주당 환원 {s.price_delta:+,.0f}원 (입력: {tail})"
    parts = [f"{s.input_delta_pct:+g}{'%p' if s.template in ('cost_ratio', 'payout') else '%'}"]
    if s.op_delta is not None:
        parts.append(f"영업이익 {s.op_delta / 10000:+,.1f}조")
    if s.eps_delta is not None:
        parts.append(f"EPS {s.eps_delta:+,.0f}원")
    if s.price_delta is not None:
        parts.append(f"적정주가 {s.price_delta:+,.0f}원" + (f" ({s.price_delta_pct:+.1f}%)" if s.price_delta_pct is not None else ""))
    return " → ".join(parts) + f" (입력: {tail})"


def sensitivity_table(m: NewMap) -> str:
    rows = [f"{m.map_id} 민감도 (1차 선형 · 다른 변수 고정 · 드라이버끼리 더하지 말 것)"]
    for n in m.drivers():
        d = n.driver
        if d is None or d.role != "cause":
            continue
        s = d.sensitivity
        rows.append(f"  {n.id} {n.label.split(' (')[0]:<14} [{TEMPLATE_LABEL.get(s.template, s.template) if s else '-'}] {sensitivity_line(s)}")
        if s is not None:
            for i in s.inputs:
                v = "없음" if i.value is None else (f"{i.value:,.4g}" if abs(i.value) < 1e6 else f"{i.value:,.0f}")
                rows.append(f"      {i.name:<14} {v:>14} {i.unit or '':<4} {i.source:<8} {i.period or '':<6} {i.note or ''}")
            if s.method:
                rows.append(f"      식: {s.method}")
    return "\n".join(rows)


def sensitivity_short(s: Sensitivity | None) -> str:
    """노드 한 줄용 짧은 표기."""
    if s is None:
        return ""
    if s.status != "계산됨":
        return "민감도 미계산"
    unit = "%p" if s.template in ("cost_ratio", "payout") else "%"
    if s.template == "payout":
        return f"민감도 {s.input_delta_pct:+g}{unit} → 주당 환원 {s.price_delta:+,.0f}원"
    return (f"민감도 {s.input_delta_pct:+g}{unit} → 적정주가 {s.price_delta:+,.0f}원" + (f" ({s.price_delta_pct:+.1f}%)" if s.price_delta_pct is not None else "")
            + (" · k 기본값" if any(i.name == "전가율 k" and i.source == "default" for i in s.inputs) else ""))


__all__ = ["compute_sensitivity", "compute_all", "sensitivity_line", "sensitivity_short", "sensitivity_table", "financial_snapshot", "segment_snapshot",
           "DEFAULT_PASS_THROUGH", "DEFAULT_DELTA", "TEMPLATE_BY_VARIABLE"]
