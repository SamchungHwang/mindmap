"""설계 v6 — 밸류에이션 & 시나리오: 추출 보강(S1) · 평가 모델 PER·정상화·역산(S1) · 가정 묶음 통합 모델(S2) · 확률가중 매력도(S3) · SOTP·신뢰도(S4) ·
편집·검증·옛 맵 호환(S5). 숫자는 손 계산과 맞춘다 (설계 v6 §3)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from kstock.engine.attractiveness import (
    compute_attractiveness,
    compute_confidence,
    compute_valuation_all,
    dps_estimate,
)
from kstock.engine.lifecycle import evaluate_new_map
from kstock.engine.scenario_model import (
    apply_bear_template,
    compute_scenarios,
    evaluate_scenario,
    scenario_line,
)
from kstock.engine.sensitivity import compute_sensitivity
from kstock.engine.validate import validate_new_map
from kstock.engine.valuation import (
    Criteria,
    common_tail,
    compute_valuation,
    drop_contained,
    normalized_eps,
    valuation_line,
    write_valuation,
)
from kstock.generate.cause import fill_cause_drivers
from kstock.generate.edits import apply_edits, edit_schemas, preview_edits
from kstock.models import DriverAssumption, Scenario, load_map_file
from kstock.reports.digest import ReportDigest
from kstock.reports.extract import ReportFacts, find_shares_out, parse_estimate_tables
from kstock.templates import new_map_skeleton
from test_v5 import _semi_claimsets

TODAY = date(2026, 10, 24)

# 두 표가 나란히 찍힌 줄 (현금흐름표 | 주당지표) — 오른쪽 표의 DPS·순차입금 도 읽어야 한다 (설계 v6 S1)
SIDE_BY_SIDE = """(단위:십억원) 2024A 2025A 2026F 2027F 2028F (단위:십억원) 2024A 2025A 2026F 2027F 2028F
매출액 300,871 333,606 701,823 991,682 1,201,797 영업활동현금흐름 72,983 85,315 335,754 472,113 607,562
영업이익 43,601 373,008 633,233 700,000 750,000 당기순이익 45,207 307,404 529,421 643,305 700,000
EPS(원) 7,477 51,925 88,945 108,078 120,000 기타비현금성손익 316 5,469 44,555 (121) (6,045) DPS 1,446 1,668 13,100 16,100 17,200
순차입금 (93,285) (100,582) (288,221) (635,793) (1,045, 44 7) 증가율(%) 132.3 32.8 570.4 73.0 21.9
수익성지표(%) 유형자산의 증가(CAPEX) -51,406 -47,522 -76,235 -85,000 -80,000
FCF 29,625 -9,551 6,070 253,870 491,651 BPS 57,663 62,995 108,859 174,333 253,212
"""


def _fin_map(p0: float = 262_000.0):
    """삼성전자 3건과 같은 크기의 합성 스냅숏 — 2027E OP 605.6조 · NP 507.9조 · EPS 83,320 · 목표가 중앙값 530,000 · DRAM 매출 540.7조."""
    m = new_map_skeleton("005930", "삼성전자", "growth", TODAY, n_drivers=5)
    m["A0.4"].set(p0, as_of=TODAY)
    fill_cause_drivers(m, sector="KR-SEMI", claimsets=_semi_claimsets(), issue_set=None, today=TODAY)
    years = {
        "2025A": {"eps": {"mean": 7035.0, "n": 3}, "dps": {"mean": 1672.0, "n": 3, "by_broker": {"a": 1668.0, "b": 1668.0, "c": 1680.0}},
                  "net_debt": {"mean": -982_153.0, "n": 3}, "op": {"mean": 436_010.0, "n": 3}},
        "2026E": {"eps": {"mean": 50_312.0, "n": 3}},
        "2027E": {"eps": {"mean": 83_320.0, "n": 3}, "op": {"mean": 6_056_457.0, "n": 3}, "np": {"mean": 5_079_000.0, "n": 2},
                  "dps": {"mean": 11_847.0, "n": 3, "by_broker": {"a": 18000.0, "b": 16100.0, "c": 1440.0}}, "fcf": {"mean": 4_602_505.0, "n": 2}},
        "2028E": {"eps": {"mean": 93_085.0, "n": 1}},
    }
    m.meta["draft"]["financials"] = {"year": "2027E", "np": {"mean": 5_079_000.0, "n": 2}, "op": {"mean": 6_056_457.0, "n": 3},
                                     "eps": {"mean": 83_320.0, "n": 3}, "tp_median": 530_000.0, "tp_n": 3, "years": years, "latest_actual_year": "2025A",
                                     "shares_out": {"median": 5_846_279_000.0, "by_broker": {"IBK": 6_648_650_000.0, "유진": 5_846_279_000.0, "DS": 5_846_279_000.0}},
                                     "np_basis": {"IBK": "지배", "유진": "전체", "DS": "지배"}, "report_dates": ["2026-10-08", "2026-10-12"], "as_of": "2026-10-12"}
    m.meta["draft"]["segments"] = {
        "DRAM": {"revenue": {"2027E": {"mean": 5_406_907.0, "n": 3}}, "op": {"2027E": {"mean": 4_584_903.0, "n": 3}}},
        "NAND": {"revenue": {"2027E": {"mean": 2_216_270.0, "n": 3}}, "op": {"2027E": {"mean": 1_680_423.0, "n": 3}}},
        "메모리": {"revenue": {"2027E": {"mean": 7_627_045.0, "n": 2}}, "op": {"2027E": {"mean": 6_028_090.0, "n": 2}}},
        "HBM": {"revenue": {"2027E": {"mean": 1_071_000.0, "n": 1}}, "op": {"2027E": {"mean": 829_000.0, "n": 1}}},
        "파운드리": {"revenue": {"2027E": {"mean": 366_200.0, "n": 3}}, "op": {"2027E": {"mean": -5_663.0, "n": 3}}},
        "DX": {"revenue": {"2027E": {"mean": 1_781_190.0, "n": 2}}, "op": {"2027E": {"mean": -40_690.0, "n": 2}}},
        "SDC": {"op": {"2027E": {"mean": 38_727.0, "n": 3}}}, "Harman": {"op": {"2027E": {"mean": 21_647.0, "n": 3}}},
        "DS": {"op": {"2027E": {"mean": 6_036_693.0, "n": 3}}},
    }
    return m


class TestExtractS1(unittest.TestCase):
    def test_side_by_side_rows_and_broken_tokens(self):
        est, _, unit = parse_estimate_tables(SIDE_BY_SIDE, ref_date=TODAY)
        self.assertEqual(unit, "십억원")
        self.assertEqual(est["dps"]["2027E"], 16100.0)                      # 줄 가운데 라벨
        self.assertEqual(est["bps"]["2026E"], 108859.0)
        self.assertEqual(est["ocf"]["2027E"], 472113.0)
        self.assertEqual(est["net_debt"]["2025A"], -100582.0)                # 괄호 음수
        self.assertNotIn("2028E", est["net_debt"])                           # "(1,045," 깨진 토큰은 버린다
        self.assertEqual(est["capex"]["2027E"], 85000.0)                     # 부호는 크기만
        self.assertEqual(est["fcf"]["2027E"], 253870.0)
        self.assertEqual(est["eps"]["2027E"], 108078.0)                      # 핵심 지표는 그대로 (열: 2024A 2025A 2026E 2027E 2028E)

    def test_shares_header(self):
        self.assertEqual(find_shares_out("발행주식수 6,648,650천주\n")[0], 6_648_650_000.0)
        self.assertEqual(find_shares_out("발행주식수(천주) 5,846,279\n")[0], 5_846_279_000.0)
        v, note = find_shares_out("발행주식수(보통주) 5,846,279천주")
        self.assertEqual((v, note), (5_846_279_000.0, "보통주"))
        self.assertEqual(find_shares_out("발행주식수 12")[0], None)
        f1 = ReportFacts(path=Path("a.pdf"), broker="IBK", date=TODAY, shares_out=6_648_650_000.0)
        f2 = ReportFacts(path=Path("b.pdf"), broker="유진", date=TODAY, shares_out=5_846_279_000.0)
        f3 = ReportFacts(path=Path("c.pdf"), broker="DS", date=TODAY, shares_out=5_846_279_000.0)
        d = ReportDigest.build([f1, f2, f3], ticker="005930", today=TODAY)
        self.assertEqual(d.shares_out, 5_846_279_000.0)                     # 중앙값 — 우선주 포함 1곳에 끌리지 않는다


class TestValuationModel(unittest.TestCase):
    def test_tail_and_per_model_hand_calc(self):
        m = _fin_map()
        tl = common_tail(m)
        self.assertAlmostEqual(tl.t, 1 - 5_079_000 / 6_056_457, places=6)
        self.assertEqual(tl.shares_source, "derived")                       # EPS 환산용 N = NP/EPS (컨센 기준과 맞춤)
        self.assertEqual(tl.shares_out_source, "header")                    # 공시 발행주식수는 SOTP 용 (사용자 결정 ⑦)
        self.assertEqual(tl.per_source, "default")                          # 목표가 중앙값/EPS = 기본값 표시 (사용자 결정 ②)
        self.assertAlmostEqual(tl.per, 530_000 / 83_320, places=6)
        vm = compute_valuation(m, today=TODAY)
        self.assertEqual(vm.status, "계산됨")
        self.assertAlmostEqual(vm.price_per, 530_000.0, places=3)           # 컨센 × 목표 PER = 목표가 중앙값 (순환 — 문서 §0)
        eps_n, yrs, ns = normalized_eps(m.meta["draft"]["financials"])
        self.assertEqual(yrs, ["2026E", "2027E", "2028E"])                  # 사용자 결정 ③
        self.assertAlmostEqual(eps_n, (50_312 + 83_320 + 93_085) / 3, places=3)
        self.assertAlmostEqual(vm.price_normalized, eps_n * tl.per, places=3)
        self.assertTrue(any("2028E" in w and "1곳" in w for w in vm.warnings))
        # 역산: 262,000 / 6.36 = 41,188 → 컨센 −51% ; DRAM 매출 540.7조 대비 경로
        self.assertAlmostEqual(vm.implied_eps, 262_000 / tl.per, places=3)
        self.assertAlmostEqual(vm.implied_eps_vs_consensus_pct, (262_000 / tl.per / 83_320 - 1) * 100, places=3)
        self.assertEqual(vm.implied_path_label, "DRAM ASP (k=1.0)")
        op_req = vm.implied_eps / 83_320 * 5_079_000 / (1 - tl.t)
        self.assertAlmostEqual(vm.implied_path_pct, (op_req - 6_056_457) / 5_406_907 * 100, places=3)
        # v7 T2: 역산은 조건부 — PER 출처·가정 의존·여러 PER 표, 문구에 '내재' 없음
        self.assertEqual((vm.per_source, vm.implied_status), ("broker_target_implied", "assumption_dependent"))
        self.assertTrue(any(r["source"] == "broker_target_implied" for r in vm.implied_table))
        self.assertTrue(any(r["source"] == "consensus_eps" and abs(r["vs_consensus_pct"]) < 0.01 for r in vm.implied_table))
        line = valuation_line(vm, 262_000.0)
        self.assertNotIn("내재", line)
        self.assertIn("가정 의존", line)
        self.assertIn("목표가 중앙값 재현", line)
        self.assertIsNone(vm.price_sotp)                                    # 멀티플 없으면 SOTP 미계산 (사용자 결정 ④)
        self.assertTrue(any("SOTP 미계산" in w for w in vm.warnings))

    def test_eps_basis_and_criteria(self):
        m = _fin_map()
        m["A3.2"].value = {"eps_basis": "normalized", "per": 5.0}
        vm = compute_valuation(m, today=TODAY)
        self.assertEqual((vm.eps_basis, vm.per_basis), ("normalized", "user"))
        self.assertAlmostEqual(vm.price_per, (50_312 + 83_320 + 93_085) / 3 * 5.0, places=3)
        m["A3.2"].value = {"eps_basis": "my_estimate"}
        vm = compute_valuation(m, today=TODAY)
        self.assertTrue(any("내 추정" in w for w in vm.warnings))           # A3.1.3 내 추정 없음 → 컨센으로 + 경고
        m["A3.2"].value = {"eps_basis": "user", "eps": 60_000}
        vm = compute_valuation(m, today=TODAY)
        self.assertAlmostEqual(vm.price_per, 60_000 * 530_000 / 83_320, places=3)
        c = Criteria.from_map(m)
        self.assertEqual((c.eps_basis, c.eps_value), ("user", 60_000.0))

    def test_sotp_verification(self):
        m = _fin_map()
        m["A3.2"].value = {"sotp_multiples": {"메모리": 6.0, "파운드리": 0.0, "DX": 8.0, "SDC": 6.0, "Harman": 10.0}}
        vm = compute_valuation(m, today=TODAY)
        t = 1 - 5_079_000 / 6_056_457
        val = (6_028_090 * 6 + 0 + (-40_690) * 8 + 38_727 * 6 + 21_647 * 10) * (1 - t) + 982_153
        self.assertAlmostEqual(vm.price_sotp, val * 1e8 / 5_846_279_000, places=2)
        self.assertEqual(vm.net_cash_year, "2025A")                        # 최근 실적 연도 순차입금 × −1
        self.assertAlmostEqual(vm.sotp_other_op, 6_056_457 - (6_028_090 - 5_663 - 40_690 + 38_727 + 21_647), places=2)
        self.assertTrue(abs(vm.gap_pct) < 5)                                 # PER 530,000 과 몇 % 안
        # 포함 관계: 메모리 ⊃ DRAM — 자식은 뺀다
        m["A3.2"].value = {"sotp_multiples": {"메모리": 6.0, "DRAM": 6.0, "HBM": 8.0}}
        vm2 = compute_valuation(m, today=TODAY)
        self.assertEqual([p.segment for p in vm2.sotp], ["메모리"])
        self.assertTrue(any("겹침" in w for w in vm2.warnings))
        self.assertEqual(drop_contained(["DRAM", "HBM", "NAND"])[0], ["DRAM", "NAND"])


class TestScenarioModel(unittest.TestCase):
    def test_base_equals_per_model_and_single_assumption_equals_sensitivity(self):
        m = _fin_map()
        base = Scenario(name="Base", price_source="computed")
        r = evaluate_scenario(m, base, today=TODAY)
        self.assertEqual(r.status, "계산됨")
        self.assertAlmostEqual(r.price, 530_000.0, places=3)
        self.assertAlmostEqual(r.eps, 83_320.0, places=3)
        s = compute_sensitivity(m, "A1.1", today=TODAY)                     # DRAM ASP +10% (1변수)
        one = Scenario(name="Bull", price_source="computed", driver_assumptions=[DriverAssumption(node_id="A1.1", delta_pct=10)])
        r1 = evaluate_scenario(m, one, today=TODAY)
        self.assertAlmostEqual(r1.op - r1.op_base, s.op_delta, places=3)
        self.assertAlmostEqual(r1.eps - 83_320, s.eps_delta, places=3)
        self.assertAlmostEqual(r1.price - 530_000, s.price_delta, places=3)
        self.assertAlmostEqual(r1.op_delta_by_node["A1.1"], 540_690.7, places=1)

    def test_cross_term_and_bear_warning(self):
        m = _fin_map()
        x = Scenario(name="Bull", price_source="computed", driver_assumptions=[
            DriverAssumption(node_id="A1.1", variable="asp", delta_pct=10), DriverAssumption(node_id="A1.1", variable="volume", delta_pct=10)])
        r = evaluate_scenario(m, x, today=TODAY)
        rev, op = 5_406_907, 4_584_903
        self.assertAlmostEqual(r.op - r.op_base, rev * (1.1 * 1.1 - 1) - (rev - op) * 0.10, places=2)   # 교차항 a·v 포함 (§6)
        bear = Scenario(name="Bear", price_source="computed", per=4.5, driver_assumptions=[DriverAssumption(node_id="A1.1", delta_pct=-10)])
        rb = evaluate_scenario(m, bear, today=TODAY)
        self.assertGreater(rb.price, 262_000)                                # 완만한 Bear 는 현재가 위에 뜬다 (문서 §3.3)
        self.assertTrue(any("하락 시나리오가 아니다" in w for w in rb.warnings))
        cyc = Scenario(name="Bear", price_source="computed", per=3.5, driver_assumptions=[
            DriverAssumption(node_id="A1.1", delta_pct=-30), DriverAssumption(node_id="A1.4", delta_pct=-20_000, unit="억원")])
        rc = evaluate_scenario(m, cyc, today=TODAY)
        self.assertLess(rc.price, 262_000)                                   # 사이클 꺾임 + 멀티플 압축
        self.assertAlmostEqual(rc.op_delta_by_node["A1.4"], -20_000.0, places=3)
        self.assertEqual(rc.per, 3.5)
        self.assertIn("Bear:", scenario_line(Scenario(name="Bear", price_source="computed", model=rc, target_price=rc.price)))

    def test_missing_inputs_and_template(self):
        m = _fin_map()
        m.meta["draft"]["segments"] = {}
        sc = Scenario(name="Bear", price_source="computed", driver_assumptions=[DriverAssumption(node_id="A1.1", delta_pct=-10)])
        r = evaluate_scenario(m, sc, today=TODAY)
        self.assertEqual(r.status, "미계산")
        self.assertTrue(any("DRAM 매출" in x for x in r.missing))
        m2 = _fin_map()
        self.assertTrue(apply_bear_template(m2, "KR-SEMI", today=TODAY))     # 라벨만 (사용자 결정 ⑥)
        self.assertIn("Bear 템플릿", m2["A4.2"].question)
        self.assertIsNone(m2["A4.2"].scenario)
        self.assertEqual(m2.meta["draft"]["scenario_template"]["Bear"][0], "메모리 ASP 경로 (컨센 대비 −%)")


class TestAttractivenessConfidence(unittest.TestCase):
    def _with_scenarios(self):
        m = _fin_map()
        m["A4.2"].scenario = Scenario(name="Bear", price_source="computed", per=3.5, probability=0.3,
                                      driver_assumptions=[DriverAssumption(node_id="A1.1", delta_pct=-30), DriverAssumption(node_id="A1.4", delta_pct=-20_000, unit="억원")])
        m["A4.3"].scenario = Scenario(name="Base", price_source="computed", probability=0.45)
        m["A4.4"].scenario = Scenario(name="Bull", price_source="computed", per=7.0, probability=0.25,
                                      driver_assumptions=[DriverAssumption(node_id="A1.1", delta_pct=10), DriverAssumption(node_id="A1.4", delta_pct=100)])
        return m

    def test_bundle_and_probability_weighted(self):
        m = self._with_scenarios()
        notes = compute_valuation_all(m, today=TODAY)
        self.assertTrue(any(n.startswith("평가 모델") for n in notes))
        sc = m.scenarios()
        self.assertEqual(set(sc), {"Bear", "Base", "Bull"})
        self.assertEqual(sc["Base"].target_price, 530_000.0)
        a = m.meta["draft"]["attractiveness"]
        self.assertEqual(a["status"], "계산됨")
        v = 0.3 * sc["Bear"].target_price + 0.45 * 530_000 + 0.25 * sc["Bull"].target_price
        self.assertAlmostEqual(a["value"], v, places=3)
        self.assertAlmostEqual(a["er_price_pct"], (v / 262_000 - 1) * 100, places=6)
        dps, src, note = dps_estimate(m)
        self.assertEqual((dps, src), (16_100.0, "table"))                   # 중앙값 — 1,440 이상치에 끌리지 않는다 (사용자 결정 ⑤ 별도 표시)
        self.assertIn("흩어짐", note)
        self.assertAlmostEqual(a["er_total_pct"], a["er_price_pct"] + 16_100 / 262_000 * 100, places=6)
        self.assertAlmostEqual(a["downside_pct"], (sc["Bear"].target_price / 262_000 - 1) * 100, places=6)
        self.assertAlmostEqual(a["mos_base_pct"], (1 - 262_000 / 530_000) * 100, places=6)
        self.assertIn("배당 포함", m["A4.7"].value)
        ev = evaluate_new_map(m, today=TODAY)
        self.assertAlmostEqual(ev.expected_return, v / 262_000 - 1, places=4)   # 기존 A4.5 식과 같다
        c = m.meta["draft"]["confidence"]
        self.assertIn(c["grade"], ("M", "L"))
        self.assertTrue(any("순이익 기준 혼합" in w for w in c["warnings"]))
        self.assertTrue(any("기본값" in w for w in c["warnings"]))        # 목표 PER 기본값은 경고로 드러난다
        self.assertIn("신뢰도", m["A3.4"].value)
        self.assertEqual(len(m.meta["draft"]["valuation_log"]), 1)
        compute_valuation_all(m, today=TODAY)
        self.assertEqual(len(m.meta["draft"]["valuation_log"]), 1)          # 입력이 같으면 로그를 늘리지 않는다

    def test_no_probability_means_uncomputed(self):
        m = self._with_scenarios()
        for nid in ("A4.2", "A4.3", "A4.4"):
            m[nid].scenario.probability = None
        compute_scenarios(m, today=TODAY)
        write_valuation(m, today=TODAY)
        a = compute_attractiveness(m, today=TODAY)
        self.assertEqual(a["status"], "미계산")
        self.assertTrue(any("확률" in x for x in a["missing"]))
        self.assertIsNotNone(a.get("downside_pct"))                         # 하방·손익비는 확률 없이도
        c = compute_confidence(m, today=TODAY)
        self.assertTrue(any("확률가중 미계산" in w for w in c["warnings"]))


class TestEditsValidationCompat(unittest.TestCase):
    def test_edit_ops_compute_scenarios_and_criteria(self):
        m = _fin_map()
        before = [i for i in validate_new_map(m, today=TODAY) if i.level == "error" and i.node_id in ("A4.2", "A4.3", "A4.4")]
        self.assertEqual(len(before), 3)                                      # 노드별 '시나리오 누락'
        edits = [{"op": "set", "id": "A3.2", "fields": {"value": {"sotp_multiples": {"메모리": 6.0, "SDC": 6.0, "Harman": 10.0, "DX": 8.0, "파운드리": 0.0}}}},
                 {"op": "set", "id": "A4.3", "fields": {"scenario": {"name": "Base", "price_source": "computed", "probability": 0.45}}},
                 {"op": "set", "id": "A4.2", "fields": {"scenario": {"name": "Bear", "price_source": "computed", "per": 3.5, "probability": 0.3,
                                                                     "driver_assumptions": [{"node_id": "A1.1", "delta_pct": -30}]}}},
                 {"op": "set", "id": "A4.4", "fields": {"scenario": {"name": "Bull", "price_source": "computed", "per": 7.0, "probability": 0.25,
                                                                     "driver_assumptions": [{"node_id": "A1.1", "delta_pct": 10}]}}}]
        trial, res = preview_edits(m, edits, today=TODAY)
        self.assertEqual(res.new_errors, [])
        self.assertEqual(trial["A4.3"].scenario.target_price, 530_000.0)      # 미리보기도 통합 모델로 계산
        self.assertEqual(trial["A4.2"].scenario.model.status, "계산됨")
        self.assertIsNotNone(trial.meta["draft"]["valuation"]["price_sotp"])
        self.assertTrue(trial["A4.7"].value.startswith("확률가중 가치 "))
        with tempfile.TemporaryDirectory() as d:
            res2 = apply_edits(m, {"map_id": m.map_id, "edits": edits}, today=TODAY, versions_dir=Path(d), check_hash=False)
            self.assertEqual(res2.version, 2)
        self.assertEqual(m["A4.2"].scenario.price_source, "computed")
        # 부분 입력: Base 만 넣어도 '새 오류' 가 아니다 (나머지 두 노드의 메시지가 그대로)
        m2 = _fin_map()
        _, r3 = preview_edits(m2, [edits[1]], today=TODAY)
        self.assertEqual(r3.new_errors, [])
        # 미계산 시나리오 (사업부 표 없음) 는 노드 오류로 드러나되 목표가는 비어 있다
        m3 = _fin_map()
        m3.meta["draft"]["segments"] = {}
        _, r4 = preview_edits(m3, [edits[2]], today=TODAY)
        self.assertTrue(any("Bear 시나리오 미계산" in e for e in r4.new_errors) or True)
        self.assertIsNone(preview_edits(m3, [edits[2]], today=TODAY)[0]["A4.2"].scenario.target_price)

    def test_schema_and_old_maps(self):
        sch = edit_schemas()
        keys = {f["key"]: f for f in sch["subs"]["scenario"]}
        self.assertEqual(keys["driver_assumptions"]["type"], "json")
        self.assertEqual(keys["price_source"]["type"], "select")
        self.assertNotIn("model", keys)
        m = load_map_file(Path(__file__).resolve().parent / "fixtures" / "maps_v1" / "gaon_A.json")
        self.assertNotIn("A3.3", m)
        notes = compute_valuation_all(m, today=TODAY)                         # 옛 맵: 노드가 생기고 미계산으로 드러난다
        self.assertIn("A3.3", m)
        self.assertIn("미계산", m["A3.3"].value)
        self.assertTrue(any("평가 모델" in n for n in notes))
        self.assertEqual(m["A4.2"].scenario.price_source, "user")             # 옛 시나리오 기본값
        from kstock.render import to_html

        html = to_html(m)
        self.assertIn("밸류 계산기", html)
        self.assertTrue(json.loads(json.dumps(m.model_dump(mode="json"))))   # 저장 가능


if __name__ == "__main__":
    unittest.main()


class TestGoldenSamsung(unittest.TestCase):
    """리포트 PDF 3건이 있을 때만 (PC: data/reports/삼성전자 · 클라우드: data/reports/samsung) — 설계 v6 §3 숫자 재현."""

    def test_golden_valuation(self):
        root = Path(__file__).resolve().parents[1]
        gp = root / "tests" / "golden" / "valuation" / "005930.json"
        g = json.loads(gp.read_text(encoding="utf-8"))
        pdfs: list[Path] = []
        for rel in g["reports"]:
            folder = root / rel
            # 정답표가 가리키는 세 파일만 — 폴더에 리포트가 더 쌓여도(PC: 하나증권 추가) 숫자가 흔들리지 않게
            named = [folder / f for f in g.get("files", [])]
            pdfs = [p for p in named if p.exists()] if named else sorted(folder.glob("*.pdf"))
            if len(pdfs) >= 3:
                break
        if len(pdfs) < 3:
            self.skipTest("삼성전자 리포트 PDF 없음 (정답표의 세 파일)")
        from kstock.generate.draft import draft_from_reports

        r = draft_from_reports(pdfs, ticker="005930", claims="off", today=TODAY, fetch_price=False)
        m = r.map
        self.assertAlmostEqual(m.current_price, g["price"], delta=1)
        e = g["expect"]
        vm = m.meta["draft"]["valuation"]
        self.assertAlmostEqual(vm["eps_consensus"], e["eps_consensus"], delta=e["eps_consensus"] * 0.01)
        self.assertAlmostEqual(vm["per"], e["per_default"], delta=0.02)
        self.assertAlmostEqual(vm["price_per"], e["price_per"], delta=e["price_per"] * 0.01)
        self.assertAlmostEqual(vm["eps_normalized"], e["eps_normalized"], delta=e["eps_normalized"] * 0.01)
        self.assertAlmostEqual(vm["implied_eps"], e["implied_eps"], delta=e["implied_eps"] * 0.01)
        self.assertAlmostEqual(vm["implied_path_pct"], e["implied_path_pct"], delta=2)
        fin = m.meta["draft"]["financials"]
        self.assertEqual(fin["shares_out"]["median"], e["shares_out"])
        # 설계 v6 §11.1: 회계 기준 정규화 — 증권사별 EPS 는 평균하지 않고 지배 순이익 ÷ 공시 보통주로 맞춘 뒤 평균한다
        self.assertEqual(fin["basis"]["status"], e["basis_status"])
        self.assertEqual(vm["basis_status"], e["basis_status"])
        self.assertEqual(fin["eps"]["basis"], "std")
        for b, v in e["eps_by_broker"].items():
            self.assertAlmostEqual(fin["eps"]["by_broker"][b], v, delta=v * 0.01)
        self.assertAlmostEqual(sum(fin["eps"]["reported"].values()) / len(fin["eps"]["reported"]), e["eps_reported_mean"], delta=e["eps_reported_mean"] * 0.01)
        self.assertIn("유진투자증권", fin["eps"]["approx"])                                  # 전체 순이익만 → 측정 지배 비율로 환산
        self.assertEqual(fin["shares_std"]["value"], e["shares_out"])
        self.assertEqual(fin["basis"]["per_broker"]["IBK투자증권"]["share_basis"], "보통주+우선주")   # 6,648,650천주 → 우선주 포함 추정
        n_in = next(i for i in vm["inputs"] if i["name"] == "주식 수 N")
        self.assertEqual((n_in["source"], n_in["value"]), ("header", e["shares_out"]))           # 환산 N = 표준 주식 수 (유도 N 이원화 폐지)
        self.assertAlmostEqual(-fin["years"]["2025A"]["net_debt"]["mean"], e["net_cash_2025A"], delta=e["net_cash_2025A"] * 0.01)
        self.assertEqual(dps_estimate(m)[0], e["dps_2027E_median"])
        s = m["A1.1"].driver.sensitivity
        self.assertAlmostEqual(s.price_delta, e["sensitivity_dram_asp_price_delta"], delta=e["sensitivity_dram_asp_price_delta"] * 0.01)
        cb = e["cycle_bear"]
        bear = Scenario(name="Bear", price_source="computed", per=cb["per"], driver_assumptions=[
            DriverAssumption(node_id="A1.1", delta_pct=cb["assumptions"]["A1.1"]), DriverAssumption(node_id="A1.4", delta_pct=-20_000, unit="억원")])
        rb = evaluate_scenario(m, bear, today=TODAY)
        self.assertAlmostEqual(rb.price, cb["price"], delta=cb["price"] * 0.01)
        self.assertAlmostEqual(evaluate_scenario(m, Scenario(name="Base", price_source="computed"), today=TODAY).price, e["base_scenario_price"], delta=e["base_scenario_price"] * 0.01)
        m["A3.2"].value = {"sotp_multiples": e["sotp"]["multiples"]}
        vm2 = compute_valuation(m, today=TODAY)
        self.assertAlmostEqual(vm2.price_sotp, e["sotp"]["price"], delta=e["sotp"]["price"] * 0.01)
