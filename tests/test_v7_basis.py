"""설계 v6 §11.1 / v7 W0-1 — 회계 기준 정규화: 증권사별 순이익 기준·주식 수 기준 분류, 정규화 EPS(지배 순이익 ÷ 공시 보통주),
호환성 상태(comparable·mixed·not_comparable·unavailable) 가 평가 모델·검증·신뢰도로 전파되는 것, 표 단위 보정."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from helpers import D0  # noqa: F401  (sys.path 설정)
from kstock.engine.attractiveness import compute_valuation_all
from kstock.engine.sensitivity import financial_snapshot
from kstock.engine.validate import validate_new_map
from kstock.engine.valuation import common_tail, compute_valuation, valuation_line
from kstock.reports.digest import ReportDigest
from kstock.reports.extract import ReportFacts, parse_estimate_tables
from kstock.templates import new_map_skeleton

TODAY = date(2026, 10, 26)
N_STD = 5_846_279_000.0


def _facts(broker: str, *, np_basis: str | None, shares: float | None, eps: float, np_val: float, np_ctrl: float | None = None,
           np_total: float | None = None, unit: str | None = "십억원", shares_note: str | None = None, tp: float = 530_000.0,
           path: str | None = None) -> ReportFacts:
    est: dict[str, dict[str, float]] = {"revenue": {"2027E": 7_000_000 / 10}, "op": {"2027E": 605_600.0}, "np": {"2027E": np_val}, "eps": {"2027E": eps}}
    if np_ctrl is not None:
        est["np_ctrl"] = {"2027E": np_ctrl}
    if np_total is not None:
        est["np_total"] = {"2027E": np_total}
    f = ReportFacts(path=Path(path or f"{broker}.pdf"), broker=broker, date=TODAY, ticker="005930", name="삼성전자", target_price=tp,
                    estimates=est, unit=unit, shares_out=shares, shares_note=shares_note)
    if np_basis:
        f.basis["np"] = np_basis
    return f


class TestBrokerBasis(unittest.TestCase):
    def test_share_basis_label_and_inference(self):
        # 삼성전자 꼴: 6,648,650천주(우선주 포함) · 5,846,279천주 × 2
        d = ReportDigest.build([_facts("IBK", np_basis="지배", shares=6_648_650_000.0, eps=70_006, np_val=465_448.0),
                                _facts("유진", np_basis="전체", shares=N_STD, eps=88_945, np_val=529_421.0),
                                _facts("DS", np_basis="지배", shares=N_STD, eps=91_008, np_val=537_789.0)], ticker="005930", today=TODAY)
        b = d.broker_bases()
        self.assertEqual((b["IBK"].share_basis, b["IBK"].share_basis_source), ("보통주+우선주", "inferred"))
        self.assertEqual((b["DS"].share_basis, b["DS"].share_basis_source), ("보통주", "inferred"))
        sv, src, _ = d.shares_std()
        self.assertEqual((sv, src), (N_STD, "header"))                       # 공시 보통주 (사용자 결정 ⑦) — 우선주 포함 값은 표준에서 뺀다
        # NAVER 꼴: 159,340천주 · 152,094천주 (−4.5%) → 자기주식 제외 추정 ; 라벨 '보통주' 는 그대로
        d2 = ReportDigest.build([_facts("하나", np_basis="전체", shares=159_335_900.0, eps=16_412, np_val=2_615.0),
                                 _facts("한화", np_basis="지배", shares=152_094_000.0, eps=14_229, np_val=2_098.0),
                                 _facts("교보", np_basis="전체", shares=159_340_000.0, eps=14_196, np_val=2_227.0, shares_note="보통주")],
                                ticker="035420", today=TODAY)
        b2 = d2.broker_bases()
        self.assertEqual(b2["한화"].share_basis, "자기주식 제외")
        self.assertEqual((b2["교보"].share_basis, b2["교보"].share_basis_source), ("보통주", "label"))
        self.assertAlmostEqual(d2.shares_std()[0], (159_335_900.0 + 159_340_000.0) / 2, delta=1)   # 보통주 2곳 중앙값
        # 30% 넘게 벌어지면 불명
        d3 = ReportDigest.build([_facts("a", np_basis="지배", shares=1.0e9, eps=1000, np_val=100.0),
                                 _facts("b", np_basis="지배", shares=1.5e9, eps=1000, np_val=100.0)], ticker="000001", today=TODAY)
        self.assertTrue(all(x.share_basis is None for x in d3.broker_bases().values()))
        self.assertEqual(d3.shares_std()[1], "header_median")

    def test_eps_std_exact_ratio_excluded_and_status(self):
        # IBK·DS 는 지배 순이익(정확), 유진은 전체만 — DS 가 지배/전체 두 행을 다 적어 지배 비율 0.977 이 측정된다
        d = ReportDigest.build([_facts("IBK", np_basis="지배", shares=6_648_650_000.0, eps=70_006, np_val=465_448.0, np_ctrl=465_448.0),
                                _facts("유진", np_basis="전체", shares=N_STD, eps=88_945, np_val=529_421.0, np_total=529_421.0),
                                _facts("DS", np_basis="지배", shares=N_STD, eps=91_008, np_val=550_443.0, np_ctrl=537_789.0, np_total=550_443.0)],
                               ticker="005930", today=TODAY)
        r, n, yr = d.control_ratio("2027E")
        self.assertAlmostEqual(r, 537_789 / 550_443, places=6)
        self.assertEqual((n, yr), (1, "2027E"))
        e = d.eps_std("2027E")
        self.assertEqual(e.status, "comparable")
        self.assertAlmostEqual(e.by_broker["IBK"], 465_448.0 * 10 * 1e8 / N_STD, places=3)      # 79,614 — 증권사가 적은 70,006 (우선주 포함 N) 이 아니다
        self.assertAlmostEqual(e.by_broker["DS"], 537_789.0 * 10 * 1e8 / N_STD, places=3)       # 91,988 — 지배 행
        self.assertAlmostEqual(e.by_broker["유진"], 529_421.0 * 10 * r * 1e8 / N_STD, places=3)  # 전체 × 측정 비율
        self.assertIn("유진", e.approx)
        self.assertEqual(e.excluded, {})
        self.assertAlmostEqual(e.mean, 86_693, delta=5)
        # 비율을 잴 수 없으면(두 행 다 적은 곳이 없음) 전체만 적은 증권사는 제외 → 2곳 comparable
        d2 = ReportDigest.build([_facts("IBK", np_basis="지배", shares=6_648_650_000.0, eps=70_006, np_val=465_448.0),
                                 _facts("유진", np_basis="전체", shares=N_STD, eps=88_945, np_val=529_421.0),
                                 _facts("DS", np_basis="지배", shares=N_STD, eps=91_008, np_val=537_789.0)], ticker="005930", today=TODAY)
        e2 = d2.eps_std("2027E")
        self.assertEqual((e2.status, e2.n), ("comparable", 2))
        self.assertIn("유진", e2.excluded)
        # 전체만 적은 곳뿐이면 not_comparable ; 발행주식수가 없으면 unavailable
        d3 = ReportDigest.build([_facts("a", np_basis="전체", shares=N_STD, eps=80_000, np_val=500_000.0),
                                 _facts("b", np_basis="전체", shares=N_STD, eps=82_000, np_val=510_000.0)], ticker="005930", today=TODAY)
        self.assertEqual(d3.eps_std("2027E").status, "not_comparable")
        d4 = ReportDigest.build([_facts("a", np_basis="지배", shares=None, eps=80_000, np_val=500_000.0),
                                 _facts("b", np_basis="지배", shares=None, eps=82_000, np_val=510_000.0)], ticker="005930", today=TODAY)
        self.assertEqual(d4.eps_std("2027E").status, "unavailable")
        self.assertEqual(d4.basis_check("2027E")["status"], "unavailable")

    def test_unit_assumption_corrected_against_peers(self):
        # 단위를 못 읽은 리포트(십억원 가정) 의 매출이 다른 증권사의 1/1000 이면 조원으로 보정한다
        f_known = _facts("a", np_basis="지배", shares=N_STD, eps=80_000, np_val=500_000.0, unit="십억원")       # 매출 7,000,000억
        f_unk = _facts("b", np_basis="지배", shares=N_STD, eps=82_000, np_val=500.0, unit=None)
        f_unk.estimates["revenue"]["2027E"] = 700.0                                                    # 조원인데 단위를 못 읽음
        f_unk.estimates["op"]["2027E"] = 605.6
        d = ReportDigest.build([f_known, f_unk], ticker="005930", today=TODAY)
        cell = d.cell("revenue", "2027E")
        self.assertAlmostEqual(cell.by_broker["b"], 7_000_000.0, delta=1)                              # ×1000 보정
        self.assertTrue(any("보정" in w for w in d.warnings))


class TestBasisPropagation(unittest.TestCase):
    def _map(self, facts: list[ReportFacts]):
        d = ReportDigest.build(facts, ticker="005930", today=TODAY)
        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY, n_drivers=3)
        m["A0.4"].set(262_000.0, as_of=TODAY)
        financial_snapshot(m, d, year="2027E")
        return m

    def test_snapshot_tail_and_unified_shares(self):
        m = self._map([_facts("IBK", np_basis="지배", shares=6_648_650_000.0, eps=70_006, np_val=465_448.0, np_ctrl=465_448.0),
                       _facts("유진", np_basis="전체", shares=N_STD, eps=88_945, np_val=529_421.0, np_total=529_421.0),
                       _facts("DS", np_basis="지배", shares=N_STD, eps=91_008, np_val=550_443.0, np_ctrl=537_789.0, np_total=550_443.0)])
        fin = m.meta["draft"]["financials"]
        self.assertEqual(fin["basis"]["status"], "comparable")
        self.assertEqual(fin["eps"]["basis"], "std")
        self.assertAlmostEqual(fin["eps"]["mean"], 86_693, delta=5)
        self.assertEqual(fin["shares_std"]["value"], N_STD)
        self.assertEqual(fin["shares_out"]["median"], N_STD)
        tl = common_tail(m)
        self.assertEqual((tl.shares, tl.shares_source), (N_STD, "header"))      # 환산 N = 표준 주식 수 — 유도 N 이원화 폐지
        self.assertEqual(tl.shares_out, N_STD)
        self.assertEqual(tl.basis_status, "comparable")
        vm = compute_valuation(m, today=TODAY)
        self.assertEqual(vm.basis_status, "comparable")
        self.assertAlmostEqual(vm.price_per, 530_000.0, places=3)             # Base = 정규화 EPS × (목표가 중앙값 ÷ 정규화 EPS) 그대로
        self.assertFalse(valuation_line(vm, 262_000.0).startswith("⚠"))
        self.assertFalse(any("A3.9" in str(i) for i in validate_new_map(m, TODAY)))

    def test_not_comparable_blocks_definitive_display(self):
        m = self._map([_facts("a", np_basis="전체", shares=N_STD, eps=80_000, np_val=500_000.0),
                       _facts("b", np_basis="전체", shares=N_STD, eps=82_000, np_val=510_000.0)])
        fin = m.meta["draft"]["financials"]
        self.assertEqual(fin["basis"]["status"], "not_comparable")
        self.assertEqual(fin["eps"]["basis"], "reported")                     # 정규화 0곳 → 증권사 EPS 평균을 참고값으로
        vm = compute_valuation(m, today=TODAY)
        self.assertEqual((vm.status, vm.basis_status), ("계산됨", "not_comparable"))   # 값은 남지만
        self.assertTrue(valuation_line(vm, 262_000.0).startswith("⚠ 기준 불일치 — 참고값"))  # 확정 표시는 하지 않는다
        self.assertTrue(any("기준 불일치" in w for w in vm.warnings))
        issues = validate_new_map(m, TODAY)
        self.assertTrue(any(i.level == "error" and i.node_id == "A3.9" for i in issues))
        compute_valuation_all(m, today=TODAY)
        conf = m.meta["draft"]["confidence"]
        self.assertEqual(conf["grade"], "L")
        self.assertEqual(conf["checks"]["basis_status"], "not_comparable")
        self.assertEqual(m.meta["draft"]["valuation"]["basis_status"], "not_comparable")
        for sc in m.scenario_nodes().values():
            if sc.scenario is not None and sc.scenario.model is not None:
                self.assertEqual(sc.scenario.model.basis_status, "not_comparable")

    def test_mixed_and_unavailable_are_warnings(self):
        m = self._map([_facts("a", np_basis="지배", shares=N_STD, eps=80_000, np_val=500_000.0),
                       _facts("b", np_basis="전체", shares=N_STD, eps=82_000, np_val=510_000.0)])
        self.assertEqual(m.meta["draft"]["financials"]["basis"]["status"], "mixed")
        issues = validate_new_map(m, TODAY)
        self.assertTrue(any(i.level == "warn" and i.node_id == "A3.9" and "1곳" in i.message for i in issues))
        self.assertFalse(any(i.level == "error" and i.node_id == "A3.9" for i in issues))
        m2 = self._map([_facts("a", np_basis="지배", shares=None, eps=80_000, np_val=500_000.0),
                        _facts("b", np_basis="지배", shares=None, eps=82_000, np_val=510_000.0)])
        fin2 = m2.meta["draft"]["financials"]
        self.assertEqual(fin2["basis"]["status"], "unavailable")
        self.assertEqual(fin2["eps"]["basis"], "reported")
        tl = common_tail(m2)
        self.assertEqual(tl.shares_source, "derived")                         # 예전 규칙 그대로 (NP/EPS 유도)
        self.assertTrue(any(i.level == "warn" and i.node_id == "A3.9" for i in validate_new_map(m2, TODAY)))

    def test_old_map_without_basis_is_unchecked(self):
        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY, n_drivers=3)
        m["A0.4"].set(262_000.0, as_of=TODAY)
        m.meta.setdefault("draft", {})["financials"] = {"year": "2027E", "np": {"mean": 5_079_000.0, "n": 2}, "op": {"mean": 6_056_457.0, "n": 3},
                                                        "eps": {"mean": 83_320.0, "n": 3}, "tp_median": 530_000.0, "tp_n": 3}
        tl = common_tail(m)
        self.assertEqual((tl.basis_status, tl.shares_source), ("unchecked", "derived"))
        vm = compute_valuation(m, today=TODAY)
        self.assertEqual(vm.basis_status, "unchecked")
        self.assertFalse(any(i.node_id == "A3.9" for i in validate_new_map(m, TODAY)))


class TestExtractNpRows(unittest.TestCase):
    def test_ctrl_and_total_rows_kept_separately(self):
        text = """(단위: 십억원) 2025A 2026E 2027E
매출액 318,500 605,000 725,000
영업이익 38,600 305,300 413,300
당기순이익 34,500 242,500 342,500
지배주주순이익 33,600 236,000 333,000
순이익률(%) 10.8 40.1 47.2
EPS(원) 5,200 36,200 49,500
"""
        est, _, unit = parse_estimate_tables(text, ref_date=TODAY)
        self.assertEqual(est["np_total"]["2027E"], 342_500.0)
        self.assertEqual(est["np_ctrl"]["2027E"], 333_000.0)
        self.assertNotIn("2027E", est.get("np_total", {}).keys() - {"2025A", "2026E", "2027E"})
        self.assertEqual(est["np"]["2027E"], 342_500.0)                      # 첫 행(전체) 이 np — 예전 그대로
        self.assertNotEqual(est["np_ctrl"].get("2025A"), 10.8)               # 순이익률 행은 지배/전체 행이 아니다


if __name__ == "__main__":
    unittest.main()
