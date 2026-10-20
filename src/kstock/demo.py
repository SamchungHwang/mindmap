"""예시 인스턴스 (템플릿 §9) — 가상 종목 '가온로보틱스' 로 A → B → C 를 끝까지 돌린다.

CLI `mindmap demo` 와 테스트가 함께 쓴다. 숫자는 모두 임의값이다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .engine import (
    apply_event,
    evaluate_holding,
    evaluate_new_map,
    record_decision,
    rollup,
    set_thesis_state,
    snapshot_to_holding,
    update_driver,
    update_market,
    validate,
)
from .models import (
    BiasCheck,
    Driver,
    Event,
    HoldingMap,
    MarketState,
    Measurement,
    NewMap,
    Portfolio,
    Position,
    Rule,
    Scenario,
    Source,
)
from .render import to_markdown, write_html
from .templates import new_map_skeleton

D0 = date(2026, 10, 19)  # A 작성·매수
D1 = date(2027, 1, 15)  # B 평가


def build_new_map(today: date = D0) -> NewMap:
    a = new_map_skeleton("000000", "가온로보틱스", "growth", today)
    krx = Source(name="KRX 종가", retrieved_at=today)
    rep = Source(name="분기보고서 (2026.2Q)", url="https://dart.fss.or.kr/", retrieved_at=today)
    a["A0"].set("가온로보틱스는 휴머노이드 액추에이터 양산 전환으로 12개월 내 OPM 8%→13%, 주가 85,000원. "
                "시장은 아직 2027년 수주잔고 전환율을 반영하지 않음", as_of=today)
    a["A0.2"].set("1Y", as_of=today)
    a["A0.3"].set("M", as_of=today)
    a["A0.4"].set(58000, as_of=today, source=krx)

    a["A1.1"].label, a["A1.1"].unit = "영업이익률", "%"
    a["A1.1"].driver = Driver(current=Measurement(value=8.1, as_of=date(2026, 8, 14), source=rep),
                              consensus=Measurement(value=10.0, as_of=date(2026, 10, 10)),
                              my_estimate=Measurement(value=13.0, as_of=today, rationale="원가 구조 개선 + 믹스 변화"),
                              check_indicator="분기보고서 영업이익률, 월별 수출 단가", next_check=date(2026, 11, 14))
    a["A1.2"].label, a["A1.2"].unit = "수주잔고", "억원"
    a["A1.2"].driver = Driver(current=Measurement(value=4200, as_of=date(2026, 8, 14), source=rep),
                              consensus=Measurement(value=4500), my_estimate=Measurement(value=6000),
                              check_indicator="IR 자료·공시", next_check=date(2026, 11, 14))
    a["A1.3"].label, a["A1.3"].unit = "양산 라인 가동률", "%"
    a["A1.3"].driver = Driver(current=Measurement(value=55, as_of=date(2026, 9, 30)),
                              consensus=Measurement(value=65), my_estimate=Measurement(value=80),
                              check_indicator="탐방·공급사 코멘트", next_check=date(2026, 12, 1))

    a["A2.1"].set("OPM 10% 안착, 수주잔고 4,500억 유지, PER 15배", as_of=today)
    a["A2.2"].set("양산 라인 가동률 80% 도달 시 OPM 13%. 2027년 해외 고객 수주가 잔고에 아직 없음", as_of=today)
    a["A2.3"].set("시간축", as_of=today)

    a["A3.1"].scenario = Scenario(name="Bear", probability=0.25, target_price=48000,
                                  assumptions=["양산 지연 6개월", "OPM 7%"], valuation_method="PER 12배 × 2027E EPS")
    a["A3.2"].scenario = Scenario(name="Base", probability=0.50, target_price=85000,
                                  assumptions=["OPM 13%", "PER 18배"], valuation_method="PER 밴드 중앙값 × 2027E EPS")
    a["A3.3"].scenario = Scenario(name="Bull", probability=0.25, target_price=120000,
                                  assumptions=["해외 고객 추가", "PER 25배"], valuation_method="PER 25배 × 2027E EPS")
    a["A3.6.1"].set(45, as_of=today, source=krx)
    a["A3.6.3"].set("up", as_of=today, source=Source(name="컨센서스 집계", retrieved_at=today))

    a["A4.1"].set({"probability": 0.3, "impact_pct": -25, "text": "양산 수율 문제로 전환 지연"}, as_of=today)
    a["A4.2"].set({"probability": 0.2, "impact_pct": -15, "text": "경쟁사 가격 인하"}, as_of=today)
    a["A4.4.1"].rule = Rule(trigger_type="fact", condition="driver[A1.2].current < 3500", action="exit",
                            escalate_to="RED", note="수주잔고가 매수 시점보다 줄면 논제 폐기")
    a["A4.5"].set("월별 수출 단가, 공급사 가동률 코멘트", as_of=today)

    a["A5.1"].question = "날짜가 박힌 사건만"
    a["A6.2"].set({"price_range": [55000, 60000], "tranches": 3}, as_of=today)
    a["A6.3"].rule = Rule(trigger_type="price", condition="drawdown > 0.25", action="review", escalate_to="YELLOW")
    a["A6.4"].rule = Rule(trigger_type="price", condition="price > 110000", action="trim", size="50%")
    a["A6.5"].set("BUY", as_of=today)
    a["A8.2"].links = ["S.KR-ROBOT"]
    a["A8.3"].links = ["M0"]
    a["A8.1.1"].set("휴머노이드·협동로봇용 고정밀 액추에이터 모듈 B2B 공급", as_of=today, source=rep)
    return a


def build_all(out_dir: str | Path | None = None) -> tuple[NewMap, HoldingMap, Portfolio, dict]:
    """A 평가 → 매수 스냅숏 → 시장 갱신 → B 평가 → 결정 로그 → C 롤업. out_dir 를 주면 JSON·HTML 저장."""
    report: dict = {}
    a = build_new_map()
    a_issues = validate(a, today=D0)
    ev = evaluate_new_map(a, today=D0)
    a["A6.1"].value = {**(a["A6.1"].value or {}), "confirmed": 0.05, "reason": "신규 섹터 — 보수적으로 5%"}
    report["A"] = {"issues": [str(i) for i in a_issues], "expected_return": ev.expected_return,
                   "risk_reward": ev.risk_reward, "suggested_weight": ev.suggested_weight,
                   "gate": ev.rr_gate_passed, "decision": a.decision.value if a.decision else None}

    pos = Position(entry_date=D0, entry_price=58000, quantity=862, w_target=0.05, eps_fwd_entry=3200)
    b = snapshot_to_holding(a, pos, today=D0)
    rep = Source(name="분기보고서 (2026.3Q)", url="https://dart.fss.or.kr/", retrieved_at=D1)
    update_market(b, MarketState(as_of=D1, price=76000, eps_fwd=3392, band_percentile=82, w_now=0.072,
                                 total_assets=1_000_000_000, adv_shares=50_000, cash_yield=0.03))
    update_driver(b, "B2.1.1", 9.0, as_of=D1, source=rep)
    update_driver(b, "B2.1.2", 4400, as_of=D1, source=rep)
    update_driver(b, "B2.1.3", 62, as_of=D1)
    for n in b.drivers():
        if n.driver:
            n.driver.next_check = date(2027, 2, 14)
    apply_event(b, Event(date=date(2027, 1, 10), fact="3Q 영업이익률 9.0% — 컨센서스 소폭 하회", affects=["B2.1.1"],
                         direction="약화", priced_in="부분", materiality="M", source=rep))
    apply_event(b, Event(date=date(2026, 12, 20), fact="국내 대형 고객 추가 수주 200억", affects=["B2.1.2"],
                         direction="강화", priced_in="예", materiality="M", source=rep))
    set_thesis_state(b, "유효", today=D1, note="OPM 개선 속도는 느리지만 방향 유지, 수주 증가")
    b["B0.1"].set(True, as_of=D1)
    # B3 재추정 (Bear 상향)
    b["B3.1"].scenario = Scenario(name="Bear", probability=0.25, target_price=62000, assumptions=["OPM 9% 정체"])
    b["B3.2"].scenario = Scenario(name="Base", probability=0.50, target_price=85000, assumptions=["OPM 13%"])
    b["B3.3"].scenario = Scenario(name="Bull", probability=0.25, target_price=110000, assumptions=["해외 고객"])
    res = evaluate_holding(b, today=D1)
    log = record_decision(b, "trim", today=D1, reason="멀티플 확장만으로 상승, 목표 비중 초과", size="7.2% → 5%",
                          bias_check=BiasCheck(disposition=True, note="이익 종목이라 팔기 쉬운 쪽으로 기울었는지 확인"))
    report["B"] = {"light": res.light.light.value, "reasons": res.light.reasons, "matrix": str(res.matrix),
                   "expected_return": res.expected_return, "risk_reward": res.risk_reward,
                   "decomposition": res.decomposition.as_dict() if res.decomposition else None,
                   "decision": log.decision.value, "override": log.override,
                   "issues": [str(i) for i in validate(b, today=D1)]}

    c = rollup([b], cash=80_000_000, total_assets=1_000_000_000, today=D1)
    report["C"] = {"C0": c.value("C0"), "C3.1": c.value("C3.1"), "C3.2": c.value("C3.2"),
                   "C5.4": c.value("C5.4"), "warnings": c.meta.get("warnings", [])}

    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        a.save(out / "gaon_A.json")
        b.save(out / "gaon_B.json")
        c.save(out / "gaon_C.json")
        write_html(a, out / "gaon_A.html")
        write_html(b, out / "gaon_B.html")
        write_html(b, out / "gaon_B_summary.html", summary=True)
        write_html(c, out / "gaon_C.html")
        (out / "gaon_A.md").write_text(to_markdown(a), encoding="utf-8")
        report["files"] = sorted(p.name for p in out.iterdir())
    return a, b, c, report


__all__ = ["build_new_map", "build_all", "D0", "D1"]
