"""설계 v7 T3~T10 — 관측값 데이터 계약 · 같은 자 규칙 · 신호등 · 띠/경계 제안 · 필요 경로 · 역산(한 변수·두 변수) · 가정 표 ·
판단 수정 규칙(감지→검증→제안→승인) · 플래그와 WATCH 강제 · Claim 품질 통계/비교/게이트 · 일괄 실행 · 지식 내보내기 · CLI · 콘솔 · HTML 패널."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from pydantic import ValidationError

from helpers import NO_LLM_ENV  # noqa: F401  (sys.path 설정)
from kstock.engine.attractiveness import compute_valuation_all
from kstock.engine.decision import compute_v7_all
from kstock.engine.evidence import (
    apply_proposed_bounds,
    cadence_days,
    evidence_plan,
    light_counts,
    light_for,
    no_evidence,
    normalize_period,
    observation_from_dict,
    observe,
    period_end,
    propose_bounds,
)
from kstock.engine.lifecycle import evaluate_new_map
from kstock.engine.path import (
    assumption_table,
    build_required_path,
    inversion_table,
    required_paths,
    solve_one,
    solve_pair,
)
from kstock.engine.revision import (
    add_rule,
    alerts,
    approve_event,
    compute_flags,
    evaluate_rules,
    rule_problems,
    rules_text,
)
from kstock.engine.sensitivity import compute_all
from kstock.engine.validate import validate_new_map
from kstock.models import (
    Decision,
    DriverAssumption,
    Light,
    Observation,
    PathPoint,
    RequiredPath,
    Rule,
    Ruler,
    Scenario,
)
from kstock.reports.claim_stats import claim_compare, claim_gate, claim_stats, gate_line, stats_text
from test_v5 import _semi_claimsets
from test_v6 import TODAY, _fin_map

FIX = Path(__file__).parent / "fixtures" / "reports"
TXTS = sorted(FIX.glob("*_005930_*.txt"))


def _obs(period: str, value: float | str, *, compare_at: str | None = None, as_of: date | None = None, metric: str = "DRAM_revenue_index",
         verification: str = "confirmed", value_type: str = "abs", basis: str = "none") -> Observation:
    return Observation(metric_id=metric, period=period, value_type=value_type, basis=basis, value=value, as_of=as_of or TODAY,
                       compare_at=compare_at or period, source="테스트", verification=verification, entered_by="user")


def _v7_map(*, quarterly: bool = True):
    """test_v6 의 합성 삼성전자 맵 + 사업부 표 분기 열(DRAM 매출: 3Q26 중복 접미 · 4Q26E~4Q27E) + NAND 연간 두 점(등비 보간용)."""
    m = _fin_map()
    seg = m.meta["draft"]["segments"]
    if quarterly:
        seg["DRAM"]["revenue"].update({
            "2Q26": {"mean": 800_000.0, "n": 3}, "3Q26": {"mean": 950_000.0, "n": 1}, "3Q26E": {"mean": 1_000_000.0, "n": 3}, "3Q26P": {"mean": 990_000.0, "n": 2},
            "4Q26E": {"mean": 1_100_000.0, "n": 3}, "1Q27E": {"mean": 1_200_000.0, "n": 3}, "2Q27E": {"mean": 1_300_000.0, "n": 2}, "4Q27E": {"mean": 1_500_000.0, "n": 1},
        })
    seg["NAND"]["revenue"].update({"2026E": {"mean": 1_500_000.0, "n": 3}})
    m.meta["draft"]["sector"] = "KR-SEMI"
    compute_all(m, today=TODAY)                  # 민감도 (draft 가 하는 순서대로: 민감도 → 평가 모델 → v7)
    compute_valuation_all(m, today=TODAY)
    return m


def _with_scenarios(m):
    m["A4.2"].scenario = Scenario(name="Bear", price_source="computed", per=3.5, probability=0.3, driver_assumptions=[DriverAssumption(node_id="A1.1", delta_pct=-30)])
    m["A4.3"].scenario = Scenario(name="Base", price_source="computed", probability=0.5)
    m["A4.4"].scenario = Scenario(name="Bull", price_source="computed", per=7.0, probability=0.2, driver_assumptions=[DriverAssumption(node_id="A1.1", delta_pct=10)])
    compute_valuation_all(m, today=TODAY)
    return m


# ============================================================================ T4 데이터 계약 · 기간
class TestObservationContract(unittest.TestCase):
    def test_eight_fields_required_and_parsing(self):
        with self.assertRaises(ValidationError):
            Observation(metric_id="x", period="26Q4", value=1.0, as_of=TODAY, compare_at="26Q4")          # source 없음
        o = observation_from_dict({"metric_id": "x", "period": "4Q26E", "value": "1,234.5", "as_of": "2026-10-24T09:00", "compare_at": "26Q4", "source": "s"})
        self.assertEqual((o.value, o.as_of, o.verification, o.value_type, o.basis), (1234.5, TODAY, "confirmed", "abs", "none"))
        self.assertEqual(Observation.model_validate(o.model_dump()).ruler.label(), "x (abs/none)")
        with self.assertRaises(ValidationError):
            observation_from_dict({"metric_id": "x", "period": "26Q4", "value": 1, "as_of": "2026-10-24", "compare_at": "26Q4", "source": "s", "verification": "guess"})

    def test_periods_and_ruler(self):
        self.assertEqual(normalize_period("4Q26E"), "26Q4")
        self.assertEqual(normalize_period("2027Q1"), "27Q1")
        self.assertEqual(normalize_period("2026-11"), "2026-11")
        self.assertEqual(period_end("26Q4"), date(2026, 12, 31))
        self.assertEqual(period_end("2026-02"), date(2026, 2, 28))
        self.assertEqual(period_end("2027"), date(2027, 12, 31))
        self.assertIsNone(period_end("언젠가"))
        self.assertEqual((cadence_days("2026-10"), cadence_days("26Q4"), cadence_days("2027")), (31, 92, 366))
        a = Ruler(metric_id="dram_asp", value_type="pct", basis="qoq")
        self.assertTrue(a.same(Ruler(metric_id="dram_asp", value_type="pct", basis="qoq", unit="%")))
        self.assertFalse(a.same(Ruler(metric_id="dram_asp", value_type="abs", basis="none")))
        self.assertFalse(a.same(None))

    def test_observe_warns_but_stores(self):
        m = _v7_map()
        compute_v7_all(m, today=TODAY)
        d = m["A1.1"].driver
        self.assertIsNotNone(d.required)
        w = observe(m, "A1.1", _obs("26Q4", 110.0, metric="other_metric"))
        self.assertTrue(any("자가 다름" in x for x in w))
        w = observe(m, "A1.1", _obs("26Q4", 112.0, verification="forecast"))
        self.assertTrue(any("전망값" in x for x in w))
        w = observe(m, "A1.1", _obs("4Q26E", 113.0))
        self.assertEqual(d.observations[-1].period, "26Q4")                     # 기간 정규화
        w = observe(m, "A1.1", _obs("26Q4", 114.0))
        self.assertTrue(any("교체" in x for x in w))
        self.assertEqual(len([o for o in d.observations if o.metric_id == "DRAM_revenue_index" and o.verification == "confirmed"]), 1)
        with self.assertRaises(ValueError):
            observe(m, "A0.1", _obs("26Q4", 1.0))


# ============================================================================ T5 필요 경로
class TestRequiredPath(unittest.TestCase):
    def test_segment_quarters_dedupe_and_base_period(self):
        m = _v7_map()
        rp = build_required_path(m, "A1.1", today=TODAY)
        self.assertIsNotNone(rp)
        self.assertEqual(rp.ruler.metric_id, "DRAM_revenue_index")
        self.assertEqual(rp.ruler.index_base, "26Q3 = 100")
        ats = [p.at for p in rp.points]
        self.assertEqual(ats, ["26Q3", "26Q4", "27Q1", "27Q2", "27Q4"])           # 2Q26 은 기준 이전 → 제외
        self.assertEqual(rp.points[0].value, 100.0)
        self.assertEqual(rp.points[1].value, 110.0)                              # 3Q26E(n=3) 100만 기준 → 4Q26E 110만 = 110
        self.assertEqual(rp.points[1].source, "consensus")
        self.assertEqual(rp.points[-1].source, "broker")                         # n=1
        self.assertFalse(rp.interpolated)
        self.assertEqual(rp.target_price, 530_000.0)
        self.assertIn("사업부 표 분기 열", rp.method)

    def test_annual_geometric_interpolation_is_flagged(self):
        m = _v7_map(quarterly=False)
        # NAND 는 A 맵의 드라이버가 아니다 — HBM(A1.2) 드라이버에 NAND 사업부를 잠시 붙여 연간 보간을 본다
        m.meta["draft"]["drivers"]["A1.2"]["segment"] = "NAND"
        m.meta["draft"]["drivers"]["A1.2"]["variable"] = "volume"
        rp = build_required_path(m, "A1.2", today=TODAY)
        self.assertIsNotNone(rp)
        self.assertEqual([p.at for p in rp.points], ["2026E", "27Q1", "27Q2", "27Q3", "2027E"])
        self.assertTrue(rp.interpolated)
        self.assertEqual([p.source for p in rp.points], ["consensus", "interpolated", "interpolated", "interpolated", "consensus"])
        self.assertAlmostEqual(rp.points[-1].value, 2_216_270 / 1_500_000 * 100, places=1)
        ratio = (2_216_270 / 1_500_000) ** 0.25
        self.assertAlmostEqual(rp.points[1].value, 100 * ratio, places=1)
        self.assertIn("보간", rp.method)

    def test_no_path_for_cost_and_single_year(self):
        m = _v7_map(quarterly=False)
        self.assertIsNone(build_required_path(m, "A1.3", today=TODAY))          # 원가율 — 사업부 매출 지수로 잴 수 없다
        self.assertIsNone(build_required_path(m, "A1.1", today=TODAY))          # DRAM 연간 한 점 — 숫자를 만들지 않는다
        notes = required_paths(m, today=TODAY)
        self.assertTrue(any("A1.1 필요 경로: 없음" in n for n in notes))

    def test_path_kept_when_observations_exist(self):
        m = _v7_map()
        required_paths(m, today=TODAY)
        observe(m, "A1.1", _obs("26Q4", 108.0))
        # 새 리포트로 기준 분기가 바뀌어도 (오늘이 27Q1 이후) 관측값이 있으면 지수 100 기준을 옮기지 않는다
        later = date(2027, 2, 1)
        notes = required_paths(m, today=later)
        self.assertTrue(any("관측값이 있어 기준 유지" in n for n in notes))
        self.assertEqual(m["A1.1"].driver.required.ruler.index_base, "26Q3 = 100")
        notes = required_paths(m, today=later, overwrite=True)
        self.assertEqual(m["A1.1"].driver.required.ruler.index_base, "26Q4 = 100")
        # 사용자 점이 있으면 overwrite 가 아니면 그대로
        m["A1.1"].driver.required.points[0].source = "user"
        notes = required_paths(m, today=later)
        self.assertTrue(any("사용자 점이 있어 유지" in n for n in notes))


# ============================================================================ T7 신호등 · 띠/경계 제안
class TestLights(unittest.TestCase):
    def _map(self):
        m = _v7_map()
        compute_v7_all(m, today=TODAY)
        d = m["A1.1"].driver
        d.band, d.bound_bear, d.bounds_source = 5.0, 95.0, "user"
        d.observations.clear()
        return m

    def test_green_yellow_red_white(self):
        m = self._map()
        n = m["A1.1"]
        self.assertIsNone(light_for(n, today=TODAY).light)
        self.assertIn("관측값 없음", light_for(n, today=TODAY).reason)
        observe(m, "A1.1", _obs("26Q4", 112.0, as_of=date(2027, 1, 20)))
        r = light_for(n, today=date(2027, 1, 25))
        self.assertEqual((r.light, r.deviation, r.band), (Light.GREEN, 2.0, 5.0))
        observe(m, "A1.1", _obs("26Q4", 103.0, as_of=date(2027, 1, 20)))
        r = light_for(n, today=date(2027, 1, 25))
        self.assertEqual(r.light, Light.YELLOW)
        self.assertIn("띠 밖", r.reason)
        observe(m, "A1.1", _obs("26Q4", 94.0, as_of=date(2027, 1, 20)))
        r = light_for(n, today=date(2027, 1, 25))
        self.assertEqual(r.light, Light.RED)
        self.assertIn("Bear 경계", r.reason)
        # 전망값 · 다른 자 · 보도 등급은 신호등에 안 쓴다
        d = n.driver
        d.observations.clear()
        observe(m, "A1.1", _obs("26Q4", 94.0, verification="forecast"))
        observe(m, "A1.1", _obs("26Q4", 94.0, verification="reported"))
        observe(m, "A1.1", _obs("26Q4", 94.0, metric="dram_asp", value_type="pct", basis="qoq"))
        r = light_for(n, today=TODAY)
        self.assertIsNone(r.light)
        self.assertIn("자가 다름", r.reason)
        self.assertIn("확인됨만", r.reason)

    def test_stale_drifting_and_band_missing(self):
        m = self._map()
        n = m["A1.1"]
        observe(m, "A1.1", _obs("26Q4", 112.0, as_of=date(2027, 1, 20)))
        r = light_for(n, today=date(2027, 1, 20) + timedelta(days=2 * 92 + 1))
        self.assertEqual(r.light, Light.YELLOW)
        self.assertTrue(r.stale)
        self.assertIn("오래됨", r.reason)
        # 추세: 두 관측이 연속 Bear 쪽으로 멀어짐 → 띠 안이라도 🟡
        observe(m, "A1.1", _obs("27Q1", 116.0, as_of=date(2027, 4, 20)))        # 필요 120 → −4 (이전 +2 보다 나쁨, Bear 쪽)
        r = light_for(n, today=date(2027, 4, 25))
        self.assertEqual((r.light, r.trend), (Light.YELLOW, "drifting"))
        self.assertIn("멀어지는", r.reason)
        n.driver.band = None
        r = light_for(n, today=date(2027, 4, 25))
        self.assertEqual(r.light, Light.YELLOW)
        self.assertIn("띠 미입력", r.reason)
        # 비교 시점이 경로에 없으면 ⚪
        n.driver.observations.clear()
        observe(m, "A1.1", _obs("27Q3", 130.0, compare_at="27Q3"))
        r = light_for(n, today=date(2027, 10, 25))
        self.assertIsNone(r.light)
        self.assertIn("점이 없다", r.reason)

    def test_state_type_light(self):
        m = self._map()
        d = m["A1.2"].driver
        d.required = RequiredPath(ruler=Ruler(metric_id="hbm_stage", value_type="state", basis="none"), points=[PathPoint(at="26Q4", value="양산", source="user")])
        d.bound_bear = "탈락"
        d.bounds_source = "user"
        observe(m, "A1.2", _obs("26Q4", "퀄 진행", metric="hbm_stage", value_type="state"))
        r = light_for(m["A1.2"], today=TODAY)
        self.assertEqual(r.light, Light.YELLOW)
        r = light_for(m["A1.2"], today=date(2027, 1, 5))
        self.assertEqual(r.light, Light.RED)                                      # 기한 지남
        observe(m, "A1.2", _obs("26Q4", "양산 개시", metric="hbm_stage", value_type="state"))
        self.assertEqual(light_for(m["A1.2"], today=TODAY).light, Light.GREEN)
        observe(m, "A1.2", _obs("26Q4", "퀄 탈락", metric="hbm_stage", value_type="state"))
        self.assertEqual(light_for(m["A1.2"], today=TODAY).light, Light.RED)

    def test_counts_by_role_and_no_evidence_majority(self):
        m = self._map()
        rows = evidence_plan(m, today=TODAY, write=True)
        c = light_counts(rows)
        self.assertEqual(sum(sum(v.values()) for v in c.values()), 5)
        self.assertTrue(no_evidence(rows))                                        # 전부 ⚪
        self.assertTrue(m.meta["flags"]["no_evidence"] if "flags" in m.meta else True)
        self.assertIn("A5.5", m)
        self.assertIn("⚪", m["A5.5"].value)
        self.assertIn("DRAMeXchange", m["A5.5"].value)
        observe(m, "A1.1", _obs("26Q4", 112.0, as_of=date(2027, 1, 20)))
        rows = evidence_plan(m, today=date(2027, 1, 25), write=True)
        self.assertTrue(no_evidence(rows))                                        # 1/5 만 켜짐 → 여전히 과반 ⚪
        self.assertEqual(m["A1.1"].driver.light, Light.GREEN)
        roles = {r.node_id: r.role for r in rows}
        self.assertEqual(roles["A1.1"], "core")                                    # 섹터 사전 첫 KPI = 핵심 (제안)
        self.assertEqual({roles["A1.3"], roles["A1.5"]}, {"required"})            # 정성 드라이버 = 필수 후보
        self.assertEqual(m["A1.1"].driver.thesis_role_source, "proposed")

    def test_propose_bounds(self):
        m = _v7_map()
        compute_v7_all(m, today=TODAY)
        pr = propose_bounds(m, "A1.1")
        self.assertEqual(pr["status"], "제안")
        self.assertAlmostEqual(pr["band_pct"], abs(pr["bound_bear_pct"]) / 2, places=9)
        self.assertEqual(pr["at"], "26Q4")                                        # 오늘 이후 첫 점
        self.assertAlmostEqual(pr["bound_bear_value"], 110.0 * (1 + pr["bound_bear_pct"] / 100), places=6)
        self.assertLess(pr["bound_bear_pct"], 0)
        self.assertIn("Base × 0.8", pr["how"])                                    # Bear 시나리오 없음 → 임시 Bear 표시
        d = m["A1.1"].driver
        self.assertEqual(d.bounds_source, "proposed")                             # compute_v7_all 이 넣었다
        self.assertAlmostEqual(d.bound_bear, round(pr["bound_bear_value"], 4), places=4)
        self.assertEqual(propose_bounds(m, "A1.5")["status"], "미계산")           # 환원 변수 — 사람이 정한다
        self.assertIn("환원", propose_bounds(m, "A1.5")["note"])
        d.bound_bear, d.band, d.bounds_source = 90.0, 3.0, "user"
        pr2 = apply_proposed_bounds(m, "A1.1")
        self.assertIn("덮어쓰지 않음", pr2["note"])
        self.assertEqual((d.bound_bear, d.band), (90.0, 3.0))


# ============================================================================ T6 역산 · 가정 표
class TestInversion(unittest.TestCase):
    def test_single_variable_closed_form(self):
        m = _v7_map()
        inv = solve_one(m, "A1.1", 530_000.0, today=TODAY)
        self.assertEqual(inv.status, "ok")
        self.assertAlmostEqual(inv.value, 0.0, places=6)                          # Base 가격 = 목표가 중앙값 → 변화 0
        slope = inv.extra["slope_per_pct"]
        inv2 = solve_one(m, "A1.1", 530_000.0 + slope * 10, today=TODAY)
        self.assertAlmostEqual(inv2.value, 10.0, places=6)
        self.assertEqual(solve_one(m, "A1.5", 530_000.0, today=TODAY).status, "not_applicable")
        far = solve_one(m, "A1.1", 1.0, today=TODAY)
        self.assertEqual(far.status, "out_of_range")
        self.assertIn("범위", far.note)

    def test_pair_table_and_inversion_table(self):
        m = _v7_map()
        compute_v7_all(m, today=TODAY)
        rows = solve_pair(m, "A1.1", 262_000.0, y="per", grid=(-20.0, 0.0, 20.0), today=TODAY)
        self.assertEqual([r["x"] for r in rows], [-20.0, 0.0, 20.0])
        self.assertAlmostEqual(rows[1]["y"], 262_000 / 83_320, places=6)          # x=0 → PER = 현재가 ÷ 컨센 EPS
        self.assertGreater(rows[0]["y"], rows[2]["y"])                             # ASP 가 낮을수록 더 높은 PER 이 필요
        t = inversion_table(m, price=262_000.0, today=TODAY)
        self.assertEqual(t["pair"]["x"], "A1.1")                                   # 기본 x = 핵심 수익 변수
        self.assertEqual(t["pair"]["y_label"], "적용 PER")
        single = {r["node_id"]: r for r in t["single"]}
        self.assertEqual(single["PER"]["unit"], "배")
        self.assertEqual(single["A1.5"]["status"], "not_applicable")
        self.assertEqual(single["A1.1"]["status"], "ok")
        self.assertEqual(t["per_source"], "목표가 유도")
        t2 = inversion_table(m, price=262_000.0, pair_x="A1.2", today=TODAY)
        self.assertEqual(t2["pair"]["x"], "A1.2")

    def test_assumption_table_has_only_written_numbers(self):
        m = _v7_map()
        m.meta["draft"]["reports"] = [{"broker": "유진", "target_price": 560_000.0}, {"broker": "DS", "target_price": 530_000.0}]
        t = assumption_table(m)
        self.assertEqual(t["brokers"], ["DS", "유진"])
        self.assertEqual(t["broker_tp"]["유진"], "TP 560,000")
        rows = {r["node_id"]: r for r in t["rows"]}
        self.assertIn("asp qoq 3Q26 21%", rows["A1.1"]["by_broker"]["유진"])
        self.assertIn("asp qoq 3Q26 19%", rows["A1.1"]["by_broker"]["DS"])
        self.assertEqual(rows["A1.2"]["by_broker"]["DS"], "말하지 않음")
        self.assertEqual(rows["A1.1"]["consensus"], "20% (3Q26)")
        self.assertEqual(rows["A1.4"]["by_broker"]["유진"], "op level 3Q26 -15,000억원")   # 지수 표기 없음
        self.assertEqual(rows["PER"]["consensus"], f"목표가 유도 {530_000 / 83_320:.2f} (순환)")
        self.assertNotIn("역산", json.dumps(rows, ensure_ascii=False))


# ============================================================================ T8 판단 수정 규칙
class TestRules(unittest.TestCase):
    def _map(self):
        m = _v7_map()
        compute_v7_all(m, today=TODAY)
        d = m["A1.1"].driver
        d.band, d.bound_bear, d.bounds_source = 5.0, 95.0, "user"
        return m

    def test_rule_requirements(self):
        r = Rule(trigger_type="evidence", condition="x", action="review", grade="shift", driver_id="A1.1", threshold=95.0)
        self.assertIn("확률 이동 규칙엔 제안 %p(proposal.pct_points) 가 있어야 한다", rule_problems(r))
        r2 = Rule(trigger_type="evidence", condition="x", action="review")
        self.assertEqual(len(rule_problems(r2)), 3)
        self.assertEqual(rule_problems(Rule(trigger_type="fact", condition="x", action="review")), [])
        m = self._map()
        with self.assertRaises(ValueError):
            add_rule(m, driver_id="A0.1", grade="warn", threshold=1.0)
        n = add_rule(m, driver_id="A1.1", grade="shift", threshold=95.0, persistence=2, pct_points=15.0, today=TODAY)
        self.assertEqual((n.id, n.parent, n.rule.grade, n.rule.proposal["pct_points"], n.rule.proposal["kind"]), ("A5.6.1", "A5.6", "shift", 15.0, "shift"))
        self.assertIn("연속 2회", n.rule.condition)
        n2 = add_rule(m, driver_id="A1.3", grade="review", threshold="2분기 연속 상승", today=TODAY)
        self.assertEqual(n2.rule.proposal["kind"], "hold_new_buy")                   # 필수 조건 위반 → 신규 BUY 보류 제안
        self.assertEqual(m["A1.3"].driver.bound_bear, "2분기 연속 상승")
        self.assertTrue(any("판단 수정 규칙" in i.message for i in validate_new_map(m, today=TODAY) if i.level == "error") is False)

    def test_detect_verify_propose_approve_shift(self):
        m = self._map()
        add_rule(m, driver_id="A1.1", grade="shift", threshold=95.0, persistence=2, pct_points=15.0, today=TODAY)
        _with_scenarios(m)
        d1 = date(2027, 1, 25)
        observe(m, "A1.1", _obs("26Q4", 93.0, as_of=d1))
        ch = evaluate_rules(m, today=d1)
        ev = m.meta["draft"]["rule_events"]
        self.assertEqual((len(ch), len(ev), ev[0]["status"], ev[0]["streak"]), (1, 1, "detected", 1))
        self.assertIn("지속성 1/2", ev[0]["note"])
        self.assertFalse(m.meta["flags"]["hold_new_buy"])
        d2 = date(2027, 4, 25)
        observe(m, "A1.1", _obs("27Q1", 90.0, as_of=d2))
        evaluate_rules(m, today=d2)
        self.assertEqual((ev[0]["status"], ev[0]["streak"]), ("proposed", 2))
        self.assertEqual(ev[0]["proposal"]["kind"], "shift")
        self.assertEqual(ev[0]["impact"]["driver"], "DRAM ASP")
        self.assertNotIn("note", ev[0])
        self.assertFalse(m.meta["flags"]["hold_new_buy"])                          # 핵심 변수 — 확률 이동은 제안일 뿐
        self.assertEqual(m.meta["flags"]["pending_proposals"], 1)
        self.assertTrue(any("제안 대기" in a for a in alerts(m, today=d2)))
        self.assertTrue(any(x["kind"] == "rule" for x in m["A8.2"].value))
        out = approve_event(m, "ev1", accept=True, today=d2, pct_points=10.0)
        self.assertEqual(out["status"], "approved")
        self.assertIn("10%p 이동", out["applied"])
        self.assertAlmostEqual(m.scenario_nodes()["Base"].scenario.probability, 0.4, places=6)
        self.assertAlmostEqual(m.scenario_nodes()["Bear"].scenario.probability, 0.4, places=6)
        self.assertEqual(m.meta["flags"]["pending_proposals"], 0)
        with self.assertRaises(ValueError):
            approve_event(m, "ev1", accept=True, today=d2)
        kinds = [x["kind"] for x in m["A8.2"].value]
        self.assertIn("probability", kinds)
        self.assertIn("approval", kinds)
        txt = rules_text(m)
        self.assertIn("ev1", txt)
        self.assertIn("approved", txt)
        # 다시 띠 안으로 → 새 이벤트는 감지만, 이전 승인은 그대로
        observe(m, "A1.1", _obs("27Q2", 131.0, as_of=date(2027, 7, 25)))
        evaluate_rules(m, today=date(2027, 7, 25))
        self.assertEqual(ev[0]["status"], "approved")
        self.assertEqual(len(ev), 1)

    def test_required_violation_holds_buy_and_forces_watch(self):
        m = self._map()
        d = m["A1.1"].driver
        d.thesis_role, d.thesis_role_source = "required", "user"
        add_rule(m, driver_id="A1.1", grade="review", threshold=95.0, persistence=1, today=TODAY)
        m["A7.5"].set(Decision.BUY.value, as_of=TODAY)
        d1 = date(2027, 1, 25)
        observe(m, "A1.1", _obs("26Q4", 93.0, as_of=d1))
        evaluate_rules(m, today=d1)
        ev = m.meta["draft"]["rule_events"][0]
        self.assertEqual((ev["status"], ev["proposal"]["kind"]), ("proposed", "hold_new_buy"))
        self.assertTrue(m.meta["flags"]["hold_new_buy"])                           # 자동으로 바뀌는 유일한 것
        errs = [i.message for i in validate_new_map(m, today=d1) if i.level == "error" and i.node_id == "A7.5"]
        self.assertTrue(any("신규 BUY 보류" in e for e in errs))
        res = evaluate_new_map(m, today=d1, enforce_gate=True)
        self.assertEqual(m.decision, Decision.WATCH)
        self.assertTrue(any("WATCH 강제" in n for n in res.notes))
        # 거절하면 플래그 해제, 승인하면 재검토 플래그
        approve_event(m, "ev1", accept=False, today=d1, note="일시적")
        self.assertFalse(compute_flags(m)["hold_new_buy"])
        ev["status"] = "proposed"
        approve_event(m, "ev1", accept=True, today=d1)
        fl = compute_flags(m)
        self.assertTrue(fl["review"] and fl["hold_new_buy"])
        self.assertIn("[상태] 신규 BUY 보류 중", alerts(m, today=d1))

    def test_alerts_schedule_and_stale(self):
        m = self._map()
        d = m["A1.1"].driver
        d.next_check = date(2027, 1, 3)
        a = alerts(m, today=date(2026, 12, 29), horizon_days=7)
        self.assertTrue(any("[일정 2026-12-31]" in x and "26Q4 필요값" in x for x in a))
        self.assertTrue(any("[일정 2027-01-03]" in x for x in a))
        self.assertTrue(any("증거 없는 판단" in x for x in a))
        observe(m, "A1.1", _obs("26Q4", 112.0, as_of=date(2027, 1, 20)))
        a = alerts(m, today=date(2027, 9, 1))
        self.assertTrue(any("[노후]" in x for x in a))


# ============================================================================ T3 Claim 품질
class TestClaimQuality(unittest.TestCase):
    def test_stats_compare_gate(self):
        sets = _semi_claimsets()
        st = claim_stats(sets)
        self.assertEqual((st.n, st.status["verified"], st.core_n), (6, 6, 6))
        self.assertEqual(st.core_verified_ratio, 1.0)
        self.assertIn("Claim 품질", stats_text(st))
        g = claim_gate(sets)
        self.assertEqual(g["status"], "ok")
        self.assertIn("통과", gate_line(g))
        # 규칙 분류만이면 게이트 대상 아님
        for cs in sets:
            cs.quality = "rules"
        self.assertEqual(claim_gate(sets)["status"], "n/a")
        for cs in sets:
            cs.quality = "llm"
        # 핵심 Claim 검증률이 떨어지면 미달
        for c in sets[0].claims:
            c.status = "needs_review"
        g = claim_gate(sets)
        self.assertEqual(g["status"], "needs_claim_review")
        self.assertTrue(any("검증률" in r for r in g["reasons"]))
        self.assertIn("⚠ 미달", gate_line(g))
        # 기준 대비 재현율: 비교에 셀이 반만 있으면 < 70%
        ref = _semi_claimsets()
        cmp = _semi_claimsets()
        cmp[0].claims = cmp[0].claims[:1]
        c = claim_compare(ref, cmp)
        self.assertEqual(c["totals"]["reports"], 2)
        self.assertLess(c["totals"]["cell_recall"], 0.7)
        self.assertIn("hbm", c["per_report"]["hk_1"]["missing_drivers"])
        self.assertEqual(c["totals"]["direction_agreement"], 1.0)
        g = claim_gate(cmp, reference=ref)
        self.assertEqual(g["status"], "needs_claim_review")
        self.assertTrue(any("재현율" in r for r in g["reasons"]))


# ============================================================================ T10 일괄 실행 · 지식 내보내기 · T9 HTML
class TestBatchKnowledgeHtml(unittest.TestCase):
    def test_conditions_html_and_to_html(self):
        from kstock.render.conditions import conditions_html
        from kstock.render.native import to_html

        m = _v7_map()
        compute_v7_all(m, today=TODAY)
        h = conditions_html(m)
        for key in ("가정 표", "역산 표", "필요 경로", "증거 계획", "판단 수정 규칙", "증거 일정", "증거 없는 판단"):
            self.assertIn(key, h)
        self.assertIn("26Q3 = 100", h)
        page = to_html(m)
        self.assertIn('id="condbtn"', page)
        self.assertIn('"conditions": "', page)
        opts = json.loads(page.split("const OPTS = ", 1)[1].split(";\n", 1)[0])
        self.assertIn("가정 표", opts["conditions"])
        self.assertNotIn("</", page.split("const OPTS = ", 1)[1].split(";\n", 1)[0])       # </ 는 <\/ 로 — script 안전
        self.assertIn('"conditions": null', to_html(m, summary=True))

    def test_map_without_valuation_does_not_break(self):
        """평가 모델이 없는 맵(데모·옛 맵, 적용 PER 없음) 에서도 v7 묶음·A2.4 텍스트·HTML 패널이 돈다 — PC 테스트 로그의 '재계산 실패' 회귀."""
        from helpers import D0, build_new_map
        from kstock.render.conditions import conditions_html

        m = build_new_map()
        notes = compute_v7_all(m, today=D0)
        self.assertTrue(any("신호등" in n for n in notes))
        self.assertIn("적용 PER 없음", m["A2.4"].value)
        self.assertIn("적용 PER 없음", conditions_html(m))

    def test_export_knowledge(self):
        from kstock.generate.knowledge import export_knowledge, map_to_markdown

        m = _v7_map()
        compute_v7_all(m, today=TODAY)
        md = map_to_markdown(m)
        for key in ("삼성전자", "성립하기 위한 조건", "증거 계획", "상태 플래그", "투자 조언이 아니다"):
            self.assertIn(key, md)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "A_005930.json"
            m.save(p)
            out = export_knowledge([p, Path(td) / "없는파일.json"], out_dir=Path(td) / "k")
            self.assertEqual([x.name for x in out], ["005930.md"])
            self.assertIn("성립하기 위한 조건", out[0].read_text(encoding="utf-8"))

    def test_batch_draft_then_changes(self):
        from kstock.generate.batch import run_batch

        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, NO_LLM_ENV):
            rd = Path(td) / "reports" / "005930"
            rd.mkdir(parents=True)
            for p in TXTS:
                (rd / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
            maps = Path(td) / "maps"
            res = run_batch(Path(td) / "reports", maps_dir=maps, today=date(2026, 10, 24), claims="off", summary_path=Path(td) / "s.md", json_path=Path(td) / "s.json")
            self.assertEqual(len(res["items"]), 1)
            it = res["items"][0]
            self.assertEqual((it["kind"], it["ticker"]), ("draft", "005930"))
            self.assertTrue(Path(it["map"]).exists())
            self.assertTrue(Path(it["map"]).with_suffix(".html").exists())
            self.assertIn("초안", res["text"])
            self.assertIn("lights", it)
            self.assertTrue((Path(td) / "s.md").exists())
            self.assertEqual(json.loads((Path(td) / "s.json").read_text(encoding="utf-8"))["items"][0]["folder"], "005930")
            res2 = run_batch(Path(td) / "reports", maps_dir=maps, today=date(2026, 10, 25), claims="off")
            it2 = res2["items"][0]
            self.assertEqual(it2["kind"], "changes")
            self.assertTrue(Path(it2["changeset"]).exists())
            self.assertIn("승인은 사람", res2["text"])


# ============================================================================ A4.1 시나리오 한눈에
class TestScenarioOverview(unittest.TestCase):
    def test_empty_filled_and_old_map(self):
        from kstock.engine.scenario_model import scenario_overview
        from kstock.engine.valuation import VALUATION_NODES
        from kstock.render.native import build_tree

        m = _v7_map()
        m.meta["draft"]["scenario_candidates"] = [
            {"broker": "DS", "report_date": "2026-10-08", "kind": "산식 재계산", "name": None, "target_price": 530_000.0, "method": "PER", "multiple": 6.0, "base_year": "2027E", "base_value": 88_333.0},
            {"broker": "유진", "report_date": "2026-10-12", "kind": "목표가", "name": None, "target_price": 560_000.0}]
        compute_valuation_all(m, today=TODAY)
        self.assertEqual([n.id for n in m.children("A4")][:4], ["A4.1", "A4.2", "A4.3", "A4.4"])      # 시나리오 묶음: 한눈에 → Bear/Base/Bull
        txt = m["A4.1"].value
        self.assertEqual(txt.count("미입력"), 3)
        self.assertIn("증권사 후보 2개", txt)
        self.assertIn("1) DS 산식 재계산 PER 6배 × 2027E 88,333 → 530,000원 (+102%)", txt)
        self.assertIn("빠진 것: Bear 가정 · Base 가정 · Bull 가정", txt)
        _with_scenarios(m)
        txt = m["A4.1"].value
        self.assertIn("Bear (무엇이 무너지면 어디까지 떨어지나): DRAM ASP -30% · PER 3.50 · p 30% →", txt)
        self.assertIn("Base (논제가 그대로 실현되면): 가정 없음 = 컨센 경로 · PER 6.36* · p 50% → 530,000원 (+102%)", txt)
        self.assertIn("(* = 적용 PER 기본값", txt)
        self.assertIn("확률가중 V = Σ p·TP", txt)
        self.assertIn("세 갈래 모두", txt)
        self.assertEqual(scenario_overview(m), txt)
        # 옛 맵(A4.1 없음) 도 밸류 묶음을 돌리면 생긴다
        old = _v7_map()
        del old.nodes["A4.1"]
        self.assertNotIn("A4.1", old)
        self.assertIn("A4.1", VALUATION_NODES)
        compute_valuation_all(old, today=TODAY)
        self.assertIn("A4.1", old)
        # HTML 상자에는 첫 줄만 + '… n줄 — 우클릭 「상세 보기」', 상세에는 전부
        tree = build_tree(m)
        a4 = next(c for c in tree["children"] if c["id"] == "A4")
        n0 = next(c for c in a4["children"] if c["id"] == "A4.1")
        self.assertIn("줄 — 우클릭 「상세 보기」", n0["html"])
        self.assertNotIn("증권사 후보", n0["html"])
        self.assertIn("증권사 후보 2개", n0["detail"])


# ============================================================================ CLI · 콘솔
class TestCliV7(unittest.TestCase):
    def _run(self, args: list[str]) -> tuple[int, str]:
        from kstock.cli import main as cli_main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch.dict(os.environ, NO_LLM_ENV):
            rc = cli_main(args)
        return rc, buf.getvalue()

    def test_path_observe_evidence_rule_alerts(self):
        m = _v7_map()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "A_005930.json"
            m.save(p)
            db = str(Path(td) / "db.sqlite")
            rc, out = self._run(["path", str(p), "--db", db, "--date", "2026-10-24", "--pair", "A1.2", "--save"])
            self.assertEqual(rc, 0)
            for key in ("가정 표", "역산 표", "필요 경로", "26Q3 = 100", "두 변수 조합 — HBM"):
                self.assertIn(key, out)
            rc, out = self._run(["evidence", str(p), "--db", db, "--date", "2026-10-24", "--node", "A1.1", "--role", "required", "--band", "5", "--bear", "95"])
            self.assertEqual(rc, 0)
            self.assertIn("A1.1: 역할 required (user) · 띠 5.0 · 경계 Bear 95.0", out)
            self.assertIn("증거 계획 (A5.5)", out)
            self.assertIn("증거 일정 (A8.1)", out)
            rc, out = self._run(["rule", "add", str(p), "--db", db, "--date", "2026-10-24", "--driver", "A1.1", "--grade", "review", "--threshold", "95", "--persistence", "1"])
            self.assertEqual(rc, 0)
            self.assertIn("A5.6.1 논제 재검토 — DRAM ASP", out)
            rc, out = self._run(["observe", str(p), "A1.1", "--db", db, "--date", "2027-01-25", "--metric", "DRAM_revenue_index", "--period", "26Q4", "--value", "93",
                                 "--compare-at", "26Q4", "--source", "실적 공시", "--as-of", "2027-01-25"])
            self.assertEqual(rc, 0)
            self.assertIn("저장: A1.1 DRAM_revenue_index 26Q4 = 93.0", out)
            self.assertIn("신호등: RED", out)
            rc, out = self._run(["alerts", str(p), "--db", db, "--date", "2027-01-25", "--json"])
            self.assertEqual(rc, 1)                                                   # 알림이 있으면 1
            j = json.loads(out)
            self.assertTrue(any("제안 대기" in a for a in j["alerts"]))
            self.assertTrue(j["flags"]["hold_new_buy"])
            rc, out = self._run(["rule", "decide", str(p), "--db", db, "--date", "2027-01-26", "--event", "ev1", "--reject", "--note", "일시적"])
            self.assertEqual(rc, 0)
            self.assertIn("ev1", out)
            self.assertIn("rejected", out)
            rc, out = self._run(["rule", "list", str(p), "--db", db])
            self.assertIn("플래그: 신규 BUY 보류 아니오", out)
            # --from-file 로 수집기 JSON
            f = Path(td) / "obs.json"
            f.write_text(json.dumps([{"node_id": "A1.1", "metric_id": "DRAM_revenue_index", "period": "27Q1", "value": 125, "as_of": "2027-04-20", "compare_at": "27Q1",
                                      "source": "공시", "verification": "confirmed"}, {"node_id": "A1.1", "metric_id": "x", "period": "27Q1", "value": 1}]), encoding="utf-8")
            rc, out = self._run(["observe", str(p), "--db", db, "--date", "2027-04-25", "--from-file", str(f)])
            self.assertEqual(rc, 0)
            self.assertIn("저장: A1.1 DRAM_revenue_index 27Q1 = 125.0", out)
            self.assertIn("건너뜀 A1.1", out)

    def test_every_subcommand_help_renders(self):
        """argparse 는 help 문자열에 % 포맷을 적용한다 — '%p' · '% →' 같은 글자가 있으면 --help 가 죽는다 (PC 에서 발견)."""
        import argparse

        from kstock.cli import build_parser

        parser = build_parser()
        self.assertTrue(parser.format_help())
        sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        for name, sp in sub.choices.items():
            self.assertTrue(sp.format_help(), name)

    def test_claims_stats_and_compare(self):
        from kstock.reports.claims import save_claimset

        with tempfile.TemporaryDirectory() as td:
            ref, cmp = Path(td) / "ref", Path(td) / "cmp"
            for cs in _semi_claimsets():
                save_claimset(cs, ref)
            sets = _semi_claimsets()
            sets[0].claims = sets[0].claims[:1]
            for cs in sets:
                save_claimset(cs, cmp)
            rc, out = self._run(["claims-stats", str(cmp), "--ref", str(ref), "--json", str(Path(td) / "q.json")])
            self.assertEqual(rc, 1)                                                   # 게이트 미달 → 1
            self.assertIn("Claim 비교", out)
            self.assertIn("needs_claim_review", out)
            self.assertIn("gate", json.loads((Path(td) / "q.json").read_text(encoding="utf-8")))
            rc, out = self._run(["claims-compare", str(ref), str(cmp)])
            self.assertEqual(rc, 0)
            self.assertIn("재현율", out)
            rc, out = self._run(["claims-stats", str(Path(td) / "없음")])
            self.assertEqual(rc, 2)

    def test_export_knowledge_cli(self):
        m = _v7_map()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "A_005930.json"
            m.save(p)
            rc, out = self._run(["export-knowledge", str(p), "--out", str(Path(td) / "k")])
            self.assertEqual(rc, 0)
            self.assertIn("005930.md", out)


class TestConsoleV7(unittest.TestCase):
    def setUp(self):
        from kstock.store import Store

        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db" / "kstock.sqlite")
        m = _v7_map()
        self.store.save_map(m)
        self.map_id = m.map_id

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _run(self, inputs: list[str]) -> str:
        from test_store_console import run_console

        return run_console(self.store, inputs, today=TODAY).text()

    def test_q_o_g_t_menus(self):
        txt = self._run([
            "2", "D", "1", "", "A1.2",                      # 성립 조건 (D — Q 는 종료 키): 맵 1 · 기준 가격 비움 · 두 변수 x = HBM
            "E", "1",                                       # 회계 기준 표
            "G", "1", "role", "A1.1", "core", "5", "95", "", "back",
            "O", "1", "manual", "A1.1", "", "26Q4", "", "", "112", "", "26Q4", "실적 공시", "",
            "G", "1", "rule", "A1.1", "shift", "95", "2", "10", "back",
            "T", str(FIX.parent / "claims_none"), "",
            "0", "0",
        ])
        for key in ("가정 표", "역산 표", "두 변수 조합 — HBM", "필요 경로", "회계 기준", "A1.1: 역할 core (user) · 띠 5.0 · 경계 Bear 95.0",
                    "저장: A1.1 DRAM_revenue_index 26Q4 = 112.0", "신호등: GREEN", "A5.6.1 확률 이동 — DRAM ASP", "ClaimSet 없음", "종료."):
            self.assertIn(key, txt)
        m = self.store.load_map(self.map_id)
        self.assertEqual(m["A1.1"].driver.light, Light.GREEN)
        self.assertEqual(len(m.children("A5.6")), 1)
        self.assertEqual((m["A1.1"].driver.thesis_role, m["A1.1"].driver.bounds_source), ("core", "user"))


if __name__ == "__main__":
    unittest.main()
