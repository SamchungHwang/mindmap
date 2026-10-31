"""원인 드라이버에 반대 성질의 셀이 들어오지 않는다 (2026-10-31 NAVER 실측).

「마케팅·인프라 비용」(cost) 에 맞는 셀이 없자 같은 driver_id 의 영업이익(op) 셀 4,789억원을 컨센으로 집어 왔다 —
비용 드라이버가 영업이익을 들고 higher_is_better=False 라 민감도·성립 조건이 거꾸로 돈다. 변수가 안 맞을 때의 떨어짐은
서로 대신할 수 있는 변수 사이에서만 한다.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from helpers import D0  # noqa: E402, F401
from kstock.generate.cause import (  # noqa: E402
    cause_cells,
    fill_cause_drivers,
    pick_cell,
    variables_compatible,
)
from kstock.models import Claim, ClaimSet, QuantRef  # noqa: E402
from kstock.templates import new_map_skeleton  # noqa: E402

TODAY = date(2026, 10, 31)


def _claim(cid: str, rid: str, broker: str, prop: str, did: str, quants: list[tuple[str, str, float, str]]) -> Claim:
    return Claim(claim_id=cid, report_id=rid, broker=broker, proposition=prop, driver_id=did, status="verified",
                 quant_refs=[QuantRef(metric=mt, col=col, value=v, unit=u, source="text") for mt, col, v, u in quants])


class TestCostDriverGuard(unittest.TestCase):
    def test_compatibility_table(self):
        self.assertTrue(variables_compatible("cost", "cost"))
        self.assertFalse(variables_compatible("cost", "op"))
        self.assertFalse(variables_compatible("op", "cost"))
        self.assertTrue(variables_compatible("volume", "asp"))      # 같은 사업부의 다른 수량 변수 — 대신할 수 있다
        self.assertTrue(variables_compatible(None, "op"))           # 정성 KPI 는 아무 셀이나

    def test_marketing_cost_does_not_take_op_cell(self):
        hana = ClaimSet(report_id="hk_97166", broker="하나증권", claims=[
            _claim("c11", "hk_97166", "하나증권", "3Q26 영업이익 4,789억원 전망 — 마케팅비·인프라비 증가에도 불구", "marketing_cost",
                   [("op", "3Q26", 4789.0, "억원")]),
        ])
        cells = cause_cells([hana], sector="KR-INTERNET")
        self.assertEqual([(c.driver_id, c.variable) for c in cells], [("marketing_cost", "op")])
        self.assertIsNone(pick_cell(cells, "marketing_cost", "cost"))
        m = new_map_skeleton("035420", "NAVER", "growth", TODAY, n_drivers=5)
        notes = fill_cause_drivers(m, sector="KR-INTERNET", claimsets=[hana], issue_set=None, today=TODAY)
        n = next(x for x in m.drivers() if x.label.startswith("마케팅·인프라 비용"))
        self.assertIsNone(n.driver.consensus.value)                   # 영업이익을 비용 컨센으로 쓰지 않는다
        self.assertEqual(n.driver.kind, "qualitative")
        self.assertNotIn("op", n.driver.metric.split(":")[1])
        self.assertNotIn("(op", n.label)
        self.assertTrue(any("쓰지 않는다" in x for x in notes), notes)


if __name__ == "__main__":
    unittest.main()


class TestRelativeMetricGuard(unittest.TestCase):
    """할인율·배수 같은 비율 수치는 드라이버의 값이 아니다 (2026-10-31 NAVER 실측: discount 45% → 「AI 데이터센터 ASP 45%」)."""

    def test_discount_is_not_asp(self):
        from kstock.generate.cause import is_relative_metric

        self.assertTrue(is_relative_metric("discount"))
        self.assertTrue(is_relative_metric("target_per"))
        self.assertTrue(is_relative_metric("", "온디맨드 가격의 45%를 할인한 장기계약 단가"))
        self.assertFalse(is_relative_metric("dram_asp_qoq"))
        self.assertFalse(is_relative_metric("capacity_1h27"))
        hana = ClaimSet(report_id="hk_97166", broker="하나증권", claims=[
            _claim("c8", "hk_97166", "하나증권",
                   "네오클라우드 매출 추정은 MW당 연간 매출액 235억원을 가정하며, 글로벌 네오클라우드 B300 On-demand 렌탈 가격의 45%를 할인한 보수적 장기 계약 단가이므로",
                   "ai_dc", [("discount", "", 45.0, "%")]),
        ])
        skipped: list[dict] = []
        cells = cause_cells([hana], sector="KR-INTERNET", skipped=skipped)
        self.assertEqual(cells, [])
        self.assertEqual([(x["driver_id"], x["metric"], x["value"]) for x in skipped], [("ai_dc", "discount", 45.0)])
        m = new_map_skeleton("035420", "NAVER", "growth", TODAY, n_drivers=5)
        notes = fill_cause_drivers(m, sector="KR-INTERNET", claimsets=[hana], issue_set=None, today=TODAY)
        n = next(x for x in m.drivers() if x.label.startswith("AI 데이터센터"))
        self.assertIsNone(n.driver.consensus.value)
        self.assertEqual(n.driver.kind, "qualitative")
        self.assertNotIn("(asp", n.label)
        self.assertIn("가정 메모", n.question)                                   # 버리지 않고 사람이 보게
        self.assertIn("45", n.question)
        self.assertTrue(any("가정 메모" in x for x in notes), notes)
        self.assertEqual(m.meta["draft"]["skipped_cells"][0]["claim_id"], "c8")


class TestFlowDirectionAndPeriod(unittest.TestCase):
    """2026-10-31 네 번째 검토 (실제 10-31 NAVER 지도). ① 「네이버클라우드가 연간 지급할 GPUaaS 이용료 연 3조원」(비용) 이 AI DC 매출 컨센 3조원이 됐다.
    ② 2025 마케팅 비용 1.92조원이 2026-10 의 컨센·내 추정이 됐다 (2026E 2.53조원이 있었는데도). ③ 제안 라벨이 「하나증권 77,599조원」."""

    # 실제 Claim 문장 (data/claims/hk_97166.json c3 · c17 · c18)
    C3 = "NAVER가 약 18.7조원의 지급보증을 제공하며, 이는 네이버클라우드가 연간 지급할 GPUaaS 이용료가 대략 연 3조원임을 시사한다."
    C17 = "마케팅 비용은 2026년 2,527.3십억원으로 전년 1,920.3십억원 대비 크게 늘어나며, 3Q26F·4Q26F에 각각 702.7십억원, 707.9십억원으로 증가해 영업이익률이 2026F 14.9%로 하락한다."
    C18 = "서치플랫폼 매출은 2026년 77,599억원, 2027년 84,482억원으로 성장할 전망이다."

    def _hana(self) -> ClaimSet:
        return ClaimSet(report_id="hk_97166", broker="하나증권", claims=[
            _claim("c3", "hk_97166", "하나증권", self.C3, "ai_dc", [("guarantee", "", 18.7, "조원"), ("annual_gpuaas_fee", "", 3.0, "조원")]),
            _claim("c17", "hk_97166", "하나증권", self.C17, "marketing_cost",
                   [("marketing_cost", "2026E", 2527.3, "십억원"), ("marketing_cost", "2025", 1920.3, "십억원"), ("opm", "2026E", 14.9, "%")]),
            _claim("c18", "hk_97166", "하나증권", self.C18, "ad_growth", [("search_platform_revenue", "2026", 77599.0, "억원"), ("search_platform_revenue", "2027", 84482.0, "억원")]),
        ])

    def test_flow_direction(self):
        from kstock.generate.cause import flow_direction, is_classified

        self.assertEqual(flow_direction("annual_gpuaas_fee", self.C3), "cost")                       # 지급할 · 지급보증 → 나가는 돈
        self.assertEqual(flow_direction("gpuaas_fee", "GPUaaS 이용료 매출이 연 3조원으로 인식될 전망"), "revenue")
        self.assertIsNone(flow_direction("fee", "연간 이용료 3조원"))                                 # 방향을 못 정함
        self.assertIsNone(flow_direction("fee", "지급 이용료 3조원이 매출로 인식"))                     # 둘 다 → 못 정함
        self.assertTrue(is_classified("cost") and is_classified("revenue") and not is_classified("annual_gpuaas_fee") and not is_classified(None))

    def test_paid_fee_is_cost_not_ai_dc_revenue(self):
        hana = self._hana()
        skipped: list[dict] = []
        cells = cause_cells([hana], sector="KR-INTERNET", skipped=skipped)
        fee = next(c for c in cells if c.driver_id == "ai_dc" and "annual_gpuaas_fee" in c.metric_names)
        self.assertEqual(fee.variable, "cost")                                                         # 이름(fee) 이 아니라 방향(지급) 으로
        gua = next(c for c in cells if c.driver_id == "ai_dc" and "guarantee" in c.metric_names)
        self.assertEqual((gua.variable, gua.is_classified()), ("commitment", False))                   # 지급보증 18.7조원은 값이 아니다
        self.assertEqual(fee.unit, "억원")
        self.assertAlmostEqual(fee.mean, 30000.0)
        self.assertIsNone(pick_cell(cells, "ai_dc", "revenue", today=TODAY))                           # 매출 자리엔 안 들어간다
        m = new_map_skeleton("035420", "NAVER", "growth", TODAY, n_drivers=5)
        notes = fill_cause_drivers(m, sector="KR-INTERNET", claimsets=[hana], issue_set=None, today=TODAY)
        n = next(x for x in m.drivers() if x.label.startswith("AI 데이터센터"))
        self.assertIsNone(n.driver.consensus.value)                                                    # 3조원이 컨센이 되지 않는다
        self.assertEqual(n.driver.kind, "qualitative")
        self.assertNotIn("annual_gpuaas_fee", n.label)
        self.assertIn("가정 메모", n.question)
        self.assertIn("3조원", n.question)
        self.assertIn("비용, 매출 아님", n.question)
        self.assertIn("지급할 GPUaaS 이용료", n.question)                                              # 왜 비용인지 문장이 보인다
        memo = next(x for x in m.meta["draft"]["skipped_cells"] if x.get("reason") == "incompatible" and x["driver_id"] == "ai_dc")
        self.assertEqual((memo["variable"], memo["value"], memo["unit"], memo["claim_id"]), ("cost", 30000.0, "억원", "c3"))
        self.assertTrue(any(x.get("reason") == "commitment" and x["value"] == 187000.0 for x in m.meta["draft"]["skipped_cells"]))
        self.assertTrue(any("값으로 안 쓴다" in x and "3조원" in x for x in notes), notes)

    def test_unclassified_fee_is_never_a_driver_value(self):
        hana = ClaimSet(report_id="r", broker="하나증권", claims=[_claim("c1", "r", "하나증권", "연간 이용료 3조원", "ai_dc", [("annual_fee", "", 3.0, "조원")])])
        cells = cause_cells([hana], sector="KR-INTERNET")
        self.assertEqual(cells[0].variable, "annual_fee")                                              # 뜻 미정 — 이름 그대로
        self.assertFalse(cells[0].is_classified())
        self.assertIsNone(pick_cell(cells, "ai_dc", None, today=TODAY))                                # 변수를 안 줘도 뜻 미정은 안 고른다
        self.assertIsNotNone(pick_cell(cells, "ai_dc", "annual_fee", today=TODAY))                     # 꼭 집어 달라면 준다
        m = new_map_skeleton("035420", "NAVER", "growth", TODAY, n_drivers=5)
        fill_cause_drivers(m, sector="KR-INTERNET", claimsets=[hana], issue_set=None, today=TODAY)
        n = next(x for x in m.drivers() if x.label.startswith("AI 데이터센터"))
        self.assertIsNone(n.driver.consensus.value)
        self.assertIn("못 정함", n.question)

    def test_past_period_is_not_consensus(self):
        from kstock.units import is_past_period

        self.assertTrue(is_past_period("2025", TODAY))
        self.assertFalse(is_past_period("2026E", TODAY))
        self.assertFalse(is_past_period("3Q26", TODAY))                                                # 끝났지만 아직 발표 전(45일) — 전망이 맞다
        self.assertTrue(is_past_period("2Q26", TODAY))
        self.assertFalse(is_past_period("1H27", TODAY))
        self.assertFalse(is_past_period("", TODAY))
        self.assertFalse(is_past_period("2025", None))
        hana = self._hana()
        cells = cause_cells([hana], sector="KR-INTERNET")
        cons = pick_cell(cells, "marketing_cost", "cost", today=TODAY)
        self.assertEqual((cons.col, round(cons.mean)), ("2026", 25273))                                 # 2026E 가 컨센, 2025 는 아니다
        self.assertEqual(pick_cell(cells, "marketing_cost", "cost").col, "2025")                       # today 없이는 예전 그대로 (호환)
        m = new_map_skeleton("035420", "NAVER", "growth", TODAY, n_drivers=5)
        fill_cause_drivers(m, sector="KR-INTERNET", claimsets=[hana], issue_set=None, today=TODAY)
        n = next(x for x in m.drivers() if x.label.startswith("마케팅·인프라 비용"))
        self.assertEqual((n.driver.period, n.unit, n.driver.consensus.value), ("2026", "조원", 2.53))
        self.assertIn("(cost 2026)", n.label)
        self.assertIsNone(n.driver.current.value)                                                      # 2025 수치는 확정 실적 확인 전 — 값으로 안 씀
        self.assertIn("2025 1.92조원", n.driver.current.rationale)
        self.assertIn("확정 실적", n.driver.current.rationale)
        self.assertIn("지난 기간", n.question)
        memo = next(x for x in m.meta["draft"]["skipped_cells"] if x.get("reason") == "past_period")
        self.assertEqual((memo["col"], memo["value"], memo["claim_id"]), ("2025", 19203.0, "c17"))

    def test_units_are_consistent_everywhere(self):
        """원천 억원 → 노드 조원: 컨센 rationale · meta by_broker · 제안 라벨이 같은 금액을 말한다 (「77,599조원」 이 다시 안 나온다)."""
        from kstock.generate.suggest import build_suggestions
        from kstock.units import convert, fmt_amount, to_eok

        self.assertEqual(fmt_amount(77599.0, "억원"), "7.76조원")
        self.assertEqual(fmt_amount(19203.0, "억원"), "1.92조원")
        self.assertEqual(fmt_amount(4789.0, "억원"), "4,789억원")
        self.assertEqual(fmt_amount(7.76, "조원"), "7.76조원")
        self.assertEqual(fmt_amount(2527.3, "십억원"), "2.53조원")
        self.assertEqual(fmt_amount(14.9, "%"), "14.9%")
        self.assertEqual(to_eok(2527.3, "십억원"), (25273.0, "억원"))
        self.assertAlmostEqual(convert(77599.0, "억원", "조원"), 7.7599)
        self.assertAlmostEqual(convert(7.7599, "조원", "억원"), 77599.0)
        hana = self._hana()
        m = new_map_skeleton("035420", "NAVER", "growth", TODAY, n_drivers=5)
        fill_cause_drivers(m, sector="KR-INTERNET", claimsets=[hana], issue_set=None, today=TODAY)
        n = next(x for x in m.drivers() if x.label.startswith("광고 성장"))
        self.assertEqual((n.unit, n.driver.consensus.value), ("조원", 7.76))                                   # 표시는 소수 둘째 자리
        self.assertIn("하나증권 7.76조원", n.driver.consensus.rationale)                                 # 컨센 근거의 증권사별 값
        info = m.meta["draft"]["drivers"][n.id]
        self.assertEqual((info["by_broker"], info["by_broker_unit"]), ({"하나증권": 7.7599}, "조원"))      # meta 도 노드 단위
        sugg = build_suggestions(m)[n.id][0]
        self.assertEqual(sugg["label"], "컨센 7.76조원 (하나증권 7.76조원)")                                # 제안 라벨
        self.assertNotIn("77,599", sugg["label"])
        # 옛 맵 호환 — by_broker_unit 이 없고 값이 억원 원천이면 조원으로 고쳐 보인다
        info.pop("by_broker_unit")
        info["by_broker"] = {"하나증권": 77599.0}
        n.driver.my_estimate.value = None
        self.assertEqual(build_suggestions(m)[n.id][0]["label"], "컨센 7.76조원 (하나증권 7.76조원)")
